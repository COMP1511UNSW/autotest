"""
Tests of autotest.py's main(): how each kind of failure is reported to the
person running autotest (a bad tests.txt, an internal error, with and
without AUTOTEST_DEBUG) and that the SIGINT handler which stops everything
and exits 2 is only installed when not debugging, so a debugging developer
gets a Python traceback from an interrupt instead.

Already covered elsewhere and not repeated here: SIGINT removing the
temporary directory and killing the tests (test_fixes, test_parallel), the
sandbox decision (test_integration), obsolete and removed options
(test_fixes) and --print_test_names (test_run_tests).
"""

import os
import signal
import subprocess
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REPO_INFORMATION = (
    "Test specification documentation & source at:"
    " https://github.com/COMP1511UNSW/autotest - issues welcome"
)
SH = "#!/bin/sh\n"
SPEC = 'files=a.sh\nprogram=./a.sh\n1 command="echo x" expected_stdout="x\\n"\n'


def test_bad_tests_txt_names_the_file_and_line_and_exits_2(
    tmp_path, make_exercise, run_autotest
):
    ex = make_exercise(
        tmp_path, SPEC + '2 command="echo y" nonsense_parameter=1\n', files={"a.sh": SH}
    )
    stdout, stderr, status = run_autotest(ex.args)
    assert status == 2
    assert stderr == (
        f"autotest: {ex.tests_txt}:4: error - unknown parameter 'nonsense_parameter'\n"
    )
    # the pointer to the documentation goes to stdout, after a blank line
    assert stdout == "\n" + REPO_INFORMATION + "\n"


def test_bad_tests_txt_with_autotest_debug_adds_a_traceback(
    tmp_path, make_exercise, run_autotest
):
    ex = make_exercise(tmp_path, SPEC + "2 command=\n", files={"a.sh": SH})
    stdout, stderr, status = run_autotest(
        ex.args, env=dict(os.environ, AUTOTEST_DEBUG="1")
    )
    assert status == 2
    assert f"autotest: {ex.tests_txt}:4:" in stderr
    assert "Traceback (most recent call last):" in stderr
    assert "TestSpecificationError" in stderr
    assert REPO_INFORMATION in stdout


def test_bad_parameter_on_the_command_line_is_a_specification_error(
    tmp_path, make_exercise, run_autotest
):
    ex = make_exercise(tmp_path, SPEC, files={"a.sh": SH})
    _, stderr, status = run_autotest(ex.args + ["-P", "nonsense=1"])
    assert status == 2
    assert (
        stderr
        == "autotest: <command-line argument>:1: error - unknown parameter 'nonsense'\n"
    )
    # a value which is only checked once tests.txt is read is reported there
    _, stderr, status = run_autotest(ex.args + ["-P", "parallel_tests=lots"])
    assert status == 2
    assert stderr.startswith(f"autotest: {ex.tests_txt}:")
    assert "invalid value for parameter 'parallel_tests': lots" in stderr
    assert "internal error" not in stderr


def test_autotest_exception_is_reported_without_the_repo_information(
    tmp_path, make_exercise, run_autotest
):
    # a die() (InternalError, an AutotestException) is a message alone
    ex = make_exercise(tmp_path, SPEC, files={"a.sh": SH})
    stdout, stderr, status = run_autotest(ex.args + ["-l", "nope"])
    assert status == 2
    assert stderr == "autotest: unknown labels: nope\n"
    assert stdout == ""


def test_unexpected_exception_is_reported_as_an_internal_error(
    tmp_path, make_exercise, run_autotest
):
    # copying a submission directory which does not exist raises an OSError
    # nothing catches: the person sees "internal error" plus where to report it
    ex = make_exercise(tmp_path, SPEC)
    stdout, stderr, status = run_autotest(
        ["-D", str(tmp_path / "missing"), "-a", ex.autotest]
    )
    assert status == 2
    assert stderr.startswith("autotest: internal error: FileNotFoundError: ")
    assert "Traceback" not in stderr
    assert stdout == "\n" + REPO_INFORMATION + "\n"
    stdout, stderr, status = run_autotest(
        ["-D", str(tmp_path / "missing"), "-a", ex.autotest],
        env=dict(os.environ, AUTOTEST_DEBUG="1"),
    )
    assert status == 2
    assert "autotest: internal error: FileNotFoundError: " in stderr
    assert "Traceback (most recent call last):" in stderr


def test_no_exercise_dies_with_a_message(run_autotest):
    _, stderr, status = run_autotest([])
    assert status == 2
    assert stderr == "autotest: no exercise specified\n"


def test_unknown_exercise_dies_with_a_message(tmp_path, run_autotest):
    _, stderr, status = run_autotest(["nosuch"], cwd=str(tmp_path))
    assert status == 2
    assert stderr == "autotest: no autotest found for nosuch\n"


# ---- SIGINT


def wait_for_sleeping_test(tmpdir, deadline=20):
    """
    wait until the test command (sleep 30, run from a test directory under
    tmpdir) is running, so an interrupt arrives while tests are in progress
    """
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cmdline = f.read()
                cwd = os.readlink(f"/proc/{pid}/cwd")
            except OSError:
                continue
            if cmdline.startswith(b"sleep\x0030\x00") and cwd.startswith(tmpdir):
                return True
        time.sleep(0.05)
    return False


@pytest.fixture
def interruptible_run(tmp_path, make_exercise):
    """start an autotest whose only test sleeps; yields a function
    (extra_env) -> Popen with its test already running"""
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    ex = make_exercise(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\n1 command="sleep 30" expected_stdout=""\n',
        files={"a.sh": SH},
    )
    processes = []

    def start(extra_env):
        env = dict(os.environ, TMPDIR=str(tmpdir), **extra_env)
        p = subprocess.Popen(
            [sys.executable, os.path.join(REPO_ROOT, "autotest.py")] + ex.args,
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
        processes.append(p)
        assert wait_for_sleeping_test(str(tmpdir)), "the test did not start"
        return p

    yield start
    for p in processes:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.wait()
        # a test which failed before communicate() would leak the pipes
        for pipe in (p.stdout, p.stderr):
            if pipe is not None:
                pipe.close()


def test_sigint_without_debug_exits_2_silently(interruptible_run):
    p = interruptible_run({})
    p.send_signal(signal.SIGINT)
    stdout, stderr = p.communicate(timeout=20)
    assert p.returncode == 2, stdout + stderr
    assert "Traceback" not in stderr
    assert "KeyboardInterrupt" not in stderr


def test_sigint_removes_the_temporary_directory(tmp_path, interruptible_run):
    p = interruptible_run({})
    p.send_signal(signal.SIGINT)
    stdout, stderr = p.communicate(timeout=20)
    assert p.returncode == 2, stdout + stderr
    assert os.listdir(tmp_path / "tmp") == []


def test_sigint_with_autotest_debug_gives_a_keyboard_interrupt_traceback(
    interruptible_run,
):
    p = interruptible_run({"AUTOTEST_DEBUG": "1"})
    p.send_signal(signal.SIGINT)
    stdout, stderr = p.communicate(timeout=20)
    # the handler is not installed, so Python's default handling applies:
    # a traceback ending in KeyboardInterrupt and death by the signal
    assert p.returncode != 2, stdout + stderr
    assert "Traceback (most recent call last):" in stderr
    assert stderr.rstrip().endswith("KeyboardInterrupt")
