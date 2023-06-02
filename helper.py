import json, os, subprocess, sys
from parameter_descriptions import search_path

MAX_EXPECTED_STDOUT = 8192
MAX_STDIN = 8192
MAX_STDOUT = 8192
MAX_STDERR = 8192
MAX_FILE_SIZE = 16384
AUTOTEST_HELPER = "autotest-helper"


def run_helper(tests, parameters, args):
    failed_tests = [t for t in tests.values() if t.test_passed == False]
    if not failed_tests:
        return
    test = failed_tests[0]
    expected_stdout = test.expected_stdout
    expected_stderr = test.expected_stderr
    if expected_stderr or len(expected_stdout) > MAX_EXPECTED_STDOUT:
        return
    stdin = getattr(test, "stdin", "")
    if len(stdin) > MAX_STDIN:
        return
    stderr = getattr(test, "stderr", "")
    if len(stderr) > MAX_STDERR:
        return
    stdout = getattr(test, "stdout", "")
    if len(stdout) > MAX_STDOUT:
        return
    files = getattr(test, "files", "")
    if len(files) != 1:
        return
    filename = files[0]

    if not search_path(AUTOTEST_HELPER):
        return

    try:
        if os.path.getsize(filename) > MAX_FILE_SIZE:
            return
        with open(filename) as f:
            source = f.read(MAX_FILE_SIZE)
    except OSError:
        pass

    helper_info = {
        "test_label": test.label,
        "stdin": stdin,
        "stderr": stderr,
        "stdout": stdout,
        "expected_stdout": expected_stdout,
        "file": filename,
        "source": source,
    }
    for k, v in helper_info.items():
        os.environ["HELPER_" + k.upper()] = v
    os.environ["HELPER_JSON"] = json.dumps(helper_info, separators=(",", ":"))

    if args.debug:
        print(f"running {AUTOTEST_HELPER} info='{helper_info}'")

    for var in ["HOME", "LOGNAME"]:
        value = parameters["__environment_original"].get(var, "")
        if value:
            os.environ[var] = value

    try:
        sys.stdout.flush()
        sys.stderr.flush()
        p = subprocess.run([AUTOTEST_HELPER])
        return p.returncode == 0
    except OSError as e:
        if args.debug:
            print(e)
    return False
