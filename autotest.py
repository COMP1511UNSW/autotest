#!/usr/bin/python3 -I

# main function for autotests

# try to catch keyboard interrupt in imports
import os, signal

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda signum, frame: os._exit(2))

import json, re, sys, traceback
from collections import OrderedDict


# add autotest directory to module path
if __name__ == "__main__":
    sys.path.append(os.path.dirname(os.path.realpath(__file__)))

from util import AutotestException
from command_line_arguments import process_arguments
from copy_files_to_temp_directory import copy_files_to_temp_directory
from run_tests import run_tests
from upload_results import run_tests_and_upload_results


def main():
    debug = os.environ.get("AUTOTEST_DEBUG", 0)  # turn on debugging
    my_name = re.sub(r"\.py$", "", os.path.basename(sys.argv[0]))
    # there may be other threads running so use os._exit(1) to terminate entire program on interrupt
    if not debug:
        signal.signal(signal.SIGINT, lambda signum, frame: os._exit(2))
    try:
        sys.exit(run_autotest())
    except AutotestException as e:
        print(f"{my_name}: {e}", file=sys.stderr)
        if debug:
            traceback.print_exc(file=sys.stderr)
        sys.exit(2)
    except Exception:
        etype, evalue, etraceback = sys.exc_info()
        eformatted = "\n".join(traceback.format_exception_only(etype, evalue))
        print(f"{my_name}: internal error: {eformatted}", file=sys.stderr)
        if debug:
            traceback.print_exc(file=sys.stderr)
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

    if parameters.get("upload_url", ""):
        return run_tests_and_upload_results(tests, parameters, args)
    else:
        return run_tests(tests, parameters, args)


if __name__ == "__main__":
    main()
