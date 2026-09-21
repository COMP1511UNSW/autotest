"""Tests for sandbox.py: run real commands through the real machinery.

The whole module is skipped when this host cannot build a sandbox, through
conftest's needs_sandbox marker so that the host is probed once, with one
configuration, for the whole suite.  The parent-side code (mount
validation, config_from_parameters, the seccomp filter) is tested without
a sandbox in test_sandbox_unit.py.
"""

import errno
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from conftest import unique_sleep

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sandbox
import sandbox_seccomp

pytestmark = pytest.mark.needs_sandbox

PATH = "/usr/local/bin:/usr/bin:/bin"
PYTHON_INSIDE = "/usr/bin/python3"


def _work_and_root(parent=None):
    """A work directory and an empty root directory, both on the host."""
    base = tempfile.mkdtemp(prefix="autotest_sandbox_test_", dir=parent)
    work = os.path.join(base, "work")
    root = os.path.join(base, "root")
    os.mkdir(work)
    os.mkdir(root)
    yield work, root
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def dirs():
    yield from _work_and_root()


@pytest.fixture
def outside_home_dirs():
    """dirs, but certainly not under $HOME (TMPDIR may be)."""
    yield from _work_and_root("/tmp")


def run_sandboxed(
    cmd,
    work,
    root,
    *,
    config=None,
    executable_check=None,
    after=None,
    timeout=30,
    stdin=subprocess.DEVNULL,
):
    """Run cmd through Sandbox exactly as the subprocess runner will.

    after, if given, is called in the child after sandbox.preexec() so that
    tests can compose a second preexec step (resource limits).
    Returns (stdout, stderr, returncode, error_message, notes).
    """
    sb = sandbox.Sandbox(
        config or sandbox.SandboxConfig(),
        work,
        root,
        executable_check=executable_check,
        env={"PATH": PATH},
    )

    def preexec():
        sb.preexec()
        if after is not None:
            after()

    p = subprocess.Popen(
        cmd,
        preexec_fn=preexec,
        start_new_session=True,
        pass_fds=sb.pass_fds(),
        cwd=work,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": PATH, "HOME": work},
    )
    try:
        stdout, stderr = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        stdout, stderr = p.communicate()
    return stdout, stderr, p.returncode, sb.error(), sb.notes


def sh(script, work, root, **kw):
    return run_sandboxed(["sh", "-c", script], work, root, **kw)


def test_stdout_captured_and_exit_status(dirs):
    out, err, rc, error, _ = sh("echo hello; echo oops >&2; exit 3", *dirs)
    assert out == b"hello\n"
    assert err == b"oops\n"
    assert rc == 3
    assert error is None


def test_signal_death_propagates(dirs):
    _, _, rc, error, _ = sh("kill -SEGV $$", *dirs)
    assert rc == -signal.SIGSEGV
    assert error is None


def test_shell_reporting_a_child_signal_is_not_translated(dirs):
    # sh reports its child's death as 128+11; that must stay an exit status,
    # as it is when the command is run unsandboxed
    _, _, rc, _, _ = sh("sh -c 'kill -SEGV $$'; exit $?", *dirs)
    assert rc == 139


def test_cpu_limit_kills_with_sigxcpu(dirs):
    # RLIMIT_CPU set by a second preexec step, as the subprocess runner does;
    # this only works because the program is not PID 1 of the namespace
    def limit():
        resource.setrlimit(resource.RLIMIT_CPU, (1, 2))

    start = time.monotonic()
    _, _, rc, _, _ = sh("while :; do :; done", *dirs, after=limit, timeout=20)
    assert rc == -signal.SIGXCPU
    assert time.monotonic() - start < 10


def test_home_and_host_filesystem_hidden(outside_home_dirs):
    # the dirs fixture honours TMPDIR, which on a teaching machine may be
    # under $HOME; this test is about $HOME being invisible, so it uses
    # directories which are certainly not in it rather than skipping
    work, root = outside_home_dirs
    home = os.path.expanduser("~")
    out, _, rc, _, _ = sh(
        f"test -e '{home}' && echo home_visible; "
        "ls -A /home 2>/dev/null | wc -l; "
        f"test -e '{root}' && echo root_visible; true",
        work,
        root,
    )
    assert rc == 0
    assert b"home_visible" not in out
    assert b"root_visible" not in out
    assert out.strip() == b"0"


def test_system_directories_read_only(dirs):
    for path in ("/usr/autotest_probe", "/etc/autotest_probe"):
        _, err, rc, _, _ = sh(f"echo x > {path}", *dirs)
        assert rc != 0
        assert b"Read-only file system" in err
        assert not os.path.exists(path)


def test_work_dir_and_private_tmp_writable(dirs):
    work, root = dirs
    name = f"autotest_sandbox_{os.getpid()}_{time.time_ns()}"
    out, _, rc, _, _ = sh(
        f"echo inside > out.txt && echo t > /tmp/{name} && cat /tmp/{name}", work, root
    )
    assert rc == 0
    assert out == b"t\n"
    with open(os.path.join(work, "out.txt")) as f:
        assert f.read() == "inside\n"
    assert not os.path.exists(f"/tmp/{name}")
    assert os.listdir(root) == []


def test_devices(dirs):
    out, _, rc, _, _ = sh(
        "echo x > /dev/null && head -c 16 /dev/urandom | wc -c", *dirs
    )
    assert rc == 0
    assert out.strip() == b"16"


def test_proc_shows_only_the_sandbox(dirs):
    out, _, rc, _, _ = sh("ls /proc | grep -c '^[0-9]'", *dirs)
    assert rc == 0
    assert int(out) <= 5


def test_uid_is_the_invoking_user(dirs):
    out, _, rc, _, _ = run_sandboxed(["id", "-u"], *dirs)
    assert rc == 0
    assert int(out) == os.getuid()


@pytest.mark.skipif(not os.path.exists(PYTHON_INSIDE), reason="no /usr/bin/python3")
def test_network_isolated_but_loopback_works(dirs):
    script = """
import socket, errno
s = socket.socket()
s.settimeout(5)
try:
    s.connect(("1.1.1.1", 53))
    print("connected")
except OSError as e:
    print("errno", e.errno)
l = socket.socket()
l.bind(("127.0.0.1", 0))
l.listen(1)
c = socket.socket()
c.connect(l.getsockname())
a, _ = l.accept()
c.send(b"x")
print("loopback", a.recv(1).decode())
"""
    out, err, rc, _, _ = run_sandboxed([PYTHON_INSIDE, "-c", script], *dirs)
    assert rc == 0, err
    lines = out.decode().splitlines()
    assert lines[0].startswith("errno ")
    assert int(lines[0].split()[1]) in (
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
        errno.EACCES,
    )
    assert lines[1] == "loopback x"


@pytest.mark.skipif(
    not sandbox_seccomp.SUPPORTED, reason="seccomp filter is x86_64 only"
)
def test_every_privilege_is_dropped_before_the_command_runs(dirs):
    """The command must hold no capability and be unable to gain one.

    Emptying the bounding set is what makes execve of a setuid binary or a
    file with capabilities powerless (README "Security model"), and
    no_new_privs is what makes that permanent for the whole process tree.
    Neither has any other visible effect, so without this test both can be
    deleted with the rest of the suite still green.
    """
    out, err, rc, _, _ = sh(
        "grep -E '^(NoNewPrivs|Cap[A-Za-z]+):' /proc/self/status", *dirs
    )
    assert rc == 0, err
    status = dict(
        line.split(":", 1)[0:2] for line in out.decode().splitlines() if ":" in line
    )
    assert status["NoNewPrivs"].strip() == "1", status
    for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        assert int(status[name], 16) == 0, (name, status)


def test_every_namespace_is_the_sandbox_s_own(dirs):
    """Nothing the command can reach may be shared with the host.

    The mount, pid and network namespaces have obvious tests of their
    effect; ipc, uts and cgroup do not, so dropping one of those flags is
    otherwise invisible although it puts the student's program back on the
    host's System V shared memory, semaphores and message queues.
    """
    names = ("user", "mnt", "pid", "ipc", "uts", "cgroup", "net")
    script = "".join(f"echo {n} $(readlink /proc/self/ns/{n});" for n in names)
    out, err, rc, _, _ = sh(script, *dirs)
    assert rc == 0, err
    inside = dict(line.split() for line in out.decode().splitlines())
    for name in names:
        outside = os.readlink(f"/proc/self/ns/{name}")
        assert inside[name] != outside, f"{name} namespace is the host's"


def test_nested_user_namespace_denied(dirs):
    _, err, rc, _, _ = run_sandboxed(["unshare", "-Ur", "true"], *dirs)
    assert rc != 0
    assert b"Operation not permitted" in err


def test_mount_denied(dirs):
    _, _, rc, _, _ = run_sandboxed(["mount", "-t", "tmpfs", "none", "/tmp"], *dirs)
    assert rc != 0


def test_missing_command_reports_127(dirs):
    _, _, rc, error, _ = run_sandboxed(
        ["no_such_program_xyz"], *dirs, executable_check="no_such_program_xyz"
    )
    assert rc == 127
    assert error == "No such file or directory: 'no_such_program_xyz'"


def _survivors(cmdline, wait=3.0):
    """Host processes whose command line is exactly cmdline, polled for up
    to wait seconds because a namespace is torn down asynchronously."""
    deadline = time.monotonic() + wait
    while True:
        p = subprocess.run(["pgrep", "-xf", cmdline], stdout=subprocess.PIPE)
        if not p.stdout or time.monotonic() > deadline:
            return p.stdout
        time.sleep(0.05)


def test_no_orphans_survive(dirs):
    seconds = unique_sleep(5)
    start = time.monotonic()
    _, _, rc, _, _ = sh(f"sleep {seconds} & exit 0", *dirs)
    assert rc == 0
    assert time.monotonic() - start < 10
    assert _survivors(f"sleep {seconds}") == b""


def test_killing_the_popen_child_kills_the_command(dirs):
    # SIGKILL to A alone (Popen.kill()): B must die with A, and B's death as
    # PID 1 takes the command with it
    work, root = dirs
    seconds = unique_sleep(6)
    sb = sandbox.Sandbox(sandbox.SandboxConfig(), work, root)
    p = subprocess.Popen(
        ["sh", "-c", f"sleep {seconds}"],
        preexec_fn=sb.preexec,
        start_new_session=True,
        pass_fds=sb.pass_fds(),
        cwd=work,
        stdin=subprocess.DEVNULL,
        env={"PATH": PATH},
    )
    assert _survivors(f"sleep {seconds}", wait=5.0) != b"", "it never started"
    p.kill()
    assert p.wait(timeout=10) == -signal.SIGKILL
    assert sb.error() is None
    assert _survivors(f"sleep {seconds}") == b""


def test_stdin_can_be_reopened(dirs):
    # the runner gives stdin as an unlinked host temporary file or /dev/null,
    # neither of which is beneath any sandbox path: Landlock must grant the
    # descriptor itself or /dev/stdin and /proc/self/fd/0 are EACCES
    with tempfile.TemporaryFile() as f:
        f.write(b"input\n")
        f.seek(0)
        out, err, rc, _, _ = sh(
            "cat /dev/stdin; cat /proc/self/fd/0; cat < /dev/stdin", *dirs, stdin=f
        )
    assert rc == 0, err
    # each re-open is a fresh file description, so each cat starts at 0
    assert out == b"input\n" * 3
    out, err, rc, _, _ = sh(
        "cat /dev/stdin && echo ok", *dirs, stdin=subprocess.DEVNULL
    )
    assert rc == 0, err
    assert out == b"ok\n"


def test_own_proc_entries_writable_but_not_proc_sys(dirs):
    # glibc's pthread_setname_np on another thread writes /proc/self/task/
    # <tid>/comm, so a program's own procfs files must be writable under
    # Landlock, while procfs's ownership checks still guard /proc/sys
    out, err, rc, _, _ = sh("printf foo > /proc/self/comm && cat /proc/$$/comm", *dirs)
    assert rc == 0, err
    assert out == b"foo\n"
    _, err, rc, _, _ = sh("echo 1 > /proc/sys/kernel/sysrq", *dirs)
    assert rc != 0
    assert b"Permission denied" in err


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not installed")
def test_naming_another_thread(dirs):
    work, root = dirs
    source = os.path.join(work, "setname.c")
    with open(source, "w") as f:
        f.write("""
#define _GNU_SOURCE
#include <pthread.h>
#include <unistd.h>
static void *idle(void *p) { sleep(2); return p; }
int main(void) {
    pthread_t t;
    pthread_create(&t, NULL, idle, NULL);
    return pthread_setname_np(t, "other");
}
""")
    subprocess.run(
        ["gcc", "-o", "setname", "setname.c", "-lpthread"], cwd=work, check=True
    )
    _, err, rc, _, _ = run_sandboxed(["./setname"], work, root)
    assert rc == 0, err


def test_init_process_is_masked(dirs):
    # PID 1 inside is a forked autotest interpreter; its /proc entry would
    # show the student autotest's command line and the python path
    out, _, rc, _, _ = sh(
        "ls -A /proc/1 | wc -l; cat /proc/1/cmdline 2>/dev/null | wc -c; "
        "readlink /proc/1/exe; true",
        *dirs,
    )
    assert rc == 0
    assert out.split() == [b"0", b"0"]
    # and the mask is read-only, not merely unwritable by its mode
    _, err, _, _, _ = sh("echo x > /proc/1/zz", *dirs)
    assert b"Read-only file system" in err, err


def test_reaped_child_is_not_reported_as_success(dirs, monkeypatch):
    # if a supervisor's waitpid fails with ECHILD (SIGCHLD ignored, a
    # subreaper) the status is unknowable and must not become "exit 0"
    def reaped(pid):
        raise ChildProcessError(errno.ECHILD, "No child processes")

    monkeypatch.setattr(sandbox, "_waitpid", reaped)
    _, _, rc, error, _ = sh("exit 3", *dirs)
    assert rc == 125
    assert "ECHILD" in error


def test_pipe_has_reader():
    r, w = os.pipe()
    try:
        assert sandbox._pipe_has_reader(w)
        os.close(r)
        assert not sandbox._pipe_has_reader(w)
    finally:
        os.close(w)


def test_session_and_terminal_without_start_new_session(dirs):
    # the sandbox makes its own session even when the caller forgets, so
    # the command cannot get the invoking user's terminal through /dev/tty
    work, root = dirs
    sb = sandbox.Sandbox(sandbox.SandboxConfig(), work, root)
    p = subprocess.Popen(
        ["sh", "-c", "read line; : > /dev/tty"],
        preexec_fn=sb.preexec,
        pass_fds=sb.pass_fds(),
        cwd=work,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": PATH},
    )
    assert os.getsid(p.pid) == p.pid != os.getsid(0)
    _, err = p.communicate(b"go\n", timeout=30)
    assert sb.error() is None
    assert p.returncode != 0
    assert b"/dev/tty" in err


@pytest.mark.skipif(shutil.which("gdb") is None, reason="gdb not installed")
def test_gdb_can_run_a_program(dirs):
    _, err, rc, _, _ = run_sandboxed(
        ["gdb", "-batch", "-ex", "run", "--args", "/bin/true"], *dirs, timeout=60
    )
    assert rc == 0, err


def test_overhead_per_command(dirs):
    runs = 20
    start = time.monotonic()
    for _ in range(runs):
        _, _, rc, _, _ = run_sandboxed(["/bin/true"], *dirs)
        assert rc == 0
    average = (time.monotonic() - start) / runs
    load = os.getloadavg()[0]
    cpus = os.cpu_count() or 1
    print(
        f"\naverage sandboxed /bin/true: {average * 1000:.1f} ms (load {load:.0f} on {cpus} CPUs)"
    )
    if load > cpus:
        # every fork, unshare and mount is paying scheduling latency, which
        # says nothing about the sandbox; the number above is still reported
        pytest.skip(
            f"host overloaded (load {load:.0f}), measured {average * 1000:.1f} ms"
        )
    assert average < 0.1


def test_sandbox_failure_reports_125(dirs):
    # a mountpoint that cannot be created (under a read-only tree)
    config = sandbox.SandboxConfig(
        read_only_mounts=sandbox.DEFAULT_READ_ONLY_MOUNT_BASE
        + [("/etc/hostname", "/usr/autotest_injected")]
    )
    _, _, rc, error, _ = run_sandboxed(["/bin/true"], *dirs, config=config)
    assert rc == 125
    assert error.startswith("sandbox: ")
    assert "/usr/autotest_injected" in error


def test_constructor_checks_arguments(dirs):
    work, root = dirs
    with pytest.raises(sandbox.SandboxError):
        sandbox.Sandbox(
            sandbox.SandboxConfig(read_write_mounts=["/nonexistent"]), work, root
        )
    with pytest.raises(sandbox.SandboxError):
        sandbox.Sandbox(sandbox.SandboxConfig(), work, "relative/root")
    with pytest.raises(sandbox.SandboxError, match="is not a directory"):
        sandbox.Sandbox(sandbox.SandboxConfig(), os.path.join(work, "none"), root)
    with open(os.path.join(root, "x"), "w"):
        pass
    with pytest.raises(sandbox.SandboxError):
        sandbox.Sandbox(sandbox.SandboxConfig(), work, root)


def test_probe_is_cached(monkeypatch):
    config = sandbox.SandboxConfig()
    assert sandbox.probe(config) is None

    def explode(*args, **kwargs):
        raise AssertionError("probe should not build a sandbox again")

    monkeypatch.setattr(sandbox, "Sandbox", explode)
    assert sandbox.probe(config) is None


def test_notes_when_backstops_disabled(dirs):
    config = sandbox.SandboxConfig(seccomp=False, landlock=False)
    _, _, rc, _, notes = run_sandboxed(["/bin/true"], *dirs, config=config)
    assert rc == 0
    assert any("seccomp" in note for note in notes)
    assert any("Landlock" in note for note in notes)


def test_host_network_when_disabled(dirs):
    config = sandbox.SandboxConfig(network=False)
    out, _, rc, _, _ = sh("cat /proc/net/dev | wc -l", *dirs, config=config)
    assert rc == 0
    # the host has more interfaces than a lone loopback (2 header lines + lo)
    assert int(out) > 3


def test_unix_sockets_in_work_dir(dirs):
    # Landlock must grant MAKE_SOCK in the work directory
    work, root = dirs
    if not os.path.exists(PYTHON_INSIDE):
        pytest.skip("no /usr/bin/python3")
    script = (
        "import socket; s = socket.socket(socket.AF_UNIX); s.bind('sock'); print('ok')"
    )
    out, err, rc, _, _ = run_sandboxed([PYTHON_INSIDE, "-c", script], work, root)
    assert rc == 0, err
    assert out == b"ok\n"
    assert os.path.exists(os.path.join(work, "sock"))


def test_error_never_blocks_and_close_is_idempotent(dirs):
    work, root = dirs
    sb = sandbox.Sandbox(sandbox.SandboxConfig(), work, root)
    start = time.monotonic()
    assert sb.error() is None
    assert time.monotonic() - start < 5
    sb.close()
    sb.close()
    assert sb.error() is None
