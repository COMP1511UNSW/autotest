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
import contextlib
import copy
import errno
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
        # held around a per-test copy only while the shared directory holds a
        # file whose mode forbids reading: see copy_readable and copy_lock()
        self.unreadable_file_lock = threading.Lock()
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
        # one lock per command line, so the second caller waits for the first
        # to finish rather than running the command a second time
        self.support_command_locks: dict[str, threading.Lock] = {}
        self.chmod_done: set = set()
        # the program link in the shared directory (see prepare_test)
        self.linked_program: dict[str, str] = {}
        # hex-stripped long explanation -> label of the first test which
        # produced it, for "failed (X - same as Test Y)"
        self.previous_errors: dict[str, str] = {}
        # set by detect_unreadable_files() before any worker starts, and
        # left False for a serial run, which cannot race with itself
        self._shared_dir_has_unreadable_file: bool = False

    def detect_unreadable_files(self) -> None:
        """
        Ask, once, whether the shared directory holds a file nothing can read.

        Called on the main thread before any worker starts, and never after.
        Asking later would be asking during another worker's copy: copy_readable
        widens the mode of exactly such a file for the length of its copy, so a
        probe that ran inside that window would find the file readable, conclude
        there was nothing to protect, and turn the lock off for every thread.
        That is not a theoretical ordering -- it is what made the test for this
        pass alone and fail under load.
        """
        self._shared_dir_has_unreadable_file = directory_has_unreadable_file(
            self.shared_dir
        )
        if self._shared_dir_has_unreadable_file and self.debug > 1:
            print(
                "a file in the shared directory can not be read:"
                " per-test copies will be serialised",
                file=sys.stderr,
            )

    def copy_lock(self) -> "contextlib.AbstractContextManager[Any]":
        """
        The lock a per-test copy of the shared directory must hold, if any.

        copy_readable has to widen the mode of an unreadable file in the
        shared directory to copy it, and that file is one every other worker
        is copying at the same time: a worker copying while another holds the
        mode open takes the widened mode, and a test which inspects file
        permissions is then marked on the wrong one.  COMP1521's file_modes,
        the exercise copy_readable was written for, is exactly that test.

        Serialising every copy would cost all of the parallelism, so the
        shared directory is asked once, by detect_unreadable_files, whether it
        holds such a file at all.  Almost no exercise does, and those that do
        serialise their copies, which is what a serial run did anyway.  A
        serial run never asks, and needs no lock.
        """
        if self._shared_dir_has_unreadable_file:
            return self.unreadable_file_lock
        return contextlib.nullcontext()

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

    def run_support_command(
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
            # The command's own lock is held across running it, not just
            # across looking it up: checking the cache, running, and storing
            # the result was three steps, so every worker that reached the
            # first step before anyone reached the third ran the command too.
            with self.lock:
                cached = self.support_command_results.get(cmd_str)
                command_lock = self.support_command_locks.setdefault(
                    cmd_str, threading.Lock()
                )
            if cached is None:
                with command_lock:
                    return self._run_support_command_once(
                        cmd, cmd_str, parameters, work_dir, out, unlink, print_command
                    )
            if self.debug > 1:
                print(
                    "Using cached result of",
                    cached,
                    "for",
                    cmd_str,
                    file=sys.stderr,
                )
            return cached

        return self._run_support_command_once(
            cmd, cmd_str, parameters, work_dir, out, unlink, print_command, cache=False
        )

    def _run_support_command_once(
        self,
        cmd: Union[list[str], str],
        cmd_str: str,
        parameters: dict[str, Any],
        work_dir: str,
        out,
        unlink: Optional[str],
        print_command: bool,
        cache: bool = True,
    ) -> bool:
        """
        run the command and record its result

        Called with the command's own lock held when the result is cached, so
        whoever waited for that lock re-checks the cache first and does not
        run it again.
        """
        if cache:
            with self.lock:
                cached = self.support_command_results.get(cmd_str)
            if cached is not None:
                return cached

        if unlink:
            unlink_path = os.path.join(work_dir, unlink)
            # islink alone: os.path.exists follows the link, so it is False
            # for a dangling one, which is the link that most needs removing
            if os.path.islink(unlink_path):
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


def preparation_prefixes(tests_to_run: list[_Test]) -> Optional[list[list[Any]]]:
    """
    Where must each test prepare, and what has to have been run there first?

    Preparing once in the shared directory is what lets many tests share a
    compilation, but it is wrong when the preparation differs between tests
    and writes to the test directory.  COMP1521's 25t2final_q4 is the case
    that found this: two of its tests have pre_compile_commands which write
    different contents to the same temp.s, so whichever ran last decided what
    both tests saw and one of them failed.

    Return None when every test can share one preparation.  Otherwise return,
    for each test in order, the pre_compile_commands that a serial run would
    already have run in the directory before this test's own runs.  A serial
    run executes each distinct pre_compile_command once, when the first test
    using it is reached, so the state a test sees is the submission plus every
    distinct command up to and including its own, applied in that order --
    and a specification may depend on exactly that.  COMP1511's cs_chardle
    does: ten tests run "cp cs_chardle.c modified.c && sed ... modified.c",
    and the eleventh runs "sed ... modified.c" alone, which has nothing to
    edit unless the earlier command has already made the file.

    This costs a test one command for each distinct command before its own.
    The worst case in the COMP1511, COMP1521 and COMP2041 material is
    COMP1521's pacman, 41 distinct commands over 140 tests, which spends a
    few seconds of its run on them.  Should a specification ever make that
    expensive, the way out is to snapshot the directory after each distinct
    command in the shared one and copy a test's directory from its snapshot.
    """
    distinct: list[Any] = []
    position: dict[str, int] = {}
    for test in tests_to_run:
        command = test.parameters.get("pre_compile_command")
        if str(command) not in position:
            position[str(command)] = len(distinct)
            distinct.append(command)
    if len(distinct) < 2:
        return None
    return [
        [
            command
            for command in distinct[
                : position[str(test.parameters["pre_compile_command"])]
            ]
            if command
        ]
        for test in tests_to_run
    ]


def run_tests_concurrently(context: RunContext, tests_to_run: list[_Test]) -> list[int]:
    """
    run the tests, parallel_tests at a time, printing their results in
    test order as they complete; return their statuses in test order
    """
    # A specification which asks every test to share one directory is asking
    # for what a serial run did, so give it exactly that: preparing and
    # running one test at a time, in order, is the only way a test which
    # depends on what an earlier one left behind can see it.
    if context.parameters.get("shared_test_directory"):
        return [run_one_test(context, test) for test in tests_to_run]

    # phase 1: checkers and compilation, serially in the shared directory,
    # unless each test has to prepare in its own copy
    prefixes = preparation_prefixes(tests_to_run)
    prepared: list[
        tuple[_Test, list[str], io.StringIO, Optional[TestOutcome], Optional[list[Any]]]
    ] = []
    for index, test in enumerate(tests_to_run):
        out = io.StringIO()
        outcome = check_one_test(context, test, out)
        if outcome is not None:
            prepared.append((test, [], out, outcome, None))
            continue
        if prefixes is not None:
            prepared.append((test, [], out, None, prefixes[index]))
            continue
        test_files, outcome = prepare_test(context, test, out)
        prepared.append((test, test_files, out, outcome, None))

    # Between the phases, on this thread, while nothing else is copying:
    # see RunContext.detect_unreadable_files.
    context.detect_unreadable_files()

    # phase 2: each test in its own directory, on a worker thread
    n_workers = max(1, int(context.parameters.get("parallel_tests", 1)))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=n_workers)
    results = []
    try:
        pending: list[tuple[_Test, Any]] = []
        for test, test_files, out, outcome, prefix in prepared:
            if outcome is None:
                pending.append(
                    (
                        test,
                        executor.submit(
                            execute_test, context, test, test_files, out, prefix
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


def check_one_test(
    context: RunContext, test: _Test, out: io.StringIO
) -> Optional[TestOutcome]:
    """
    run the test's checkers, in the shared directory, before anything else

    return a TestOutcome iff a checker failed and the test can not be run
    """
    parameters = test.parameters
    glob_lists = [glob.glob(g) for g in test.files]
    test_files = [item for sublist in glob_lists for item in sublist]
    if run_checkers(context, test_files, parameters, out):
        return None
    print(
        f"Test {parameters['label']} ({parameters['description']}) - "
        + context.colored("could not be run"),
        "because",
        context.colored("check failed", "red"),
        flush=True,
        file=out,
    )
    return TestOutcome(-1, out.getvalue())


def run_one_test(context: RunContext, test: _Test) -> int:
    """
    run one test from start to finish, printing its result
    return -1 for test not run, 0 for test failed, 1 for test passed
    """
    out = io.StringIO()
    outcome = check_one_test(context, test, out)
    if outcome is not None:
        status = report_outcome(context, test, outcome)
        remove_test_directory(context, outcome)
        return status
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
    preparation_prefixes().

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

    # checkers have already run, in the shared directory, before any test
    # started: see run_checkers and check_every_test
    if not run_pre_compile_command(context, parameters, out, directory):
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

    chmod_program(context, parameters["program"], directory)

    # Leave the shared directory as a serial run left it after this test
    # ran: with the program linked to the (last) compiled binary.  Later
    # tests' checks that their files exist, and whether their compilations
    # are needed at all, depend on that link being there.
    #
    # Only when this is the shared directory.  Linking there on behalf of a
    # test that compiled somewhere else leaves a symlink to a binary the
    # shared directory does not have, every later test copies it, and a
    # compiler writing to the program name then writes through it and builds
    # the wrong file: that cost COMP1511's my_scanf and count_farnarkles a
    # test each.  execute_test links in the test's own directory instead.
    if directory != context.shared_dir:
        print(description, end="", file=out)
        return (test_files, None)

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
    file which hold anything are read.  Most filesystems which do not support
    them report the whole file as data, which is the plain copy; one which
    rejects the request instead is copied whole rather than trusted, because
    treating that error as "no more data" writes the student a file of the
    right size full of zeroes and marks them on it.
    """
    with open(source, "rb") as src, open(destination, "wb") as dst:
        size = os.fstat(src.fileno()).st_size
        offset = 0
        while offset < size:
            try:
                data = os.lseek(src.fileno(), offset, os.SEEK_DATA)
            except OSError as e:
                if e.errno == errno.ENXIO:
                    break  # nothing but hole from here to the end
                _copy_whole_file(src, dst, offset)
                break
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


def _copy_whole_file(src, dst, offset: int) -> None:
    """Copy src to dst from offset on, every byte, holes included.

    The fallback for a filesystem which will not answer SEEK_DATA.
    """
    src.seek(offset)
    dst.seek(offset)
    while True:
        chunk = src.read(_COPY_CHUNK_BYTES)
        if not chunk:
            return
        dst.write(chunk)


def directory_has_unreadable_file(directory: str) -> bool:
    """Does any file under directory have a mode which forbids reading it?"""
    for root, _dirs, names in os.walk(directory):
        for name in names:
            path = os.path.join(root, name)
            try:
                if not os.stat(path).st_mode & stat.S_IRUSR:
                    return True
            except OSError:
                continue
    return False


def copy_readable(source: str, destination: str) -> None:
    """
    Copy one file into a test's own directory, even if its mode forbids reading.

    An exercise about file permissions creates files a test then inspects, and
    some of them are deliberately not readable: COMP1521's file_modes makes one
    mode 223.  Everything here is inside a temporary directory autotest created
    and owns, so the read bit can be added for the copy and the original mode
    restored on both files afterwards.

    The source is in the shared directory, which every other test is copying,
    so widening its mode is visible to them: the caller holds
    RunContext.copy_lock() for the whole copy, which serialises the copies of
    a directory that has such a file.  Without it a test which inspects file
    permissions is marked on a mode another test's copy happened to widen.
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
    prepare_prefix: Optional[list[Any]] = None,
) -> TestOutcome:
    """
    phase 2 for one test, on a worker thread: copy the shared directory,
    then run setup_command and the test once per compile command in the copy

    A prepare_prefix that is not None means this test's checkers,
    pre_compile_command and compilers run in that copy too, rather than
    having run once in the shared directory, and that the commands it holds
    must run there first to put the directory in the state a serial run would
    have left it in (see preparation_prefixes).
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
                with context.copy_lock():
                    shutil.copytree(
                        context.shared_dir,
                        test_dir,
                        symlinks=True,
                        ignore=context.ignore_when_copying,
                        copy_function=copy_readable,
                        dirs_exist_ok=True,
                    )
        except (shutil.Error, OSError) as e:
            # A test run against a directory missing some of its files can
            # fail, or pass, for a reason that has nothing to do with the
            # submission, and the warning this used to print went to stderr
            # where the result does not show it.  Not running the test says
            # so where the student and the marker will both see it.
            print(
                f"Test {label} ({parameters['description']}) - "
                + context.colored("could not be run", "red")
                + " because its directory could not be prepared: "
                + context.colored(str(e), "red"),
                flush=True,
                file=out,
            )
            return TestOutcome(-1, out.getvalue())
        if debug > 1:
            print(f"Test {label}: running in {test_dir}", file=sys.stderr)

        if prepare_prefix is not None:
            for command in prepare_prefix:
                if not context.run_support_command(
                    command,
                    parameters,
                    test_dir,
                    out,
                    print_command=False,
                    cache=False,
                ):
                    break
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


def run_checkers(
    context: RunContext,
    test_files: list[str],
    parameters: dict[str, Any],
    out,
) -> bool:
    """
    run any checkers specified for the files in the test, if they have not
    been run before; return False iff one fails

    Always in the shared directory, and always before any test starts, so
    that each distinct checker runs exactly once and its output lands in the
    same test's block whatever else is happening.  A checker inspects a
    submitted file and its verdict does not depend on which copy of the
    directory it is looking at, so there is nothing to gain by running it
    once per test -- and a great deal to lose: COMP1521's pacman ran
    "1521 mipsy --check pacman.s" 140 times under -j where a serial run ran
    it once, and printed it 140 times.
    """
    for checker in parameters["checkers"]:
        if not checker:
            continue
        for filename in test_files:
            if not context.run_support_command(
                checker,
                parameters,
                context.shared_dir,
                out,
                arguments=[filename],
                print_command=True,
            ):
                return False
    return True


def run_pre_compile_command(
    context: RunContext,
    parameters: dict[str, Any],
    out,
    directory: str = "",
) -> bool:
    """run the test's pre_compile_command in directory, if it has one"""
    directory = directory or context.shared_dir
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


def chmod_program(context: RunContext, program: str, directory: str = "") -> None:
    """
    make program executable in directory, which defaults to the shared one

    The shared directory is done once; a test's own copy is done every time,
    because it is a fresh directory that the earlier chmod never reached.
    """
    shared = not directory or directory == context.shared_dir
    if shared:
        if program in context.chmod_done:
            return
        directory = context.shared_dir
    try:
        os.chmod(os.path.join(directory, program), 0o700)
    except OSError:
        # if program is produced by compilation, it won't exist
        return
    if shared:
        context.chmod_done.add(program)


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
            # max_rss_bytes belongs with the rest: it did nothing until this
            # branch made it real, and a 1GB default kills the generation of
            # expected output for any exercise that needs more -- every MIPS
            # game among them
            test.parameters["max_rss_bytes"] = 0
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
