"""
Regression tests for bugs fixed in parameter handling, the command line,
temporary directory handling, result upload and interrupt handling.

Unit tests import the autotest modules directly (the repo root is added to
sys.path the same way autotest.py does); the rest run ./autotest.py as a
subprocess because that is the interface students and wrappers use.
"""

import io
import os
import shutil
import subprocess
import sys
import types
import zipfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from parameter_descriptions import value_to_bool  # noqa: E402
from parse_test_specification import parse_string  # noqa: E402
from upload_results import zip_files_for_upload  # noqa: E402

INITIAL_PARAMETERS = {"supplied_files_directory": "."}


def parse(specification, **initial_parameters):
    """parse a tests.txt string returning (tests, global_parameters)"""
    parameters = dict(INITIAL_PARAMETERS)
    parameters.update(initial_parameters)
    return parse_string(specification, initial_parameters=parameters)


def run_autotest(*arguments, timeout=30, env=None):
    """run ./autotest.py from the repo root with these arguments"""
    return subprocess.run(
        [sys.executable, "./autotest.py"] + list(arguments),
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        timeout=timeout,
        encoding="utf-8",
    )


# item 1


# item 2


def test_string_compiler_gets_a_space_before_computed_compiler_args():
    tests, _ = parse('files=a.c\ncompilers=["gcc -Wall"]\nt command="./a"\n')
    assert tests["t"]["compile_commands"] == ["gcc -Wall -o a"]


def test_string_compiler_with_no_compiler_args_is_unchanged():
    tests, _ = parse('files=a.c\ncompilers=["gcc -Wall -o a"]\nt command="./a"\n')
    assert tests["t"]["compile_commands"] == ["gcc -Wall -o a"]


# item 3


# item 4


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0",
        "0.5",
        "f",
        "F",
        "false",
        "False",
        "FALSE",
        "n",
        "N",
        "no",
        "No",
        "NO",
        "off",
        "Off",
        "OFF",
        "None",
    ],
)
def test_value_to_bool_false_values(value):
    assert value_to_bool(value) is False


@pytest.mark.parametrize(
    "value", ["1", "yes", "Y", "true", "on", "auto", "required", " 0", "other"]
)
def test_value_to_bool_true_strings(value):
    assert value_to_bool(value) is True


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, False),
        (1, True),
        (None, False),
        ([], False),
        ([1], True),
        (True, True),
        (False, False),
    ],
)
def test_value_to_bool_non_strings(value, expected):
    assert value_to_bool(value) is expected


def test_boolean_parameter_no_is_false():
    tests, _ = parse('files=a.c\nt command="./a" show_stdin=no\n')
    assert tests["t"]["show_stdin"] is False


# item 5


# item 7


@pytest.mark.parametrize(
    "value,expected",
    [
        ("auto", "auto"),
        ("AUTO", "auto"),
        (" Auto ", "auto"),
        ("required", True),
        ("yes", True),
        ("1", True),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_sandbox_parameter_is_normalised(value, expected):
    _, parameters = parse(f'sandbox={value}\nfiles=a.c\nt command="./a"\n')
    assert parameters["sandbox"] == expected
    assert type(parameters["sandbox"]) is type(expected)


def test_sandbox_defaults_to_auto():
    _, parameters = parse('files=a.c\nt command="./a"\n')
    assert parameters["sandbox"] == "auto"


def test_sandbox_true_from_initial_parameters():
    _, parameters = parse('files=a.c\nt command="./a"\n', sandbox=True)
    assert parameters["sandbox"] is True


def test_sandbox_false_from_initial_parameters():
    _, parameters = parse('files=a.c\nt command="./a"\n', sandbox=False)
    assert parameters["sandbox"] is False


def test_parallel_tests_is_coerced_to_int():
    _, parameters = parse('parallel_tests="4"\nfiles=a.c\nt command="./a"\n')
    assert parameters["parallel_tests"] == 4
    assert isinstance(parameters["parallel_tests"], int)


def test_parallel_tests_defaults_to_one():
    _, parameters = parse('files=a.c\nt command="./a"\n')
    assert parameters["parallel_tests"] == 1


def test_sandbox_parameter_defaults():
    _, parameters = parse('files=a.c\nt command="./a"\n')
    assert parameters["sandbox_support_commands"] is True
    assert parameters["sandbox_tmp_bytes"] == 268435456
    assert parameters["sandbox_shm_bytes"] == 67108864
    assert parameters["sandbox_seccomp"] is True
    assert parameters["sandbox_landlock"] is True
    assert parameters["sandbox_network"] is True
    assert "/opt" in parameters["sandbox_read_only_mount_base"]


def test_deprecated_sandbox_command_is_still_accepted():
    # old wrappers set it: it must not be an unknown parameter
    _, parameters = parse('sandbox_command=["unshare"]\nfiles=a.c\nt command="./a"\n')
    assert parameters["sandbox_command"] == ["unshare"]


# item 8


def test_invalid_regex_extra_argument_gives_friendly_message():
    p = run_autotest("-a", "tests/ignore/autotest", "[")
    assert p.returncode == 2
    assert "unexpected argument '['" in p.stderr
    assert "internal error" not in p.stderr


# item 9


@pytest.mark.parametrize("arguments", [["--gitlab_cse"], ["--student", "z5555555"]])
def test_removed_cse_sources_die_with_message(arguments):
    p = run_autotest("-a", "tests/ignore/autotest", *arguments)
    assert p.returncode == 2
    assert "no longer supported, use --git URL" in p.stderr
    assert "internal error" not in p.stderr


def test_commit_without_git_dies_with_message():
    p = run_autotest("-a", "tests/ignore/autotest", "--commit", "abc123")
    assert p.returncode == 2
    assert "--git" in p.stderr
    assert "internal error" not in p.stderr


# item 10


@pytest.mark.parametrize(
    "flag,parameter",
    [
        ("--no_show_diff", "show_diff"),
        ("--no_show_input", "show_stdin"),
        ("--no_show_expected", "show_expected_output"),
        ("--no_show_actual", "show_actual_output"),
        ("--no_check_hash_bang_line", "check_hash_bang_line"),
        ("--no_fail_tests_for_errors", "allow_unexpected_stderr"),
        ("--colorize", "colorize_output"),
        ("--no_colorize", "colorize_output"),
        ("--no_show_reproduce_command", "show_reproduce_command"),
        ("--show_stdout_if_errors", "show_stdout_if_errors"),
        ("--no_style", "default_checkers"),
    ],
)
def test_obsolete_flag_dies_naming_replacement_parameter(flag, parameter):
    p = run_autotest("-a", "tests/ignore/autotest", flag)
    assert p.returncode == 2
    assert flag in p.stderr
    assert "-P" in p.stderr
    assert f"'{parameter}'" in p.stderr


@pytest.mark.parametrize(
    "flag,parameter",
    [
        ("--c_compilers", "default_compilers"),
        ("-C", "default_compilers"),
        ("--c_checkers", "default_checkers"),
        ("--ssh_upload_url", "upload_url"),
        ("--ssh_upload_max_bytes", "upload_max_bytes"),
    ],
)
def test_obsolete_flag_with_value_dies_naming_replacement_parameter(flag, parameter):
    p = run_autotest("-a", "tests/ignore/autotest", flag, "value")
    assert p.returncode == 2
    assert "-P" in p.stderr
    assert f"'{parameter}'" in p.stderr


# item 11


def test_inside_sandbox_flag_is_gone():
    p = run_autotest(
        "--inside_sandbox", "--print_test_names", "-a", "tests/ignore/autotest"
    )
    assert p.returncode != 0
    p = run_autotest("-I", "--print_test_names", "-a", "tests/ignore/autotest")
    assert p.returncode != 0


# item 12


def test_zip_for_upload_with_missing_supplied_files_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tests = {"t": types.SimpleNamespace(label="t", test_passed=True)}
    parameters = {
        "supplied_files_directory": str(tmp_path / "does_not_exist"),
        "upload_max_bytes": 2048000,
    }
    args = types.SimpleNamespace(file=set(), optional_files=set())
    buffer = io.BytesIO()
    zip_files_for_upload(buffer, tests, parameters, args)
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as zf:
        assert zf.namelist() == ["t.passed"]


# item 14a


def test_submission_can_not_replace_files_supplied_by_autotest(tmp_path):
    student_directory = tmp_path / "submission"
    student_directory.mkdir()
    shutil.copy(os.path.join(REPO_ROOT, "tests/checker/hello.sh"), student_directory)
    student_checker = student_directory / "checker.sh"
    student_checker.write_text("#!/bin/sh\necho STUDENT_CHECKER_MARKER\nexit 0\n")
    student_checker.chmod(0o755)
    p = run_autotest("-D", str(student_directory), "-a", "tests/checker/autotest")
    assert "STUDENT_CHECKER_MARKER" not in p.stdout + p.stderr
    assert "checker.sh hello.sh" in p.stdout


def test_read_only_submission_file_can_not_replace_supplied_file(tmp_path):
    student_directory = tmp_path / "submission"
    student_directory.mkdir()
    shutil.copy(os.path.join(REPO_ROOT, "tests/checker/hello.sh"), student_directory)
    student_checker = student_directory / "checker.sh"
    student_checker.write_text("#!/bin/sh\necho STUDENT_CHECKER_MARKER\nexit 0\n")
    student_checker.chmod(0o555)
    p = run_autotest("-D", str(student_directory), "-a", "tests/checker/autotest")
    assert "STUDENT_CHECKER_MARKER" not in p.stdout + p.stderr
    assert "checker.sh hello.sh" in p.stdout


# item 14b


def environment_with_tmpdir(tmpdir):
    env = dict(os.environ)
    env["TMPDIR"] = str(tmpdir)
    return env


def test_temp_directory_removed_when_tmpdir_set(tmp_path):
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    p = run_autotest(
        "-D",
        "tests/ignore",
        "-a",
        "tests/ignore/autotest",
        env=environment_with_tmpdir(tmpdir),
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert "7 tests passed 0 tests failed" in p.stdout
    assert os.listdir(tmpdir) == []


# item 18


# supplied_files_directory is relative when tests.txt is given as a relative
# -a pathname; fetching the submission must not break copying it


def bare_git_repository(tmp_path):
    """create a bare git repository containing tests/ignore's echo.sh"""
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(os.path.join(REPO_ROOT, "tests/ignore/echo.sh"), work)
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.com",
    )
    for command in (
        ["git", "init", "--quiet"],
        ["git", "add", "echo.sh"],
        ["git", "commit", "--quiet", "-m", "submission"],
    ):
        subprocess.run(command, cwd=work, env=env, check=True)
    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(work), str(bare)],
        env=env,
        check=True,
    )
    return "file://" + str(bare)


def test_relative_tests_txt_with_stdin_submission():
    p = subprocess.run(
        [
            sys.executable,
            "./autotest.py",
            "-a",
            "tests/ignore/autotest/tests.txt",
            "--stdin",
        ],
        cwd=REPO_ROOT,
        input='#!/bin/sh\necho "$@"\n',
        capture_output=True,
        timeout=30,
        encoding="utf-8",
    )
    assert "internal error" not in p.stderr
    assert p.returncode == 0, p.stdout + p.stderr
    assert "7 tests passed 0 tests failed" in p.stdout


def test_relative_tests_txt_with_git_submission(tmp_path):
    p = run_autotest(
        "-a",
        "tests/ignore/autotest/tests.txt",
        "--git",
        bare_git_repository(tmp_path),
    )
    assert "internal error" not in p.stderr
    assert p.returncode == 0, p.stdout + p.stderr
    assert "7 tests passed 0 tests failed" in p.stdout


def test_relative_supplied_files_directory_parameter_with_stdin(tmp_path):
    p = subprocess.run(
        [
            sys.executable,
            "./autotest.py",
            "-a",
            "tests/ignore/autotest",
            "-P",
            "supplied_files_directory=tests/ignore/autotest",
            "--stdin",
        ],
        cwd=REPO_ROOT,
        input='#!/bin/sh\necho "$@"\n',
        capture_output=True,
        timeout=30,
        encoding="utf-8",
    )
    assert "internal error" not in p.stderr
    assert p.returncode == 0, p.stdout + p.stderr


# autotest-helper must see the invoking user's environment, not the tests'


def test_helper_runs_with_original_environment(tmp_path):
    helper_directory = tmp_path / "bin"
    helper_directory.mkdir()
    helper = helper_directory / "autotest-helper"
    helper.write_text(
        '#!/bin/sh\necho "HELPER_RAN HOME=$HOME LOGNAME=$LOGNAME LABEL=$HELPER_TEST_LABEL"\n'
    )
    helper.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(helper_directory) + os.pathsep + env.get("PATH", "")
    env["HOME"] = str(tmp_path)
    env["LOGNAME"] = "helper_test_user"
    submission = tmp_path / "submission"
    submission.mkdir()
    wrong_echo = submission / "echo.sh"
    wrong_echo.write_text("#!/bin/sh\necho WRONG\n")
    wrong_echo.chmod(0o755)
    p = run_autotest(
        "-D",
        str(submission),
        "-a",
        "tests/ignore/autotest",
        "ignore_case",
        env=env,
    )
    assert p.returncode == 1, p.stdout + p.stderr
    assert (
        f"HELPER_RAN HOME={tmp_path} LOGNAME=helper_test_user LABEL=ignore_case"
        in p.stdout
    )
