#!/usr/bin/python3
"""Run a command with resource limits, output limits and a wall-clock limit.

This is the equivalent of subprocess.run() for running a student's program:
the program is untrusted and may loop forever, fork bomb, fill the disk or
print without end, so every one of those is bounded here and the result is
always a plain (stdout, stderr, returncode) tuple that the caller can compare
against the test's expectations.

The process is started in its own session so that everything it spawns shares
a process group we can kill as a unit: killing only the direct child leaves
its grandchildren (``sh -c "sleep 30 & wait"``) running and holding the pipes.
Output is gathered with a selectors loop on the calling thread, so there are
no event loops or timer threads and any number of threads may call run() at
once; the only shared state is a lock-guarded registry of running process
groups so a SIGINT handler can kill them all.
"""

from __future__ import annotations

import os
import re
import resource
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from typing import IO, Protocol, cast

# Process groups of commands currently running, so kill_all_running() can
# stop every student program when autotest is interrupted.
_running_process_groups: set[int] = set()
_running_process_groups_lock = threading.Lock()

# Set by stop_all(): once autotest is being interrupted no further command
# may start, or a test started after the running ones were killed would
# outlive the interrupt handler's os._exit().
_stopped = threading.Event()

# Longest single wait for output.  select() rejects a timeout beyond the
# platform's time_t and a test specification may set max_real_seconds to
# 1000000000 to mean "unlimited"; the loop re-computes the remaining time.
_MAX_SELECT_SECONDS = 86400.0

# Exit statuses a sandbox uses to report its own failure (see the sandbox
# interface described in run()).  These match the shell's conventions so a
# sandbox implemented with exec'd helpers reports the same numbers.
_SANDBOX_FAILED_STATUS = 125
_COMMAND_NOT_FOUND_STATUS = 127

_READ_CHUNK_BYTES = 65536


class SandboxLike(Protocol):
    """What run() needs from a sandbox (see run()); sandbox.Sandbox is one."""

    def preexec(self) -> None: ...

    def pass_fds(self) -> Sequence[int]: ...

    def error(self) -> str | None: ...


def run(  # noqa: C901, PLR0912 - the argument checks, Popen and the result translation in one place, as subprocess.run itself is
    command: str | Sequence[object],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    stdin: str | bytes | None = None,
    unicode_stdin: bool = True,  # noqa: ARG001 - says what stdin holds; the encoding does not depend on it
    sandbox: object = None,
    max_real_seconds: int | None = 0,
    max_cpu_seconds: int | None = 60,
    max_core_size: int | None = 0,
    max_stack_bytes: int | None = 32000000,
    max_rss_bytes: int | None = 100000000,
    max_file_size_bytes: int | None = 8192000,
    max_processes: int | None = 4096,
    max_open_files: int | None = 256,
    max_stdout_bytes: int | None = 1000000,
    max_stderr_bytes: int | None = 10000,
    nice: int = 0,
    debug: int = 0,
    **_ignored_parameters: object,
) -> tuple[bytes, bytes, int]:
    """Run command and return (stdout, stderr, returncode).

    command is a string run by bash (or /bin/sh) or a list executed directly.
    stdin is text (encoded as UTF-8, because stdout is decoded as UTF-8 by the
    caller and the two must agree) or bytes; unicode_stdin says which.

    A limit of 0 or None means the limit is not applied, except max_core_size
    where 0 is the useful value (no core files).  Limits are applied with
    setrlimit() in the child; RLIMIT_DATA and RLIMIT_AS are deliberately not
    set because they break the sanitizers used to check student programs.

    sandbox, if given, is an object with preexec() (called first in the child,
    before limits are set; it may fork so that only a grandchild returns),
    pass_fds() (file descriptors the child must keep) and error() (a message
    read once the child has exited, or None).  If the sandbox itself failed
    (status 125) a SandboxError is raised; if the command could not be found
    inside it (status 127) the result mirrors the OSError case below.

    If the command cannot be started, returns (b"", error message, 2) so the
    caller reports it like any other failed test rather than crashing.

    returncode follows Popen: negative signal number when killed by a signal,
    so a process we killed reports -9.  It is never None.

    Extra keyword parameters are ignored so callers may pass a whole test's
    parameter dictionary.
    """
    # tests.txt has a plain "sandbox" parameter (True/False/"auto" for the
    # whole-run sandbox) which arrives here via run(**parameters); only an
    # object implementing the interface above is a sandbox to us.
    active_sandbox: SandboxLike | None = None
    if sandbox is not None and hasattr(sandbox, "preexec"):
        active_sandbox = cast(SandboxLike, sandbox)

    if _stopped.is_set():
        from util import AutotestException

        raise AutotestException("autotest interrupted")

    argv = _argv_for(command)
    limits = _Limits(
        max_cpu_seconds=max_cpu_seconds,
        max_core_size=max_core_size,
        max_stack_bytes=max_stack_bytes,
        max_rss_bytes=max_rss_bytes,
        max_file_size_bytes=max_file_size_bytes,
        max_processes=max_processes,
        max_open_files=max_open_files,
        nice=nice,
    )

    def prepare_child() -> (
        None
    ):  # runs in the forked child before exec: test_sandbox_preexec_runs_in_child_before_command
        # Runs in the child between fork and exec.  The sandbox goes first so
        # the limits apply inside it (and to the grandchild it may fork).
        if active_sandbox is not None:
            active_sandbox.preexec()
        limits.apply()

    if debug > 1:
        print("run", argv, file=sys.stderr)

    stdin_file = _stdin_file(stdin)
    try:
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=stdin_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=prepare_child,
                start_new_session=True,
                pass_fds=active_sandbox.pass_fds() if active_sandbox else (),
            )
        except OSError as e:
            # e.g. executable not found; strip the "[Errno 2] " prefix.  The
            # message ends with a newline, as the one a shell prints for a
            # command it can not find does, so whatever is printed next
            # starts on its own line.
            message = re.sub(r"^\[.*?\] *", "", str(e))
            if debug > 1:
                print("run failed:", message, file=sys.stderr)
            return (b"", (message + "\n").encode("UTF-8"), 2)
        except subprocess.SubprocessError as e:
            # an exception escaped prepare_child() in the child
            from util import InternalError

            raise InternalError(f"could not run {argv}: {e}") from e

        output = _supervise(
            process,
            max_real_seconds,
            max_stdout_bytes,
            max_stderr_bytes,
            debug,
        )
    finally:
        if not isinstance(stdin_file, int):  # i.e. not subprocess.DEVNULL
            stdin_file.close()

    returncode = process.returncode
    if not output.real_time_exceeded:
        if returncode == -signal.SIGXCPU:
            output.stderr += (
                f"Error: CPU limit of {max_cpu_seconds} seconds exceeded\n".encode()
            )
        elif returncode == -signal.SIGXFSZ:
            output.stderr += f"Error: maximum file creation size of {max_file_size_bytes} bytes exceeded\n".encode()

    if active_sandbox is not None:
        failure = active_sandbox.error()
        if failure and returncode == _SANDBOX_FAILED_STATUS:
            raise _sandbox_error_class()(failure)
        if failure and returncode == _COMMAND_NOT_FOUND_STATUS:
            return (b"", (failure + "\n").encode("UTF-8"), 2)

    result = (bytes(output.stdout), bytes(output.stderr), returncode)
    if debug > 1:
        print("run returned", result, file=sys.stderr)
    return result


def kill_all_running() -> None:
    """SIGKILL every process group started by run() that has not finished.

    For a SIGINT handler: the student's program (and anything it forked)
    should not outlive an interrupted autotest.

    A signal handler runs on the main thread, which may be inside run() and
    already holding the (non-reentrant) registry lock, so this must never
    block on it.  The snapshot is taken without the lock if it cannot be
    acquired immediately: list(set) is a single call under the GIL, so it
    cannot see a half-updated set, and the lock only serialises writers.
    """
    acquired = _running_process_groups_lock.acquire(blocking=False)
    try:
        process_groups = list(_running_process_groups)
    finally:
        if acquired:
            _running_process_groups_lock.release()
    for pgid in process_groups:
        _kill_process_group(pgid)


def stop_all() -> None:
    """For the SIGINT handler: refuse to start any more commands, then kill
    those running.

    Killing the running commands lets their tests finish, and with tests
    running concurrently the pending tests would then start (creating
    directories and processes) while the handler is removing the temporary
    tree; the flag stops run() first so nothing new can appear.
    """
    _stopped.set()
    kill_all_running()


def stopped() -> bool:
    """True once stop_all() has been called: no more commands may start."""
    return _stopped.is_set()


class _Limits:
    """The resource limits to apply in the child before exec."""

    def __init__(
        self,
        *,
        max_cpu_seconds: int | None,
        max_core_size: int | None,
        max_stack_bytes: int | None,
        max_rss_bytes: int | None,
        max_file_size_bytes: int | None,
        max_processes: int | None,
        max_open_files: int | None,
        nice: int,
    ) -> None:
        self.max_cpu_seconds = max_cpu_seconds
        self.max_core_size = max_core_size
        self.max_stack_bytes = max_stack_bytes
        self.max_rss_bytes = max_rss_bytes
        self.max_file_size_bytes = max_file_size_bytes
        self.max_processes = max_processes
        self.max_open_files = max_open_files
        self.nice = nice

    def apply(
        self,
    ) -> (
        None
    ):  # runs in the forked child before exec: test_cpu_limit_kills_with_sigxcpu_and_message
        """Set the limits on the calling (child) process.

        Only simple system calls are made here because this runs after fork
        in a possibly multi-threaded parent, where taking a lock could
        deadlock the child.
        """
        # core file size: 0 is the useful value, so it is always applied
        if self.max_core_size is not None:
            _set_rlimit(resource.RLIMIT_CORE, self.max_core_size)

        # soft limit below the hard limit so the process gets SIGXCPU
        # (and a message) rather than a bare SIGKILL
        if self.max_cpu_seconds:
            _set_rlimit(resource.RLIMIT_CPU, self.max_cpu_seconds)

        if self.max_file_size_bytes:
            _set_rlimit(resource.RLIMIT_FSIZE, self.max_file_size_bytes)

        if self.max_stack_bytes:
            _set_rlimit(resource.RLIMIT_STACK, self.max_stack_bytes)

        if self.max_rss_bytes:
            _set_rlimit(resource.RLIMIT_RSS, self.max_rss_bytes)

        # note this is the user's total number of processes, not the child's
        if self.max_processes:
            _set_rlimit(resource.RLIMIT_NPROC, self.max_processes)

        if self.max_open_files:
            _set_rlimit(resource.RLIMIT_NOFILE, self.max_open_files + 1)

        if self.nice:
            os.nice(self.nice)


def _set_rlimit(  # runs in the forked child before exec: test_file_size_limit_gives_sigxfsz_and_message
    which: int, limit: int
) -> None:
    """setrlimit(which, (limit, limit + 1)), ignoring ValueError.

    The soft limit is what the process hits (SIGXCPU/SIGXFSZ/EMFILE) and the
    hard limit one above it lets the process raise its soft limit a little
    or, for CPU, be sent SIGXCPU before the SIGKILL of the hard limit.
    ValueError means a lower hard limit is already in force (from the login
    session or an outer sandbox): the stricter limit stands.
    """
    try:
        resource.setrlimit(which, (int(limit), int(limit) + 1))
    except ValueError:
        pass


def _argv_for(command: str | Sequence[object]) -> list[str]:
    """A string is run by a shell; a list is the argument vector itself."""
    if isinstance(command, str):
        return [shutil.which("bash") or "/bin/sh", "-c", command]
    return [str(argument) for argument in command]


def _stdin_file(stdin: str | bytes | None) -> int | IO[bytes]:
    """Return the file to attach to the child's stdin.

    A temporary file rather than a pipe: the child may never read its input,
    or read it after producing more output than we buffer, and a pipe writer
    in the parent would then need its own thread to avoid a deadlock.
    """
    if not stdin:
        return subprocess.DEVNULL
    stdin_file = tempfile.TemporaryFile()  # noqa: SIM115 - run() closes it
    if isinstance(stdin, str):
        # text, even when unicode_stdin is False: the flag records how the
        # test specified its input and str can only be written encoded
        stdin_file.write(stdin.encode("UTF-8"))
    else:
        # bytes arrive when unicode_stdin is False and are written as given
        stdin_file.write(stdin)
    stdin_file.seek(0)
    return stdin_file


class _Output:
    """What run() collected from the child, and why collection stopped."""

    def __init__(self) -> None:
        self.stdout = bytearray()
        self.stderr = bytearray()
        # set when the wall-clock limit killed the process group; the
        # CPU/file-size messages are then not also reported, as before
        self.real_time_exceeded = False
        # set once the process group has been killed by us for any reason
        self.killed = False


def _pipes(process: subprocess.Popen[bytes]) -> tuple[IO[bytes], IO[bytes]]:
    """The child's stdout and stderr pipes.

    run() gives Popen PIPE for both, so neither is None; the casts record
    that for the type checker without adding a runtime check.
    """
    return cast(IO[bytes], process.stdout), cast(IO[bytes], process.stderr)


def _supervise(
    process: subprocess.Popen[bytes],
    max_real_seconds: int | None,
    max_stdout_bytes: int | None,
    max_stderr_bytes: int | None,
    debug: int,
) -> _Output:
    """Collect the child's output, enforce the wall-clock limit and reap it.

    The child's process group is registered for kill_all_running() while it
    runs, and is killed on exit either way so nothing the child forked
    survives it.  On return process.returncode is set.
    """
    deadline = time.monotonic() + max_real_seconds if max_real_seconds else None
    pgid = process.pid  # start_new_session made the child a group leader
    with _running_process_groups_lock:
        _running_process_groups.add(pgid)
    output = _Output()
    try:
        _collect_output(
            process,
            pgid,
            output,
            deadline,
            max_real_seconds,
            max_stdout_bytes,
            max_stderr_bytes,
        )
        # Both pipes are closed but the child may still be running (it can
        # close its own output), so the wall clock applies to its exit too.
        if not output.killed and not _wait_without_reaping(process.pid, deadline):
            _real_time_exceeded(output, pgid, max_real_seconds)
        if output.real_time_exceeded and debug > 1:
            print("real time limit exceeded", file=sys.stderr)
    finally:
        # The child is a zombie or being killed at this point, so its pid
        # (and hence the group id) cannot yet have been recycled and killing
        # the group only reaches processes it started.  It is unregistered
        # before wait() reaps it: once reaped the pid is free for reuse and
        # kill_all_running() must not be able to kill its new owner.
        _kill_process_group(pgid)
        with _running_process_groups_lock:
            _running_process_groups.discard(pgid)
        process.wait()
        for pipe in _pipes(process):
            pipe.close()
    return output


def _collect_output(
    process: subprocess.Popen[bytes],
    pgid: int,
    output: _Output,
    deadline: float | None,
    max_real_seconds: int | None,
    max_stdout_bytes: int | None,
    max_stderr_bytes: int | None,
) -> None:
    """Read stdout and stderr until both close, a stream overflows or the
    wall clock runs out.

    Reading stops as soon as the group is killed: a process that has
    escaped the group could otherwise hold the pipe open forever.
    """
    stdout, stderr = _pipes(process)
    streams = {
        stdout.fileno(): (output.stdout, max_stdout_bytes, True),
        stderr.fileno(): (output.stderr, max_stderr_bytes, False),
    }
    selector = selectors.DefaultSelector()
    for fd in streams:
        selector.register(fd, selectors.EVENT_READ)
    try:
        while selector.get_map():
            timeout = None
            if deadline is not None:
                timeout = min(
                    max(0.0, deadline - time.monotonic()), _MAX_SELECT_SECONDS
                )
            for key, _events in selector.select(timeout):
                data = os.read(key.fd, _READ_CHUNK_BYTES)
                if not data:
                    selector.unregister(key.fd)
                    continue
                buffer, limit, is_stdout = streams[key.fd]
                if _append_limited(buffer, data, limit):
                    continue
                if is_stdout:
                    # only stdout overflow gets a message, as it always has:
                    # stderr is truncated silently so that the message does
                    # not itself push a student's error output over its limit
                    output.stderr += _too_much_output_error(limit)
                output.killed = True
                _kill_process_group(pgid)
                return
            if deadline is not None and time.monotonic() >= deadline:
                _real_time_exceeded(output, pgid, max_real_seconds)
                return
    finally:
        selector.close()


def _append_limited(buffer: bytearray, data: bytes, limit: int | None) -> bool:
    """Append data to buffer keeping it within limit (None/0 is unlimited).
    Returns False if data did not fit, after appending what did."""
    if not limit:
        buffer += data
        return True
    room = max(0, limit - len(buffer))
    buffer += data[:room]
    return len(data) <= room


def _wait_without_reaping(pid: int, deadline: float | None) -> bool:
    """Wait for the child to exit, until deadline (None waits forever).

    The zombie is left unreaped so its pid, and so its process-group id,
    stays reserved while the rest of the group is killed.  Returns False if
    the deadline passed first.
    """
    delay = 0.0005
    while True:
        if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None:
            return True
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(delay)
        delay = min(delay * 2, 0.05)


def _kill_process_group(pgid: int) -> None:
    """SIGKILL a process group; it may already be gone."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _real_time_exceeded(
    output: _Output, pgid: int, max_real_seconds: int | None
) -> None:
    """Record that the wall-clock limit was hit and kill the group."""
    output.stderr += (
        f"Error: real time limit of {max_real_seconds} seconds exceeded\n".encode()
    )
    output.real_time_exceeded = True
    output.killed = True
    _kill_process_group(pgid)


def _too_much_output_error(limit: int | None) -> bytes:
    """The stdout-overflow message run_test.py keys on; byte-exact as before."""
    return (
        f"\nError too much output - maximum stdout bytes of {limit} exceeded.".encode()
    )


def _sandbox_error_class() -> type[Exception]:
    """The exception for a sandbox that could not be set up.

    sandbox.py defines it; if that module is missing (this file is also used
    stand-alone) an InternalError is the closest thing.
    """
    try:
        from sandbox import SandboxError
    except ImportError:
        from util import InternalError

        return InternalError
    return SandboxError


if __name__ == "__main__":
    print(run(sys.argv[1:], max_cpu_seconds=10, max_real_seconds=30, debug=0))
