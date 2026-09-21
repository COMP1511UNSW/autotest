"""
Fixtures shared by the end-to-end tests.

autotest is a command-line program, so most tests drive ./autotest.py as a
subprocess with the interpreter running pytest (the shebang's /usr/bin/python3
may lack termcolor) on an exercise built in tmp_path: a spec directory
holding tests.txt (plus any files the autotest supplies) and a submission
directory holding the student's files.

The fixtures live here rather than in each module so a behaviour is pinned
against one way of running autotest; the older test modules have their own
helpers and are left alone until they are migrated.  The helpers are plain
functions handed out by session-scoped fixtures, so a module-scoped fixture
(one autotest run shared by a module's tests) can use them too.
"""

import os
import re
import shutil
import subprocess
import sys
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# tests/environment expects this variable in the environment autotest was
# invoked with (scripts/do_tests.sh sets it); setdefault so a caller's value wins
os.environ.setdefault("SAMPLE_ENVIRONMENT_VARIABLE", "sample_value")

AUTOTEST = os.path.join(REPO_ROOT, "autotest.py")


def sandbox_unavailable_reason():
    """None if this host can build the default sandbox, else why not"""
    try:
        import sandbox

        return sandbox.probe(sandbox.config_from_parameters({}))
    except (ImportError, OSError) as e:
        # the module needs ctypes and libc; without them there is no sandbox
        return f"sandbox module unavailable: {e}"


# probed once: it builds a real sandbox, which is slow enough not to repeat
SANDBOX_UNAVAILABLE = sandbox_unavailable_reason()

needs_sandbox = pytest.mark.skipif(
    SANDBOX_UNAVAILABLE is not None,
    reason=f"sandbox unavailable: {SANDBOX_UNAVAILABLE}",
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "needs_sandbox: skip when this host can not build the sandbox"
    )
    config.addinivalue_line(
        "markers", "needs_tools(*names): skip when a program is not on PATH"
    )


def pytest_runtest_setup(item):
    """implement the needs_sandbox and needs_tools markers as skips"""
    if item.get_closest_marker("needs_sandbox") and SANDBOX_UNAVAILABLE:
        pytest.skip(f"sandbox unavailable: {SANDBOX_UNAVAILABLE}")
    for marker in item.iter_markers("needs_tools"):
        for name in marker.args:
            if shutil.which(name) is None:
                pytest.skip(f"{name} not installed")


@pytest.fixture(scope="session")
def venv_python():
    """the interpreter autotest is run with: the one running pytest"""
    return sys.executable


def unique_sleep(tag):
    """A `sleep` duration no other copy of the suite on this host is using.

    The orphan tests find leftover processes with `pgrep -xf "sleep N"`,
    which searches every process on the host, so a constant N makes two
    concurrent runs of the suite (a second checkout, a bisect, a CI runner
    with two jobs) fail each other deterministically.  The pid makes N
    unique per test process and tag unique within it; the value stays in
    the 30-40 second range so the sleeps still outlive the test.
    """
    return f"3{tag}.{os.getpid()}"


def sleep_survivors(seconds):
    """pids of host processes whose command line is exactly `sleep seconds`"""
    return subprocess.run(
        ["pgrep", "-xf", f"sleep {seconds}"], stdout=subprocess.PIPE
    ).stdout.split()


@pytest.fixture
def sandbox_available():
    """skip the test when this host can not build the sandbox"""
    if SANDBOX_UNAVAILABLE:
        pytest.skip(f"sandbox unavailable: {SANDBOX_UNAVAILABLE}")


def run_autotest(args, cwd=REPO_ROOT, env=None, stdin=None, timeout=60):
    """
    run ./autotest.py with args (a list of strings) returning
    (stdout, stderr, returncode)

    The output is decoded with errors="replace" because a test can make
    autotest print anything a student's program printed.  stdin is None (no
    input), a str or bytes.
    """
    if isinstance(stdin, str):
        stdin = stdin.encode("utf-8")
    p = subprocess.run(
        [sys.executable, AUTOTEST] + list(args),
        cwd=cwd,
        env=env,
        input=stdin,
        stdin=None if stdin is not None else subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
    )
    return (
        p.stdout.decode("utf-8", errors="replace"),
        p.stderr.decode("utf-8", errors="replace"),
        p.returncode,
    )


@pytest.fixture(name="run_autotest", scope="session")
def run_autotest_fixture():
    return run_autotest


def write_file(path, contents):
    """write str or bytes; a script (starts with #!) is made executable"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(contents, bytes):
        with open(path, "wb") as f:
            f.write(contents)
        executable = contents.startswith(b"#!")
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(contents)
        executable = contents.startswith("#!")
    if executable:
        # only the user running autotest (and the sandboxed program, which
        # has the same uid) needs to run it
        os.chmod(path, 0o700)


def make_exercise(tmp_path, tests_txt, files=None, supplied=None, name="exercise"):
    """
    build an exercise under tmp_path and return a namespace describing it:

    autotest    the spec directory: tests.txt plus the supplied files
    submission  the submission directory containing files
    tests_txt   the pathname of tests.txt
    args        ["-D", submission, "-a", autotest] for run_autotest

    files and supplied map file names (which may contain "/") to str or
    bytes contents.  The layout is <tmp_path>/<name>/{autotest,submission}
    so that several exercises can share one tmp_path.
    """
    base = os.path.join(str(tmp_path), name)
    autotest_dir = os.path.join(base, "autotest")
    submission = os.path.join(base, "submission")
    os.makedirs(autotest_dir)
    os.makedirs(submission)
    tests_txt_path = os.path.join(autotest_dir, "tests.txt")
    write_file(tests_txt_path, tests_txt)
    for filename, contents in (files or {}).items():
        write_file(os.path.join(submission, filename), contents)
    for filename, contents in (supplied or {}).items():
        write_file(os.path.join(autotest_dir, filename), contents)
    return types.SimpleNamespace(
        autotest=autotest_dir,
        submission=submission,
        tests_txt=tests_txt_path,
        args=["-D", submission, "-a", autotest_dir],
    )


@pytest.fixture(name="make_exercise", scope="session")
def make_exercise_fixture():
    return make_exercise


def split_test_output(output):
    """
    split autotest's output into what it printed for each test:
    a dict from label to the text from "Test <label> (" up to the next
    test's line or the summary line
    """
    blocks = {}
    label = None
    for line in output.splitlines(keepends=True):
        m = re.match(r"Test (\w+) \(", line)
        if m:
            label = m.group(1)
            blocks[label] = line
        elif re.match(r"\d+ tests passed", line):
            label = None
        elif label is not None:
            blocks[label] += line
    return blocks


@pytest.fixture(name="split_test_output", scope="session")
def split_test_output_fixture():
    return split_test_output
