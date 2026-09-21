"""
Integration tests for the sandbox and the two-phase test runner.

Everything here runs ./autotest.py as a subprocess (the interface students
and wrappers use) except the unit tests of autotest.decide_sandbox.  Tests
which need a working sandbox are skipped where the host can not create one,
so the suite still passes on CI runners without unprivileged user
namespaces.
"""

import glob
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import types

import conftest

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import sandbox  # noqa: E402

# the host is probed once, in conftest, so every module makes the same
# skip decision from the same configuration
SANDBOX_UNAVAILABLE = conftest.SANDBOX_UNAVAILABLE
needs_sandbox = conftest.needs_sandbox

# fixtures under tests/ which work without dcc
FIXTURES = [
    "arguments",
    "checker",
    "environment",
    "expected_output",
    "f-strings",
    "ignore",
    "limits",
    "multi-file-simple",
    "non_unicode_stdout",
    "non_unicode_stderr",
    "non_unicode_stdin",
    "non_unicode_file_output",
    "shell",
    "show_parameters",
]

# fixtures whose output legitimately differs from main, and why
KNOWN_DIFFERENCES_FROM_MAIN = {
    # main copied the submission over the files supplied by the autotest,
    # so it compiled the submission's deliberately broken a.c; supplied
    # files now win (see README "Changes in behaviour")
    "multi-file-simple": r" tests passed 0 tests failed *$",
    # main prints a stray "event loop is closed" line after this fixture;
    # the branch output is otherwise stable and ends with the normal summary
    "limits": r"1 tests passed 6 tests failed *$",
}


def environment():
    """the environment every autotest run here gets (tests/environment needs the variable)"""
    env = dict(os.environ)
    env["SAMPLE_ENVIRONMENT_VARIABLE"] = "sample_value"
    return env


def run_autotest(*arguments, cwd=REPO_ROOT, timeout=60):
    """run ./autotest.py from cwd returning (combined stdout+stderr, exit status)"""
    p = subprocess.run(
        [sys.executable, "./autotest.py"] + list(arguments),
        cwd=cwd,
        env=environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    return p.stdout, p.returncode


def run_fixture(name, *arguments, cwd=REPO_ROOT):
    return run_autotest(
        "-D", f"tests/{name}", "-a", f"tests/{name}/autotest", *arguments, cwd=cwd
    )[0]


def normalise(output):
    """
    remove what legitimately varies between runs:

    the pid in bash's "line 1: 12345 File size limit exceeded" message
    (inside the sandbox's pid namespace it is small and stable, on main it
    is the host pid), and which of /bin/bash and /usr/bin/bash names the
    shell in it.  Both are the same binary on a usr-merged system; they
    differ because shutil.which() sees a different PATH on the two trees,
    main having replaced its own environment with the test's before
    resolving it.  Which path that returns is not behaviour worth pinning.
    """
    output = re.sub(r"(line \d+:) +\d+ ", r"\1 PID ", output)
    return re.sub(r"^/(usr/)?bin/(bash|sh):", r"/SHELL:", output, flags=re.MULTILINE)


# the comparison against main is the suite's broadest oracle, so a runner
# which has not fetched the default branch must say so rather than quietly
# dropping 16 tests; .github/workflows/pytest.yml sets this because its
# checkout uses fetch-depth: 0
REQUIRE_MAIN = "AUTOTEST_REQUIRE_MAIN_COMPARISON"
MAIN_REFS = ("main", "origin/main", "refs/remotes/origin/main")


@pytest.fixture(scope="session")
def main_checkout(tmp_path_factory):
    """a pristine extraction of the main branch (git archive), or a skip"""
    directory = tmp_path_factory.mktemp("main")
    problems = []
    for ref in MAIN_REFS:
        try:
            archive = subprocess.run(
                ["git", "archive", ref],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=True,
            ).stdout
            break
        except (OSError, subprocess.CalledProcessError) as e:
            problems.append(f"{ref}: {e}")
    else:
        message = "can not extract main: " + "; ".join(problems)
        if os.environ.get(REQUIRE_MAIN):
            pytest.fail(f"{message} (with {REQUIRE_MAIN} set)")
        pytest.skip(message)
    subprocess.run(["tar", "-x", "-C", str(directory)], input=archive, check=True)
    return str(directory)


def write_autotest(directory, tests_txt, files=None, supplied=None):
    """
    create a submission directory containing files and its autotest
    directory containing tests.txt and supplied; return (directory, autotest)
    """
    directory = str(directory)
    autotest = os.path.join(directory, "autotest")
    os.makedirs(autotest)
    with open(os.path.join(autotest, "tests.txt"), "w") as f:
        f.write(tests_txt)
    for name, contents in (files or {}).items():
        write_file(os.path.join(directory, name), contents)
    for name, contents in (supplied or {}).items():
        write_file(os.path.join(autotest, name), contents)
    return directory, autotest


def write_file(path, contents):
    with open(path, "w") as f:
        f.write(contents)
    if contents.startswith("#!"):
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def c_compiler_available():
    return any(shutil.which(c) for c in ("dcc", "clang", "gcc"))


# ---- serial output is identical to main and to a parallel run


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_output_is_the_same_serial_and_parallel(name):
    serial = run_fixture(name, "-j", "1")
    parallel = run_fixture(name, "-j", "4")
    # normalise: bash names the pid of the process a limit killed, and
    # without a sandbox (no pid namespace) that is the host pid
    assert normalise(serial) == normalise(parallel)


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_output_is_the_same_as_main(name, main_checkout):
    if not os.path.isdir(os.path.join(main_checkout, "tests", name)):
        pytest.skip(f"tests/{name} does not exist on main")
    ours = run_fixture(name, "-j", "1")
    theirs = run_fixture(name, cwd=main_checkout)
    if name in KNOWN_DIFFERENCES_FROM_MAIN:
        assert re.search(
            KNOWN_DIFFERENCES_FROM_MAIN[name], ours, flags=re.MULTILINE
        ), ours
        assert ours != theirs
        return
    assert normalise(ours) == normalise(theirs)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("ignore", "ignore_case expected_stdout='hello WORLD!\\n'"),
        # non-unicode streams are written as bytearray literals, as always
        ("non_unicode_stdin", "test_stdin expected_stdout=bytearray(b'U')"),
    ],
)
def test_generate_expected_output_is_the_same_as_main(main_checkout, name, expected):
    arguments = ("-D", f"tests/{name}", "-a", f"tests/{name}/autotest", "-g")
    ours, status = run_autotest(*arguments)
    theirs, _ = run_autotest(*arguments, cwd=main_checkout)
    assert status == 0, ours
    assert expected in ours
    assert ours == theirs


# ---- the meta-autotests: tests/<name>/tests.txt runs autotest on itself
#
# These five directories are the only tests of autotest written as
# autotests, and scripts/do_tests.sh was the only thing running them.
# Only checker is listed: dcc_output_checking and infer_files need dcc,
# and limits and show_parameters have expected output which is stale and
# host-dependent (it names /bin/sh where bash is used, carries the pid in
# bash's "File size limit exceeded" line, and limits' autotest has gained
# a test its expected output does not have).  Both of those fail the same
# way on main, so they are left to do_tests.sh rather than fixed here.

META_AUTOTESTS = ["checker"]

SHEBANG_INTERPRETER = "/usr/bin/python3"


def shebang_interpreter_can_import_termcolor():
    """Whether a meta-autotest can run the autotest.py it is testing.

    A meta-autotest's program is the copy of autotest.py under test, so it
    is started through its own shebang, `/usr/bin/python3 -I`.  -I ignores
    PYTHONPATH and the user site directory, so no virtualenv can supply
    termcolor: that interpreter must have it installed.
    """
    try:
        return (
            subprocess.run(
                [SHEBANG_INTERPRETER, "-I", "-c", "import termcolor"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
    except OSError:
        return False


@pytest.mark.parametrize("name", META_AUTOTESTS)
def test_meta_autotest_of_autotest_itself_passes(name):
    if not shebang_interpreter_can_import_termcolor():
        pytest.skip(f"{SHEBANG_INTERPRETER} -I has no termcolor")
    directory = os.path.join(REPO_ROOT, "tests", name)
    sources = sorted(glob.glob(os.path.join(REPO_ROOT, "*.py")))
    # --no_sandbox on the outer run: the inner autotest.py is started
    # through its own shebang, so the sandbox would have to hold that
    # interpreter, which is true of /usr/bin/python3 but not of a
    # virtualenv under a directory the sandbox does not mount.  The inner
    # run's sandbox decision is the one these specifications are about.
    p = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO_ROOT, "autotest.py"),
            "-a",
            ".",
            "--no_sandbox",
        ]
        + sources,
        cwd=directory,
        env=environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        encoding="utf-8",
        errors="replace",
    )
    assert re.search(r" tests passed 0 tests failed *$", p.stdout), p.stdout


# ---- the sandbox decision


def namespace(**kwargs):
    kwargs.setdefault("debug", 0)
    return types.SimpleNamespace(**kwargs)


def test_decide_sandbox_required_but_unavailable_raises():
    import autotest

    args = namespace()
    with pytest.raises(sandbox.SandboxUnavailable) as e:
        autotest.decide_sandbox({"sandbox": True}, args, probe=lambda config: "nope")
    assert str(e.value) == "sandbox required (sandbox=True) but unavailable: nope"


def test_decide_sandbox_auto_but_unavailable_warns_once(capsys):
    import autotest

    args = namespace()
    result = autotest.decide_sandbox(
        {"sandbox": "auto"}, args, probe=lambda config: "nope"
    )
    assert result is None
    assert args.sandbox_config is None
    captured = capsys.readouterr()
    assert captured.err.count("running WITHOUT a sandbox: nope") == 1
    assert captured.out == ""


def test_decide_sandbox_off_does_not_probe(capsys):
    import autotest

    def probe(config):
        raise AssertionError("probe called with sandbox=False")

    args = namespace()
    assert autotest.decide_sandbox({"sandbox": False}, args, probe=probe) is None
    assert args.sandbox_config is None
    assert capsys.readouterr().err == ""


def test_decide_sandbox_available_sets_config(capsys):
    import autotest

    args = namespace()
    parameters = {"sandbox": "auto", "sandbox_network": False}
    config = autotest.decide_sandbox(parameters, args, probe=lambda config: None)
    assert isinstance(config, sandbox.SandboxConfig)
    assert args.sandbox_config is config
    assert config.network is False
    assert capsys.readouterr().err == ""


def test_decide_sandbox_probe_raising_an_autotest_exception_reports_its_message(
    capsys,
):
    import autotest
    from util import AutotestException

    def probe(config):
        raise AutotestException("probe broke")

    args = namespace()
    assert autotest.decide_sandbox({"sandbox": "auto"}, args, probe=probe) is None
    assert "running WITHOUT a sandbox: probe broke" in capsys.readouterr().err


def test_decide_sandbox_probe_raising_anything_else_means_no_sandbox_module(capsys):
    import autotest

    def probe(config):
        raise RuntimeError("no ctypes")

    args = namespace()
    assert autotest.decide_sandbox({"sandbox": "auto"}, args, probe=probe) is None
    assert (
        "running WITHOUT a sandbox: sandbox module unavailable: no ctypes"
        in capsys.readouterr().err
    )


PROBE_SCRIPT = """#!/bin/sh
ls /home
cat $HOME/.bashrc
touch /usr/x
python3 -c 'import socket; socket.create_connection(("1.1.1.1", 53), 2)'
"""


@needs_sandbox
def test_sandbox_is_effective_by_default(tmp_path):
    directory, _ = write_autotest(
        tmp_path,
        'files=probe.sh\nprogram=./probe.sh\nprobe expected_stdout=""\n',
        files={"probe.sh": PROBE_SCRIPT},
    )
    output, status = run_autotest("-D", directory, "-a", directory + "/autotest")
    assert status == 1, output
    assert "cannot access '/home'" in output, output
    assert ".bashrc: No such file or directory" in output, output
    assert "Read-only file system" in output, output
    assert "Network is unreachable" in output, output
    assert "running WITHOUT a sandbox" not in output


@needs_sandbox
def test_no_sandbox_flag_runs_unconfined(tmp_path):
    directory, _ = write_autotest(
        tmp_path,
        'files=probe.sh\nprogram=./probe.sh\nprobe expected_stdout=""\n',
        files={"probe.sh": PROBE_SCRIPT},
    )
    output, _ = run_autotest(
        "-D", directory, "-a", directory + "/autotest", "--no_sandbox"
    )
    assert "cannot access '/home'" not in output, output
    assert "Read-only file system" not in output, output


@needs_sandbox
def test_sandbox_required_stops_cleanly_when_unavailable(tmp_path):
    # an impossible read-write mount makes every sandbox fail
    directory, _ = write_autotest(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\nsandbox=1\nsandbox_read_write_mount=["/nonexistent/x"]\n'
        '1 expected_stdout="hi\\n"\n',
        files={"a.sh": "#!/bin/sh\necho hi\n"},
    )
    output, status = run_autotest("-D", directory, "-a", directory + "/autotest")
    assert status == 2, output
    assert "sandbox required (sandbox=True) but unavailable:" in output
    assert "Traceback" not in output


# ---- compilation


@pytest.mark.skipif(not c_compiler_available(), reason="no C compiler")
def test_tests_sharing_a_compile_command_compile_once(tmp_path):
    directory, _ = write_autotest(
        tmp_path,
        "files=hello.c\n"
        '1 command=./hello expected_stdout="hello\\n"\n'
        '2 command="./hello" expected_stdout="hello\\n"\n'
        '3 arguments=x expected_stdout="hello\\n"\n',
        files={"hello.c": '#include <stdio.h>\nint main(void){puts("hello");}\n'},
    )
    for jobs in ("1", "4"):
        output, status = run_autotest(
            "-D", directory, "-a", directory + "/autotest", "-j", jobs
        )
        assert status == 0, output
        compile_lines = [
            line
            for line in output.splitlines()
            if re.search(r"-o hello hello\.c$", line)
        ]
        assert len(compile_lines) == 1, output
        assert "3 tests passed 0 tests failed" in output


@pytest.mark.skipif(not c_compiler_available(), reason="no C compiler")
def test_files_glob_with_c_submission_compiles(tmp_path):
    directory, _ = write_autotest(
        tmp_path,
        'files=hello.*\nprogram=hello\n1 command=./hello expected_stdout="hello\\n"\n',
        files={"hello.c": '#include <stdio.h>\nint main(void){puts("hello");}\n'},
    )
    output, status = run_autotest("-D", directory, "-a", directory + "/autotest")
    assert status == 0, output
    assert "1 tests passed 0 tests failed" in output
    assert re.search(
        r"^(dcc|clang|gcc).* -o hello hello\.c$", output, flags=re.MULTILINE
    ), output


# ---- failure reporting


def test_same_failure_is_reported_as_same_as_first_test(tmp_path):
    directory, _ = write_autotest(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\n1 expected_stdout="x\\n"\n2 expected_stdout="x\\n"\n',
        files={"a.sh": "#!/bin/sh\necho y\n"},
    )
    for jobs in ("1", "4"):
        output, status = run_autotest(
            "-D", directory, "-a", directory + "/autotest", "-j", jobs
        )
        assert status == 1
        assert "Test 1 (./a.sh) - failed (Incorrect output)\n" in output, output
        assert (
            "Test 2 (./a.sh) - failed (Incorrect output - same as Test 1)\n" in output
        ), output


def test_postprocess_output_command_gets_the_text_it_filters(tmp_path):
    directory, _ = write_autotest(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\npostprocess_output_command="tr a-z A-Z"\n'
        '1 expected_stdout="HELLO\\n"\n',
        files={"a.sh": "#!/bin/sh\necho hello\n"},
    )
    output, status = run_autotest("-D", directory, "-a", directory + "/autotest")
    assert status == 0, output


def test_failing_postprocess_output_command_is_an_internal_error(tmp_path):
    directory, _ = write_autotest(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\npostprocess_output_command="false"\n'
        '1 expected_stdout="hello\\n"\n',
        files={"a.sh": "#!/bin/sh\necho hello\n"},
    )
    output, status = run_autotest("-D", directory, "-a", directory + "/autotest")
    assert status == 2, output
    assert "non-zero exit status from postprocess_output_command" in output


# ---- resource limits inside the sandbox


@pytest.mark.parametrize("extra", [(), ("--no_sandbox",)])
def test_real_time_limit_leaves_no_orphans(tmp_path, extra):
    if not extra and SANDBOX_UNAVAILABLE:
        pytest.skip(f"sandbox unavailable: {SANDBOX_UNAVAILABLE}")
    seconds = conftest.unique_sleep(9)
    directory, _ = write_autotest(
        tmp_path,
        "files=a.sh\nprogram=./a.sh\nmax_real_seconds=1\n"
        f'1 command="sh -c \'sleep {seconds} & wait\'" expected_stdout=""\n',
        files={"a.sh": "#!/bin/sh\n"},
    )
    start = time.time()
    output, status = run_autotest(
        "-D", directory, "-a", directory + "/autotest", *extra
    )
    assert time.time() - start < 20, output
    assert status == 1, output
    assert "failed" in output
    survivors = conftest.sleep_survivors(seconds)
    assert survivors == [], survivors


# ---- legacy hooks


def test_submission_planted_compile_sh_is_not_run(tmp_path):
    directory, _ = write_autotest(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\n1 expected_stdout="hi\\n"\n',
        files={
            "a.sh": "#!/bin/sh\necho hi\n",
            "compile.sh": "#!/bin/sh\necho PLANTED\nexit 1\n",
            "runtests.pl": "#!/bin/sh\necho PLANTED\nexit 0\n",
        },
    )
    output, status = run_autotest("-D", directory, "-a", directory + "/autotest")
    assert status == 0, output
    assert "PLANTED" not in output
    assert "1 tests passed 0 tests failed" in output


def test_supplied_compile_sh_is_run(tmp_path):
    directory, _ = write_autotest(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\n1 expected_stdout="hi\\n"\n',
        files={"a.sh": "#!/bin/sh\necho hi\n"},
        supplied={"compile.sh": "#!/bin/sh\necho SUPPLIED $*\nexit 0\n"},
    )
    output, status = run_autotest("-D", directory, "-a", directory + "/autotest")
    assert status == 0, output
    assert "SUPPLIED ./a.sh\n" in output, output
    assert "1 tests passed 0 tests failed" in output


@needs_sandbox
def test_sandbox_debug_output_does_not_reach_the_tested_program(tmp_path):
    """-ddd makes the sandbox report its progress; that must go to autotest's
    stderr, not the command's, which the test compares"""
    directory, _ = write_autotest(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\n1 expected_stdout="hi\\n"\n2 expected_stdout="hi\\n"\n',
        files={"a.sh": "#!/bin/sh\necho hi\n"},
    )
    output, status = run_autotest(
        "-D", directory, "-a", directory + "/autotest", "-ddd"
    )
    assert status == 0, output
    assert "2 tests passed 0 tests failed" in output
    assert "sandbox: namespaces created" in output


@needs_sandbox
def test_submission_symlink_is_not_followed(tmp_path):
    """a link in a submission to a file outside it must not copy that file's
    contents in: the copy is made as the user running autotest, outside the
    sandbox, so it could read anything that user can"""
    private = tmp_path / "private.txt"
    private.write_text("MARKER-PRIVATE\n")
    private.chmod(0o600)
    directory, _ = write_autotest(
        tmp_path / "submission",
        'files=a.sh\nprogram=./a.sh\n1 expected_stdout="x\\n"\n',
        files={"a.sh": "#!/bin/sh\ncat data.txt\n"},
    )
    os.symlink(str(private), os.path.join(directory, "data.txt"))
    output, status = run_autotest("-D", directory, "-a", directory + "/autotest")
    assert status == 1, output
    assert "MARKER-PRIVATE" not in output, output
