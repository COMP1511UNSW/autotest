"""A per-command sandbox for running a student's untrusted program.

autotest runs unprivileged (as the student, or as a marker account), so the
sandbox is built from what an unprivileged process is allowed to do on Linux:
a user namespace that maps the invoking uid to the same number (so the program
does not appear to be root), and inside it mount, PID, IPC, UTS, cgroup and
(optionally) network namespaces.  In the mount namespace a private root is
assembled on a tmpfs from read-only binds of the system directories, a fresh
/proc, a minimal /dev, a private /tmp and a read-write bind of the test
directory; pivot_root then detaches the host's root so nothing outside the
test directory can be reached by name.  Landlock and a seccomp filter are
applied as backstops where the kernel supports them.

The process tree for one command is

    autotest --Popen--> A --fork--> B --fork--> C --exec--> the command

and the work is split like this because each process has one job:

* A is the Popen child.  It creates the namespaces (unshare only affects the
  PID namespace of *children*, so somebody must fork afterwards), writes the
  uid/gid maps, then waits for B and reproduces its exit status, including
  death by signal, so that Popen sees the command's own status.
* B is PID 1 of the new PID namespace and still holds full capabilities in
  the user namespace.  It builds the mount tree, pivots, brings loopback up,
  forks C and waits for it.  B exists so that the student's program is NOT
  PID 1: a PID-namespace init ignores every signal it has no handler for
  (SIGXCPU from RLIMIT_CPU included, which would break CPU-limit reporting),
  and when B exits the kernel kills everything left in the namespace, which
  is what guarantees no orphan processes.  B asks to be SIGKILLed when A
  dies, so that killing the Popen child alone (Popen.kill()) also takes the
  whole namespace with it.
* C drops the capability bounding set, sets no_new_privs, applies Landlock
  and seccomp, checks the executable exists and returns from preexec so that
  Popen execs the command in it.

Everything in preexec runs in a forked child of a possibly multi-threaded
parent, so it does no imports: all lookups, allocations and the seccomp
program are prepared in the constructor.  Failures are reported through a
pipe (written in the child, read by error() in the parent) and exit status
125 (sandbox could not be built) or 127 (executable not found), following
the shell's conventions.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import platform
import resource
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from typing import Any, NoReturn

import sandbox_landlock
import sandbox_seccomp
from util import AutotestException

__all__ = [
    "Sandbox",
    "SandboxConfig",
    "SandboxError",
    "SandboxUnavailable",
    "config_from_parameters",
    "probe",
]


class SandboxError(AutotestException):
    """The sandbox could not be built or used."""


class SandboxUnavailable(SandboxError):  # noqa: N818 - public name (tests, README)
    """This host cannot create a sandbox at all."""


# clone(2) namespace flags
CLONE_NEWNS = 0x00020000
CLONE_NEWCGROUP = 0x02000000
CLONE_NEWUTS = 0x04000000
CLONE_NEWIPC = 0x08000000
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000
CLONE_NEWNET = 0x40000000

_CLONE_FLAG_NAMES = (
    (CLONE_NEWUSER, "CLONE_NEWUSER"),
    (CLONE_NEWNS, "CLONE_NEWNS"),
    (CLONE_NEWPID, "CLONE_NEWPID"),
    (CLONE_NEWIPC, "CLONE_NEWIPC"),
    (CLONE_NEWUTS, "CLONE_NEWUTS"),
    (CLONE_NEWCGROUP, "CLONE_NEWCGROUP"),
    (CLONE_NEWNET, "CLONE_NEWNET"),
)

# mount(2) flags
MS_RDONLY = 0x0001
MS_NOSUID = 0x0002
MS_NODEV = 0x0004
MS_NOEXEC = 0x0008
MS_SYNCHRONOUS = 0x0010
MS_REMOUNT = 0x0020
MS_MANDLOCK = 0x0040
MS_NOATIME = 0x0400
MS_NODIRATIME = 0x0800
MS_BIND = 0x1000
MS_REC = 0x4000
MS_PRIVATE = 1 << 18
MS_RELATIME = 1 << 21

# umount2(2) flags
MNT_DETACH = 0x0002

# mount_setattr(2) attributes (the low bits coincide with the MS_* values)
MOUNT_ATTR_RDONLY = 0x00000001
MOUNT_ATTR_NOSUID = 0x00000002
MOUNT_ATTR_NODEV = 0x00000004
MOUNT_ATTR_NOEXEC = 0x00000008
AT_FDCWD = -100
AT_RECURSIVE = 0x8000

# statvfs f_flag bits that must be preserved by an unprivileged remount
_ST_RELATIME = 4096
_ST_PRESERVED = (
    MS_RDONLY
    | MS_NOSUID
    | MS_NODEV
    | MS_NOEXEC
    | MS_SYNCHRONOUS
    | MS_MANDLOCK
    | MS_NOATIME
    | MS_NODIRATIME
)

# prctl(2) options
PR_SET_PDEATHSIG = 1
PR_CAPBSET_DROP = 24
PR_SET_NO_NEW_PRIVS = 38
PR_CAP_AMBIENT = 47
PR_CAP_AMBIENT_CLEAR_ALL = 4

# ioctl(2) on a socket, <linux/sockios.h> and <net/if.h>
SIOCGIFFLAGS = 0x8913
SIOCSIFFLAGS = 0x8914
IFF_UP = 0x1
_IFREQ_SIZE = 32
_IFREQ_FLAGS_OFFSET = 16

# Exit statuses, following the shell's conventions.
_SANDBOX_FAILED_STATUS = 125
_COMMAND_NOT_FOUND_STATUS = 127

# Device nodes provided in /dev, bound from the host's nodes: a tmpfs mounted
# in a user namespace cannot have working device nodes of its own.
_DEVICE_NODES = ("null", "zero", "full", "random", "urandom", "tty")
_DEVICE_LINKS = (
    ("fd", "/proc/self/fd"),
    ("stdin", "/proc/self/fd/0"),
    ("stdout", "/proc/self/fd/1"),
    ("stderr", "/proc/self/fd/2"),
    ("ptmx", "pts/ptmx"),
)

# Signals A and B forward to their child so that terminating the Popen child
# terminates the command rather than orphaning it.
_FORWARDED_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)

_OLD_ROOT = ".old_root"

# Paths inside the sandbox, not on the host: its private tmpfs mounts.
_SANDBOX_TMP = "/tmp"  # noqa: S108 - inside the sandbox  # nosec B108
_SANDBOX_SHM = "/dev/shm"  # noqa: S108 - inside the sandbox  # nosec B108

DEFAULT_READ_ONLY_MOUNT_BASE = [
    "/bin",
    "/etc",
    "/lib",
    "/lib32",
    "/lib64",
    "/libx32",
    "/opt",
    "/sbin",
    "/usr",
]
DEFAULT_TMP_BYTES = 268435456
DEFAULT_SHM_BYTES = 67108864

# Syscall numbers.  Most of these are the same on every architecture (the
# numbers from 424 up are allocated uniformly) but pivot_root is not, and a
# wrong number calls an unrelated syscall with arguments meant for another,
# so only architectures whose table has been checked against their kernel
# headers appear here.  aarch64 uses the asm-generic table.
_SYSCALL_NUMBERS = {
    "x86_64": {
        "pivot_root": 155,
        "pidfd_open": 434,
        "mount_setattr": 442,
    },
    "aarch64": {
        "pivot_root": 41,
        "pidfd_open": 434,
        "mount_setattr": 442,
    },
}
_MACHINE = platform.machine()
_SYS: dict[str, int] = _SYSCALL_NUMBERS.get(_MACHINE, {})


def _load_libc() -> ctypes.CDLL:
    """The C library, with errno capture, and the argument types pinned so
    that ctypes does no guessing in the forked child."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    libc.unshare.argtypes = (ctypes.c_int,)
    libc.mount.argtypes = (
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_ulong,
        ctypes.c_char_p,
    )
    libc.umount2.argtypes = (ctypes.c_char_p, ctypes.c_int)
    libc.prctl.argtypes = (
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    )
    return libc


_libc = _load_libc()


class _MountAttr(ctypes.Structure):
    """struct mount_attr as expected by mount_setattr(2)."""

    _fields_ = (
        ("attr_set", ctypes.c_uint64),
        ("attr_clr", ctypes.c_uint64),
        ("propagation", ctypes.c_uint64),
        ("userns_fd", ctypes.c_uint64),
    )


def _errno_text(  # runs only in the sandbox child: test_sandbox_failure_reports_125
    code: int,
) -> str:
    """e.g. "EPERM (Operation not permitted)", for messages a tutor will read."""
    return f"{errno.errorcode.get(code, code)} ({os.strerror(code)})"


def _os_error(  # runs only in the sandbox child: test_sandbox_failure_reports_125
    what: str,
) -> OSError:
    """An OSError for a failed libc call, from the captured errno."""
    code = ctypes.get_errno()
    return OSError(code, f"{what} failed: {_errno_text(code)}")


# The syscall wrappers below run in the forked child: no imports, no
# allocation beyond the argument buffers.


def _unshare(  # runs only in the sandbox child: test_uid_is_the_invoking_user
    flags: int,
) -> None:
    ctypes.set_errno(0)
    if _libc.unshare(flags) != 0:
        names = "|".join(name for bit, name in _CLONE_FLAG_NAMES if flags & bit)
        raise _os_error(f"unshare({names})")


def _mount(  # runs only in the sandbox child: test_system_directories_read_only
    source: str, target: str, fstype: str | None, flags: int, data: str | None
) -> None:
    ctypes.set_errno(0)
    result = _libc.mount(
        source.encode(),
        target.encode(),
        None if fstype is None else fstype.encode(),
        flags,
        None if data is None else data.encode(),
    )
    if result != 0:
        raise _os_error(f"mount of {source} on {target}")


def _umount2(  # runs only in the sandbox child: test_home_and_host_filesystem_hidden
    target: str, flags: int
) -> None:
    ctypes.set_errno(0)
    if _libc.umount2(target.encode(), flags) != 0:
        raise _os_error(f"umount of {target}")


def _syscall(  # runs only in the sandbox child: test_home_and_host_filesystem_hidden
    name: str, what: str, *args: int
) -> int:
    ctypes.set_errno(0)
    result = int(
        _libc.syscall(ctypes.c_long(_SYS[name]), *(ctypes.c_long(a) for a in args))
    )
    if result < 0:
        raise _os_error(what)
    return result


def _mount_setattr(  # runs only in the sandbox child: test_system_directories_read_only
    path: str, attrs: int, recursive: bool
) -> None:
    """Apply attributes to an existing mount atomically (no window in which
    a bind is writable or honours setuid, unlike a remount)."""
    attr = _MountAttr(attr_set=attrs, attr_clr=0, propagation=0, userns_fd=0)
    buffer = ctypes.create_string_buffer(path.encode())
    _syscall(
        "mount_setattr",
        f"mount_setattr({path})",
        AT_FDCWD,
        ctypes.addressof(buffer),
        AT_RECURSIVE if recursive else 0,
        ctypes.addressof(attr),
        ctypes.sizeof(attr),
    )


def _set_mount_attributes(  # runs only in the sandbox child: test_system_directories_read_only
    path: str, attrs: int, recursive: bool
) -> None:
    """Make a mount read-only/nosuid/nodev, falling back to the classic
    MS_REMOUNT|MS_BIND dance on kernels without mount_setattr (< 5.12).

    The fallback must repeat every flag the mount already has, because an
    unprivileged remount may add restrictions but not drop them, and it
    cannot recurse into submounts.
    """
    try:
        _mount_setattr(path, attrs, recursive)
    except OSError as e:
        if e.errno != errno.ENOSYS:
            raise
    else:
        return
    existing = os.statvfs(path).f_flag
    flags = existing & _ST_PRESERVED
    if existing & _ST_RELATIME:
        flags |= MS_RELATIME
    _mount("none", path, None, MS_REMOUNT | MS_BIND | flags | attrs, None)


def _pivot_root(  # runs only in the sandbox child: test_home_and_host_filesystem_hidden
    new_root: str, put_old: str
) -> None:
    new = ctypes.create_string_buffer(new_root.encode())
    old = ctypes.create_string_buffer(put_old.encode())
    _syscall("pivot_root", "pivot_root", ctypes.addressof(new), ctypes.addressof(old))


def _prctl(  # runs only in the sandbox child: test_mount_denied
    option: int, arg2: int = 0, arg3: int = 0, arg4: int = 0, arg5: int = 0
) -> int:
    ctypes.set_errno(0)
    result = int(_libc.prctl(option, arg2, arg3, arg4, arg5))
    if result < 0:
        raise _os_error(f"prctl({option})")
    return result


def _write_file(  # runs only in the sandbox child: test_uid_is_the_invoking_user
    path: str, text: str
) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CLOEXEC)
    try:
        os.write(fd, text.encode())
    finally:
        os.close(fd)


def _create_file(path: str) -> None:  # runs only in the sandbox child: test_devices
    """An empty file to bind-mount onto (a bind target must exist)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC, 0o644)
    os.close(fd)


def _makedirs(  # runs only in the sandbox child: test_system_directories_read_only
    path: str,
) -> None:
    os.makedirs(path, mode=0o755, exist_ok=True)


def _close_fds_except(  # runs only in the sandbox child: test_stdout_captured_and_exit_status
    keep: Iterable[int],
) -> None:
    """Close every fd above 2 except those in keep.

    A and B hold copies of Popen's exec-status pipe and of the command's
    output pipes; neither must keep them, or the parent would wait on them.
    """
    limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    if limit == resource.RLIM_INFINITY or limit > 1 << 20:
        limit = 1 << 20
    low = 3
    for fd in sorted(keep):
        if fd >= low:
            os.closerange(low, fd)
            low = fd + 1
    os.closerange(low, limit)


def _read_available(  # runs only in the sandbox child: test_signal_death_propagates
    fd: int,
) -> bytes:
    """Whatever is in a pipe right now, without blocking."""
    os.set_blocking(fd, False)
    try:
        return os.read(fd, 64)
    except OSError:
        return b""


def _pipe_has_reader(write_fd: int) -> bool:
    """False once every read end of the pipe has been closed.

    poll reports POLLERR on the write end of a readerless pipe, which makes
    a pipe whose read end only one process holds a liveness test for that
    process: no PID lookup, which B could not do anyway (A is outside B's
    PID namespace, so from B os.getppid() is 0).
    """
    poller = select.poll()
    poller.register(write_fd, select.POLLOUT)
    return not any(events & select.POLLERR for _fd, events in poller.poll(0))


def _waitpid(  # runs only in the sandbox child: test_reaped_child_is_not_reported_as_success
    pid: int,
) -> int:
    """Wait for pid and return its raw status (wrapped so tests can fake it)."""
    return os.waitpid(pid, 0)[1]


def _kill_quietly(  # runs only in the sandbox child: signal forwarding, cf. test_killing_the_popen_child_kills_the_command
    pid: int, sig: int
) -> None:
    try:
        os.kill(pid, sig)
    except OSError:
        pass


def _read_cap_last_cap() -> int:
    """The highest capability number, so the bounding set can be emptied.
    Dropping too many is harmless (EINVAL is ignored), so the fallback is
    generous."""
    try:
        with open("/proc/sys/kernel/cap_last_cap") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 63


def _normalise_mounts(
    entries: Iterable[object] | None, what: str
) -> list[tuple[str, str]]:
    """Turn a mount list into (host_path, sandbox_path) pairs.

    An entry is a host path (bound at the same path inside) or a
    (host_path, sandbox_path) pair.  Both must be absolute: a relative path
    would be resolved against a cwd that differs between the parent and the
    child, which is the kind of surprise a security boundary must not have.
    """
    pairs: list[tuple[str, str]] = []
    for entry in entries or []:
        if isinstance(entry, (list, tuple)):
            if len(entry) != 2:
                raise SandboxError(
                    f"{what}: expected (host_path, sandbox_path): {entry!r}"
                )
            host, inside = str(entry[0]), str(entry[1])
        else:
            host = inside = str(entry)
        if not host.startswith("/") or not inside.startswith("/"):
            raise SandboxError(f"{what}: paths must be absolute: {entry!r}")
        inside = os.path.normpath(inside)
        if inside == "/":
            raise SandboxError(f"{what}: cannot mount over the sandbox root: {entry!r}")
        pairs.append((host, inside))
    return pairs


class SandboxConfig:
    """What a sandbox looks like, independent of the command run in it.

    network: True gives an isolated network namespace with only loopback;
        False leaves the command on the host's network stack.
    read_only_mounts: host paths bound read-only, nosuid, nodev at the same
        path inside (or (host, inside) pairs); missing paths are skipped.
    read_write_mounts: likewise, read-write.  The work directory is not in
        this list: it is passed to Sandbox separately.
    tmp_bytes / shm_bytes: sizes of the private /tmp and /dev/shm tmpfs.
    seccomp / landlock: apply the backstops when the host supports them;
        when it does not the sandbox proceeds without them (see
        Sandbox.notes).
    """

    def __init__(
        self,
        network: bool = True,
        read_only_mounts: Iterable[object] | None = None,
        read_write_mounts: Iterable[object] | None = None,
        tmp_bytes: int = DEFAULT_TMP_BYTES,
        shm_bytes: int = DEFAULT_SHM_BYTES,
        seccomp: bool = True,
        landlock: bool = True,
    ) -> None:
        self.network = bool(network)
        self.read_only_mounts = (
            list(DEFAULT_READ_ONLY_MOUNT_BASE)
            if read_only_mounts is None
            else list(read_only_mounts)
        )
        self.read_write_mounts = list(read_write_mounts or [])
        self.tmp_bytes = int(tmp_bytes)
        self.shm_bytes = int(shm_bytes)
        self.seccomp = bool(seccomp)
        self.landlock = bool(landlock)

    def key(self) -> tuple[object, ...]:
        """A hashable identity, so probe() can cache per configuration."""
        return (
            self.network,
            tuple(_normalise_mounts(self.read_only_mounts, "sandbox_read_only_mount")),
            tuple(
                _normalise_mounts(self.read_write_mounts, "sandbox_read_write_mount")
            ),
            self.tmp_bytes,
            self.shm_bytes,
            self.seccomp,
            self.landlock,
        )

    def __repr__(self) -> str:
        return f"SandboxConfig({self.__dict__!r})"


def _resolver_mounts(read_only: Iterable[object]) -> list[str]:
    """The extra paths a name lookup needs when the network is left on.

    /etc/resolv.conf is a symbolic link wherever a local resolver maintains
    it: to /run/systemd/resolve/stub-resolv.conf under systemd-resolved, to
    /mnt/wsl/resolv.conf under WSL.  Neither target is in
    sandbox_read_only_mount_base, so inside the sandbox the link dangles and
    every name lookup fails -- an exercise which asked for the network with
    sandbox_network=False gets one it can not use.  COMP2041's shell_courses,
    python_courses_requests, python_courses_subprocess and regex_json all
    failed that way, while curl to a bare address worked.

    Only the link's target is added, and only when nothing already mounted
    covers it, so the sandbox gains one read-only file and nothing else.
    """
    target = os.path.realpath("/etc/resolv.conf")
    if target == "/etc/resolv.conf" or not os.path.exists(target):
        return []
    for host, _inside in _normalise_mounts(read_only, "sandbox_read_only_mount"):
        if target == host or target.startswith(host.rstrip("/") + "/"):
            return []
    return [target]


def config_from_parameters(parameters: Mapping[str, Any]) -> SandboxConfig:
    """Build a SandboxConfig from a test's parameter dictionary.

    The names and defaults are those of parameter_descriptions.py; .get() is
    used so that a partial dictionary (a probe before parsing, a unit test)
    still gives the documented defaults.
    """
    read_only = list(
        parameters.get("sandbox_read_only_mount_base", DEFAULT_READ_ONLY_MOUNT_BASE)
    ) + list(parameters.get("sandbox_read_only_mount", []))
    network = parameters.get("sandbox_network", True)
    if not network:
        read_only += _resolver_mounts(read_only)
    return SandboxConfig(
        network=network,
        read_only_mounts=read_only,
        read_write_mounts=list(parameters.get("sandbox_read_write_mount", [])),
        tmp_bytes=parameters.get("sandbox_tmp_bytes", DEFAULT_TMP_BYTES),
        shm_bytes=parameters.get("sandbox_shm_bytes", DEFAULT_SHM_BYTES),
        seccomp=parameters.get("sandbox_seccomp", True),
        landlock=parameters.get("sandbox_landlock", True),
    )


# Built once per process: the filter is identical for every sandbox and the
# Landlock ABI does not change while we run.
_seccomp_program: sandbox_seccomp.SeccompProgram | None = None
_landlock_abi: int | None = None


def _get_seccomp_program() -> sandbox_seccomp.SeccompProgram | None:
    global _seccomp_program  # noqa: PLW0603 - a once-per-process cache
    if _seccomp_program is None and sandbox_seccomp.SUPPORTED:
        _seccomp_program = sandbox_seccomp.build_filter()
    return _seccomp_program


def _get_landlock_abi() -> int:
    global _landlock_abi  # noqa: PLW0603 - a once-per-process cache
    if _landlock_abi is None:
        _landlock_abi = sandbox_landlock.abi_version()
    return _landlock_abi


class Sandbox:
    """One command's sandbox: construct immediately before subprocess.Popen.

    Use as
        sb = Sandbox(config, work_dir, root_dir)
        p = subprocess.Popen(argv, preexec_fn=sb.preexec, start_new_session=True,
                             pass_fds=sb.pass_fds(), cwd=work_dir, ...)
        ... wait for p ...
        message = sb.error()

    work_dir: absolute path of an existing directory; bound read-write at
        the same path inside the sandbox and made the command's cwd.
    root_dir: absolute path of an existing empty directory used as the
        mountpoint of the sandbox's root tmpfs.  The mount exists only in the
        child's private mount namespace, so the directory stays empty (and
        reusable) on the host.
    executable_check: the command's argv[0]; if it cannot be resolved inside
        the sandbox the child exits 127 and error() explains, mirroring the
        OSError Popen would raise for an unsandboxed command.
    env: the environment the command will be given (its PATH is used for
        executable_check); None means os.environ.
    """

    def __init__(  # noqa: C901 - one check per constructor argument
        self,
        config: SandboxConfig,
        work_dir: str,
        root_dir: str,
        debug: int = 0,
        executable_check: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if not _SYS:
            raise SandboxUnavailable(
                f"sandbox: no verified syscall numbers for architecture {_MACHINE!r}"
            )
        self.notes: list[str] = []
        self.config = config
        self.debug = debug
        self.work_dir = self._check_directory(work_dir, "work_dir")
        self.root_dir = self._check_directory(root_dir, "root_dir")
        if os.listdir(self.root_dir):
            raise SandboxError(f"sandbox: root_dir {self.root_dir} is not empty")
        if self.work_dir.startswith(self.root_dir + "/"):
            raise SandboxError(
                f"sandbox: work_dir {self.work_dir} must not be inside root_dir"
            )
        self.executable_check = executable_check
        environment = os.environ if env is None else env
        self._path = environment.get("PATH", os.defpath)

        self._uid = os.getuid()
        self._gid = os.getgid()
        self._unshare_flags = (
            CLONE_NEWUSER
            | CLONE_NEWNS
            | CLONE_NEWPID
            | CLONE_NEWIPC
            | CLONE_NEWUTS
            | CLONE_NEWCGROUP
            | (CLONE_NEWNET if config.network else 0)
        )
        self._read_only = self._plan_mounts(
            config.read_only_mounts, "sandbox_read_only_mount"
        )
        self._read_write = self._plan_mounts(
            config.read_write_mounts, "sandbox_read_write_mount", must_exist=True
        )
        self._tmp_options = f"mode=1777,size={config.tmp_bytes}"
        self._shm_options = f"mode=1777,size={config.shm_bytes}"
        self._device_nodes = [
            name for name in _DEVICE_NODES if os.path.exists("/dev/" + name)
        ]
        self._cap_last_cap = _read_cap_last_cap()
        # struct ifreq for "lo": name, then the flags word.
        self._ifreq = b"lo".ljust(_IFREQ_SIZE, b"\0")

        self._seccomp_program: sandbox_seccomp.SeccompProgram | None = None
        if config.seccomp:
            self._seccomp_program = _get_seccomp_program()
            if self._seccomp_program is None:
                self.notes.append(
                    f"seccomp filter unavailable on this architecture ({_MACHINE})"
                )
        else:
            self.notes.append("seccomp filter disabled (sandbox_seccomp=False)")

        self._landlock_abi = 0
        if config.landlock:
            self._landlock_abi = _get_landlock_abi()
            if not self._landlock_abi:
                self.notes.append("Landlock unavailable on this kernel")
        else:
            self.notes.append("Landlock disabled (sandbox_landlock=False)")
        self._landlock_rules = self._plan_landlock_rules()

        # The error pipe: both ends are close-on-exec (os.pipe's default) so
        # the command never inherits the write end and cannot forge a sandbox
        # failure message; the child writes to it inside preexec, before
        # Popen closes fds, which is why pass_fds() is empty.
        self._error_read, self._error_write = os.pipe()
        os.set_blocking(self._error_read, False)
        self._error_message: str | None = None

        # Debug messages from the child go to a private copy of autotest's
        # own stderr taken now: by the time preexec runs, Popen has made fd 2
        # the command's stderr pipe, which the test compares against its
        # expected output.  It is close-on-exec like the error pipe.
        self._debug_fd: int | None = None
        if debug > 2:
            try:
                self._debug_fd = os.dup(2)
            except OSError:
                pass

        if debug > 1:
            print(
                f"sandbox: work_dir={self.work_dir} root_dir={self.root_dir} "
                f"network={config.network} notes={self.notes}",
                file=sys.stderr,
            )

    @staticmethod
    def _check_directory(path: object, what: str) -> str:
        if not isinstance(path, str) or not path.startswith("/"):
            raise SandboxError(f"sandbox: {what} must be an absolute path: {path!r}")
        path = os.path.normpath(path)
        if not os.path.isdir(path):
            raise SandboxError(f"sandbox: {what} {path} is not a directory")
        return path

    def _plan_landlock_rules(
        self,
    ) -> tuple[list[tuple[str, int]], list[tuple[int, int]]]:
        """The Landlock ruleset, as (path_rules, fd_rules) for restrict_self.

        Read and execute everywhere, write where the program is supposed to
        write, device access under /dev.  /proc gets file writes only: a
        program writes its own /proc/self files (glibc's pthread_setname_np
        on another thread writes /proc/self/task/<tid>/comm) and procfs's
        own ownership checks remain the gate for everything else, since the
        command has no capabilities and /proc/sys is root's.  The standard
        streams are granted by descriptor so that re-opening /dev/stdin
        reaches the host file autotest supplied as input.
        """
        read = sandbox_landlock.READ_ACCESS
        write = sandbox_landlock.WRITE_ACCESS
        device = sandbox_landlock.DEVICE_ACCESS
        # TRUNCATE accompanies WRITE_FILE wherever a file may be written:
        # Landlock counts open(O_TRUNC), i.e. a shell's ">", as truncation.
        stream_write = (
            sandbox_landlock.ACCESS_FS_WRITE_FILE | sandbox_landlock.ACCESS_FS_TRUNCATE
        )
        path_rules = [
            ("/", read),
            ("/dev", read | device),
            ("/proc", read | stream_write),
        ]
        writable = [self.work_dir, _SANDBOX_TMP, _SANDBOX_SHM]
        writable += [inside for _host, inside, _is_dir in self._read_write]
        path_rules.extend((path, read | write) for path in writable)
        fd_rules = [
            (0, sandbox_landlock.ACCESS_FS_READ_FILE | device),
            (1, stream_write | device),
            (2, stream_write | device),
        ]
        return path_rules, fd_rules

    @staticmethod
    def _plan_mounts(
        entries: Iterable[object], what: str, must_exist: bool = False
    ) -> list[tuple[str, str, bool | None]]:
        """Decide in the parent what each mount entry becomes inside.

        Returns (host, inside, is_dir) triples; a host path that is a symlink
        (e.g. /bin -> usr/bin on a merged-usr system) is recorded with
        is_dir=None and its link target as host, so the same symlink is
        recreated inside rather than bound as a directory.  Missing read-only
        paths are skipped, because the base list names directories that not
        every distribution has.
        """
        plan: list[tuple[str, str, bool | None]] = []
        for host, inside in _normalise_mounts(entries, what):
            if os.path.islink(host):
                plan.append((os.readlink(host), inside, None))
            elif os.path.isdir(host):
                plan.append((host, inside, True))
            elif os.path.exists(host):
                plan.append((host, inside, False))
            elif must_exist:
                raise SandboxError(f"sandbox: {what}: {host} does not exist")
        return plan

    def pass_fds(self) -> tuple[int, ...]:
        """fds Popen must keep open in the child: none, see the constructor."""
        return ()

    def error(self) -> str | None:
        """The failure message written by the child, or None.

        Call once the process has exited.  Never blocks: it reads what is in
        the pipe (whose write end is closed when the child exits) and closes
        both ends.
        """
        if self._error_read is not None:
            chunks = []
            try:
                while True:
                    data = os.read(self._error_read, 4096)
                    if not data:
                        break
                    chunks.append(data)
            except BlockingIOError:
                pass
            except OSError:
                pass
            message = b"".join(chunks).decode("utf-8", "replace").strip()
            if message:
                self._error_message = message
        self.close()
        return self._error_message

    def close(self) -> None:
        """Release the pipe; idempotent."""
        for name in ("_error_read", "_error_write", "_debug_fd"):
            fd = getattr(self, name, None)
            if fd is not None:
                setattr(self, name, None)
                try:
                    os.close(fd)
                except OSError:
                    pass

    def __del__(self) -> None:
        self.close()

    # ---- the child side: everything below runs after fork, without imports

    def preexec(  # runs only in the sandbox child: test_uid_is_the_invoking_user
        self,
    ) -> None:
        """Popen's preexec_fn: build the sandbox; only C returns.

        The order (namespaces -> maps -> fork -> mounts -> pivot -> loopback
        -> fork -> chdir -> caps -> no_new_privs -> Landlock -> seccomp) is
        the design, not a detail: each step is possible only because of the
        one before it and cannot be undone from inside.
        """
        stage = "creating namespaces"
        try:
            self._become_session_leader()
            self._create_namespaces()
            stage = "forking"
            # B, as PID 1 of the new PID namespace, cannot die by a signal
            # it sends itself, so it relays the command's fatal signal to A
            # through this pipe and A dies by it instead.  A holds the only
            # read end, which also makes the pipe B's test of A's liveness.
            relay_read, relay_write = os.pipe()
            b_pid = os.fork()
            if b_pid:
                os.close(relay_write)
                self._supervise(b_pid, relay_read=relay_read)  # A: never returns
            os.close(relay_read)
            self._die_with_parent(relay_write)
            stage = "building the root filesystem"
            self._build_root()
            stage = "forking"
            c_pid = os.fork()
            if c_pid:
                self._supervise(c_pid, relay_write=relay_write)  # B: never returns
            os.close(relay_write)
            stage = "restricting the command"
            self._restrict()
            self._check_executable()  # C returns, or exits 127
        except BaseException as e:  # noqa: BLE001 - a failed sandbox must never exec
            self._fail(f"sandbox: {stage}: {self._describe(e)}")

    def _describe(  # runs only in the sandbox child: test_sandbox_failure_reports_125
        self, e: BaseException
    ) -> str:
        if isinstance(e, OSError) and e.errno is not None and " failed: " in str(e):
            return e.strerror or str(e)
        return f"{type(e).__name__}: {e}"

    def _fail(  # runs only in the sandbox child: test_sandbox_failure_reports_125
        self, message: str
    ) -> NoReturn:
        try:
            os.write(self._error_write, message.encode("utf-8", "replace")[:4000])
        except OSError:
            pass
        os._exit(_SANDBOX_FAILED_STATUS)

    def _debug_write(  # runs only in the sandbox child: test_integration.py::test_sandbox_debug_output_does_not_reach_the_tested_program
        self, message: str
    ) -> None:
        if self._debug_fd is not None:
            try:
                os.write(self._debug_fd, f"sandbox: {message}\n".encode())
            except OSError:
                pass

    def _become_session_leader(  # runs only in the sandbox child: test_session_and_terminal_without_start_new_session
        self,
    ) -> None:
        """A: own session, so the command has no controlling terminal.

        Popen does this when the caller passes start_new_session=True (and
        setsid then fails here with EPERM, which is fine); doing it again
        means the invariant does not depend on every caller, since /dev/tty
        is bound from the host and would otherwise be the marker's terminal.
        SIGCHLD is reset too: inherited SIG_IGN would make the kernel reap
        B before A can collect its status.
        """
        try:
            os.setsid()
        except OSError:
            pass
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)

    def _die_with_parent(  # runs only in the sandbox child: test_killing_the_popen_child_kills_the_command
        self, relay_write: int
    ) -> None:
        """B: be SIGKILLed when A dies, so nothing outlives the Popen child.

        B's death as PID 1 kills everything in the namespace, so this is
        what turns Popen.kill() (SIGKILL to A alone) or a failure in A into
        the end of the command rather than an orphan running on the host.
        PR_SET_PDEATHSIG only fires for deaths after it is set, so A is
        checked afterwards: if the relay pipe has lost its reader, A is
        already gone.
        """
        _prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
        if not _pipe_has_reader(relay_write):
            os._exit(_SANDBOX_FAILED_STATUS)

    def _create_namespaces(  # runs only in the sandbox child: test_uid_is_the_invoking_user
        self,
    ) -> None:
        """A: enter the new namespaces and map our uid/gid to themselves.

        setgroups must be denied before an unprivileged process may write
        gid_map, and the maps must be written by A itself, before B is
        forked, so that B can mount.
        """
        _unshare(self._unshare_flags)
        _write_file("/proc/self/setgroups", "deny")
        _write_file("/proc/self/uid_map", f"{self._uid} {self._uid} 1")
        _write_file("/proc/self/gid_map", f"{self._gid} {self._gid} 1")
        self._debug_write("namespaces created")

    def _supervise(  # runs only in the sandbox child: test_signal_death_propagates  # noqa: C901 - one branch per way the child can end
        self, child: int, relay_read: int | None = None, relay_write: int | None = None
    ) -> NoReturn:
        """Wait for child and exit exactly as it did.  Never returns.

        A signal death is reproduced by killing ourselves with the same
        signal, so that Popen reports -SIGXCPU and friends for the command.
        B cannot do that: the kernel discards a default-action signal sent
        to a PID namespace's init from inside the namespace, itself
        included.  So B writes the signal number to relay_write and exits,
        and A, which is in the host's PID namespace, reads it from
        relay_read and dies by it.

        The core-file soft limit is zeroed first: A runs with the invoking
        user's limits and cwd, and a core of the Python interpreter
        appearing in the test directory would be baffling.
        """
        keep = [self._error_write] + [
            fd for fd in (relay_read, relay_write) if fd is not None
        ]
        _close_fds_except(keep)
        for forwarded in _FORWARDED_SIGNALS:
            signal.signal(
                forwarded, lambda signum, _frame: _kill_quietly(child, signum)
            )
        try:
            status = _waitpid(child)
        except ChildProcessError:
            # our child was reaped behind our back (SIGCHLD ignored, or a
            # subreaper): its status is unknowable and must not become 0
            self._fail("sandbox: waitpid failed: ECHILD (is SIGCHLD ignored?)")
        sig: int | None = None
        if os.WIFSIGNALED(status):
            sig = os.WTERMSIG(status)
        elif relay_read is not None:
            relayed = _read_available(relay_read)
            if relayed.isdigit():
                sig = int(relayed)
        if sig is None:
            os._exit(os.WEXITSTATUS(status))
        if relay_write is not None:
            try:
                os.write(relay_write, str(sig).encode())
            except OSError:
                pass
            os._exit(128 + sig)
        try:
            hard = resource.getrlimit(resource.RLIMIT_CORE)[1]
            resource.setrlimit(resource.RLIMIT_CORE, (0, hard))
        except (OSError, ValueError):
            pass
        try:
            signal.signal(sig, signal.SIG_DFL)
        except (OSError, ValueError):
            pass
        os.kill(os.getpid(), sig)
        os._exit(128 + sig)

    def _build_root(  # runs only in the sandbox child: test_home_and_host_filesystem_hidden
        self,
    ) -> None:
        """B: assemble the private root on a tmpfs and pivot into it."""
        root = self.root_dir
        # Until propagation is private, mounts made here could travel back
        # to the host's namespace.
        _mount("none", "/", None, MS_REC | MS_PRIVATE, None)
        _mount("tmpfs", root, "tmpfs", MS_NOSUID | MS_NODEV, "mode=0755")

        _makedirs(root + "/proc")
        _mount("proc", root + "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, None)
        self._mask_init_process(root + "/proc")

        # sysfs can only be mounted by the owner of the network namespace, so
        # with the host's network this fails; an empty /sys is acceptable.
        _makedirs(root + "/sys")
        try:
            _mount(
                "sysfs",
                root + "/sys",
                "sysfs",
                MS_NOSUID | MS_NODEV | MS_NOEXEC | MS_RDONLY,
                None,
            )
        except OSError:
            pass

        _makedirs(root + _SANDBOX_TMP)
        _mount(
            "tmpfs",
            root + _SANDBOX_TMP,
            "tmpfs",
            MS_NOSUID | MS_NODEV,
            self._tmp_options,
        )

        self._build_dev(root + "/dev")

        # Bind mounts come after the private /tmp and /dev are mounted: a
        # bound path under them (the work directory is often under /tmp)
        # would otherwise be hidden by the later mount.  Read-only first, so
        # a read-write mount inside a read-only tree stays writable.
        for host, inside, is_dir in self._read_only:
            self._bind(root, host, inside, is_dir, read_only=True)
        self._bind(root, self.work_dir, self.work_dir, True, read_only=False)
        for host, inside, is_dir in self._read_write:
            self._bind(root, host, inside, is_dir, read_only=False)

        self._pivot(root)
        self._debug_write("root filesystem built")

        if self.config.network:
            self._bring_loopback_up()

    @staticmethod
    def _mask_init_process(  # runs only in the sandbox child: test_init_process_is_masked
        proc: str,
    ) -> None:
        """Hide PID 1's /proc entry behind an empty read-only tmpfs.

        PID 1 is B, a forked copy of the autotest interpreter, so /proc/1
        would otherwise show the student's program autotest's command line
        and the path of the Python running it.  Its memory, fds, cwd and
        environment are already refused by ownership checks; the directory
        is masked whole so the rest is not a per-file argument.
        """
        _mount(
            "tmpfs",
            proc + "/1",
            "tmpfs",
            MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC,
            "mode=0555,size=4k",
        )

    def _bind(  # runs only in the sandbox child: test_system_directories_read_only
        self, root: str, host: str, inside: str, is_dir: bool | None, read_only: bool
    ) -> None:
        """Bind host at inside (relative to root), or recreate a symlink.

        System trees are bound recursively (so /usr/local on its own
        filesystem still appears) and made read-only in one atomic step;
        writable directories are bound non-recursively so that a stray mount
        inside a test directory cannot drag anything else in.
        """
        target = root + inside
        try:
            if is_dir is None:
                _makedirs(os.path.dirname(target))
                if not os.path.lexists(target):
                    os.symlink(host, target)
                return
            if is_dir:
                _makedirs(target)
            else:
                _makedirs(os.path.dirname(target))
                if not os.path.exists(target):
                    _create_file(target)
        except OSError as e:
            raise OSError(
                e.errno,
                f"creating mountpoint {inside} for {host} failed: {_errno_text(e.errno or 0)}",
            ) from e
        recursive = bool(read_only and is_dir)
        _mount(host, target, None, MS_BIND | (MS_REC if recursive else 0), None)
        attrs = MOUNT_ATTR_NOSUID | MOUNT_ATTR_NODEV
        if read_only:
            attrs |= MOUNT_ATTR_RDONLY
        _set_mount_attributes(target, attrs, recursive)

    def _build_dev(  # runs only in the sandbox child: test_devices
        self, dev: str
    ) -> None:
        """A minimal /dev: a few host device nodes bound onto a tmpfs.

        The tmpfs is nosuid,noexec but not nodev, since the binds beneath it
        are of device nodes; it is made read-only once populated so the
        program cannot fill it.  /dev/pts is attempted and ignored on
        failure: only programs that open a pseudo-terminal need it.
        """
        _makedirs(dev)
        _mount("tmpfs", dev, "tmpfs", MS_NOSUID | MS_NOEXEC, "mode=0755")
        for name in self._device_nodes:
            target = dev + "/" + name
            _create_file(target)
            _mount("/dev/" + name, target, None, MS_BIND, None)
        _makedirs(dev + "/pts")
        try:
            _mount(
                "devpts",
                dev + "/pts",
                "devpts",
                MS_NOSUID | MS_NOEXEC,
                "newinstance,ptmxmode=0666,mode=0620",
            )
        except OSError:
            pass
        for name, link_target in _DEVICE_LINKS:
            os.symlink(link_target, dev + "/" + name)
        shm = dev + "/shm"
        _makedirs(shm)
        _mount("tmpfs", shm, "tmpfs", MS_NOSUID | MS_NODEV, self._shm_options)

    def _pivot(  # runs only in the sandbox child: test_home_and_host_filesystem_hidden
        self, root: str
    ) -> None:
        """Make root "/" and detach the host's root from this namespace.

        pivot_root rather than chroot: afterwards the old root is not merely
        unreachable by path, it is gone from the mount namespace.  The root
        and /dev tmpfs are then made read-only (non-recursively, so /tmp,
        /dev/shm and the work directory stay writable) and everything is made
        nosuid in one recursive step.
        """
        old_root = root + "/" + _OLD_ROOT
        os.mkdir(old_root, 0o700)
        os.chdir(root)
        _pivot_root(".", _OLD_ROOT)
        os.chdir("/")
        _umount2("/" + _OLD_ROOT, MNT_DETACH)
        os.rmdir("/" + _OLD_ROOT)
        _set_mount_attributes("/", MOUNT_ATTR_NOSUID, recursive=True)
        _set_mount_attributes("/", MOUNT_ATTR_RDONLY, recursive=False)
        _set_mount_attributes("/dev", MOUNT_ATTR_RDONLY, recursive=False)

    def _bring_loopback_up(  # runs only in the sandbox child: test_network_isolated_but_loopback_works
        self,
    ) -> None:
        """A new network namespace has "lo" but it is down; 127.0.0.1 must
        work, because plenty of course code talks to itself over TCP."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            ifreq = fcntl.ioctl(sock.fileno(), SIOCGIFFLAGS, self._ifreq)
            offset = _IFREQ_FLAGS_OFFSET
            flags = int.from_bytes(ifreq[offset : offset + 2], sys.byteorder)
            flags |= IFF_UP
            ifreq = (
                bytes(ifreq[:offset])
                + flags.to_bytes(2, sys.byteorder)
                + bytes(ifreq[offset + 2 :])
            )
            fcntl.ioctl(sock.fileno(), SIOCSIFFLAGS, ifreq)
        except OSError as e:
            raise OSError(
                e.errno, f"bringing loopback up failed: {_errno_text(e.errno or 0)}"
            ) from e
        finally:
            sock.close()

    def _restrict(self) -> None:  # runs only in the sandbox child: test_mount_denied
        """C: give up every privilege, then apply the backstops.

        Emptying the bounding set needs CAP_SETPCAP, so it comes first, while
        we still hold it; afterwards no execve of anything - file
        capabilities, setuid binary, anything - can raise a capability in
        this process tree.  no_new_privs is also what lets an unprivileged
        process install a seccomp filter and a Landlock ruleset.
        """
        os.chdir(self.work_dir)
        for capability in range(self._cap_last_cap + 1):
            try:
                _prctl(PR_CAPBSET_DROP, capability)
            except OSError as e:
                if e.errno != errno.EINVAL:
                    raise
        _prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL)
        _prctl(PR_SET_NO_NEW_PRIVS, 1)
        if self._landlock_abi:
            path_rules, fd_rules = self._landlock_rules
            sandbox_landlock.restrict_self(self._landlock_abi, path_rules, fd_rules)
        if self._seccomp_program is not None:
            sandbox_seccomp.install(self._seccomp_program)
        self._debug_write("restrictions applied")

    def _check_executable(  # runs only in the sandbox child: test_missing_command_reports_127
        self,
    ) -> None:
        """Report a missing command the way Popen would have, with status 127.

        Popen's own exec-failure report still works through the sandbox, but
        by then the parent may have stopped listening; resolving argv[0]
        here, with execvp's PATH rules, gives a message that names the
        command rather than the interpreter.
        """
        name = self.executable_check
        if name is None:
            return
        if "/" in name:
            candidates = [name]
        else:
            candidates = [
                os.path.join(directory or ".", name)
                for directory in self._path.split(":")
            ]
        denied = False
        for candidate in candidates:
            if os.path.isfile(candidate):
                if os.access(candidate, os.X_OK):
                    return
                denied = True
        reason = "Permission denied" if denied else "No such file or directory"
        try:
            os.write(
                self._error_write, f"{reason}: '{name}'".encode("utf-8", "replace")
            )
        except OSError:
            pass
        os._exit(_COMMAND_NOT_FOUND_STATUS)


_probe_cache: dict[tuple[object, ...], str | None] = {}


def probe(config: SandboxConfig) -> str | None:
    """Try to build a real sandbox and run /bin/true in it.

    Returns None if it worked, else a one-line reason a tutor can act on.
    The result is cached per configuration for the life of the process:
    autotest may run hundreds of commands and the answer does not change.
    """
    try:
        key = config.key()
    except SandboxError as e:
        return str(e)
    if key not in _probe_cache:
        _probe_cache[key] = _probe_uncached(config)
    return _probe_cache[key]


def _probe_uncached(config: SandboxConfig) -> str | None:
    base = tempfile.mkdtemp(prefix="autotest_sandbox_probe_")
    try:
        work_dir = os.path.join(base, "work")
        root_dir = os.path.join(base, "root")
        os.mkdir(work_dir)
        os.mkdir(root_dir)
        try:
            sb = Sandbox(config, work_dir, root_dir, executable_check="/bin/true")
        except SandboxError as e:
            return str(e)
        try:
            p = subprocess.Popen(
                ["/bin/true"],
                preexec_fn=sb.preexec,
                start_new_session=True,
                pass_fds=sb.pass_fds(),
                cwd=work_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin"},
            )
        except (OSError, subprocess.SubprocessError) as e:
            sb.close()
            return f"sandbox: could not start a sandboxed process: {e}"
        try:
            status = p.wait(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            sb.close()
            return "sandbox: a sandboxed /bin/true did not finish within 60 seconds"
        message = sb.error()
        if status == 0 and not message:
            return None
        return _explain_probe_failure(
            message or f"sandbox: /bin/true exited with status {status}"
        )
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _explain_probe_failure(message: str) -> str:
    """Add the advice a tutor needs for the common failure: a host with
    unprivileged user namespaces disabled."""
    if "unshare(" in message and ("EPERM" in message or "ENOSPC" in message):
        message += (
            "; unprivileged user namespaces appear to be disabled on this host "
            "(see /proc/sys/user/max_user_namespaces and "
            "/proc/sys/kernel/unprivileged_userns_clone); "
            "set the parameter sandbox=False to run without a sandbox"
        )
    return message
