# run all the tests
# This code needs extensive revision.

import copy, glob, io, os, re, subprocess, sys
from termcolor import colored as termcolor_colored
from parse_test_specification import output_file_without_parameters
from util import die
from command_line_arguments import REPO

# necessary for typehinting
from typing import Dict, List, Any, Union

from run_test import _Test
from argparse import Namespace


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

    with open("autotest.log", "w", encoding="utf-8") as f:
        return run_tests(tests, parameters, args, file=Tee(f))


def run_tests(
    tests: Dict[str, _Test],
    global_parameters: Dict[str, Any],
    args: Namespace,
    file=sys.stdout,
) -> int:

    debug = global_parameters["debug"]
    colored = (
        termcolor_colored
        if global_parameters["colorize_output"]
        else lambda x, *a, **kw: x
    )

    if os.path.exists("./compile.sh"):
        if subprocess.call(["./compile.sh"] + args.programs) != 0:
            die("compilation failed")
    if os.path.exists("./runtests.pl"):
        return subprocess.call(["./runtests.pl"] + args.extra_arguments)
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

    results = [run_one_test(test, file=file) for test in tests_to_run]

    if debug > 3:
        subprocess.call("echo after tests run;ls -l;pwd", shell=True)

    n_tests_passed = results.count(1)
    n_tests_failed = results.count(0)
    n_tests_not_run = results.count(-1)

    if n_tests_passed:
        print(
            colored(str(n_tests_passed) + " tests passed", "green"), end=" ", file=file
        )
    else:
        print(colored("0 tests passed", "red"), end=" ")
    if n_tests_failed:
        print(colored(str(n_tests_failed) + " tests failed", "red"), end="", file=file)
    else:
        print(colored("0 tests failed", "green"), end=" ", file=file)
    if n_tests_not_run:
        print("", n_tests_not_run, "tests could not be run", end="", file=file)
    print(file=file)
    return 1 if n_tests_failed + n_tests_not_run else 0


# TODO: provide stricter type for previous_errors
def run_one_test(
    # pylint: disable=dangerous-default-value
    test: _Test,
    file=sys.stdout,
    previous_errors: Dict[str, Any] = {},
) -> int:
    """
    return -1 for test not run, 0 for test failed, 1 for test passed
    """
    parameters = test.parameters
    debug = parameters["debug"]
    label = parameters["label"]
    colored = (
        termcolor_colored if parameters["colorize_output"] else lambda x, *a, **kw: x
    )
    description = f"Test {label} ({parameters['description']}) - "
    not_run_description = description + colored("could not be run")

    glob_lists = [glob.glob(g) for g in test.files]
    test_files = [item for sublist in glob_lists for item in sublist]

    if not run_checkers_pre_compile_command(test_files, parameters, file=file):
        print(
            not_run_description,
            "because",
            colored("check failed", "red"),
            flush=True,
            file=file,
        )
        return -1

    missing_files = [f for f in test.files if not glob.glob(f)]
    if missing_files:
        print(
            not_run_description,
            "because these files are missing:",
            colored(" ".join(missing_files), "red"),
            flush=True,
            file=file,
        )
        return -1

    if not run_compilers(test_files, parameters, file=file, debug=debug):
        print(
            not_run_description,
            "because",
            colored("compilation failed", "red"),
            flush=True,
            file=file,
        )
        return -1

    if debug > 3:
        subprocess.call("echo after run_compilers;ls -l", shell=True)

    chmod_program(**parameters)

    print(description, end="", file=file)

    individual_tests = []
    for compile_command in parameters["compile_commands"] or [""]:

        if compile_command:
            link_program(
                parameters["program"], compile_command, test_files, debug=debug
            )

        if parameters["setup_command"]:
            run_support_command(
                test.parameters["setup_command"],
                result_cache={},
                debug=test.parameters["debug"],
            )
        individual_test = copy.copy(test)

        if not compile_command:
            compile_command_str = ""
        elif isinstance(compile_command, list):
            compile_command_str = " ".join(compile_command)
        else:
            compile_command_str = compile_command

        if not parameters["compiler_args"]:
            compile_command_str += " " + " ".join(test_files)

        individual_test.run_test(compile_command=compile_command_str)
        individual_tests.append(individual_test)
        if not individual_test.stderr_ok and not parameters["allow_unexpected_stderr"]:
            break

    if debug > 3:
        subprocess.call("echo after for test run;ls -l", shell=True)

    failed_individual_tests = [it for it in individual_tests if not it.test_passed]
    test.test_passed = not failed_individual_tests
    test.stdout = individual_tests[0].stdout
    test.stderr = individual_tests[0].stderr
    if test.test_passed:
        print(colored("passed", "green"), flush=True, file=file)
        return 1

    # pick the best failed test to report
    # if we have errors then should be more informative than incorrect output except memory leaks
    if not failed_individual_tests[-1].stderr_ok and (
        not parameters["unicode_stderr"]
        or ("free not called" not in failed_individual_tests[-1].stderr)
    ):
        individual_test = failed_individual_tests[-1]
    else:
        individual_test = failed_individual_tests[0]

    long_explanation = individual_test.get_long_explanation()
    stdin_explanation = individual_test.get_stdin_explanation()
    reproduce_command_explanation = individual_test.get_reproduce_command_explanation()
    # remove hexadecimal constants
    reduced_long_explanation = re.sub(r"0x[0-9a-f]+", "", long_explanation, flags=re.I)
    if parameters["squash_repeated"] and reduced_long_explanation in previous_errors:
        print(
            colored("failed", "red"),
            "({} - same as Test {})".format(
                individual_test.short_explanation,
                previous_errors[reduced_long_explanation],
            ),
            flush=True,
            file=file,
        )
        if parameters["show_squashed_input"] and stdin_explanation:
            print(stdin_explanation, flush=True, file=file, end="")
        if (
            parameters["show_squashed_reproduce_command"]
            and reproduce_command_explanation
        ):
            print(reproduce_command_explanation, flush=True, file=file, end="")
    else:
        print(
            colored("failed", "red"),
            f"({individual_test.short_explanation})",
            file=file,
        )
        if long_explanation:
            print(long_explanation, flush=True, file=file, end="")
        if stdin_explanation:
            print(stdin_explanation, flush=True, file=file, end="")
        if reproduce_command_explanation:
            print(reproduce_command_explanation, flush=True, file=file, end="")
        previous_errors.setdefault(reduced_long_explanation, label)

    return 0


def run_checkers_pre_compile_command(
    test_files: List[str], parameters: Dict[str, Any], file=sys.stdout
) -> bool:
    """
    run any checkers specified for the files in the test
    plus any pre_compile_command
    if they haven't been run before
    return False iff any checker fails, True otherwise
    """
    debug = parameters["debug"]
    for checker in parameters["checkers"]:
        if not checker:
            continue
        for filename in test_files:
            if not run_support_command(
                checker,
                arguments=[filename],
                print_command=True,
                file=file,
                debug=debug,
            ):
                return False

    pre_compile_command = parameters["pre_compile_command"]
    if pre_compile_command:
        return run_support_command(
            pre_compile_command, print_command=False, file=file, debug=debug
        )

    return True


def run_compilers(
    test_files: List[str], parameters: Dict[str, Any], file=sys.stdout, debug: int = 0
) -> bool:
    """
    run any compilers specified for the the test
    return False iff any compiler fails, True otherwise
    """

    compile_commands = parameters["compile_commands"]
    if not compile_commands:
        compile_commands = provide_multi_language_support(test_files, **parameters)
    if not compile_commands:
        return True

    program = parameters["program"]
    for compile_command in parameters["compile_commands"]:
        arguments = [] if parameters["compiler_args"] else test_files
        if not run_support_command(
            compile_command,
            arguments=arguments,
            unlink=program,
            print_command=parameters["show_compile_command"],
            file=file,
            debug=debug,
        ):
            return False

        if not os.path.exists(program):
            continue

        unique_program_name = get_unique_program_name(
            program, compile_command, test_files
        )
        try:
            if debug > 1:
                print(f"os.rename({program}, {unique_program_name})", file=sys.stderr)
            if not os.path.islink(program):
                # this rename, in conjunction with link_program, will *always* cause a symlink loop
                # there probably are reasons to rename a symlink but unless link_program is changed we cannot do so
                os.rename(program, unique_program_name)
        except OSError as e:
            if debug:
                print(e, file=file)
            return False
    return True


# TODO: make debug levels an enum, possibly implement better debugging
def link_program(
    program: str,
    compile_command: List[str],
    test_files: List[str],
    # pylint: disable=dangerous-default-value
    linked_program: Dict[str, str] = {},
    debug: int = 0,
) -> None:
    """
    link appropriate binary for test execution
    linked_program is used to track current link to allows us to avoid some work
    """

    if debug > 3:
        print(
            "\n\nlink_program",
            program,
            compile_command,
            test_files,
            linked_program,
            debug,
        )

    unique_program_name = get_unique_program_name(program, compile_command, test_files)

    # should already be linked
    if linked_program.get(program, None) == unique_program_name and os.path.exists(
        program
    ):
        if debug > 2:
            print("link_program - using existing link")
        return

    # for safety don't remove anything but a link
    if os.path.islink(program):
        if debug > 2:
            print("link_program - removing existing link")
        os.unlink(program)

    # what should we do if program already exists?
    if not os.path.exists(program):
        if debug > 3:
            print("link_program - before link command")
            subprocess.call("pwd", shell=True)
            subprocess.call("ls -l", shell=True)
            print(f"os.symlink({unique_program_name}, {program})", file=sys.stderr)

        # os.path.islink() only checks for symlinks, not hard links
        os.symlink(unique_program_name, program)

        if debug > 3:
            print("link_program - after link command")
            subprocess.call("pwd", shell=True)
            subprocess.call("ls -l", shell=True)

    linked_program[program] = unique_program_name


def get_unique_program_name(
    program: str, compile_command: Union[List[str], str], test_files: List[str]
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
        program
        + "."
        + "__".join([compile_command_str] + test_files).replace("/", "___")
    )


def chmod_program(
    # pylint: disable=dangerous-default-value
    program: str,
    chmod_cache: Dict[str, bool] = {},
    **_other_parameters: Dict[str, Any],
) -> None:
    chmod_str = "chmod " + program
    if chmod_str in chmod_cache:
        return
    try:
        os.chmod(program, 0o700)
        chmod_cache[program] = True
    except OSError:
        # if program is produced by compilation, it won't exist
        pass


def provide_multi_language_support(
    test_files: List[str],
    program: str,
    files: List[str],
    default_compilers: Dict[str, List[List[str]]],
    debug: int,
    **_other_parameters: Dict[str, Any],
) -> List[List[str]]:
    """
    provide backwards-compatible support of autotests which accept multiple languages
    this needs to be generalized and incorporated in parameter_descriptions.py
    """
    if os.path.exists(program) or not files:
        return []
    extension_glob = os.path.splitext(files[0])[1]
    if not set("[?*") & set(extension_glob):
        return []
    f = [p for p in [os.path.splitext(file) for file in test_files] if p[0] == program]
    if not f:
        return []
    (basename, extension) = f[0]
    if not extension:
        return []
    suffix = extension[1:]
    filename = basename + extension
    if suffix in ["pl", "py", "sh"]:
        os.link(filename, basename)
        return []
    elif suffix in ["java", "js"]:
        # changed variable name to stop mypy complaing...was f rather than file...
        with open(filename, "w", encoding="utf-8") as file:
            file.write(
                f"#!/bin/bash\n{'node' if suffix == 'js' else 'java'} {basename} \"$@\""
            )
        return []
    elif suffix in ["c", "cc"]:
        compilers = default_compilers.get(suffix, [])
        for (index, compiler) in enumerate(compilers):
            compilers[index] = [program if a == "%" else str(a) for a in compiler]
        return compilers
    # Just in case. If expected behaviour is None, can do that
    # with a slight tweak to mypy.ini
    return []


def run_support_command(
    command: List[str],
    # pylint: disable=dangerous-default-value
    result_cache: Dict[str, bool] = {},
    print_command: bool = False,
    file=sys.stdout,
    arguments: List[str] = None,
    unlink: str = None,
    debug: int = 0,
) -> bool:
    """
    run support command, shell used iff command is a string

    command is not resource-limited, unlike tests

    if command is in result_cache, it is not run and previous result returned

    result_cache needs to be set to run command repeatedly
    e.g test setup_command needs to be run for every tests

    if unlink is set, it is removed iff it is a symlink, before the command is run

    return True if command has 0 exit status, False otherwise
    """
    arguments = arguments or []
    if isinstance(command, str):
        cmd = command + " " + " ".join(arguments)
        cmd_str = cmd
    else:
        cmd = command + arguments
        cmd_str = " ".join(cmd)

    if isinstance(result_cache, dict) and cmd_str in result_cache:
        if debug > 1:
            print(
                "Using cached result of",
                result_cache[cmd_str],
                "for",
                cmd_str,
                file=sys.stderr,
            )
        return result_cache[cmd_str]

    if unlink and os.path.exists(unlink) and os.path.islink(unlink):
        if debug > 1:
            print("run_support_command unlinking: ", unlink)
        os.unlink(unlink)

    if print_command or debug:
        print(cmd_str, file=file, flush=True)

    # FileNotFoundError may be raised
    # it will be caught and an internal error message printed
    #
    # FIXME: sending the output straight to file would be better but
    # doesn't work for unknown reasons
    # when it is a Tee object created in upload_results.py

    p = subprocess.run(
        cmd,
        shell=isinstance(cmd, str),
        input="",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        check=False,
    )
    file.write(p.stdout)

    if debug > 1:
        print(f"{cmd} exit status {p.returncode}", file=sys.stderr)
        if debug > 3:
            subprocess.call("echo after run_support_command;ls -l", shell=True)

    result = p.returncode == 0
    if isinstance(result_cache, dict):
        result_cache[cmd_str] = result
    return result


def generate_expected_output(
    tests: Dict[str, _Test],
    args: Namespace,
) -> None:
    """
    generate expected output for tests from supplied solution
    """

    try:
        method, format = args.generate_expected_output.split(":")
    except ValueError:
        method, format = args.generate_expected_output, "inlined"

    method = re.sub(r"\W", "", method)
    format = re.sub(r"\W", "", format)

    if format not in ["inline", "inlined", "multiline"]:
        raise ValueError(f"invalid format {format} for --generate-expected-output")

    # print test specification with generated expected output to stdout
    if method in ["stdout", "dump", "print", "echo", "show", "display"]:
        print_tests_and_expected_output(tests, args, sys.stdout, format)
        return

    if method in ["update", "inplace", "replace", "overwrite", "write"]:
        # update test specification file in place with generated expected output
        # file might temporarily exist with partial contents but
        # write is small & this avoids handling issues with permissions and symlinks using a rename
        path = args.test_specification_pathname
        output = io.StringIO()
        print_tests_and_expected_output(tests, args, output, format)
        new_contents = output.getvalue()
        output.close()
        with open(path, encoding="utf-8") as f:
            old_contents = f.read()
        if old_contents != new_contents:
            with open(path, "w", encoding="utf-8") as g:
                g.write(new_contents)
        return

    # print only generated expected output
    if method in ["generated"]:
        print_expected_output(tests, args, sys.stdout, format)
        return

    raise ValueError(f"invalid method {method} for --generate-expected-output")


def print_tests_and_expected_output(
    tests: Dict[str, _Test], args: Namespace, file, format: str
) -> None:
    output_file_without_parameters(
        args.test_specification_pathname,
        initial_parameters=args.initial_parameters,
        initial_tests=args.initial_tests,
        debug=args.debug,
        file=file,
    )
    print(
        f"\n### generated by: autotest --generate_expected_output - see {REPO}\n",
        file=file,
    )
    print_expected_output(tests, args, file, format)


def print_expected_output(
    tests: Dict[str, _Test], args: Namespace, file, format: str
) -> None:
    # ignore output from tests
    with open(os.devnull, "w", encoding="utf-8") as dev_null:
        for (label, test) in tests.items():
            if label not in args.labels:
                continue
            # override any checkers, so expected output can be generated from solutions with non-permitted features
            test.parameters["checkers"] = []
            run_one_test(test, file=dev_null)
            if not hasattr(test, "stdout"):
                die(f"Test {label} could not be run")

            if format in ["inline", "inlined"]:
                if test.stdout:
                    print(f"{label} expected_stdout={repr(test.stdout)}", file=file)
                if test.stderr:
                    print(f"{label} expected_stderr={repr(test.stderr)}", file=file)
            else:
                if test.stdout:
                    trailing_newline = test.stdout[-1] == "\n"
                    stdout = "\n".join(
                        list(
                            map(lambda line: repr(line)[1:-1], test.stdout.splitlines())
                        )
                    ) + ("\n" if trailing_newline else "")
                    print(f'{label} expected_stdout="""\\\n{stdout}"""', file=file)
                    print(file=file)
                if test.stderr:
                    trailing_newline = test.stderr[-1] == "\n"
                    stderr = "\n".join(
                        list(
                            map(lambda line: repr(line)[1:-1], test.stderr.splitlines())
                        )
                    ) + ("\n" if trailing_newline else "")
                    print(f'{label} expected_stderr="""\\\n{stderr}"""', file=file)
                    print(file=file)
