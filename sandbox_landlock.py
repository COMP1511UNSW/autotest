"""Landlock ruleset applied to a program run in the autotest sandbox.

Landlock restricts which paths a process may reach, enforced by the kernel
against the file rather than against the mount table.  That is what makes it
worth having on top of the mount namespace built by sandbox.py: an attack that
manipulates mounts does not thereby gain access, because the ruleset was
never expressed in terms of mounts.

The ruleset is deliberately coarse: read and execute over the sandbox root,
write over the handful of directories a student program is supposed to write
to.  A finer ruleset would be more satisfying and much easier to get subtly
wrong, and this is a second line of defence, not the first.

Landlock ABI levels differ in which access bits exist.  Asking for a bit the
running kernel does not know about fails the whole ruleset with EINVAL, so
the requested set is masked down to what the kernel reports it supports.

One bit needs calling out: LANDLOCK_ACCESS_FS_IOCTL_DEV (ABI 5) governs ioctl
on device files, and a terminal is a device file.  Without it granted over
/dev, a shell in the sandbox breaks.

A rule may name an already-open file rather than a path.  That matters for
the command's standard streams: autotest gives a program its stdin as an
unlinked temporary file (or /dev/null) opened on the host before the sandbox
exists, and a program that re-opens it through /dev/stdin or /proc/self/fd/0
resolves to that host inode, which is beneath no path inside the sandbox.
A rule on the descriptor itself grants exactly that file and nothing else.

abi_version() is called in the parent, restrict_self() in the forked child
immediately before exec: it does no imports because the child of a
multi-threaded process must not take the import lock.  The syscall numbers
used here (444, 445, 446) are the same on every Linux architecture.
"""

from __future__ import annotations

import ctypes
import errno
import os
import stat
from collections.abc import Iterable

__all__ = [
    "ACCESS_FS_READ_FILE",
    "ACCESS_FS_TRUNCATE",
    "ACCESS_FS_WRITE_FILE",
    "DEVICE_ACCESS",
    "READ_ACCESS",
    "WRITE_ACCESS",
    "abi_version",
    "restrict_self",
]

_SYS_LANDLOCK_CREATE_RULESET = 444
_SYS_LANDLOCK_ADD_RULE = 445
_SYS_LANDLOCK_RESTRICT_SELF = 446

LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_RULE_PATH_BENEATH = 1

ACCESS_FS_EXECUTE = 1 << 0
ACCESS_FS_WRITE_FILE = 1 << 1
ACCESS_FS_READ_FILE = 1 << 2
ACCESS_FS_READ_DIR = 1 << 3
ACCESS_FS_REMOVE_DIR = 1 << 4
ACCESS_FS_REMOVE_FILE = 1 << 5
ACCESS_FS_MAKE_CHAR = 1 << 6
ACCESS_FS_MAKE_DIR = 1 << 7
ACCESS_FS_MAKE_REG = 1 << 8
ACCESS_FS_MAKE_SOCK = 1 << 9
ACCESS_FS_MAKE_FIFO = 1 << 10
ACCESS_FS_MAKE_BLOCK = 1 << 11
ACCESS_FS_MAKE_SYM = 1 << 12
ACCESS_FS_REFER = 1 << 13
ACCESS_FS_TRUNCATE = 1 << 14
ACCESS_FS_IOCTL_DEV = 1 << 15

# How much of struct landlock_ruleset_attr each ABI level defines:
# handled_access_fs (ABI 1), handled_access_net (ABI 4), scoped (ABI 6).
# Passing a size larger than the kernel's is fatal (E2BIG) unless the extra
# bytes are zero, and a size smaller than the kernel's is always accepted,
# so the table names the real size rather than relying on that tolerance.
_RULESET_ATTR_SIZES = {1: 8, 2: 8, 3: 8, 4: 16, 5: 16, 6: 24}
_MAX_KNOWN_ABI = 6

# Highest access bit each ABI level knows about.  Anything above the running
# kernel's level is masked out before the ruleset is created.
_ABI_ACCESS_FS = {
    1: (1 << 13) - 1,  # EXECUTE .. MAKE_SYM
    2: (1 << 14) - 1,  # + REFER
    3: (1 << 15) - 1,  # + TRUNCATE
    4: (1 << 15) - 1,  # ABI 4 adds network access only
    5: (1 << 16) - 1,  # + IOCTL_DEV
    6: (1 << 16) - 1,  # ABI 6 adds IPC scoping only
}

# Read-only access: enough to run the system's binaries and read their data.
READ_ACCESS = ACCESS_FS_EXECUTE | ACCESS_FS_READ_FILE | ACCESS_FS_READ_DIR

# Write access granted over the program's own directories.  Device-node
# creation is excluded: every mount in the sandbox is nodev (or a tmpfs in a
# user namespace, where device nodes are inert), so there is no reason to
# allow making one.
WRITE_ACCESS = (
    ACCESS_FS_WRITE_FILE
    | ACCESS_FS_REMOVE_DIR
    | ACCESS_FS_REMOVE_FILE
    | ACCESS_FS_MAKE_DIR
    | ACCESS_FS_MAKE_REG
    | ACCESS_FS_MAKE_SOCK
    | ACCESS_FS_MAKE_FIFO
    | ACCESS_FS_MAKE_SYM
    | ACCESS_FS_REFER
    | ACCESS_FS_TRUNCATE
)

# Granted over /dev: read and write on the files themselves plus the ioctl
# right on ABI 5, and nothing that creates or removes entries.  WRITE_FILE is
# not optional: without it every write to /dev/null is denied, which is very
# nearly every program.
DEVICE_ACCESS = ACCESS_FS_WRITE_FILE | ACCESS_FS_TRUNCATE | ACCESS_FS_IOCTL_DEV


class RulesetAttr(ctypes.Structure):
    """struct landlock_ruleset_attr (a kernel ABI layout).

    Only the prefix the running ABI understands is passed to the kernel, via
    the explicit size argument.
    """

    _fields_ = (
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    )


class PathBeneathAttr(ctypes.Structure):
    """struct landlock_path_beneath_attr: packed, as the kernel declares it."""

    _pack_ = 1
    _fields_ = (
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    )


def _load_libc() -> ctypes.CDLL:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


_libc = _load_libc()


def _syscall(number: int, what: str, *args: int) -> int:
    """syscall(2) raising OSError with the real errno on failure."""
    ctypes.set_errno(0)
    result = int(
        _libc.syscall(ctypes.c_long(number), *(ctypes.c_long(a) for a in args))
    )
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(
            code,
            f"{what} failed: {errno.errorcode.get(code, code)} ({os.strerror(code)})",
        )
    return result


def abi_version() -> int:
    """Return the Landlock ABI version the running kernel supports.

    0 means Landlock is unavailable (not built in, or disabled at boot).
    """
    try:
        return _syscall(
            _SYS_LANDLOCK_CREATE_RULESET,
            "landlock_create_ruleset",
            0,
            0,
            LANDLOCK_CREATE_RULESET_VERSION,
        )
    except OSError:
        return 0


def _supported_access(version: int) -> int:
    """Return the access bits this ABI version understands.

    A newer kernel than this table knows is treated as the newest known one:
    Landlock never removes a bit, and asking for bits we know nothing about
    would gain nothing.
    """
    return _ABI_ACCESS_FS.get(version, _ABI_ACCESS_FS[max(_ABI_ACCESS_FS)])


def _create_ruleset(  # runs only in the sandbox child: test_unix_sockets_in_work_dir
    handled: int, version: int
) -> int:
    attr = RulesetAttr(handled_access_fs=handled, handled_access_net=0, scoped=0)
    size = _RULESET_ATTR_SIZES[min(version, _MAX_KNOWN_ABI)]
    return _syscall(
        _SYS_LANDLOCK_CREATE_RULESET,
        "landlock_create_ruleset",
        ctypes.addressof(attr),
        size,
        0,
    )


def _add_rule_for_fd(  # runs only in the sandbox child: test_unix_sockets_in_work_dir
    ruleset_fd: int, parent_fd: int, access: int, what: str
) -> None:
    attr = PathBeneathAttr(allowed_access=access, parent_fd=parent_fd)
    _syscall(
        _SYS_LANDLOCK_ADD_RULE,
        f"landlock_add_rule({what})",
        ruleset_fd,
        LANDLOCK_RULE_PATH_BENEATH,
        ctypes.addressof(attr),
        0,
    )


def _add_path_rule(  # runs only in the sandbox child: test_unix_sockets_in_work_dir
    ruleset_fd: int, path: str, access: int
) -> None:
    """Grant access beneath path; the path must exist inside the sandbox."""
    try:
        parent_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError as e:
        raise OSError(
            e.errno,
            f"Landlock rule for {path}: cannot open it: {os.strerror(e.errno or 0)}",
        ) from e
    try:
        _add_rule_for_fd(ruleset_fd, parent_fd, access, path)
    finally:
        os.close(parent_fd)


def _add_open_file_rule(  # runs only in the sandbox child: test_own_proc_entries_writable_but_not_proc_sys
    ruleset_fd: int, fd: int, access: int
) -> None:
    """Grant access to the file already open on fd, if it is a file.

    A closed descriptor, a pipe, a socket or a directory is skipped: the
    first is nothing, the next two are not filesystem objects Landlock can
    name (the kernel says EBADFD), and a directory on a standard stream is
    nobody's intention.  Every other failure is a real one.
    """
    try:
        mode = os.fstat(fd).st_mode
    except OSError:
        return
    if not (stat.S_ISREG(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode)):
        return
    try:
        _add_rule_for_fd(ruleset_fd, fd, access, f"fd {fd}")
    except OSError as e:
        if e.errno not in (errno.EBADF, errno.EBADFD):
            raise


def restrict_self(  # runs only in the sandbox child: test_unix_sockets_in_work_dir
    version: int,
    path_rules: Iterable[tuple[str, int]],
    fd_rules: Iterable[tuple[int, int]] = (),
) -> None:
    """Apply a Landlock ruleset to the calling process, irreversibly.

    version is the ABI reported by abi_version() (called in the parent, so
    that this function makes no unnecessary syscalls in the child).
    PR_SET_NO_NEW_PRIVS must already be set; the kernel refuses otherwise.
    The restriction is inherited across fork and execve.

    path_rules: (path, access) pairs; each path must exist in the sandbox
    and the access bits (READ_ACCESS, WRITE_ACCESS, DEVICE_ACCESS or any
    combination of ACCESS_FS_*) are granted beneath it.  Within one ruleset
    the rights of nested rules accumulate, so ("/", READ_ACCESS) plus
    ("/tmp", WRITE_ACCESS) makes /tmp readable and writable.
    fd_rules: (fd, access) pairs for files already open on those
    descriptors, typically the standard streams (see the module docstring);
    descriptors that are not files are skipped.

    Access bits the running ABI does not know are masked out.  Raises
    OSError naming the step that failed.
    """
    supported = _supported_access(version)
    handled = (READ_ACCESS | WRITE_ACCESS | DEVICE_ACCESS) & supported
    ruleset_fd = _create_ruleset(handled, version)
    try:
        for path, access in path_rules:
            _add_path_rule(ruleset_fd, path, access & supported)
        for fd, access in fd_rules:
            _add_open_file_rule(ruleset_fd, fd, access & supported)
        _syscall(_SYS_LANDLOCK_RESTRICT_SELF, "landlock_restrict_self", ruleset_fd, 0)
    finally:
        os.close(ruleset_fd)
