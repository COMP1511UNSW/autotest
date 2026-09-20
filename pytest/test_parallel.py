"""
Tests of running tests concurrently (parallel_tests / -j): each test gets
its own copy of the working directory, setup_command runs for every test,
and the output is printed in test order whatever order the tests finish in.
"""

import os
import re
import signal
import stat
import subprocess
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_autotest(directory, *arguments, timeout=60):
    """run ./autotest.py on a submission directory made by write_autotest"""
    p = subprocess.run(
        [
            sys.executable,
            "./autotest.py",
            "-D",
            directory,
            "-a",
            directory + "/autotest",
        ]
        + list(arguments),
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    return p.stdout, p.returncode


def write_autotest(directory, tests_txt, files=None):
    directory = str(directory)
    autotest = os.path.join(directory, "autotest")
    os.makedirs(autotest)
    with open(os.path.join(autotest, "tests.txt"), "w") as f:
        f.write(tests_txt)
    for name, contents in (files or {}).items():
        path = os.path.join(directory, name)
        with open(path, "w") as f:
            f.write(contents)
        if contents.startswith("#!"):
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return directory


ISOLATION_SPEC = """files=a.sh
program=./a.sh
1 command="touch made_by_test1; echo ok" expected_stdout="ok\\n"
2 command="test -e made_by_test1 && echo present || echo absent" expected_stdout="absent\\n"
3 command="echo 1 >one.txt" expected_stdout="" expected_files={"one.txt": "1\\n"}
4 command="cat setup_count" expected_stdout="setup\\n" setup_command="echo setup >>setup_count"
5 command="cat setup_count" expected_stdout="setup\\n" setup_command="echo setup >>setup_count"
"""


@pytest.mark.parametrize("jobs", ["1", "4"])
def test_each_test_runs_in_its_own_directory(tmp_path, jobs):
    directory = write_autotest(tmp_path, ISOLATION_SPEC, {"a.sh": "#!/bin/sh\n"})
    output, status = run_autotest(directory, "-j", jobs)
    assert status == 0, output
    assert "5 tests passed 0 tests failed" in output
    # nothing a test made leaked into the submission directory
    assert not os.path.exists(os.path.join(directory, "made_by_test1"))


def test_setup_command_output_appears_after_the_test_line(tmp_path):
    directory = write_autotest(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\nsetup_command="echo setting up"\n'
        '1 expected_stdout="hi\\n"\n2 expected_stdout="hi\\n"\n',
        {"a.sh": "#!/bin/sh\necho hi\n"},
    )
    for jobs in ("1", "3"):
        output, status = run_autotest(directory, "-j", jobs)
        assert status == 0, output
        assert (
            "Test 1 (./a.sh) - setting up\npassed\nTest 2 (./a.sh) - setting up\npassed\n"
            in output
        ), output


ORDER_SPEC = """files=a.sh
program=./a.sh
slow command="sleep 1.5; echo slow" expected_stdout="slow\\n"
medium command="sleep 0.5; echo medium" expected_stdout="medium\\n"
fast command="echo fast" expected_stdout="fast\\n"
wrong command="echo wrong" expected_stdout="right\\n"
wrong_again command="echo wrong" expected_stdout="right\\n"
"""


def test_results_are_printed_in_test_order_and_concurrently(tmp_path):
    directory = write_autotest(tmp_path, ORDER_SPEC, {"a.sh": "#!/bin/sh\n"})
    serial, status = run_autotest(directory, "-j", "1")
    assert status == 1, serial
    start = time.time()
    parallel, status = run_autotest(directory, "-j", "8")
    elapsed = time.time() - start
    assert status == 1, parallel
    assert serial == parallel
    labels = re.findall(r"^Test (\w+) ", parallel, flags=re.MULTILINE)
    assert labels == ["slow", "medium", "fast", "wrong", "wrong_again"]
    assert "3 tests passed 2 tests failed" in parallel
    # dedup is applied in print order, so the later test is the duplicate
    assert (
        "wrong_again ('echo wrong') - failed (Incorrect output - same as Test wrong)"
        in parallel
    )
    # the sleeps overlapped: a serial run needs at least 2 s of sleeping
    assert elapsed < 2 + 8, elapsed


def test_parallel_tests_parameter_is_honoured(tmp_path):
    # four 2-second tests take about 2 s with 4 workers and 8 s with one,
    # so the ceiling leaves 4 s for a loaded host without admitting a
    # serial run
    spec = "files=a.sh\nprogram=./a.sh\nparallel_tests=4\n" + "".join(
        f'{i} command="sleep 2" expected_stdout=""\n' for i in range(4)
    )
    directory = write_autotest(tmp_path, spec, {"a.sh": "#!/bin/sh\n"})
    start = time.time()
    output, status = run_autotest(directory)
    elapsed = time.time() - start
    assert status == 0, output
    assert "4 tests passed 0 tests failed" in output
    assert elapsed < 6, elapsed


def test_temporary_directories_are_removed(tmp_path):
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    directory = write_autotest(
        tmp_path / "submission", ISOLATION_SPEC, {"a.sh": "#!/bin/sh\n"}
    )
    env = dict(os.environ)
    env["TMPDIR"] = str(tmpdir)
    p = subprocess.run(
        [
            sys.executable,
            "./autotest.py",
            "-D",
            directory,
            "-a",
            directory + "/autotest",
            "-j",
            "4",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        encoding="utf-8",
    )
    assert p.returncode == 0, p.stdout
    assert os.listdir(tmpdir) == []


SLOW_SPEC = "files=a.sh\nprogram=./a.sh\n" + "".join(
    f'{i} expected_stdout=""\n' for i in range(1, 7)
)


@pytest.mark.parametrize("extra", [(), ("--no_sandbox",)])
def test_interrupt_during_parallel_run_leaves_nothing_behind(tmp_path, extra):
    """
    SIGINT kills the running tests, which lets the pending tests start
    (creating directories) while the handler removes the temporary tree
    unless they are stopped first
    """
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    directory = write_autotest(
        tmp_path / "submission", SLOW_SPEC, {"a.sh": "#!/bin/sh\nsleep 5\n"}
    )
    env = dict(os.environ)
    env["TMPDIR"] = str(tmpdir)
    with subprocess.Popen(
        [
            sys.executable,
            "./autotest.py",
            "-D",
            directory,
            "-a",
            directory + "/autotest",
            "-j",
            "2",
        ]
        + list(extra),
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
    ) as p:
        # wait until the first tests are running
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not sleeping_processes(str(tmpdir)):
            time.sleep(0.05)
        assert sleeping_processes(str(tmpdir)), "tests did not start"
        p.send_signal(signal.SIGINT)
        output, _ = p.communicate(timeout=20)
    assert p.returncode == 2, output
    assert "Warning" not in output, output
    # the killed tests are reaped after autotest has exited, so poll
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and (
        os.listdir(tmpdir) or sleeping_processes(str(tmpdir))
    ):
        time.sleep(0.05)
    assert os.listdir(tmpdir) == []
    assert not sleeping_processes(str(tmpdir))


def sleeping_processes(tmpdir):
    """the pids of test commands (`sleep 5` run from a test directory under tmpdir)"""
    pids = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read()
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            continue
        if cmdline.startswith(b"sleep\x005") and cwd.startswith(tmpdir):
            pids.append(int(pid))
    return pids


def test_shared_test_directory_lets_a_test_use_an_earlier_test_s_files(
    run_autotest, tmp_path
):
    """
    Specifications written before per-test directories rely on shared state.

    COMP1521's unique_files has two tests create files with setup_command and
    three more, with no setup_command of their own, that read them.  Such a
    specification sets shared_test_directory to get the old behaviour back.
    """
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "tests.txt").write_text(
        "files=show.sh\n"
        "program=./show.sh\n"
        'maker setup_command="touch left_behind" command="ls left_behind"\n'
        'maker expected_stdout="left_behind\\n"\n'
        'user command="ls left_behind" expected_stdout="left_behind\\n"\n'
    )
    submission = tmp_path / "sub"
    submission.mkdir()
    show = submission / "show.sh"
    show.write_text("#!/bin/sh\n")
    show.chmod(0o700)
    args = ["-D", str(submission), "-a", str(spec), "--no_sandbox"]

    # the default isolates every test, so the second one finds nothing
    stdout, _stderr, _status = run_autotest(args)
    assert "1 tests passed 1 tests failed" in stdout, stdout

    stdout, _stderr, status = run_autotest([*args, "-P", "shared_test_directory=1"])
    assert "2 tests passed 0 tests failed" in stdout, stdout
    assert status == 0

    # asking for both is not an error: sharing wins and the tests are serialised
    stdout, _stderr, status = run_autotest(
        [*args, "-j", "8", "-P", "shared_test_directory=1"]
    )
    assert "2 tests passed 0 tests failed" in stdout, stdout
    assert status == 0


def test_shared_test_directory_prepares_one_test_at_a_time(run_autotest, tmp_path):
    """
    Sharing a directory serialises preparation as well as execution.

    Running every pre_compile_command up front would let the last one decide
    what the first test saw, which is the defect per-test directories were
    given their own preparation to fix.  A shared directory has to interleave
    instead, exactly as a serial run did.
    """
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "tests.txt").write_text(
        "files=show.sh\n"
        "program=./show.sh\n"
        "shared_test_directory=1\n"
        'one pre_compile_command="echo first > chosen.txt"\n'
        'one command="cat chosen.txt" expected_stdout="first\\n"\n'
        'two pre_compile_command="echo second > chosen.txt"\n'
        'two command="cat chosen.txt" expected_stdout="second\\n"\n'
    )
    submission = tmp_path / "sub"
    submission.mkdir()
    show = submission / "show.sh"
    show.write_text("#!/bin/sh\n")
    show.chmod(0o700)

    stdout, _stderr, status = run_autotest(
        ["-D", str(submission), "-a", str(spec), "--no_sandbox"]
    )
    assert "2 tests passed 0 tests failed" in stdout, stdout
    assert status == 0
