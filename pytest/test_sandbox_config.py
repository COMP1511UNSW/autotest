"""
Tests of the sandbox_* parameters as a tests.txt author uses them: each
one changes what a test's command can see or do, checked by running
./autotest.py on an exercise whose command probes the sandbox from inside.

Every test needs a working sandbox and is skipped where the host can not
build one.  The sandbox itself (namespaces, mounts, seccomp, Landlock) is
tested directly in test_sandbox.py; the sandbox decision (auto/required/
off) in test_integration.py.  Here only the wiring from parameters to the
running command is pinned.
"""

import os
import platform
import shutil

import pytest

pytestmark = pytest.mark.needs_sandbox

SH = "#!/bin/sh\n"
HEADER = "files=a.sh\nprogram=./a.sh\n"


def spec(parameters, command, expected_stdout=""):
    return (
        HEADER
        + "".join(f"{name}={value}\n" for name, value in parameters.items())
        + f'1 command="{command}" expected_stdout="{expected_stdout}"\n'
    )


def run(make_exercise, run_autotest, tmp_path, tests_txt, *extra):
    exercise = make_exercise(tmp_path, tests_txt, files={"a.sh": SH})
    return run_autotest(exercise.args + list(extra))


# ---- mounts


@pytest.fixture
def host_directory(tmp_path):
    """a directory outside the exercise with a file to look for"""
    directory = tmp_path / "host"
    directory.mkdir()
    (directory / "data.txt").write_text("DATA\n")
    return str(directory)


def test_host_directories_are_hidden_by_default(
    tmp_path, make_exercise, run_autotest, host_directory
):
    stdout, _, status = run(
        make_exercise,
        run_autotest,
        tmp_path,
        spec({}, f"cat {host_directory}/data.txt", "DATA\\n"),
    )
    assert status == 1, stdout
    assert f"cat: {host_directory}/data.txt: No such file or directory\n" in stdout


def test_read_write_mount_pair_binds_a_host_directory_at_another_path(
    tmp_path, make_exercise, run_autotest, host_directory
):
    stdout, stderr, status = run(
        make_exercise,
        run_autotest,
        tmp_path,
        spec(
            {"sandbox_read_write_mount": f'[("{host_directory}", "/mnt/data")]'},
            "cat /mnt/data/data.txt; echo written >/mnt/data/out.txt",
            "DATA\\n",
        ),
    )
    assert status == 0, stdout + stderr
    with open(os.path.join(host_directory, "out.txt")) as f:
        assert f.read() == "written\n"


def test_read_write_mount_plain_path_binds_a_host_directory_at_its_own_path(
    tmp_path, make_exercise, run_autotest, host_directory
):
    stdout, stderr, status = run(
        make_exercise,
        run_autotest,
        tmp_path,
        spec(
            {"sandbox_read_write_mount": f'["{host_directory}"]'},
            f"cat {host_directory}/data.txt; echo written >{host_directory}/out.txt",
            "DATA\\n",
        ),
    )
    assert status == 0, stdout + stderr
    with open(os.path.join(host_directory, "out.txt")) as f:
        assert f.read() == "written\n"


def test_read_only_mount_makes_a_host_directory_visible_but_not_writable(
    tmp_path, make_exercise, run_autotest
):
    # a directory which is not under /tmp (see the next test): the
    # repository's own test fixtures, which the sandbox otherwise hides
    repo_directory = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "shell"
    )
    stdout, stderr, status = run(
        make_exercise,
        run_autotest,
        tmp_path,
        spec(
            {"sandbox_read_only_mount": f'["{repo_directory}"]'},
            f"cat {repo_directory}/a.sh; touch {repo_directory}/x",
            "#!/bin/sh\\necho hello\\n",
        ),
    )
    assert status == 1, stdout + stderr
    assert "Read-only file system" in stdout, stdout
    assert not os.path.exists(os.path.join(repo_directory, "x"))


def test_read_only_mount_under_tmp_is_visible(
    tmp_path, make_exercise, run_autotest, host_directory
):
    if not host_directory.startswith("/tmp/"):
        pytest.skip("tmp_path is not under /tmp")
    stdout, stderr, status = run(
        make_exercise,
        run_autotest,
        tmp_path,
        spec(
            {"sandbox_read_only_mount": f'["{host_directory}"]'},
            f"cat {host_directory}/data.txt",
            "DATA\\n",
        ),
    )
    assert status == 0, stdout + stderr


# ---- network


def interface_count_spec(parameters):
    # /proc/net/dev has two header lines then one line per interface
    return spec(parameters, "wc -l </proc/net/dev", "3\\n")


def test_only_loopback_is_visible_by_default(tmp_path, make_exercise, run_autotest):
    stdout, stderr, status = run(
        make_exercise, run_autotest, tmp_path, interface_count_spec({})
    )
    assert status == 0, stdout + stderr


def test_sandbox_network_false_shows_the_host_interfaces(
    tmp_path, make_exercise, run_autotest
):
    stdout, stderr, status = run(
        make_exercise,
        run_autotest,
        tmp_path,
        interface_count_spec({"sandbox_network": "False"}),
    )
    assert status == 1, stdout + stderr
    interfaces = int(
        stdout.split("Your program produced this line of output:\n")[1].split()[0]
    )
    assert interfaces > 3


# ---- private /tmp and /dev/shm


@pytest.mark.parametrize(
    "parameter,path",
    [("sandbox_tmp_bytes", "/tmp/big"), ("sandbox_shm_bytes", "/dev/shm/big")],
)
def test_small_private_tmpfs_runs_out_of_space(
    tmp_path, make_exercise, run_autotest, parameter, path
):
    stdout, stderr, status = run(
        make_exercise,
        run_autotest,
        tmp_path,
        spec({parameter: 65536}, f"head -c 200000 /dev/zero >{path}"),
    )
    assert status == 1, stdout + stderr
    assert "No space left on device" in stdout
    # the file was written in a private tmpfs, not the host's
    assert not os.path.exists(path)


def test_default_private_tmpfs_has_room(tmp_path, make_exercise, run_autotest):
    stdout, stderr, status = run(
        make_exercise,
        run_autotest,
        tmp_path,
        spec(
            {},
            "head -c 200000 /dev/zero >/tmp/big && head -c 200000 /dev/zero >/dev/shm/big && echo ok",
            "ok\\n",
        ),
    )
    assert status == 0, stdout + stderr


# ---- seccomp and Landlock


@pytest.mark.needs_tools("unshare")
def test_sandbox_seccomp_false_allows_a_nested_user_namespace(
    tmp_path, make_exercise, run_autotest
):
    stdout, stderr, status = run(
        make_exercise,
        run_autotest,
        tmp_path,
        spec(
            {"sandbox_seccomp": "False"},
            "unshare -Ur true && echo unshared",
            "unshared\\n",
        ),
    )
    assert status == 0, stdout + stderr


@pytest.mark.needs_tools("unshare")
@pytest.mark.skipif(
    platform.machine() != "x86_64", reason="the seccomp filter is x86_64 only"
)
def test_nested_user_namespace_is_denied_by_default(
    tmp_path, make_exercise, run_autotest
):
    stdout, stderr, status = run(
        make_exercise,
        run_autotest,
        tmp_path,
        spec({}, "unshare -Ur true && echo unshared", "unshared\\n"),
    )
    assert status == 1, stdout + stderr
    assert "unshare failed: Operation not permitted" in stdout


def test_sandbox_landlock_false_is_noted_in_debug_output(
    tmp_path, make_exercise, run_autotest
):
    stdout, stderr, status = run(
        make_exercise,
        run_autotest,
        tmp_path,
        spec({"sandbox_landlock": "False"}, "echo x", "x\\n"),
        "-dd",
    )
    assert status == 0, stdout + stderr
    assert "Landlock disabled (sandbox_landlock=False)" in stderr
    assert "sandbox notes: Landlock disabled (sandbox_landlock=False)" in stderr


def test_sandbox_seccomp_false_is_noted_once_in_debug_output(
    tmp_path, make_exercise, run_autotest
):
    stdout, stderr, status = run(
        make_exercise,
        run_autotest,
        tmp_path,
        HEADER + "sandbox_seccomp=False\n"
        '1 command="echo x" expected_stdout="x\\n"\n'
        '2 command="echo x" expected_stdout="x\\n"\n',
        "-dd",
    )
    assert status == 0, stdout + stderr
    assert stderr.count("sandbox notes:") == 1
    assert "sandbox notes: seccomp filter disabled (sandbox_seccomp=False)" in stderr


def test_no_notes_line_when_every_backstop_is_in_use(
    tmp_path, make_exercise, run_autotest
):
    stdout, stderr, status = run(
        make_exercise, run_autotest, tmp_path, spec({}, "echo x", "x\\n"), "-dd"
    )
    assert status == 0, stdout + stderr
    assert "sandbox: on SandboxConfig(" in stderr
    if (
        "Landlock unavailable" not in stderr
        and "seccomp filter unavailable" not in stderr
    ):
        assert "sandbox notes:" not in stderr


# ---- support commands


def test_sandbox_support_commands_false_runs_the_compiler_on_the_host(
    tmp_path, make_exercise, run_autotest
):
    """
    with sandbox_support_commands=False a compiler sees the host (here: a
    marker file the sandbox would hide) while the test command still does not
    """
    marker = tmp_path / "marker.txt"
    marker.write_text("HOST_VISIBLE\n")
    compiler = (
        f"#!/bin/sh\ncat {marker}\nprintf '#!/bin/sh\\ntrue\\n' >$1\nchmod +x $1\n"
    )
    tests_txt = (
        "files=a.c\nsandbox_support_commands=False\n"
        'compile_commands=["./cc.sh a"]\n'
        f'1 command="cat {marker}" expected_stdout=""\n'
    )
    exercise = make_exercise(
        tmp_path, tests_txt, files={"a.c": ""}, supplied={"cc.sh": compiler}
    )
    stdout, stderr, status = run_autotest(exercise.args)
    assert status == 1, stdout + stderr
    assert stdout.startswith("./cc.sh a a.c\nHOST_VISIBLE\n")
    assert f"cat: {marker}: No such file or directory" in stdout


def test_support_commands_are_sandboxed_by_default(
    tmp_path, make_exercise, run_autotest
):
    marker = tmp_path / "marker.txt"
    marker.write_text("HOST_VISIBLE\n")
    compiler = (
        f"#!/bin/sh\ncat {marker}\nprintf '#!/bin/sh\\ntrue\\n' >$1\nchmod +x $1\n"
    )
    tests_txt = 'files=a.c\ncompile_commands=["./cc.sh a"]\n1 command="./a" expected_stdout=""\n'
    exercise = make_exercise(
        tmp_path, tests_txt, files={"a.c": ""}, supplied={"cc.sh": compiler}
    )
    stdout, stderr, status = run_autotest(exercise.args)
    assert status == 0, stdout + stderr
    assert "HOST_VISIBLE" not in stdout
    assert stdout.startswith(
        f"./cc.sh a a.c\ncat: {marker}: No such file or directory\n"
    )


# ---- sandbox=True and what is left behind


def test_sandbox_required_runs_normally_where_the_sandbox_works(
    tmp_path, make_exercise, run_autotest
):
    stdout, stderr, status = run(
        make_exercise,
        run_autotest,
        tmp_path,
        spec({"sandbox": "True"}, "echo x", "x\\n"),
    )
    assert status == 0, stdout + stderr
    assert "running WITHOUT a sandbox" not in stderr
    assert stdout.endswith("1 tests passed 0 tests failed \n")


def test_sandbox_root_directories_are_never_left_behind(
    tmp_path, make_exercise, run_autotest
):
    """
    each command gets an empty mountpoint directory (.sandbox-root-*) next
    to the test directories; it is removed as soon as the command finishes,
    so even the temporary tree kept by debug level 10 has none
    """
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    exercise = make_exercise(
        tmp_path,
        HEADER + '1 command="echo x" expected_stdout="x\\n"\n'
        '2 command="echo y" expected_stdout="y\\n"\n',
        files={"a.sh": SH},
    )
    stdout, stderr, status = run_autotest(
        exercise.args + ["-" + "d" * 10], env=dict(os.environ, TMPDIR=str(tmpdir))
    )
    assert status == 0, stdout + stderr
    (kept,) = os.listdir(tmpdir)
    contents = os.listdir(tmpdir / kept)
    assert sum(name.startswith(".test-") for name in contents) == 2, contents
    assert not [name for name in contents if name.startswith(".sandbox-root-")]
    shutil.rmtree(tmpdir / kept)
