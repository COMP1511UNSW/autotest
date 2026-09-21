"""The seccomp filter applied to a program run in the autotest sandbox.

This is defence in depth, not the boundary.  The boundary is the mount
namespace built by sandbox.py, nosuid everywhere and an empty capability
bounding set.  The filter exists because one specific thing would undo all of
that: unshare(CLONE_NEWUSER) gives an unprivileged process a namespace in
which it holds CAP_SYS_ADMIN, and from there the mount layout becomes
attackable.  Denying user-namespace creation is the filter's actual job;
everything else it denies is a bonus.

Two consequences worth knowing:

* clone3 returns ENOSYS.  Its arguments live in a struct in memory, which
  classic BPF cannot dereference, so it cannot be inspected for CLONE_NEWUSER.
  ENOSYS makes glibc fall back to clone, which can be inspected.  This is the
  same approach container runtimes take.
* Debugging tools are deliberately left alone: ptrace, process_vm_readv and
  process_vm_writev are permitted because dcc runs gdb on the student's
  program, and valgrind and strace are used in courses.

Hand-computed jump offsets are the classic way to get a seccomp filter subtly
wrong: an off-by-one turns a "deny" into a "fall through to allow", and the
filter still loads and still looks plausible.  The filter is therefore
assembled from symbolic labels by the small Assembler below, so it can be read
as a list of rules rather than as arithmetic.

The program is built once, in the parent, by build_filter(); install() is
called in the forked child immediately before exec and does no imports and no
allocation beyond the syscall itself, because the child of a multi-threaded
process must not take the import lock.

The filter is only written for x86_64 (its syscall numbers and AUDIT_ARCH
value are architecture specific): SUPPORTED says whether this machine is one
the filter is verified for.  A wrong number in a filter is worse than a wrong
number in a call, because it fails open and silently.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform

from util import AutotestException

__all__ = ["SUPPORTED", "Assembler", "SeccompProgram", "build_filter", "install"]


class SeccompFilterError(AutotestException):
    """The filter could not be assembled: a programming error in this file."""


# Instruction classes and modifiers from <linux/bpf_common.h>.
BPF_LD = 0x00
BPF_W = 0x00
BPF_ABS = 0x20
BPF_JMP = 0x05
BPF_JEQ = 0x10
BPF_JSET = 0x40
BPF_K = 0x00
BPF_RET = 0x06

# Widest jump classic BPF can encode: offsets are single unsigned bytes.
_MAX_JUMP = 255

SECCOMP_SET_MODE_FILTER = 1

SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_ALLOW = 0x7FFF0000
SECCOMP_RET_DATA = 0x0000FFFF

AUDIT_ARCH_X86_64 = 0xC000003E

# Bit set in the syscall number for the x86_64 x32 ABI.  x32 shares syscall
# numbers with x86_64 but not their semantics, so a filter written for one is
# wrong for the other; the ABI is refused outright rather than filtered.
X32_SYSCALL_BIT = 0x40000000

CLONE_NEWUSER = 0x10000000

# Offsets into struct seccomp_data.
_OFFSET_NR = 0
_OFFSET_ARCH = 4
_OFFSET_ARG0_LOW = 16

_SUPPORTED_ARCH = "x86_64"
SUPPORTED = platform.machine() == _SUPPORTED_ARCH

# The seccomp(2) syscall number itself is architecture specific too.
_SYS_SECCOMP_X86_64 = 317

# x86_64 syscall numbers, from the kernel's syscall_64.tbl.
_NR = {
    "clone": 56,
    "clone3": 435,
    "unshare": 272,
    "setns": 308,
    "mount": 165,
    "umount2": 166,
    "pivot_root": 155,
    "mount_setattr": 442,
    "open_tree": 428,
    "move_mount": 429,
    "fsopen": 430,
    "fsconfig": 431,
    "fsmount": 432,
    "fspick": 433,
    "init_module": 175,
    "finit_module": 313,
    "delete_module": 176,
    "kexec_load": 246,
    "kexec_file_load": 320,
    "bpf": 321,
    "perf_event_open": 298,
    "userfaultfd": 323,
    "add_key": 248,
    "request_key": 249,
    "keyctl": 250,
    "swapon": 167,
    "swapoff": 168,
    "syslog": 103,
    "modify_ldt": 154,
    "kcmp": 312,
    "name_to_handle_at": 303,
    "open_by_handle_at": 304,
    "io_uring_setup": 425,
    "io_uring_enter": 426,
    "io_uring_register": 427,
    "reboot": 169,
    "acct": 163,
    "quotactl": 179,
    "ioperm": 173,
    "iopl": 172,
}

# Denied outright with EPERM.  A student program has no legitimate use for any
# of these, and each is either a route out of the sandbox or a route to
# affecting the host.  Mount-family calls would already fail without
# privilege; they are listed so that they still fail if some future change
# hands the process capabilities it should not have.
_DENIED_EPERM = (
    "setns",
    "mount",
    "umount2",
    "pivot_root",
    "mount_setattr",
    "open_tree",
    "move_mount",
    "fsopen",
    "fsconfig",
    "fsmount",
    "fspick",
    "init_module",
    "finit_module",
    "delete_module",
    "kexec_load",
    "kexec_file_load",
    "bpf",
    "perf_event_open",
    "userfaultfd",
    "add_key",
    "request_key",
    "keyctl",
    "swapon",
    "swapoff",
    "reboot",
    # io_uring submits work that kernel threads perform on the caller's
    # behalf, and much of it is not evaluated against the caller's seccomp
    # filter.  Leaving it reachable would make everything above advisory.
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    # The handle-based open pair is the classic escape from a chroot or a
    # bind-mounted subtree.  It needs CAP_DAC_READ_SEARCH, which the program
    # does not have, so this is depth rather than the only thing stopping it.
    "name_to_handle_at",
    "open_by_handle_at",
    # Kernel ring buffer: addresses and host detail a student has no use for.
    "syslog",
    "modify_ldt",
    "kcmp",
    "acct",
    "quotactl",
    "ioperm",
    "iopl",
)

# Inspected for CLONE_NEWUSER in their first argument rather than denied, so
# that ordinary fork and thread creation keep working.
_FLAG_CHECKED = ("clone", "unshare")


class SockFilter(ctypes.Structure):
    """struct sock_filter: one classic-BPF instruction (a kernel ABI layout)."""

    _fields_ = (
        ("code", ctypes.c_uint16),
        ("jt", ctypes.c_uint8),
        ("jf", ctypes.c_uint8),
        ("k", ctypes.c_uint32),
    )


class SockFprog(ctypes.Structure):
    """struct sock_fprog: a counted array of instructions (a kernel ABI layout)."""

    _fields_ = (
        ("len", ctypes.c_uint16),
        ("filter", ctypes.POINTER(SockFilter)),
    )


class SeccompProgram:
    """An assembled filter, keeping both ctypes objects alive together.

    The sock_fprog only points at the instruction array; if the array were
    collected before the kernel read it the filter would be garbage, so the
    two are kept in one object whose lifetime the caller controls.
    """

    def __init__(
        self, program: SockFprog, instructions: ctypes.Array[SockFilter]
    ) -> None:
        self.program = program
        self.instructions = instructions

    def address(self) -> int:
        return ctypes.addressof(self.program)


class _Instruction:
    """One instruction, with jump targets still expressed as labels."""

    __slots__ = ("code", "jf", "jt", "k")

    def __init__(self, code: int, jt: str | None, jf: str | None, k: int):
        self.code = code
        self.jt = jt
        self.jf = jf
        self.k = k


class Assembler:
    """Builds a classic-BPF program from labelled instructions.

    Jump targets are strings.  A target may be defined before or after the
    jump that refers to it; everything is resolved by assemble().  A jump to
    an undefined label is an error rather than a silently-zero offset.
    """

    def __init__(self) -> None:
        self._instructions: list[_Instruction] = []
        self._labels: dict[str, int] = {}

    def label(self, name: str) -> None:
        """Define name as pointing at the next instruction to be emitted."""
        if name in self._labels:
            raise SeccompFilterError(f"BPF label {name!r} defined twice")
        self._labels[name] = len(self._instructions)

    def load_absolute(self, offset: int) -> None:
        """Load the 32-bit word at offset in seccomp_data into A."""
        self._instructions.append(
            _Instruction(BPF_LD | BPF_W | BPF_ABS, None, None, offset)
        )

    def jump_if_equal(self, value: int, then: str, otherwise: str) -> None:
        """Branch to then if A equals value, else to otherwise."""
        self._instructions.append(
            _Instruction(BPF_JMP | BPF_JEQ | BPF_K, then, otherwise, value)
        )

    def jump_if_any_bit_set(self, mask: int, then: str, otherwise: str) -> None:
        """Branch to then if A shares any bit with mask."""
        self._instructions.append(
            _Instruction(BPF_JMP | BPF_JSET | BPF_K, then, otherwise, mask)
        )

    def ret(self, value: int) -> None:
        """Return value as the filter's verdict."""
        self._instructions.append(_Instruction(BPF_RET | BPF_K, None, None, value))

    def assemble(self) -> SeccompProgram:
        """Resolve labels and build the kernel-facing program."""
        count = len(self._instructions)
        if count == 0:
            raise SeccompFilterError("refusing to assemble an empty BPF program")

        array = (SockFilter * count)()
        for index, instruction in enumerate(self._instructions):
            array[index].code = instruction.code
            array[index].jt = self._offset(instruction.jt, index)
            array[index].jf = self._offset(instruction.jf, index)
            array[index].k = instruction.k

        program = SockFprog(len=count, filter=array)
        return SeccompProgram(program, array)

    def _offset(self, target: str | None, index: int) -> int:
        """Return the encoded jump offset from index to target.

        Classic BPF has no loops, so a backwards jump is a bug in the filter
        rather than something to encode.
        """
        if target is None:
            return 0
        if target not in self._labels:
            raise SeccompFilterError(f"BPF jump to undefined label {target!r}")
        distance = int(self._labels[target]) - index - 1
        if distance < 0:
            raise SeccompFilterError(f"BPF jump to {target!r} goes backwards")
        if distance > _MAX_JUMP:
            raise SeccompFilterError(
                f"BPF jump to {target!r} is {distance} instructions away, "
                f"more than the {_MAX_JUMP} classic BPF can encode"
            )
        return distance


def _verdict(base: int, code: int = 0) -> int:
    """Combine a seccomp return action with its data field."""
    return base | (code & SECCOMP_RET_DATA)


def build_filter() -> SeccompProgram:
    """Assemble the sandbox seccomp filter (x86_64 only).

    Raises SeccompFilterError on an architecture this filter has not been
    written for; callers check SUPPORTED first and skip the filter with a
    note rather than refusing to run the program.
    """
    if not SUPPORTED:
        raise SeccompFilterError(
            f"no verified seccomp filter for architecture {platform.machine()!r}; "
            f"only {_SUPPORTED_ARCH} is supported"
        )

    asm = Assembler()

    # Refuse anything that is not the architecture this filter was written
    # for, including the x32 ABI.  Killing rather than returning an error is
    # right here: a mismatch means the filter's syscall numbers do not mean
    # what it thinks, so no verdict it could give would be trustworthy.
    asm.load_absolute(_OFFSET_ARCH)
    asm.jump_if_equal(AUDIT_ARCH_X86_64, then="arch_ok", otherwise="kill")
    asm.label("kill")
    asm.ret(_verdict(SECCOMP_RET_KILL_PROCESS))

    asm.label("arch_ok")
    asm.load_absolute(_OFFSET_NR)
    asm.jump_if_any_bit_set(X32_SYSCALL_BIT, then="kill_x32", otherwise="rules")
    asm.label("kill_x32")
    asm.ret(_verdict(SECCOMP_RET_KILL_PROCESS))

    asm.label("rules")
    for index, name in enumerate(_DENIED_EPERM):
        asm.label(f"deny_{index}")
        asm.jump_if_equal(_NR[name], then="eperm", otherwise=f"deny_{index + 1}")
    asm.label(f"deny_{len(_DENIED_EPERM)}")

    # clone3 cannot be inspected: its flags are in a struct in memory and
    # classic BPF cannot follow a pointer.  ENOSYS makes callers fall back to
    # clone, which the next rules do inspect.
    asm.jump_if_equal(_NR["clone3"], then="enosys", otherwise="flag_checks")

    asm.label("flag_checks")
    for index, name in enumerate(_FLAG_CHECKED):
        asm.jump_if_equal(
            _NR[name], then="check_newuser", otherwise=f"flag_{index + 1}"
        )
        asm.label(f"flag_{index + 1}")

    asm.label("allow")
    asm.ret(_verdict(SECCOMP_RET_ALLOW))

    # Out of line, reached only by a forward jump from the rules above.
    asm.label("check_newuser")
    asm.load_absolute(_OFFSET_ARG0_LOW)
    asm.jump_if_any_bit_set(CLONE_NEWUSER, then="eperm", otherwise="allow_after_check")
    asm.label("allow_after_check")
    asm.ret(_verdict(SECCOMP_RET_ALLOW))

    asm.label("eperm")
    asm.ret(_verdict(SECCOMP_RET_ERRNO, errno.EPERM))

    asm.label("enosys")
    asm.ret(_verdict(SECCOMP_RET_ERRNO, errno.ENOSYS))

    return asm.assemble()


def _load_libc() -> ctypes.CDLL:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


_libc = _load_libc()


def install(program: SeccompProgram) -> None:
    """Install a pre-built filter on the calling process.

    PR_SET_NO_NEW_PRIVS must already be set: the kernel refuses the filter
    otherwise unless the caller holds CAP_SYS_ADMIN.  The filter is inherited
    across fork and execve, so installing it once before handing control to
    the student's program covers every process it goes on to start.

    Safe to call between fork and exec: no imports, no Python allocation
    beyond the exception on failure.
    """
    ctypes.set_errno(0)
    result = _libc.syscall(
        ctypes.c_long(_SYS_SECCOMP_X86_64),
        ctypes.c_long(SECCOMP_SET_MODE_FILTER),
        ctypes.c_long(0),
        ctypes.c_long(program.address()),
    )
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(
            code, f"seccomp(SECCOMP_SET_MODE_FILTER) failed: {os_strerror(code)}"
        )


def os_strerror(code: int) -> str:
    """errno name and text, e.g. "EPERM (Operation not permitted)"."""
    return f"{errno.errorcode.get(code, code)} ({os.strerror(code)})"
