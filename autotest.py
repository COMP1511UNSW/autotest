#!/usr/bin/python3 -I

# main function for autotests

# try to catch keyboard interrupt in imports
import os, signal

# don't complain about os._exit
# pylint: disable=protected-access

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda signum, frame: os._exit(2))

# pylint: disable=wrong-import-position

import json, re, sys, traceback
from collections import OrderedDict


# add autotest directory to module path
if __name__ == "__main__":
    sys.path.append(os.path.dirname(os.path.realpath(__file__)))

from util import AutotestException, TestSpecificationError
from command_line_arguments import process_arguments
from copy_files_to_temp_directory import copy_files_to_temp_directory
from run_tests import run_tests, run_tests_creating_log, generate_expected_output
from upload_results import upload_results_http
from command_line_arguments import REPO_INFORMATION
from sandbox import run_tests_in_sandbox, continue_inside_sandbox


def main():
    debug = os.environ.get("AUTOTEST_DEBUG", 0)  # turn on debugging
    my_name = re.sub(r"\.py$", "", os.path.basename(sys.argv[0]))
    # there may be other threads running so use os._exit(1) to terminate entire program on interrupt
    if not debug:
        signal.signal(signal.SIGINT, lambda signum, frame: os._exit(2))
    try:
        sys.exit(run_autotest())
    except TestSpecificationError as e:
        print(f"{my_name}: {e}", file=sys.stderr)
        if debug:
            traceback.print_exc(file=sys.stderr)
        print("\n" + REPO_INFORMATION)
        sys.exit(2)
    except AutotestException as e:
        print(f"{my_name}: {e}", file=sys.stderr)
        if debug:
            traceback.print_exc(file=sys.stderr)
        #        print('\n' + REPO_INFORMATION)
        sys.exit(2)
    except Exception:
        etype, evalue, _etraceback = sys.exc_info()
        eformatted = "\n".join(traceback.format_exception_only(etype, evalue))
        print(f"{my_name}: internal error: {eformatted}", file=sys.stderr)
        if debug:
            traceback.print_exc(file=sys.stderr)
        print("\n" + REPO_INFORMATION)
        sys.exit(2)


def run_autotest():
    args, tests, parameters = process_arguments()

    if args.print_test_names:
        test_groups = OrderedDict()
        for test in tests.values():
            files = tuple(sorted(test.files))
            test_groups.setdefault(files, []).append(test.label)
        print(
            json.dumps(
                [
                    {"files": files, "labels": labels}
                    for (files, labels) in test_groups.items()
                ]
            )
        )
        return 0

    inside_sandbox = args.inside_sandbox
    starting_sandbox = not inside_sandbox and parameters.get("sandbox", "")

    if starting_sandbox:
        # get absolute path for argv[0]
        # before lost because of cd in copy_files_to_temp_directory
        argv0_realpath = os.path.realpath(sys.argv[0])

    if inside_sandbox:
        # we have been re-invoked inside the  sandbox
        # files were copied to the temporary directory before re-invocation
        # need to recover variables from original invocation
        (tests, args, parameters) = continue_inside_sandbox()
    else:
        copy_files_to_temp_directory(args, parameters)

    uploading_results = parameters.get("upload_url", "")

    if starting_sandbox:
        exit_status = run_tests_in_sandbox(argv0_realpath, tests, args, parameters)
    else:
        if args.generate_expected_output != "no":
            return generate_expected_output(tests, args)

        if uploading_results:
            exit_status = run_tests_creating_log(tests, parameters, args)
        else:
            exit_status = run_tests(tests, parameters, args)

    if uploading_results and not inside_sandbox:
        # upload of tests in sandbox may fail because network is sandbox
        upload_results_http(tests, parameters, args)

    return exit_status


if __name__ == "__main__":
    main()
