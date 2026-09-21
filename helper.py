import json
import os
import subprocess
import sys

from parameter_descriptions import search_path

MAX_EXPECTED_STDOUT = 8192
MAX_STDIN = 8192
MAX_STDOUT = 8192
MAX_STDERR = 8192
MAX_FILE_SIZE = 16384
AUTOTEST_HELPER = "autotest-helper"


def as_text(value):
    """
    Return value as a str for passing to the helper in the environment.

    Streams are bytes rather than str when unicode_stdin/unicode_stdout/
    unicode_stderr is false, and neither environment variables nor
    bytes.replace with str arguments accept that, so decode them in a way
    that can not fail.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="backslashreplace")
    return value


def run_helper(  # noqa: C901, PLR0911, PLR0912 - one early return per size limit
    tests, parameters, args
):
    """
    Run autotest-helper (if it is installed) for the first failed test,
    giving it the test's inputs, outputs and source in the environment.

    The helper is only run for small, simple, single-file tests because it
    is meant to explain a failure to a novice, not to handle everything.
    """
    failed_tests = [t for t in tests.values() if t.test_passed is False]
    if not failed_tests:
        return None
    test = failed_tests[0]
    expected_stdout = as_text(test.expected_stdout)
    expected_stderr = as_text(test.expected_stderr)
    if expected_stderr or len(expected_stdout) > MAX_EXPECTED_STDOUT:
        return None
    stdin = as_text(getattr(test, "stdin", ""))
    if len(stdin) > MAX_STDIN:
        return None
    stderr = as_text(getattr(test, "stderr", ""))
    if len(stderr) > MAX_STDERR:
        return None
    stdout = as_text(getattr(test, "stdout", ""))
    if len(stdout) > MAX_STDOUT:
        return None
    files = getattr(test, "files", "")
    if len(files) != 1:
        return None
    filename = files[0]

    if not search_path(AUTOTEST_HELPER):
        return None

    try:
        if os.path.getsize(filename) > MAX_FILE_SIZE:
            return None
        with open(filename) as f:
            source = f.read(MAX_FILE_SIZE)
    except OSError:
        return None
    helper_info = {
        "test_label": test.label,
        "stdin": stdin,
        "stderr": stderr,
        "stdout": stdout,
        "expected_stdout": expected_stdout,
        "file": filename,
        "source": source,
        "autotest_directory": args.autotest_directory,
    }
    # the environment autotest was invoked with, not the one the tests ran
    # in (HOME=. and no LOGNAME), even if the runner has modified os.environ
    helper_environment = dict(parameters.get("__environment_original") or os.environ)
    for k, v in helper_info.items():
        helper_environment["HELPER_" + k.upper()] = v.replace("\x00", "\\x00")
    helper_environment["HELPER_JSON"] = json.dumps(helper_info, separators=(",", ":"))

    if args.debug:
        print(f"running {AUTOTEST_HELPER} info='{helper_info}'", file=sys.stderr)

    try:
        sys.stdout.flush()
        sys.stderr.flush()
        p = subprocess.run([AUTOTEST_HELPER], env=helper_environment, check=False)
    except OSError as e:
        if args.debug:
            print(e, file=sys.stderr)
    else:
        return p.returncode == 0
    return False
