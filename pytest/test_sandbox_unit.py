"""
Tests of the sandbox code which runs in the parent: mount validation, the
configuration built from a test's parameters, probe()'s explanation of a
failure, and the assembled seccomp filter.

Nothing here builds a sandbox, so these run on every host - including the
CI runners and teaching machines where unprivileged user namespaces are
disabled and test_sandbox.py is skipped entirely.  The sandbox's effect on
a running command is tested in test_sandbox.py, the sandbox_* parameters in
test_sandbox_config.py.
"""

import errno
import os
import re
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sandbox
import sandbox_seccomp

# ---- mount validation
#
# A mount list comes from tests.txt, so a malformed one is an autotest
# author's mistake and must be reported rather than silently mounted
# somewhere unintended.  These paths are a security boundary: the checks
# are pinned individually because each is a separate way of getting a
# mount where it was not meant to go.


def test_mount_pair_of_the_wrong_length_is_rejected():
    config = sandbox.SandboxConfig(read_only_mounts=[("/a", "/b", "/c")])
    with pytest.raises(sandbox.SandboxError) as e:
        config.key()
    assert "sandbox_read_only_mount: expected (host_path, sandbox_path)" in str(e.value)


def test_relative_mount_path_is_rejected():
    # a relative path would resolve against a cwd which differs between the
    # parent and the sandbox child
    config = sandbox.SandboxConfig(read_write_mounts=["tmp/x"])
    with pytest.raises(sandbox.SandboxError) as e:
        config.key()
    assert "sandbox_read_write_mount: paths must be absolute" in str(e.value)


def test_relative_sandbox_side_of_a_mount_pair_is_rejected():
    config = sandbox.SandboxConfig(read_only_mounts=[("/usr", "usr")])
    with pytest.raises(sandbox.SandboxError) as e:
        config.key()
    assert "paths must be absolute" in str(e.value)


def test_mount_over_the_sandbox_root_is_rejected():
    config = sandbox.SandboxConfig(read_only_mounts=["/"])
    with pytest.raises(sandbox.SandboxError) as e:
        config.key()
    assert "cannot mount over the sandbox root" in str(e.value)


def test_mount_over_the_sandbox_root_by_a_path_which_normalises_to_it():
    # "/usr/.." is "/": the check is made after normalisation, not before
    config = sandbox.SandboxConfig(read_only_mounts=[("/usr", "/usr/..")])
    with pytest.raises(sandbox.SandboxError, match="cannot mount over"):
        config.key()


def test_valid_mounts_normalise_to_host_and_sandbox_pairs():
    config = sandbox.SandboxConfig(
        read_only_mounts=["/usr", ("/opt/x", "/opt/y/")],
        read_write_mounts=[],
    )
    _network, read_only, read_write, *_rest = config.key()
    assert read_only == (("/usr", "/usr"), ("/opt/x", "/opt/y"))
    assert read_write == ()


# ---- the configuration a test's parameters build


def test_config_from_parameters_defaults():
    config = sandbox.config_from_parameters({})
    assert config.network is True
    assert config.read_only_mounts == [
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
    assert config.read_write_mounts == []
    assert config.tmp_bytes == 268435456
    assert config.shm_bytes == 67108864
    assert config.seccomp is True
    assert config.landlock is True

    config = sandbox.config_from_parameters(
        {
            "sandbox_network": False,
            "sandbox_read_only_mount_base": ["/usr"],
            "sandbox_read_only_mount": ["/opt/extra"],
            "sandbox_read_write_mount": ["/scratch"],
            "sandbox_tmp_bytes": 1,
            "sandbox_shm_bytes": 2,
            "sandbox_seccomp": False,
            "sandbox_landlock": False,
        }
    )
    assert config.network is False
    assert config.read_only_mounts == ["/usr", "/opt/extra"]
    assert config.read_write_mounts == ["/scratch"]
    assert (config.tmp_bytes, config.shm_bytes) == (1, 2)
    assert config.seccomp is False and config.landlock is False


def test_probe_failure_without_a_namespace_denial_gets_no_advice():
    message = "sandbox: /bin/true exited with status 1"
    assert sandbox._explain_probe_failure(message) == message


def test_probe_explains_disabled_user_namespaces(monkeypatch):
    def fail(flags):
        raise OSError(errno.EPERM, "unshare(CLONE_NEWUSER) failed: EPERM")

    monkeypatch.setattr(sandbox, "_unshare", fail)
    monkeypatch.setattr(sandbox, "_probe_cache", {})
    reason = sandbox.probe(sandbox.SandboxConfig())
    assert reason is not None
    assert "unshare(CLONE_NEWUSER)" in reason
    assert "user namespaces" in reason
    assert "sandbox=False" in reason


# ---- the seccomp filter, as the kernel will read it
#
# The filter is classic BPF, so what it does to a given syscall can be
# worked out exactly without installing it.  Running it here is the only
# check that covers every rule: installing it and making the call reaches
# only the syscalls a test can safely make, and many of them would fail
# with EPERM anyway for want of a capability, which the filter's own EPERM
# is indistinguishable from.

seccomp_x86_64 = pytest.mark.skipif(
    not sandbox_seccomp.SUPPORTED, reason="the seccomp filter is x86_64 only"
)

# struct seccomp_data: int nr; __u32 arch; __u64 instruction_pointer; __u64 args[6]
_SECCOMP_DATA = struct.Struct("=iIQ6Q")

DENY = sandbox_seccomp.SECCOMP_RET_ERRNO | errno.EPERM
ENOSYS = sandbox_seccomp.SECCOMP_RET_ERRNO | errno.ENOSYS
ALLOW = sandbox_seccomp.SECCOMP_RET_ALLOW
KILL = sandbox_seccomp.SECCOMP_RET_KILL_PROCESS


def verdict(program, nr, arch=sandbox_seccomp.AUDIT_ARCH_X86_64, arg0=0):
    """What the assembled filter returns for one syscall.

    A classic-BPF interpreter for the four instructions the filter uses.
    Writing it here rather than reusing the assembler's own view means a
    wrong jump offset (the classic way to get a seccomp filter subtly
    wrong) shows up as a wrong verdict.
    """
    data = _SECCOMP_DATA.pack(nr, arch, 0, arg0, 0, 0, 0, 0, 0)
    instructions = program.instructions
    accumulator = 0
    pc = 0
    for _step in range(10 * len(instructions) + 10):
        instruction = instructions[pc]
        code, jt, jf, k = (
            instruction.code,
            instruction.jt,
            instruction.jf,
            instruction.k,
        )
        if (
            code
            == sandbox_seccomp.BPF_LD | sandbox_seccomp.BPF_W | sandbox_seccomp.BPF_ABS
        ):
            (accumulator,) = struct.unpack_from("=I", data, k)
        elif (
            code
            == sandbox_seccomp.BPF_JMP | sandbox_seccomp.BPF_JEQ | sandbox_seccomp.BPF_K
        ):
            pc += jt if accumulator == k else jf
        elif (
            code
            == sandbox_seccomp.BPF_JMP
            | sandbox_seccomp.BPF_JSET
            | sandbox_seccomp.BPF_K
        ):
            pc += jt if accumulator & k else jf
        elif code == sandbox_seccomp.BPF_RET | sandbox_seccomp.BPF_K:
            return k
        else:
            raise AssertionError(f"unknown BPF instruction {code:#x} at {pc}")
        pc += 1
    raise AssertionError("BPF program did not return")


# The syscalls the filter must refuse, written out here rather than taken
# from the module so that deleting one from _DENIED_EPERM fails this test
# instead of quietly shrinking it.  README "Security model" names setns,
# mount and the user-namespace check.
MUST_BE_DENIED = (
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
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    "name_to_handle_at",
    "open_by_handle_at",
    "syslog",
    "modify_ldt",
    "kcmp",
    "acct",
    "quotactl",
    "ioperm",
    "iopl",
)


def test_denied_syscall_list_is_exactly_the_one_this_file_pins():
    # a syscall added to the filter needs a verdict pinned here too
    assert sorted(sandbox_seccomp._DENIED_EPERM) == sorted(MUST_BE_DENIED)


@seccomp_x86_64
@pytest.mark.parametrize("name", MUST_BE_DENIED)
def test_denied_syscall_returns_eperm(name):
    program = sandbox_seccomp.build_filter()
    assert verdict(program, sandbox_seccomp._NR[name]) == DENY


@seccomp_x86_64
def test_ordinary_syscalls_are_allowed():
    program = sandbox_seccomp.build_filter()
    # read, write, openat, execve, fork, wait4: what a student's program does
    for number in (0, 1, 257, 59, 57, 61):
        assert verdict(program, number) == ALLOW, number


@seccomp_x86_64
def test_user_namespace_creation_is_denied_but_ordinary_cloning_is_not():
    program = sandbox_seccomp.build_filter()
    newuser = sandbox_seccomp.CLONE_NEWUSER
    for name in ("clone", "unshare"):
        number = sandbox_seccomp._NR[name]
        assert verdict(program, number, arg0=newuser) == DENY, name
        assert verdict(program, number, arg0=newuser | 0x100) == DENY, name
        assert verdict(program, number, arg0=0) == ALLOW, name
        # CLONE_VM|CLONE_FS|CLONE_FILES|CLONE_SIGHAND: a thread
        assert verdict(program, number, arg0=0xF00) == ALLOW, name


@seccomp_x86_64
def test_clone3_returns_enosys_so_callers_fall_back_to_clone():
    program = sandbox_seccomp.build_filter()
    assert verdict(program, sandbox_seccomp._NR["clone3"]) == ENOSYS


@seccomp_x86_64
def test_another_architecture_and_the_x32_abi_are_killed():
    program = sandbox_seccomp.build_filter()
    # AUDIT_ARCH_I386: the syscall numbers would mean something else
    assert verdict(program, 0, arch=0x40000003) == KILL
    x32 = sandbox_seccomp.X32_SYSCALL_BIT | sandbox_seccomp._NR["mount"]
    assert verdict(program, x32) == KILL


HEADER_CANDIDATES = (
    "/usr/include/x86_64-linux-gnu/asm/unistd_64.h",
    "/usr/include/asm/unistd_64.h",
)


@seccomp_x86_64
def test_syscall_numbers_match_this_host_s_kernel_headers():
    """A wrong number in a filter fails open and silently.

    The numbers in sandbox_seccomp are transcribed from the kernel's
    syscall_64.tbl; this checks the transcription against the same table as
    the C library sees it, where the headers are installed.
    """
    path = next((p for p in HEADER_CANDIDATES if os.path.exists(p)), None)
    if path is None:
        pytest.skip("kernel syscall headers are not installed")
    with open(path) as f:
        header = f.read()
    numbers = {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"^#define __NR_(\w+) (\d+)$", header, re.MULTILINE)
    }
    unknown = sorted(set(sandbox_seccomp._NR) - set(numbers))
    assert not unknown, f"not in {path}: {unknown}"
    wrong = {
        name: (number, numbers[name])
        for name, number in sandbox_seccomp._NR.items()
        if numbers[name] != number
    }
    assert not wrong, f"(ours, {path}): {wrong}"
