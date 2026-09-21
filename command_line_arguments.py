# process command-line arguments

import argparse
import fnmatch
import os
import re
import sys

from copy_files_to_temp_directory import load_embedded_autotest
from parameter_descriptions import value_to_bool
from parse_test_specification import parse_file, parse_string
from run_test import _Test
from util import die

# rewrite the extra help

REPO = "https://github.com/COMP1511UNSW/autotest"
REPO_INFORMATION = (
    f"Test specification documentation & source at: {REPO} - issues welcome"
)

EXTRA_HELP = f"""

{REPO_INFORMATION}

Examples:
autotest lab06                                 # all tests for lab06
autotest lab08 -l lectures_3 lectures_4        # run specified tests
"""


def process_arguments():
    args = parse_arguments()

    test_specification_pathname = find_test_specification(args)
    # kept so --lint can name the file its findings are about
    args.test_specification_pathname = test_specification_pathname
    tests_as_dicts, parameters = parse_file(
        test_specification_pathname,
        initial_parameters=args.initial_parameters,
        initial_tests=args.initial_tests,
        debug=args.debug,
    )
    tests = {
        label: _Test(args.autotest_directory, **t)
        for (label, t) in tests_as_dicts.items()
    }
    if not tests:
        die(f"no tests found for {args.exercise}")
    normalize_arguments(args, tests)
    return args, tests, parameters


def parse_arguments():  # noqa: C901, PLR0912, PLR0915 - one branch and one statement per command-line option
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EXTRA_HELP
    )
    parser.add_argument(
        "-a", "--autotest_directory", help="DIRECTORY containing test specification"
    )
    parser.add_argument(
        "-c", "--commit", help="test files from COMMIT instead of latest commit"
    )
    parser.add_argument("-d", "--debug", action="count", help="print debug information")
    parser.add_argument("-e", "--exercise", help="run tests for EXERCISE")
    parser.add_argument(
        "-E",
        "--exercise_directory",
        action="append",
        help="parent DIRECTORY containing exercises",
    )
    parser.add_argument(
        "-f",
        "--file",
        nargs="+",
        default=[],
        help="add a copy of this file to the test directory ",
    )
    parser.add_argument(
        "-g",
        "--generate_expected_output",
        nargs="?",
        const="stdout",
        default="no",
        help="generate expected output for tests based on supplied solution",
    )
    parser.add_argument(
        "-l", "--labels", nargs="+", default=[], help="execute tests with these LABELS"
    )
    parser.add_argument(
        "-m", "--marking", action="store_true", help="run automarking tests"
    )

    parser.add_argument(
        "--print_test_names", action="store_true", help="print names of tests and files"
    )
    parser.add_argument(
        "-p", "--programs", nargs="+", default=[], help="execute tests for PROGRAMS"
    )
    parser.add_argument(
        "-P", "--parameters", help="set parameter values", action="append"
    )

    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        metavar="N",
        help="run N tests concurrently (sets parameter parallel_tests, 0 = one per CPU)",
    )
    parser.add_argument(
        "--lint",
        action="store_true",
        help="report commands the specification names (checkers, setup_command, pre_compile_command, postprocess_output_command) that can not be run, without running any test",
    )
    parser.add_argument(
        "--json",
        metavar="FILE",
        dest="json_results_file",
        help="write a machine-readable description of the run to FILE ('-' for stdout); nothing is written if no test ran",
    )
    parser.add_argument(
        "--check_stability",
        nargs="?",
        # not type=int: the exercise is a positional, so
        # "--check_stability lab06" would be an argparse error rather than
        # lab06 run twice.  The value is sorted out below.
        const="2",
        metavar="N",
        help="run each test N times (default 2, as --check_stability=N) and report any test whose result is not the same every time (sets parameter stability_runs)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="print what each test cost: peak memory and wall clock (sets parameter report_resource_usage)",
    )
    parser.add_argument(
        "--no_sandbox",
        action="store_true",
        help="INSECURE: run programs outside the sandbox, with all the privileges of the invoking user (sets parameter sandbox=False)",
    )

    parser.add_argument("extra_arguments", nargs="*", default=[], help="")

    source_args = parser.add_mutually_exclusive_group()
    source_args.add_argument(
        "-D", "--directory", help="add files from this directory to the test directory"
    )
    source_args.add_argument(
        "-G",
        "--git",
        help="add files from this git repository to the test directory",
    )
    source_args.add_argument(
        "-S",
        "--stdin",
        action="store_true",
        help="test file supplied on standard input",
    )
    source_args.add_argument(
        "-t",
        "--tarfile",
        help="add files from this tarfile to the test directory, can be http URL",
    )

    # removed CSE-specific sources: kept so old wrappers get a clear message
    source_args.add_argument(
        "--gitlab_cse", action="store_true", help=argparse.SUPPRESS
    )
    source_args.add_argument("--student", help=argparse.SUPPRESS)

    add_obsolete_arguments(parser)

    args = parser.parse_args()

    check_obsolete_arguments(args)
    if args.gitlab_cse or args.student is not None:
        die("--gitlab_cse and --student are no longer supported, use --git URL")

    args.debug = int(args.debug or os.environ.get("AUTOTEST_DEBUG", "") or 0)
    args.initial_tests, args.initial_parameters = parse_string(
        "\n".join(args.parameters or ""),
        source_name="<command-line argument>",
        normalize_global_parameters=False,
        debug=args.debug,
    )

    # backwards compatibility
    args.initial_parameters.setdefault("debug", args.debug)

    # command-line conveniences for global parameters
    if args.jobs is not None:
        args.initial_parameters["parallel_tests"] = args.jobs
    if args.no_sandbox:
        # A marking wrapper sets "sandbox = True" with -P and forwards "$@",
        # so --no_sandbox on the end of its arguments turned the sandbox off
        # and said nothing.  Refusing is the only safe answer: a marking run
        # must not be talked out of its sandbox by a trailing argument.
        if "sandbox" in args.initial_parameters and value_to_bool(
            args.initial_parameters["sandbox"]
        ):
            die("--no_sandbox contradicts the sandbox=True given with -P")
        args.initial_parameters["sandbox"] = False
    if args.stats:
        args.initial_parameters["report_resource_usage"] = True
    if args.check_stability is not None:
        # a word which is not a count is the exercise, not the count
        if str(args.check_stability).isdigit():
            args.initial_parameters["stability_runs"] = int(args.check_stability)
        else:
            args.initial_parameters["stability_runs"] = 2
            args.extra_arguments.insert(0, args.check_stability)
    # so a specification can see it, and dcc_output_checking can default off
    args.initial_parameters.setdefault("marking", bool(args.marking))

    if args.debug:
        print("raw args:", args, file=sys.stderr)
    if len(args.extra_arguments) == 2 and re.search(r"\.tar$", args.extra_arguments[0]):
        # give calls dryrun this way
        args.tarfile = args.extra_arguments[0]
        args.exercise = args.extra_arguments[1]
        args.extra_arguments = []
    if not args.exercise and not args.autotest_directory:
        if args.extra_arguments:
            args.exercise = args.extra_arguments.pop(0)
        else:
            die("no exercise specified")
    if not args.exercise and args.autotest_directory:
        args.exercise = os.path.basename(args.autotest_directory)
    return args


def normalize_arguments(  # noqa: C901, PLR0912, PLR0915 - one branch per legacy way of spelling an argument
    args, tests
):
    test_labels = set(tests.keys())
    programs = list({tests[t].program for t in tests})
    files = list({f for t in tests for f in tests[t].files})
    unknown_labels = set(args.labels) - test_labels
    if unknown_labels:
        die("unknown labels: " + " ".join(unknown_labels))
    args.optional_files = []
    for arg in args.extra_arguments:
        p = re.sub(r"^\./", "", arg)
        basename_p = re.sub(r"\.[a-z]{1,4}$", "", p)
        if any(fnmatch.fnmatch(arg, f) for f in files):
            args.file += [arg]
        elif p in programs:
            args.programs += [p]
        elif basename_p in programs:
            args.programs += [basename_p]
            args.file += [p]
        elif arg in test_labels:
            args.labels += [arg]
        elif re.search(r".*\.tar(.[a-z]+)?$", arg) and not args.tarfile:
            args.tarfile = arg
        elif re.search(r"^git\w*@", arg) and not args.git:
            args.git = arg
        elif os.path.isfile(arg):
            args.optional_files += [arg]
        elif matching := regex_matching_labels(arg, test_labels):
            args.labels += matching
        else:
            matching_labels = [
                t
                for t in tests
                if (arg in t) or (arg in tests[t].program) or (tests[t].program in arg)
            ]
            if not matching_labels:
                if not os.path.exists(
                    os.path.join(args.autotest_directory, "runtests.pl")
                ):
                    die(
                        f"unexpected argument '{arg}'\n"
                        f"Specify 1+ of these filenames: {' '.join(files)}\n"
                        f"Or 1+ of these individual tests: {' '.join(test_labels)}"
                    )
            else:
                args.labels += matching_labels
    # if programs are specified run all the tests for them
    if args.debug:
        print("programs:", args.programs, file=sys.stderr)
    if args.programs and not args.labels:
        args.labels += [
            label for label in tests if tests[label].program in args.programs
        ]
    if args.file and not args.labels:
        args.file = set(args.file)
        extra_labels = [
            label for label in tests if set(tests[label].files) == args.file
        ]
        if not extra_labels:
            extra_labels = [
                label
                for label in tests
                if set(tests[label].files).intersection(args.file)
            ]
        args.labels += extra_labels
    if args.debug:
        print("labels:", args.labels, file=sys.stderr)
    # if no labels or programs, run all the tests for the exercise
    if not args.labels:
        args.labels = list(tests.keys())
    args.programs = {tests[label].program for label in args.labels}
    if not args.file:
        args.file = {f for label in args.labels for f in tests[label].files}
    args.file = set(args.file)
    args.optional_files += [
        f
        for label in args.labels
        for f in tests[label].parameters.get("optional_files", [])
    ]
    args.optional_files = set(args.optional_files)
    if args.commit and not args.git:
        die("--commit requires the git repository to be specified with --git URL")
    if args.debug:
        print("normalized args:", args, file=sys.stderr)


def regex_matching_labels(arg, test_labels):
    """
    Return the test labels matching arg treated as a regex, in a stable order.

    An extra argument is usually a filename or a label, so it may well not be
    a valid regex (e.g. '[' or 'a(b'); that is not an error, it just means
    the argument gets the plain substring matching tried next.
    """
    try:
        regex = re.compile(arg)
    except re.error:
        return []
    return sorted(label for label in test_labels if regex.search(label))


def find_test_specification(  # noqa: C901, PLR0912 - one branch per place a specification can be
    args,
):
    if not args.exercise_directory and not args.autotest_directory and args.exercise:
        test_specification_pathname = load_embedded_autotest(args.exercise)
        if test_specification_pathname:
            args.test_specification_pathname = test_specification_pathname
            args.autotest_directory = os.path.dirname(test_specification_pathname)
            return test_specification_pathname

    if not args.exercise_directory:
        args.exercise_directory = ["."]

    # FIXME - generalize this code
    if args.autotest_directory:
        if os.path.isfile(args.autotest_directory):
            # absolute so supplied_files_directory (derived from it) stays
            # valid after the working directory changes
            args.test_specification_pathname = os.path.realpath(args.autotest_directory)
            args.autotest_directory = (
                os.path.dirname(args.test_specification_pathname) + "/"
            )
            return args.test_specification_pathname
        exercise = ""
        exercise_directories = [args.autotest_directory]
        if args.marking:
            sub_pathnames = ["automarking.txt", "tests.txt"]
        else:
            sub_pathnames = ["tests.txt"]
    else:
        exercise_directories = args.exercise_directory
        exercise = args.exercise
        if args.marking:
            sub_pathnames = [
                "automarking.txt",
                "automarking/tests.txt",
                "automarking/automarking.txt",
            ]
        else:
            sub_pathnames = [
                "tests.txt",
                "autotest/tests.txt",
                "autotest/automarking.txt",
            ]

    test_specification_pathname = find_autotest_dir(
        exercise_directories, exercise, sub_pathnames, debug=args.debug
    )

    if not args.autotest_directory:
        args.autotest_directory = os.path.dirname(test_specification_pathname)

    args.autotest_directory = os.path.realpath(args.autotest_directory)

    if args.autotest_directory[-1] != "/":
        args.autotest_directory += "/"
    if args.debug:
        print("autotest_dir:", args.autotest_directory, file=sys.stderr)

    args.test_specification_pathname = os.path.realpath(test_specification_pathname)
    return args.test_specification_pathname


def find_autotest_dir(  # noqa: C901 - one branch per place an autotest directory can be
    exercise_directories, exercise, sub_pathnames, debug=0
):
    """
    search for a test specification file
    """

    # for convenience massage exercise name into several possibilities
    # so for example is exercises is specified as prime.c
    # we try prime as an exercise name if prime.c doesn't exist
    # similarly prime will be tried  if lab03_prime is the exercise name

    exercise_alternative_names = [exercise]
    if "." in exercise:
        exercise_alternative_names.append(re.sub(r"\..*", "", exercise))

    # should this code be generalized?
    m = re.match(r"(\w+?\d{1,2}[ab]?_)(.*)", exercise)
    if m:
        exercise_alternative_names.append(re.sub(r"\..*", "", m.group(2)))

    m = re.match(r"(\w+\d{1,2}[ab]?_)(.*)", exercise)
    if m:
        exercise_alternative_names.append(re.sub(r"\..*", "", m.group(2)))

    for exercise_directory in exercise_directories:
        for possible_exercise_name in exercise_alternative_names:
            for sub_pathname in sub_pathnames:
                path = os.path.join(
                    exercise_directory, possible_exercise_name, sub_pathname
                )
                if debug > 2:
                    print("looking for test specification in", path)
                if os.path.exists(path):
                    if debug > 1:
                        print("test specification found in", path)
                    return path
    if exercise:
        die(f"no autotest found for {exercise}")
    else:
        die("no autotest found")
    sys.exit(0)


# obsolete command-line arguments: (option strings, argparse dest, takes a value, parameter now used instead)
# kept so we can give a helpful message naming the replacement parameter before dying
OBSOLETE_ARGUMENTS = [
    (["-C", "--c_compilers"], "c_compilers", True, "default_compilers"),
    (["--c_checkers"], "c_checkers", True, "default_checkers"),
    (["--colorize", "--no_colorize"], "colorize", False, "colorize_output"),
    (["--no_show_input"], "show_input", False, "show_stdin"),
    (["--no_show_expected"], "show_expected", False, "show_expected_output"),
    (["--no_show_actual"], "show_actual", False, "show_actual_output"),
    (["--no_show_diff"], "show_diff", False, "show_diff"),
    (
        ["--no_show_reproduce_command"],
        "show_reproduce_command",
        False,
        "show_reproduce_command",
    ),
    (
        ["--no_check_hash_bang_line"],
        "check_hash_bang_line",
        False,
        "check_hash_bang_line",
    ),
    (
        ["--no_fail_tests_for_errors"],
        "fail_tests_for_errors",
        False,
        "allow_unexpected_stderr",
    ),
    (
        ["--show_stdout_if_errors"],
        "show_stdout_if_errors",
        False,
        "show_stdout_if_errors",
    ),
    (["--ssh_upload_url"], "ssh_upload_url", True, "upload_url"),
    (["--ssh_upload_host"], "ssh_upload_host", True, "upload_url"),
    (["--ssh_upload_max_bytes"], "ssh_upload_max_bytes", True, "upload_max_bytes"),
    (["--no_style"], "no_style", False, "default_checkers"),
]


def add_obsolete_arguments(parser):
    """
    add obsolete arguments so we can give a helpful message before dying if they are used

    Flags store the option string actually used so the message can name it;
    the default is None so check_obsolete_arguments can tell "not given" apart
    from any value.
    """
    for option_strings, dest, takes_value, _parameter_name in OBSOLETE_ARGUMENTS:
        if takes_value:
            parser.add_argument(
                *option_strings, dest=dest, default=None, help=argparse.SUPPRESS
            )
        else:
            for option_string in option_strings:
                parser.add_argument(
                    option_string,
                    dest=dest,
                    action="store_const",
                    const=option_string,
                    default=None,
                    help=argparse.SUPPRESS,
                )


def check_obsolete_arguments(args):
    """
    give helpful message for obsolete arguments then die
    """
    for option_strings, dest, takes_value, parameter_name in OBSOLETE_ARGUMENTS:
        value = getattr(args, dest, None)
        if value is None:
            continue
        argument = option_strings[-1] if takes_value else value
        die(
            f"argument '{argument}' no longer supported, instead use -P to specify an equivalent value for parameter '{parameter_name}'"
        )
