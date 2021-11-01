# process command-line arguments

import argparse, fnmatch, os, re, sys
from parse_test_specification import parse_file, parse_string
from util import die
from run_test import _Test
from copy_files_to_temp_directory import load_embedded_autotest

# rewrite the extra help

EXTRA_HELP = """
Examples:
autotest lab06                                 # all tests for lab06
autotest lab08 -l lectures_3 lectures_4        # run specified tests
"""


def process_arguments():
    (parser, args) = parse_arguments()

    test_specification_pathname = find_test_specification(args)
    tests_as_dicts, parameters = parse_file(
        test_specification_pathname,
        initial_parameters=args.initial_parameters,
        initial_tests=args.initial_tests,
        debug=args.debug,
    )
    tests = dict(
        (label, _Test(args.autotest_directory, **t))
        for (label, t) in tests_as_dicts.items()
    )
    if not tests:
        die("no tests found for %s" % args.exercise)
    normalize_arguments(parser, args, tests)
    return args, tests, parameters


def parse_arguments():
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

    parser.add_argument("extra_arguments", nargs="*", default=[], help="")

    source_args = parser.add_mutually_exclusive_group()
    source_args.add_argument(
        "-D", "--directory", help="add files from this directory to the test directory"
    )
    source_args.add_argument(
        "-G",
        "--git",
        help="add files from this this git repository to the test directory",
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

    # these CSE specific parameters should be move parameters which can be specified in a shell wrapper
    source_args.add_argument(
        "--gitlab_cse",
        action="store_true",
        help="deprocated: test files from gitlab.cse.unsw.edu.au",
    )
    source_args.add_argument(
        "--student",
        help="deprocated: test files from STUDENT's repository on gitlab.cse.unsw.edu.au",
    )

    add_obsolete_arguments(parser)

    args = parser.parse_args()

    check_obsolete_arguments(args)

    args.debug = int(args.debug or os.environ.get("AUTOTEST_DEBUG", 0) or 0)
    args.initial_tests, args.initial_parameters = parse_string(
        "\n".join(args.parameters or ""),
        source_name="<command-line argument>",
        normalize_global_parameters=False,
        debug=args.debug,
    )

    # backwards compatibility
    args.initial_parameters.setdefault("debug", args.debug)

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
    return (parser, args)


def normalize_arguments(parser, args, tests):
    test_labels = set(list(tests.keys()))
    programs = list(set(tests[t].program for t in tests))
    files = list(set(f for t in tests for f in tests[t].files))
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
        elif re.search(r"^git\w+@", arg) and not args.git:
            args.git = arg
        elif os.path.isfile(arg):
            args.optional_files += [arg]
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
                        f"unexpected argument '{arg}'\nSpecify 1+ of these filenames: {' '.join(files)}\nOr 1+ of these individual tests: {' '.join(test_labels)}"
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
    args.programs = set(tests[label].program for label in args.labels)
    if not args.file:
        args.file = set(f for label in args.labels for f in tests[label].files)
    args.file = set(args.file)
    args.optional_files += [
        f
        for label in args.labels
        for f in tests[label].parameters.get("optional_files", [])
    ]
    args.optional_files = set(args.optional_files)
    if (args.gitlab_cse or args.commit or args.student) and not args.git:
        args.git = repository_name(args.exercise, account=args.student)
    if args.debug:
        print("normalized args:", args, file=sys.stderr)


def find_test_specification(args):
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
            args.test_specification_pathname = args.autotest_directory
            args.autotest_directory = os.path.dirname(args.autotest_directory) + "/"
            return args.test_specification_pathname
        exercise = ""
        exercise_directories = [args.autotest_directory]
        if args.marking:
            sub_pathnames = ["automarking,txt", "tests.txt"]
        else:
            sub_pathnames = ["tests.txt"]
    else:
        exercise_directories = args.exercise_directory
        exercise = args.exercise
        if args.marking:
            sub_pathnames = [
                "automarking,txt",
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


def find_autotest_dir(exercise_directories, exercise, sub_pathnames, debug=0):
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


def add_obsolete_arguments(parser):
    """
    add obsolete arguments so we can give a helpful message before dying if they are used
    """
    parser.add_argument("-C", "--c_compilers", help=argparse.SUPPRESS)
    parser.add_argument("--c_checkers", help=argparse.SUPPRESS)
    # 	parser.add_argument("-j", "--json", help=argparse.SUPPRESS)
    parser.add_argument("--colorize", dest="colorize", help=argparse.SUPPRESS)
    parser.add_argument("--no_colorize", dest="colorize", help=argparse.SUPPRESS)
    parser.add_argument("--no_show_input", dest="show_input", help=argparse.SUPPRESS)
    parser.add_argument(
        "--no_show_expected", dest="show_expected", help=argparse.SUPPRESS
    )
    parser.add_argument("--no_show_actual", dest="show_actual", help=argparse.SUPPRESS)
    parser.add_argument("--no_show_diff", dest="show_diff", help=argparse.SUPPRESS)
    parser.add_argument(
        "--no_show_reproduce_command",
        dest="show_reproduce_command",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--no_check_hash_bang_line", dest="check_hash_bang_line", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--no_fail_tests_for_errors",
        dest="fail_tests_for_errors",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--show_stdout_if_errors", dest="show_stdout_if_errors", help=argparse.SUPPRESS
    )
    parser.add_argument("--ssh_upload_url", help=argparse.SUPPRESS)
    parser.add_argument("--ssh_upload_host", help=argparse.SUPPRESS)
    # 	parser.add_argument("--ssh_upload_username", help=argparse.SUPPRESS)
    # 	parser.add_argument("--ssh_upload_keyfile", help=argparse.SUPPRESS)
    # 	parser.add_argument("--ssh_upload_key", help=argparse.SUPPRESS)
    parser.add_argument("--ssh_upload_max_bytes", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--no_style", help=argparse.SUPPRESS)


# link obsolete argument to a parameter making it obsolete
PARAMETERS_MATCHING_OBSOLETE_ARGUMENTS = [
    ("c_compilers", "default_compilers"),
    ("c_checkers", "default_checkers"),
    ("colorize", "colorize_output"),
    ("show_input", "show_stdin"),
    ("show_expected", "show_reproduce_command"),
    ("show_stdout_if_errors", "show_stdout_if_errors"),
    ("no_fail_tests_for_errors", "allow_unexpected_stderr"),
    ("ssh_upload_url", "upload_url"),
    ("ssh_upload_max_bytes", "upload_max_bytes"),
    ("no_style", "default_checkers"),
]


def check_obsolete_arguments(args):
    """
    give helpful message for obsolete arguments then die
    """
    for (argument, parameter_name) in PARAMETERS_MATCHING_OBSOLETE_ARGUMENTS:
        if hasattr(args, argument) and getattr(args, argument) != None:
            print(getattr(args, argument))
            die(
                f"argument '{argument}' no longer supported, instead use -P to specify an equivalent value for parameter '{parameter_name}'"
            )


# FIXME
# move this CSE specific code to to the shell shim
def repository_name(submission_name, account=None):
    from autotest import get_zid

    zid = get_zid(account)
    if re.search(r"^lab", submission_name):
        submission_name = "labs"
    import course_configuration  # type: ignore

    c = course_configuration["course_code"].lower()
    return "gitlab@gitlab.cse.unsw.EDU.AU:%s/%s-%s-%s" % (
        zid,
        course_configuration["unsw_session"],
        c,
        submission_name,
    )