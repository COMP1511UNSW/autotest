"""Tests for subprocess_with_resource_limits.run(), the process runner.

Run from the repository root:
    python -m pytest -q pytest/test_subprocess_limits.py
"""

import concurrent.futures
import os
import signal
import sys
import threading
import time
from typing import Any

from conftest import sleep_survivors, unique_sleep

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subprocess_with_resource_limits as runner
from subprocess_with_resource_limits import kill_all_running, run

PYTHON = sys.executable


def python_command(source):
    """A list command running source with this test's interpreter."""
    return [PYTHON, "-c", source]


# ---------------------------------------------------------------- basics


def test_list_command_captures_output_and_exit_status():
    result = run(
        python_command(
            "import sys; sys.stdout.write('out'); sys.stderr.write('err'); sys.exit(3)"
        )
    )
    assert result == (b"out", b"err", 3)


def test_list_command_elements_are_converted_to_str():
    result = run(["echo", 42, 3.5])
    assert result == (b"42 3.5\n", b"", 0)


def test_str_command_is_run_by_a_shell():
    result = run("echo hello; echo bad >&2; exit 4")
    assert result == (b"hello\n", b"bad\n", 4)


def test_returncode_is_negative_signal_number_on_signal_death():
    result = run("kill -TERM $$")
    assert result[2] == -signal.SIGTERM


def test_stdin_str_is_utf8_encoded():
    result = run(["cat"], stdin="héllo wörld\n")
    assert result == ("héllo wörld\n".encode(), b"", 0)


def test_stdin_bytes_are_passed_as_is():
    data = b"\xff\x00\x01binary\n"
    result = run(["cat"], stdin=data, unicode_stdin=False)
    assert result == (data, b"", 0)


def test_empty_stdin_is_devnull():
    # cat on an empty /dev/null returns immediately rather than hanging
    assert run(["cat"], stdin="") == (b"", b"", 0)
    assert run(["cat"], stdin=None) == (b"", b"", 0)


def test_env_is_honoured_and_os_environ_not_mutated():
    before = dict(os.environ)
    result = run(
        "echo $AUTOTEST_RUNNER_TEST",
        env={"AUTOTEST_RUNNER_TEST": "value", "PATH": os.environ["PATH"]},
    )
    assert result == (b"value\n", b"", 0)
    assert os.environ == before
    assert "AUTOTEST_RUNNER_TEST" not in os.environ


def test_cwd_is_honoured(tmp_path):
    result = run(["pwd"], cwd=str(tmp_path))
    assert result[2] == 0
    assert os.path.realpath(result[0].decode().strip()) == os.path.realpath(
        str(tmp_path)
    )


def test_command_not_found_returns_status_2_with_message():
    stdout, stderr, returncode = run(["/nonexistent/program/for/autotest"])
    assert stdout == b""
    assert returncode == 2
    assert b"No such file or directory" in stderr
    assert not stderr.startswith(b"[")


def test_full_test_parameter_dictionary_is_accepted():
    # run_test.py calls run(**self.parameters) with every tests.txt parameter,
    # including "sandbox" which there is a flag rather than a sandbox object
    parameters: dict[str, Any] = {
        "command": "echo ok",
        "files": ["prog.c"],
        "expected_stdout": "ok\n",
        "expected_stderr": "",
        "environment": {"PATH": "/bin"},
        "label": "test0",
        "debug": 0,
        "program": "prog",
        "arguments": [],
        "stdin": "",
        "unicode_stdin": True,
        "unicode_stdout": True,
        "unicode_stderr": True,
        "sandbox": True,
        "sandbox_network": False,
        "max_cpu_seconds": 5,
        "max_real_seconds": 10,
        "max_stdout_bytes": 1000,
        "max_stderr_bytes": 1000,
        "max_file_size_bytes": 8192,
        "max_core_size": 0,
        "max_stack_bytes": 32000000,
        "max_rss_bytes": 100000000,
        "max_processes": 4096,
        "max_open_files": 256,
        "nice": 0,
        "compile_commands": [],
        "checkers": [],
        "setup_command": "",
        "no_replace_stdout": False,
        "ignore_case": False,
    }
    assert run(**parameters) == (b"ok\n", b"", 0)


# ---------------------------------------------------------------- limits


def test_cpu_limit_kills_with_sigxcpu_and_message():
    _stdout, stderr, returncode = run(
        'python3 -c "while 1: pass"', max_cpu_seconds=1, max_real_seconds=10
    )
    assert returncode == -signal.SIGXCPU
    assert stderr.endswith(b"Error: CPU limit of 1 seconds exceeded\n")


def test_cpu_limit_zero_means_unlimited():
    busy = (
        "import time\nt = time.time()\nwhile time.time() - t < 0.5: pass\nprint('done')"
    )
    result = run(python_command(busy), max_cpu_seconds=0)
    assert result == (b"done\n", b"", 0)


def test_every_resource_limit_reaches_the_command():
    """Each limit run() applies must be in force in the command itself.

    A limit which is quietly not applied is invisible in every other test:
    max_processes is the fork-bomb defence, max_stack_bytes decides when a
    runaway recursion dies, and max_core_size and nice are documented
    parameters a marker sets.  Reading them back from inside the child is
    the only thing that notices one being dropped.
    """
    source = (
        "import os, resource\n"
        "for name in ('NPROC', 'STACK', 'RSS', 'CORE', 'NOFILE', 'FSIZE', 'CPU'):\n"
        "    print(name, *resource.getrlimit(getattr(resource, 'RLIMIT_' + name)))\n"
        "print('PRIORITY', os.getpriority(os.PRIO_PROCESS, 0))\n"
    )
    before = os.getpriority(os.PRIO_PROCESS, 0)
    stdout, stderr, returncode = run(
        python_command(source),
        max_processes=4096,
        max_stack_bytes=32000000,
        max_rss_bytes=100000000,
        max_core_size=0,
        max_open_files=256,
        max_file_size_bytes=8192,
        max_cpu_seconds=30,
        nice=3,
    )
    assert returncode == 0, stderr
    limits = {
        line.split()[0]: line.split()[1:]
        for line in stdout.decode().splitlines()
        if line
    }
    assert limits["NPROC"] == ["4096", "4097"]
    assert limits["STACK"] == ["32000000", "32000001"]
    assert limits["RSS"] == ["100000000", "100000001"]
    assert limits["CORE"] == ["0", "1"]
    # max_open_files is a count of the files the command may open, so the
    # descriptor limit is one higher
    assert limits["NOFILE"] == ["257", "258"]
    assert limits["FSIZE"] == ["8192", "8193"]
    assert limits["CPU"] == ["30", "31"]
    # nice is an increment, as nice(1) is
    assert limits["PRIORITY"] == [str(before + 3)]


def test_real_time_limit_kills_whole_process_group():
    seconds = unique_sleep(7)
    assert not sleep_survivors(seconds), "stale sleep from an earlier run"
    start = time.monotonic()
    _stdout, stderr, returncode = run(
        f'sh -c "sleep {seconds} & wait"', max_real_seconds=1
    )
    elapsed = time.monotonic() - start
    assert elapsed < 10
    assert returncode == -signal.SIGKILL
    assert stderr == b"Error: real time limit of 1 seconds exceeded\n"
    assert not sleep_survivors(seconds), "grandchild sleep survived the kill"


def test_real_time_limit_applies_after_child_closes_its_output():
    # a program that closes stdout/stderr and keeps running must still be
    # stopped by the wall clock
    start = time.monotonic()
    _stdout, stderr, returncode = run(
        "exec >/dev/null 2>&1; sleep 30", max_real_seconds=1
    )
    assert time.monotonic() - start < 10
    assert returncode == -signal.SIGKILL
    assert stderr == b"Error: real time limit of 1 seconds exceeded\n"


def test_real_time_limit_zero_means_unlimited():
    assert run("sleep 0.2; echo ok", max_real_seconds=0) == (b"ok\n", b"", 0)


def test_stdout_overflow_truncates_kills_and_reports():
    start = time.monotonic()
    stdout, stderr, returncode = run("yes", max_stdout_bytes=1000)
    assert time.monotonic() - start < 5
    assert len(stdout) == 1000
    assert stderr == b"\nError too much output - maximum stdout bytes of 1000 exceeded."
    assert returncode == -signal.SIGKILL


def test_stderr_overflow_truncates_silently_and_kills():
    # as before the rewrite: stderr is cut at the limit with no message
    # appended (the message is only for stdout overflow), and the program
    # is killed
    start = time.monotonic()
    stdout, stderr, returncode = run("yes >&2", max_stderr_bytes=1000)
    assert time.monotonic() - start < 5
    assert stdout == b""
    assert stderr == b"y\n" * 500
    assert returncode == -signal.SIGKILL


def test_output_limit_zero_means_unlimited():
    stdout, stderr, returncode = run("head -c 3000000 /dev/zero", max_stdout_bytes=0)
    assert len(stdout) == 3000000
    assert (stderr, returncode) == (b"", 0)


def test_file_size_limit_gives_sigxfsz_and_message(tmp_path):
    # exec so the shell itself is the process writing (and killed)
    _stdout, stderr, returncode = run(
        "exec yes > out", cwd=str(tmp_path), max_file_size_bytes=32, max_real_seconds=10
    )
    assert returncode == -signal.SIGXFSZ
    assert stderr.endswith(b"Error: maximum file creation size of 32 bytes exceeded\n")
    assert os.path.getsize(tmp_path / "out") <= 32


def test_file_size_limit_reaches_grandchildren(tmp_path):
    # without exec bash forks yes and reports the signal itself
    _stdout, stderr, returncode = run(
        "yes > out", cwd=str(tmp_path), max_file_size_bytes=32, max_real_seconds=10
    )
    assert returncode == 128 + signal.SIGXFSZ
    assert b"File size limit exceeded" in stderr
    assert os.path.getsize(tmp_path / "out") <= 32


# ---------------------------------------------------------------- threads


def test_concurrent_runs_get_their_own_output():
    def one(i):
        return run(f"sleep 0.2; echo out{i}; echo err{i} >&2; exit {i}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(one, range(8)))
    for i, result in enumerate(results):
        assert result == (f"out{i}\n".encode(), f"err{i}\n".encode(), i)
    assert not runner._running_process_groups


@pytest.mark.parametrize(
    "limits",
    [
        {"max_real_seconds": 1000000000},
        {"max_cpu_seconds": 1000000000, "max_real_seconds": 20000000000},
    ],
)
def test_huge_time_limits_are_accepted(limits):
    """test specifications use 1000000000 to mean unlimited; select() would
    reject such a timeout"""
    assert run(["echo", "hi"], **limits) == (b"hi\n", b"", 0)


def test_stop_all_prevents_further_commands(monkeypatch):
    from util import AutotestException

    monkeypatch.setattr(runner, "_stopped", threading.Event())
    assert not runner.stopped()
    runner.stop_all()
    assert runner.stopped()
    with pytest.raises(AutotestException):
        run(["echo", "hi"])


def test_kill_all_running_stops_command_started_in_another_thread():
    seconds = unique_sleep(3)
    results = []
    thread = threading.Thread(
        target=lambda: results.append(run(f"sleep {seconds}", max_real_seconds=20))
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not runner._running_process_groups and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner._running_process_groups
    start = time.monotonic()
    kill_all_running()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert time.monotonic() - start < 10
    assert results == [(b"", b"", -signal.SIGKILL)]
    assert not runner._running_process_groups
    assert not sleep_survivors(seconds)


def test_kill_all_running_from_signal_handler_while_lock_held():
    """kill_all_running() runs from autotest's SIGINT handler on the main
    thread, which may be inside run() holding the registry lock; the handler
    must not block on that lock (it would hang forever) and must still kill
    the process group registered by another thread."""
    seconds = unique_sleep(9)
    results = []
    thread = threading.Thread(
        target=lambda: results.append(run(f"sleep {seconds}", max_real_seconds=20))
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not runner._running_process_groups and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner._running_process_groups

    handler_returned = threading.Event()
    watchdog_fired = threading.Event()

    def on_sigint(_signum, _frame):
        kill_all_running()
        handler_returned.set()

    def watchdog():
        # if the handler deadlocks on the lock, release it so the test fails
        # instead of hanging the whole suite
        if not handler_returned.wait(3):
            watchdog_fired.set()
            runner._running_process_groups_lock.release()

    previous = signal.signal(signal.SIGINT, on_sigint)
    runner._running_process_groups_lock.acquire()
    try:
        threading.Thread(target=watchdog, daemon=True).start()
        signal.raise_signal(signal.SIGINT)
    finally:
        signal.signal(signal.SIGINT, previous)
        if not watchdog_fired.is_set():
            runner._running_process_groups_lock.release()
    assert handler_returned.is_set(), "handler did not run synchronously"
    assert not watchdog_fired.is_set(), "kill_all_running() blocked on the lock"

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert results == [(b"", b"", -signal.SIGKILL)]
    assert not runner._running_process_groups
    assert not sleep_survivors(seconds)


# ---------------------------------------------------------------- sandbox


class FakeSandbox:
    """Implements the sandbox interface run() codes against.

    preexec() runs in the child: it records that it ran by creating a marker
    file, and can optionally report failure the way a real sandbox does, by
    writing a message to a pipe the parent reads and exiting 125/127.
    """

    def __init__(self, marker_path, exit_status=None, message=None):
        self.marker_path = marker_path
        self.exit_status = exit_status
        self.message = message
        self.read_fd, self.write_fd = os.pipe()

    def preexec(self):
        fd = os.open(self.marker_path, os.O_WRONLY | os.O_CREAT, 0o644)
        os.write(fd, b"preexec ran\n")
        os.close(fd)
        if self.exit_status is not None:
            os.write(self.write_fd, self.message.encode("utf-8"))
            os._exit(self.exit_status)

    def pass_fds(self):
        return (self.write_fd,)

    def error(self):
        os.close(self.write_fd)
        chunks = []
        while True:
            chunk = os.read(self.read_fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
        os.close(self.read_fd)
        text = b"".join(chunks).decode("utf-8")
        return text or None


def test_sandbox_preexec_runs_in_child_before_command(tmp_path):
    marker = tmp_path / "marker"
    sandbox = FakeSandbox(str(marker))
    result = run(["cat", str(marker)], sandbox=sandbox)
    assert result == (b"preexec ran\n", b"", 0)
    assert marker.read_text() == "preexec ran\n"


def test_sandbox_failure_raises_with_message(tmp_path):
    sandbox = FakeSandbox(str(tmp_path / "marker"), 125, "sandbox: unshare failed")
    expected_exception: type[Exception]
    try:
        from sandbox import SandboxError

        expected_exception = SandboxError
    except ImportError:
        from util import InternalError

        expected_exception = InternalError
    with pytest.raises(expected_exception, match="unshare failed"):
        run(["true"], sandbox=sandbox)


def test_exception_escaping_preexec_is_an_internal_error(tmp_path):
    from util import InternalError

    class BrokenSandbox(FakeSandbox):
        def preexec(self):
            raise RuntimeError("broken")

    with pytest.raises(InternalError, match="could not run"):
        run(["true"], sandbox=BrokenSandbox(str(tmp_path / "marker")))


def test_real_time_limit_is_reported_on_stderr_when_debugging(capsys):
    _stdout, _stderr, returncode = run("sleep 30", max_real_seconds=1, debug=2)
    assert returncode == -signal.SIGKILL
    assert "real time limit exceeded" in capsys.readouterr().err


def test_sandbox_command_not_found_returns_status_2(tmp_path):
    sandbox = FakeSandbox(
        str(tmp_path / "marker"), 127, "No such file or directory: 'prog'"
    )
    result = run(["prog"], sandbox=sandbox)
    assert result == (b"", b"No such file or directory: 'prog'\n", 2)


def test_sandbox_flag_from_test_parameters_is_ignored():
    # tests.txt's sandbox=1 parameter is a flag, not a sandbox object
    assert run("echo ok", sandbox=True) == (b"ok\n", b"", 0)
    assert run("echo ok", sandbox="auto") == (b"ok\n", b"", 0)
