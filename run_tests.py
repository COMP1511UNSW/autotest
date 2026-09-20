# run all the tests
#
# Tests run in two phases so that they can run concurrently while the output
# stays identical to a serial run:
#
# 1. serially, in test order, in the shared working directory: checkers,
#    pre_compile_command and compilation, each command line once however many
#    tests share it;
# 2. concurrently (parallel_tests at a time), each test in its own copy of
#    the working directory: setup_command, then the test command once per
#    compile command.
#
# What a phase would print is captured per test and printed by the main
# thread in test order, so the output does not depend on the number of
# workers or the order tests finish in.  Workers never chdir: every path is
# resolved against an explicit directory and the main thread's cwd stays the
# shared working directory (which glob, helper.py and upload_results.py rely
# on).

import concurrent.futures
import copy
import glob
import io
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
from argparse import Namespace
from typing import Any, Callable, Optional, Union

from termcolor import colored as termcolor_colored

from command_line_arguments import REPO
from parameter_descriptions import (
    finalize_dcc_output_checking,
    select_command_from_alternatives,
)
from parse_test_specification import output_file_without_parameters
from run_test import CommandRunner, _Test
from subprocess_with_resource_limits import stopped
from util import AutotestException, die

LOG_FILE_NAME = "autotest.log"
SANDBOX_ROOT_PREFIX = ".sandbox-root-"
TEST_DIRECTORY_PREFIX = ".test-"
_COPY_CHUNK_BYTES = 1 << 20


def run_tests_creating_log(tests, parameters, args):
    class Tee:
        def __init__(self, stream):
            self.stream = stream
            self.fileno = stream.fileno

        def flush(self):
            sys.stdout.flush()
            self.stream.flush()

        def write(self, message):
            sys.stdout.write(message)
            self.stream.write(message)

    # the log lives in the shared directory (upload_results.py reads it from
    # there) and is left out of the per-test copies
    with open(LOG_FILE_NAME, "w", encoding="utf-8") as f:
        return run_tests(tests, parameters, args, file=Tee(f))


class TestOutcome:
    """
    What running one test produced, handed from a worker to the main thread.

    text is everything printed up to (not including) the verdict, in the
    order a serial run prints it: checker and compile output, the
    "Test label (description) - " line, setup_command output.  For a test
    which could not be run (status -1) it holds the whole line.  The verdict
    is printed by the main thread because "same as Test X" depends on the
    tests printed before it.
    """

    def __init__(self, status: int, text: str):
        self.status = status  # 1 passed, 0 failed, -1 could not be run
        self.text = text
        self.test_passed: Optional[bool] = None
        self.stdout: Any = None
        self.stderr: Any = None
        self.short_explanation: Optional[str] = ""
        self.long_explanation = ""
        self.test_dir: Optional[str] = None


class RunContext:
    """
    Everything shared by the tests of one run.

    This replaces the module-level mutable default arguments the previous
    code used as caches (previous_errors, linked_program, chmod_cache,
    result_cache): those leaked between runs in the same process and could
    not be shared safely between threads.
    """

    def __init__(
        self,
        tests: dict[str, _Test],
        parameters: dict[str, Any],
        args: Namespace,
        file=sys.stdout,
    ):
        self.tests = tests
        self.parameters = parameters
        self.args = args
        self.file = file
        self.debug: int = parameters["debug"]
        self.colored: Callable[..., str] = (
            termcolor_colored
            if parameters["colorize_output"]
            else lambda x, *_args, **_kwargs: x
        )
        # absolute: copy_files_to_temp_directory chdir'd into it
        self.shared_dir = os.getcwd()
        # per-test directories and sandbox roots are siblings of the shared
        # directory: outside it (so they are not copied into each test) and
        # inside the temporary tree cleanup_all() removes
        self.temp_root = os.path.dirname(self.shared_dir)
        self.lock = threading.Lock()
        self.sandbox_notes: list[str] = []
        self.runner = CommandRunner(
            getattr(args, "sandbox_config", None),
            self.temp_root,
            debug=self.debug,
            note_sandbox=self.note_sandbox,
        )
        # support commands (checkers, compilers, ...) run once per distinct
        # command line however many tests share it
        self.support_command_results: dict[str, bool] = {}
        self.chmod_done: set = set()
        # the program link in the shared directory (see prepare_test)
        self.linked_program: dict[str, str] = {}
        # hex-stripped long explanation -> label of the first test which
        # produced it, for "failed (X - same as Test Y)"
        self.previous_errors: dict[str, str] = {}

    def note_sandbox(self, sandbox) -> None:
        """collect what the sandboxes had to say, once, for debug output"""
        with self.lock:
            for note in sandbox.notes:
                if note not in self.sandbox_notes:
                    self.sandbox_notes.append(note)

    def ignore_when_copying(self, directory: str, names: list[str]) -> list[str]:
        """the shutil.copytree ignore function for per-test copies"""
        if directory != self.shared_dir:
            return []
        return [
            name
            for name in names
            if name == LOG_FILE_NAME
            or name.startswith((SANDBOX_ROOT_PREFIX, TEST_DIRECTORY_PREFIX))
        ]

    def run_support_command(  # noqa: C901 - one branch per kind of support command
        self,
        command: Union[list[str], str],
        parameters: dict[str, Any],
        work_dir: str,
        out,
        arguments: Optional[list[str]] = None,
        unlink: Optional[str] = None,
        print_command: bool = False,
        cache: bool = True,
    ) -> bool:
        """
        run a support command (checker, pre_compile_command, compiler,
        setup_command) in work_dir, shell used iff command is a string,
        writing the command line (if print_command) and its output to out

        The command is not resource-limited, unlike tests.  It runs with the
        test's environment: checkers and compilers named relative to the test
        directory are found through the "." in its PATH, and its HOME (".")
        exists inside the sandbox.  Compilers still find themselves because
        the original PATH is part of the test's PATH.

        If cache is true and the command line has been run before, it is not
        run again and the previous result is returned: checkers and compilers
        run once however many tests share them, but setup_command runs for
        every test.

        If unlink is set, it is removed iff it is a symlink, before the
        command is run (so a compiler does not write through the link to a
        previous compilation's binary).

        return True iff the command exits with status 0
        """
        arguments = arguments or []
        cmd: Union[list[str], str]
        if isinstance(command, str):
            cmd = command + " " + " ".join(arguments)
            cmd_str = cmd
        else:
            cmd = list(command) + list(arguments)
            cmd_str = " ".join(cmd)

        if cache:
            with self.lock:
                cached = self.support_command_results.get(cmd_str)
            if cached is not None:
                if self.debug > 1:
                    print(
                        "Using cached result of",
                        cached,
                        "for",
                        cmd_str,
                        file=sys.stderr,
                    )
                return cached

        if unlink:
            unlink_path = os.path.join(work_dir, unlink)
            if os.path.exists(unlink_path) and os.path.islink(unlink_path):
                if self.debug > 1:
                    print("run_support_command unlinking: ", unlink_path)
                os.unlink(unlink_path)

        if print_command or self.debug:
            print(cmd_str, file=out, flush=True)

        # a command which can not be started (compiler not installed, ...)
        # comes back as a failure with a message rather than an exception
        stdout, stderr, returncode = self.runner.run(
            cmd, parameters, work_dir, parameters["environment"], support_command=True
        )
        # stdout then stderr, with universal newlines, as the merged text
        # stream the previous implementation showed
        out.write(support_command_text(stdout) + support_command_text(stderr))

        if self.debug > 1:
            print(f"{cmd} exit status {returncode}", file=sys.stderr)

        result = bool(returncode == 0)
        if cache:
            with self.lock:
                self.support_command_results[cmd_str] = result
        return result


def support_command_text(output: bytes) -> str:
    return re.sub("\r\n?", "\n", output.decode("utf-8", errors="replace"))


def run_tests(
    tests: dict[str, _Test],
    global_parameters: dict[str, Any],
    args: Namespace,
    file=sys.stdout,
) -> int:
    context = RunContext(tests, global_parameters, args, file)
    debug = context.debug
    colored = context.colored

    exit_status = run_legacy_hooks(context)
    if exit_status is not None:
        return exit_status
    if not tests:
        die(f"autotest not available for {args.exercise}")
    if not args.labels:
        die("nothing to test")

    tests_to_run = [test for (label, test) in tests.items() if label in args.labels]

    # If a file needed for all tests is missing, don't run any tests to avoid confusing output
    files_required_for_all_tests = set.intersection(
        *[set(test.parameters["files"]) for test in tests_to_run]
    )
    missing_files = [f for f in files_required_for_all_tests if not glob.glob(f)]
    if missing_files:
        error_msg = "Unable to run tests because "
        error_msg += (
            f"these files were missing: {colored(' '.join(missing_files), 'red')}"
        )
        print(error_msg, flush=True, file=file)
        return 1

    results = run_tests_concurrently(context, tests_to_run)

    if debug > 1 and context.sandbox_notes:
        print("sandbox notes:", "; ".join(context.sandbox_notes), file=sys.stderr)

    n_tests_passed = results.count(1)
    n_tests_failed = results.count(0)
    n_tests_not_run = results.count(-1)

    if n_tests_passed:
        print(
            colored(str(n_tests_passed) + " tests passed", "green"), end=" ", file=file
        )
    else:
        print(colored("0 tests passed", "red"), end=" ", file=file)
    if n_tests_failed:
        print(colored(str(n_tests_failed) + " tests failed", "red"), end="", file=file)
    else:
        print(colored("0 tests failed", "green"), end=" ", file=file)
    if n_tests_not_run:
        print("", n_tests_not_run, "tests could not be run", end="", file=file)
    print(file=file)
    return 1 if n_tests_failed + n_tests_not_run else 0


def run_legacy_hooks(context: RunContext) -> Optional[int]:
    """
    Honour the old compile.sh / runtests.pl hooks and return runtests.pl's
    exit status, or None when there is no runtests.pl.

    A hook is honoured only when the autotest itself supplies it: a
    submission could otherwise plant a compile.sh which then runs with the
    privileges of whoever runs autotest.  The copy run is the one in the
    working directory, which is the supplied copy because supplied files are
    copied over the submission last.
    """
    supplied_files_directory = context.parameters.get("supplied_files_directory", "")
    if not supplied_files_directory:
        return None
    args = context.args
    if os.path.isfile(
        os.path.join(supplied_files_directory, "compile.sh")
    ) and not context.run_support_command(
        ["./compile.sh"] + sorted(args.programs),
        context.parameters,
        context.shared_dir,
        context.file,
        cache=False,
    ):
        die("compilation failed")
    if os.path.isfile(os.path.join(supplied_files_directory, "runtests.pl")):
        stdout, stderr, returncode = context.runner.run(
            ["./runtests.pl"] + list(args.extra_arguments),
            context.parameters,
            context.shared_dir,
            context.parameters["environment"],
            support_command=True,
        )
        context.file.write(support_command_text(stdout) + support_command_text(stderr))
        context.file.flush()
        return int(returncode)
    return None


def preparation_is_per_test(tests_to_run: list[_Test]) -> bool:
    """
    Must each test prepare in its own directory rather than the shared one?

    Preparing once in the shared directory is what lets many tests share a
    compilation, but it is wrong when the preparation differs between tests
    and writes to the test directory.  COMP1521's 25t2final_q4 is the case
    that found this: two of its tests have pre_compile_commands which write
    different contents to the same temp.s, so whichever ran last decided what
    both tests saw and one of them failed.

    Only pre_compile_command is considered.  Checkers and compilers are keyed
    by their command line and produce the same result wherever they run, and
    every specification with per-test pre_compile_commands in the COMP1511,
    COMP1521 and COMP2041 material is interpreted rather than compiled, so
    nothing is losing a shared compilation here.
    """
    commands = {
        str(test.parameters.get("pre_compile_command")) for test in tests_to_run
    }
    return len(commands) > 1


def run_tests_concurrently(context: RunContext, tests_to_run: list[_Test]) -> list[int]:
    """
    run the tests, parallel_tests at a time, printing their results in
    test order as they complete; return their statuses in test order
    """
    # phase 1: checkers and compilation, serially in the shared directory,
    # unless each test has to prepare in its own copy
    per_test = preparation_is_per_test(tests_to_run)
    prepared: list[tuple[_Test, list[str], io.StringIO, Optional[TestOutcome]]] = []
    for test in tests_to_run:
        out = io.StringIO()
        if per_test:
            prepared.append((test, [], out, None))
            continue
        test_files, outcome = prepare_test(context, test, out)
        prepared.append((test, test_files, out, outcome))

    # phase 2: each test in its own directory, on a worker thread
    # tests sharing one directory would overwrite each other's files, so that
    # choice decides this one: parallel_tests is ignored rather than obeyed
    if context.parameters.get("shared_test_directory"):
        n_workers = 1
    else:
        n_workers = max(1, int(context.parameters.get("parallel_tests", 1)))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=n_workers)
    results = []
    try:
        pending: list[tuple[_Test, Any]] = []
        for test, test_files, out, outcome in prepared:
            if outcome is None:
                pending.append(
                    (
                        test,
                        executor.submit(
                            execute_test, context, test, test_files, out, per_test
                        ),
                    )
                )
            else:
                pending.append((test, outcome))
        for test, item in pending:
            outcome = item if isinstance(item, TestOutcome) else item.result()
            results.append(report_outcome(context, test, outcome))
            remove_test_directory(context, outcome)
    except BaseException:
        # a test's failure to run is fatal (an internal error): stop the
        # student programs still running rather than wait for them
        from subprocess_with_resource_limits import kill_all_running

        executor.shutdown(wait=False, cancel_futures=True)
        kill_all_running()
        raise
    executor.shutdown(wait=True)
    return results


def run_one_test(context: RunContext, test: _Test) -> int:
    """
    run one test from start to finish, printing its result
    return -1 for test not run, 0 for test failed, 1 for test passed
    """
    out = io.StringIO()
    test_files, outcome = prepare_test(context, test, out)
    if outcome is None:
        outcome = execute_test(context, test, test_files, out)
    status = report_outcome(context, test, outcome)
    remove_test_directory(context, outcome)
    return status


def prepare_test(
    context: RunContext, test: _Test, out: io.StringIO, directory: str = ""
) -> tuple[list[str], Optional[TestOutcome]]:
    """
    run one test's checkers, pre_compile_command and compilers, writing what
    they print to out

    directory is where they run, defaulting to the shared directory.  A test
    whose pre_compile_command is its own prepares in its own copy instead: see
    preparation_is_per_test().

    return (the test's files, a TestOutcome iff the test can not be run)
    """
    directory = directory or context.shared_dir
    parameters = test.parameters
    label = parameters["label"]
    colored = context.colored
    description = f"Test {label} ({parameters['description']}) - "
    not_run_description = description + colored("could not be run")

    glob_lists = [glob.glob(g) for g in test.files]
    test_files = [item for sublist in glob_lists for item in sublist]

    if not run_checkers_pre_compile_command(
        context, test_files, parameters, out, directory
    ):
        print(
            not_run_description,
            "because",
            colored("check failed", "red"),
            flush=True,
            file=out,
        )
        return (test_files, TestOutcome(-1, out.getvalue()))

    missing_files = [f for f in test.files if not glob.glob(f)]
    if missing_files:
        print(
            not_run_description,
            "because these files are missing:",
            colored(" ".join(missing_files), "red"),
            flush=True,
            file=out,
        )
        return (test_files, TestOutcome(-1, out.getvalue()))

    if not run_compilers(context, test_files, parameters, out, directory):
        print(
            not_run_description,
            "because",
            colored("compilation failed", "red"),
            flush=True,
            file=out,
        )
        return (test_files, TestOutcome(-1, out.getvalue()))

    chmod_program(context, parameters["program"])

    # Leave the shared directory as a serial run left it after this test
    # ran: with the program linked to the (last) compiled binary.  Later
    # tests' checks that their files exist, and whether their compilations
    # are needed at all, depend on that link being there.
    for compile_command in parameters["compile_commands"] or []:
        if compile_command:
            link_program(
                context.shared_dir,
                parameters["program"],
                compile_command,
                test_files,
                context.linked_program,
                debug=context.debug,
            )

    print(description, end="", file=out)
    return (test_files, None)


def copy_file_data(source: str, destination: str) -> None:
    """
    Copy one file's contents, without filling in the holes of a sparse file.

    COMP1521's file_sizes creates files of 420GB and 1TB with dd seek= and
    has a test report their sizes.  They occupy almost no disk, but a plain
    copy reads and writes every byte, so giving each test its own copy of the
    directory turned a fast test into one that never finished and would have
    filled the disk.

    The data is found with SEEK_DATA and SEEK_HOLE, so only the parts of the
    file which hold anything are read.  A filesystem which does not support
    them reports the whole file as data, which is the plain copy.
    """
    with open(source, "rb") as src, open(destination, "wb") as dst:
        size = os.fstat(src.fileno()).st_size
        offset = 0
        while offset < size:
            try:
                data = os.lseek(src.fileno(), offset, os.SEEK_DATA)
            except OSError:
                break  # nothing but hole from here to the end
            hole = os.lseek(src.fileno(), data, os.SEEK_HOLE)
            src.seek(data)
            dst.seek(data)
            remaining = hole - data
            while remaining > 0:
                chunk = src.read(min(_COPY_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                dst.write(chunk)
                remaining -= len(chunk)
            offset = hole
        # the file may end in a hole, which nothing above writes
        dst.truncate(size)
    shutil.copystat(source, destination)


def copy_readable(source: str, destination: str) -> None:
    """
    Copy one file into a test's own directory, even if its mode forbids reading.

    An exercise about file permissions creates files a test then inspects, and
    some of them are deliberately not readable: COMP1521's file_modes makes one
    mode 223.  Everything here is inside a temporary directory autotest created
    and owns, so the read bit can be added for the copy and the original mode
    restored on both files afterwards.
    """
    try:
        copy_file_data(source, destination)
    except PermissionError:
        pass
    else:
        return

    mode = os.stat(source).st_mode
    os.chmod(source, mode | stat.S_IRUSR)
    try:
        copy_file_data(source, destination)
    finally:
        os.chmod(source, mode)
        if os.path.exists(destination):
            os.chmod(destination, mode)


def execute_test(  # noqa: C901, PLR0912, PLR0915 - one branch per stage of running one test
    context: RunContext,
    test: _Test,
    test_files: list[str],
    out: io.StringIO,
    prepare_here: bool = False,
) -> TestOutcome:
    """
    phase 2 for one test, on a worker thread: copy the shared directory,
    then run setup_command and the test once per compile command in the copy

    With prepare_here the test's checkers, pre_compile_command and compilers
    run in that copy too, rather than having run once in the shared directory
    (see preparation_is_per_test).
    """
    parameters = test.parameters
    debug = context.debug
    label = parameters["label"]
    program = parameters["program"]

    # checked before anything is created: autotest is being interrupted and
    # its handler is removing the temporary tree this directory would go in
    if stopped():
        raise AutotestException("autotest interrupted")

    # A specification can ask for the old behaviour, where every test ran in
    # one directory and could use files an earlier test left behind.  Real
    # course material relies on this: COMP1521's unique_files has two tests
    # create files with setup_command and three more that read them.
    own_directory = not parameters.get("shared_test_directory")

    test_dir = (
        tempfile.mkdtemp(prefix=TEST_DIRECTORY_PREFIX, dir=context.temp_root)
        if own_directory
        else context.shared_dir
    )
    outcome = None
    try:
        try:
            if own_directory:
                shutil.copytree(
                    context.shared_dir,
                    test_dir,
                    symlinks=True,
                    ignore=context.ignore_when_copying,
                    copy_function=copy_readable,
                    dirs_exist_ok=True,
                )
        except shutil.Error as e:
            # an unreadable file should not stop the test, just as when the
            # files were first copied to the temporary directory
            print("Warning:", e, file=sys.stderr)
        if debug > 1:
            print(f"Test {label}: running in {test_dir}", file=sys.stderr)

        if prepare_here:
            test_files, prepare_outcome = prepare_test(context, test, out, test_dir)
            if prepare_outcome is not None:
                return prepare_outcome

        linked_program: dict[str, str] = {}
        individual_tests = []
        for compile_command in parameters["compile_commands"] or [""]:
            if compile_command:
                link_program(
                    test_dir,
                    program,
                    compile_command,
                    test_files,
                    linked_program,
                    debug=debug,
                )

            if parameters["setup_command"]:
                context.run_support_command(
                    parameters["setup_command"],
                    parameters,
                    test_dir,
                    out,
                    cache=False,
                )
            individual_test = copy.copy(test)

            if not compile_command:
                compile_command_str = ""
            elif isinstance(compile_command, list):
                compile_command_str = " ".join(compile_command)
            else:
                compile_command_str = compile_command
            if compile_command and not parameters["compiler_args"]:
                compile_command_str += " " + " ".join(test_files)

            individual_test.run_test(
                test_dir, context.runner, compile_command=compile_command_str
            )
            individual_tests.append(individual_test)
            if (
                not individual_test.stderr_ok
                and not parameters["allow_unexpected_stderr"]
            ):
                break

        failed_individual_tests = [it for it in individual_tests if not it.test_passed]
        outcome = TestOutcome(0 if failed_individual_tests else 1, out.getvalue())
        outcome.test_passed = not failed_individual_tests
        outcome.stdout = individual_tests[0].stdout
        outcome.stderr = individual_tests[0].stderr

        if failed_individual_tests:
            # pick the best failed test to report
            # if we have errors then should be more informative than incorrect output except memory leaks
            if not failed_individual_tests[-1].stderr_ok and (
                not parameters["unicode_stderr"]
                or ("free not called" not in failed_individual_tests[-1].stderr)
            ):
                individual_test = failed_individual_tests[-1]
            else:
                individual_test = failed_individual_tests[0]
            outcome.short_explanation = individual_test.short_explanation
            # computed here, not at print time: the postprocess command it
            # may run needs the test's directory
            outcome.long_explanation = individual_test.get_long_explanation()
        # only a directory made for this test is recorded, so that neither
        # cleanup path can remove the shared one every test is running in
        outcome.test_dir = test_dir if own_directory else None
        return outcome
    finally:
        if outcome is None and own_directory and debug < 10:
            shutil.rmtree(test_dir, ignore_errors=True)


def report_outcome(context: RunContext, test: _Test, outcome: TestOutcome) -> int:
    """
    print a test's result (main thread, in test order) and record it on the
    test for helper.py and upload_results.py
    return -1 for test not run, 0 for test failed, 1 for test passed
    """
    file = context.file
    colored = context.colored
    print(outcome.text, end="", file=file)
    if outcome.status == -1:
        file.flush()
        return -1

    test.test_passed = outcome.test_passed
    test.stdout = outcome.stdout
    test.stderr = outcome.stderr
    if outcome.status == 1:
        print(colored("passed", "green"), flush=True, file=file)
        return 1

    long_explanation = outcome.long_explanation
    # remove hexadecimal constants
    reduced_long_explanation = re.sub(
        r"0x[0-9a-f]+", "", long_explanation, flags=re.IGNORECASE
    )
    previous_errors = context.previous_errors
    if reduced_long_explanation in previous_errors:
        print(
            colored("failed", "red"),
            f"({outcome.short_explanation} - same as Test {previous_errors[reduced_long_explanation]})",
            flush=True,
            file=file,
        )
    else:
        print(
            colored("failed", "red"),
            f"({outcome.short_explanation})",
            file=file,
        )
        if long_explanation:
            print(long_explanation, flush=True, file=file, end="")
        previous_errors.setdefault(reduced_long_explanation, test.parameters["label"])
    return 0


def remove_test_directory(context: RunContext, outcome: TestOutcome) -> None:
    """remove a test's directory once its result is printed (kept for debugging at level 10+)"""
    if outcome.test_dir and context.debug < 10:
        shutil.rmtree(outcome.test_dir, ignore_errors=True)
        outcome.test_dir = None


def run_checkers_pre_compile_command(
    context: RunContext,
    test_files: list[str],
    parameters: dict[str, Any],
    out,
    directory: str = "",
) -> bool:
    """
    run any checkers specified for the files in the test
    plus any pre_compile_command
    if they haven't been run before
    return False iff any checker fails, True otherwise
    """
    directory = directory or context.shared_dir
    for checker in parameters["checkers"]:
        if not checker:
            continue
        for filename in test_files:
            if not context.run_support_command(
                checker,
                parameters,
                directory,
                out,
                arguments=[filename],
                print_command=True,
            ):
                return False

    pre_compile_command = parameters["pre_compile_command"]
    if pre_compile_command:
        # cached only while every test prepares in the one shared directory:
        # the command's effect is on the directory, so a test preparing in its
        # own copy has to run it there however many others have run it
        return context.run_support_command(
            pre_compile_command,
            parameters,
            directory,
            out,
            cache=directory == context.shared_dir,
        )

    return True


def run_compilers(
    context: RunContext,
    test_files: list[str],
    parameters: dict[str, Any],
    out,
    directory: str = "",
) -> bool:
    """
    run any compilers specified for the the test
    return False iff any compiler fails, True otherwise
    """
    directory = directory or context.shared_dir
    compile_commands = parameters["compile_commands"]
    if not compile_commands:
        compile_commands = provide_multi_language_support(
            directory, test_files, **parameters
        )
        # stored so the test runs once per compile command and the
        # reproduce command can show it
        parameters["compile_commands"] = compile_commands
    if not compile_commands:
        return True

    program = parameters["program"]
    program_path = os.path.join(directory, program)
    for compile_command in compile_commands:
        arguments = [] if parameters["compiler_args"] else test_files
        if not context.run_support_command(
            compile_command,
            parameters,
            directory,
            out,
            arguments=arguments,
            unlink=program,
            print_command=parameters["show_compile_command"],
            cache=directory == context.shared_dir,
        ):
            return False

        if not os.path.exists(program_path):
            continue

        unique_program_name = get_unique_program_name(
            program, compile_command, test_files
        )
        try:
            if context.debug > 1:
                print(f"os.rename({program}, {unique_program_name})", file=sys.stderr)
            if not os.path.islink(program_path):
                # this rename, in conjunction with link_program, will *always* cause a symlink loop
                # there probably are reasons to rename a symlink but unless link_program is changed we cannot do so
                os.rename(program_path, os.path.join(directory, unique_program_name))
        except OSError as e:
            if context.debug:
                print(e, file=out)
            return False
    return True


def link_program(
    directory: str,
    program: str,
    compile_command: Union[list[str], str],
    test_files: list[str],
    linked_program: dict[str, str],
    debug: int = 0,
) -> None:
    """
    link appropriate binary for test execution in directory
    linked_program tracks the current link in that directory to avoid some work
    """

    if debug > 3:
        print(
            "\n\nlink_program",
            directory,
            program,
            compile_command,
            test_files,
            linked_program,
            debug,
        )

    unique_program_name = get_unique_program_name(program, compile_command, test_files)
    program_path = os.path.join(directory, program)

    # should already be linked
    if linked_program.get(program) == unique_program_name and os.path.exists(
        program_path
    ):
        if debug > 2:
            print("link_program - using existing link")
        return

    # for safety don't remove anything but a link
    if os.path.islink(program_path):
        if debug > 2:
            print("link_program - removing existing link")
        os.unlink(program_path)

    # what should we do if program already exists?
    if not os.path.exists(program_path):
        if debug > 3:
            print(f"os.symlink({unique_program_name}, {program_path})", file=sys.stderr)

        # os.path.islink() only checks for symlinks, not hard links
        os.symlink(unique_program_name, program_path)

    linked_program[program] = unique_program_name


def get_unique_program_name(
    program: str, compile_command: Union[list[str], str], test_files: list[str]
) -> str:
    """
    form a unique program name based on compile arguments
    so we can have multiple binaries for a program.
    Contrive clashes possible, but comprehensible names for debugging,
    """
    compile_command_str = (
        "_".join(compile_command)
        if isinstance(compile_command, list)
        else compile_command
    )
    compile_command_str = compile_command_str.replace(" ", "_")
    return (
        "._"
        + program
        + "."
        + "__".join([compile_command_str] + test_files).replace("/", "___")
        + "_"
    )


def chmod_program(context: RunContext, program: str) -> None:
    """make program executable (once) in the shared directory"""
    if program in context.chmod_done:
        return
    try:
        os.chmod(os.path.join(context.shared_dir, program), 0o700)
        context.chmod_done.add(program)
    except OSError:
        # if program is produced by compilation, it won't exist
        pass


def provide_multi_language_support(  # noqa: C901, PLR0911 - one early return per language
    directory: str,
    test_files: list[str],
    program: str,
    files: list[str],
    default_compilers: dict[str, list[Any]],
    default_compiler_args: dict[str, list[Any]],
    **_other_parameters: Any,
) -> list[list[str]]:
    """
    provide backwards-compatible support of autotests which accept multiple languages
    (files=hello.* and the student submits hello.c, or hello.py, or ...)
    this needs to be generalized and incorporated in parameter_descriptions.py

    return the compile commands for the language the submission is in
    """
    if os.path.exists(os.path.join(directory, program)) or not files:
        return []
    extension_glob = os.path.splitext(files[0])[1]
    if not set("[?*") & set(extension_glob):
        return []
    f = [p for p in [os.path.splitext(file) for file in test_files] if p[0] == program]
    if not f:
        return []
    basename, extension = f[0]
    if not extension:
        return []
    suffix = extension[1:]
    filename = basename + extension
    if suffix in ["pl", "py", "sh"]:
        os.link(os.path.join(directory, filename), os.path.join(directory, basename))
        return []
    if suffix in ["java", "js"]:
        # a wrapper script named after the program, not over the source
        wrapper = os.path.join(directory, basename)
        with open(wrapper, "w", encoding="utf-8") as file:
            file.write(
                f"#!/bin/bash\n{'node' if suffix == 'js' else 'java'} {basename} \"$@\""
            )
        os.chmod(wrapper, 0o700)
        return []
    if suffix in ["c", "cc"]:
        # the same resolution finalize_compile_commands applies when the
        # language is known when tests.txt is parsed: pick the first
        # alternative compiler installed, then add the "-o program" arguments
        compile_commands = []
        for compiler in default_compilers.get(suffix, []):
            if compiler and isinstance(compiler[0], list):
                compiler = select_command_from_alternatives(compiler)
                if not compiler:
                    continue
            if isinstance(compiler, str):
                compiler = compiler.split()
            command = [program if a == "%" else str(a) for a in compiler]
            for compiler_args in default_compiler_args.get(suffix, []):
                command += [program if a == "%" else str(a) for a in compiler_args]
            compile_commands.append(command)
        return compile_commands
    # Just in case. If expected behaviour is None, can do that
    # with a slight tweak to mypy.ini
    return []


def generate_expected_output(
    tests: dict[str, _Test],
    args: Namespace,
    parameters: Optional[dict[str, Any]] = None,
) -> None:
    """
    generate expected output for tests from supplied solution
    """

    # print test specification with generated expected output to stdout
    if args.generate_expected_output == "stdout":
        print_tests_and_expected_output(tests, args, sys.stdout, parameters)
        return

    # print only generated expected output
    if args.generate_expected_output != "update":
        print_expected_output(tests, args, sys.stdout, parameters)
        return

    # update test specification file in place with generated expected output
    # file might temporarily exist with partial contents but
    # write is small & this avoids handling issues with permissions and symlinks using a rename
    path = args.test_specification_pathname
    output = io.StringIO()
    print_tests_and_expected_output(tests, args, output, parameters)
    new_contents = output.getvalue()
    output.close()
    with open(path, encoding="utf-8") as f:
        old_contents = f.read()
    if old_contents != new_contents:
        with open(path, "w", encoding="utf-8") as g:
            g.write(new_contents)


def print_tests_and_expected_output(
    tests: dict[str, _Test],
    args: Namespace,
    file,
    parameters: Optional[dict[str, Any]] = None,
) -> None:
    output_file_without_parameters(
        args.test_specification_pathname,
        initial_parameters=args.initial_parameters,
        initial_tests=args.initial_tests,
        debug=args.debug,
        file=file,
    )
    print(
        f"### generated by: autotest --generate_expected_output - see {REPO}",
        file=file,
    )
    print_expected_output(tests, args, file, parameters)


def as_literal(stream: Union[str, bytes, bytearray]) -> Union[str, bytearray]:
    """the value whose repr goes in a generated tests.txt"""
    if isinstance(stream, bytes):
        return bytearray(stream)
    return stream


def print_expected_output(
    tests: dict[str, _Test],
    args: Namespace,
    file,
    parameters: Optional[dict[str, Any]] = None,
) -> None:
    if parameters is None:
        # the global parameters are those every test shares
        parameters = next(iter(tests.values())).parameters
    # ignore output from tests
    with open(os.devnull, "w", encoding="utf-8") as dev_null:
        context = RunContext(tests, parameters, args, dev_null)
        for label, test in tests.items():
            if label not in args.labels:
                continue
            # override any checkers, so expected output can be generated from solutions with non-permitted features
            test.parameters["checkers"] = []
            # override limits (0 is no limit)
            test.parameters["max_stdout_bytes"] = 0
            test.parameters["max_stderr_bytes"] = 0
            test.parameters["max_file_size_bytes"] = 1000000000
            test.parameters["max_real_seconds"] = 0
            test.parameters["max_cpu_seconds"] = 0
            # override dcc output checking
            finalize_dcc_output_checking("dcc_output_checking", False, test.parameters)

            run_one_test(context, test)
            if not hasattr(test, "stdout"):
                die(f"Test {label} could not be run")
            # a non-unicode stream is printed as a bytearray literal, as it
            # always has been, so regenerated files do not change
            if test.stdout:
                print(
                    f"{label} expected_stdout={as_literal(test.stdout)!r}",
                    file=file,
                )
            if test.stderr:
                print(
                    f"{label} expected_stderr={as_literal(test.stderr)!r}",
                    file=file,
                )
