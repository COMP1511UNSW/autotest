#!/usr/bin/python3 -I

# main function for autotests

import os
import signal
import sys


def interrupt_handler(_signum, _frame):
    """
    Stop everything on SIGINT and leave nothing behind.

    Tests may be running in other threads and student programs may be running
    in other process groups, so a plain KeyboardInterrupt would not stop them:
    kill the programs, remove the temporary directories and then terminate the
    whole process with os._exit so no other thread can keep going.

    The modules are looked up in sys.modules rather than imported because this
    handler is installed before the imports below (so an interrupt during a
    slow import is caught too) and a module which has not been imported yet
    has nothing to clean up.
    """
    runner = sys.modules.get("subprocess_with_resource_limits")
    # stop_all also stops any further command starting: killing the running
    # tests would otherwise let the pending tests start while cleaning up
    stop_all = getattr(runner, "stop_all", None) or getattr(
        runner, "kill_all_running", None
    )
    try:
        if stop_all is not None:
            stop_all()
    except Exception:  # noqa: BLE001, S110 - the cleanup must go on  # nosec B110
        pass
    temp_directories = sys.modules.get("copy_files_to_temp_directory")
    cleanup_all = getattr(temp_directories, "cleanup_all", lambda: None)
    try:
        cleanup_all()
    except Exception:  # noqa: BLE001, S110 - the exit must happen  # nosec B110
        pass
    os._exit(2)


# in debug mode leave SIGINT alone so an interrupt gives a Python traceback
if __name__ == "__main__" and not os.environ.get("AUTOTEST_DEBUG", ""):
    signal.signal(signal.SIGINT, interrupt_handler)

# the rest of the imports are below the handler (ruff E402 is waived for
# this file in pyproject.toml) so that an interrupt during them is handled
import json
import re
import traceback
from collections import OrderedDict

# add autotest directory to module path
if __name__ == "__main__":
    sys.path.append(os.path.dirname(os.path.realpath(__file__)))

from command_line_arguments import REPO_INFORMATION, process_arguments
from copy_files_to_temp_directory import copy_files_to_temp_directory
from helper import run_helper
from run_tests import (
    generate_expected_output,
    run_tests,
    run_tests_creating_log,
    write_results_document,
)
from upload_results import upload_results_http
from util import AutotestException, TestSpecificationError, warn


def main():
    debug = os.environ.get("AUTOTEST_DEBUG", "")  # turn on debugging
    my_name = re.sub(r"\.py$", "", os.path.basename(sys.argv[0]))
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
        sys.exit(2)
    except Exception:  # noqa: BLE001 - anything else is an internal error
        etype, evalue, _etraceback = sys.exc_info()
        eformatted = "\n".join(traceback.format_exception_only(etype, evalue))
        print(f"{my_name}: internal error: {eformatted}", file=sys.stderr)
        if debug:
            traceback.print_exc(file=sys.stderr)
        print("\n" + REPO_INFORMATION)
        sys.exit(2)


def decide_sandbox(parameters, args, probe=None):
    """
    Decide once, for the whole run, whether student code runs in a sandbox
    and record the decision as args.sandbox_config (a SandboxConfig, or None
    for no sandbox) for run_tests.py.

    parameters["sandbox"] is False (--no_sandbox, sandbox=0), True
    (sandbox=1: the sandbox is required, so a marking host which can not
    create it stops instead of running student code unconfined) or "auto"
    (use it if the host supports it, warn once if it does not).

    The decision is made here rather than per test because the probe (which
    really builds a sandbox) is slow enough to do once, and so that a test
    can not accidentally run outside the sandbox.  probe is a parameter so
    the decision can be tested without a real sandbox.
    """
    setting = parameters.get("sandbox", "auto")
    if setting is False:
        args.sandbox_config = None
        if args.debug > 1:
            print("sandbox: off (sandbox=False)", file=sys.stderr)
        return None

    config = None
    unavailable = AutotestException
    try:
        # imported here so autotest still runs where the sandbox module can
        # not be imported at all (not Linux) and the sandbox is off
        import sandbox

        unavailable = sandbox.SandboxUnavailable
        if probe is None:
            probe = sandbox.probe
        config = sandbox.config_from_parameters(parameters)
        reason = probe(config)
    except AutotestException as e:
        reason = str(e)
    except Exception as e:  # noqa: BLE001 - whatever stops the import means no sandbox
        reason = f"sandbox module unavailable: {e}"

    if reason is None:
        args.sandbox_config = config
        if args.debug > 1:
            print(f"sandbox: on {config!r}", file=sys.stderr)
        return config

    if setting is True:
        raise unavailable(f"sandbox required (sandbox=True) but unavailable: {reason}")
    warn(f"running WITHOUT a sandbox: {reason}")
    args.sandbox_config = None
    return None


def run_autotest():
    args, tests, parameters = process_arguments()

    if args.print_test_names:
        test_groups: dict[tuple[str, ...], list[str]] = OrderedDict()
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

    copy_files_to_temp_directory(args, parameters)

    decide_sandbox(parameters, args)

    if args.generate_expected_output != "no":
        return generate_expected_output(tests, args, parameters)

    uploading_results = parameters.get("upload_url", "")

    if uploading_results:
        exit_status = run_tests_creating_log(tests, parameters, args)
    else:
        exit_status = run_tests(tests, parameters, args)

    run_helper(tests, parameters, args)

    if uploading_results:
        upload_results_http(tests, parameters, args)

    # last, so that a --json path which can not be written does not cost the
    # student the helper or the upload of their results
    document = getattr(args, "json_document", None)
    if document is not None:
        write_results_document(args.json_results_file, document)

    return exit_status


if __name__ == "__main__":
    main()
