"""
Unit tests for helper: when autotest-helper is run for a failed test and
what it is told.

A fake autotest-helper script placed first on a PATH built under tmp_path
records the HELPER_* environment it was given and exits with the status
the test asks for, so no real helper is needed.
"""

import json
import os
import sys
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import helper  # noqa: E402


@pytest.fixture
def fake_helper(tmp_path, monkeypatch):
    """
    put a fake autotest-helper first on PATH and chdir to a directory
    holding hello.c; returns the pathname of the file the helper writes
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    captured = tmp_path / "captured"
    script = bindir / helper.AUTOTEST_HELPER
    script.write_text(
        "#!/bin/sh\n"
        f"env | grep '^HELPER_' | sort > '{captured}'\n"
        f"echo \"HOME=$HOME\" >> '{captured}'\n"
        f"echo \"LOGNAME=$LOGNAME\" >> '{captured}'\n"
        'exit "${HELPER_EXIT_STATUS:-0}"\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hello.c").write_text("int main(void) {}\n")
    return captured


def failed_test(**overrides):
    test = types.SimpleNamespace(
        label="t1",
        test_passed=False,
        expected_stdout="exp\n",
        expected_stderr="",
        stdin="in\n",
        stdout="out\n",
        stderr="",
        files=["hello.c"],
    )
    for name, value in overrides.items():
        setattr(test, name, value)
    return test


def make_args(debug=0):
    return types.SimpleNamespace(autotest_directory="/autotests/lab1/", debug=debug)


def make_parameters(**environment):
    return {
        "__environment_original": {
            "PATH": os.environ["PATH"],
            "HOME": "/home/orig",
            "LOGNAME": "orig",
            **environment,
        }
    }


def captured_variables(captured):
    variables = {}
    for line in captured.read_text().splitlines():
        name, _, value = line.partition("=")
        variables[name] = value
    return variables


def test_helper_is_run_for_the_first_failed_test_with_its_details(fake_helper):
    tests = {
        "ok": types.SimpleNamespace(label="ok", test_passed=True),
        "t1": failed_test(),
        "t2": failed_test(label="t2"),
    }
    assert helper.run_helper(tests, make_parameters(), make_args()) is True
    variables = captured_variables(fake_helper)
    assert variables["HELPER_TEST_LABEL"] == "t1"
    assert variables["HELPER_FILE"] == "hello.c"
    assert variables["HELPER_AUTOTEST_DIRECTORY"] == "/autotests/lab1/"
    assert variables["HELPER_SOURCE"] == "int main(void) {}"
    assert variables["HELPER_EXPECTED_STDOUT"] == "exp"
    assert variables["HELPER_STDIN"] == "in"
    assert variables["HELPER_STDOUT"] == "out"
    assert variables["HELPER_STDERR"] == ""


def test_helper_json_carries_every_field_exactly(fake_helper):
    helper.run_helper({"t1": failed_test()}, make_parameters(), make_args())
    assert json.loads(captured_variables(fake_helper)["HELPER_JSON"]) == {
        "test_label": "t1",
        "stdin": "in\n",
        "stderr": "",
        "stdout": "out\n",
        "expected_stdout": "exp\n",
        "file": "hello.c",
        "source": "int main(void) {}\n",
        "autotest_directory": "/autotests/lab1/",
    }


def test_bytes_streams_and_nul_characters_are_escaped_for_the_environment(fake_helper):
    test = failed_test(
        stdin=b"in\x00\xff", stdout=b"out", stderr=b"e", expected_stdout=b"exp"
    )
    assert helper.run_helper({"t1": test}, make_parameters(), make_args()) is True
    variables = captured_variables(fake_helper)
    assert variables["HELPER_STDIN"] == "in\\x00\\xff"
    assert variables["HELPER_STDOUT"] == "out"
    assert json.loads(variables["HELPER_JSON"])["stdin"] == "in\x00\\xff"


def test_helper_gets_the_environment_autotest_was_invoked_with(
    fake_helper, monkeypatch
):
    monkeypatch.setenv("HOME", "/home/runner")
    helper.run_helper({"t1": failed_test()}, make_parameters(), make_args())
    variables = captured_variables(fake_helper)
    assert variables["HOME"] == "/home/orig"
    assert variables["LOGNAME"] == "orig"


def test_helper_falls_back_to_the_current_environment(fake_helper, monkeypatch):
    monkeypatch.setenv("HOME", "/home/runner")
    monkeypatch.setenv("LOGNAME", "runner")
    helper.run_helper(
        {"t1": failed_test()}, {"__environment_original": None}, make_args()
    )
    variables = captured_variables(fake_helper)
    assert variables["HOME"] == "/home/runner"
    assert variables["LOGNAME"] == "runner"


def test_helper_exit_status_decides_the_result(fake_helper):
    assert (
        helper.run_helper(
            {"t1": failed_test()}, make_parameters(HELPER_EXIT_STATUS="3"), make_args()
        )
        is False
    )
    assert (
        helper.run_helper(
            {"t1": failed_test()}, make_parameters(HELPER_EXIT_STATUS="0"), make_args()
        )
        is True
    )


def test_helper_is_not_run_when_no_test_failed(fake_helper):
    tests = {
        "ok": types.SimpleNamespace(label="ok", test_passed=True),
        "skipped": types.SimpleNamespace(label="s", test_passed=None),
    }
    assert helper.run_helper(tests, make_parameters(), make_args()) is None
    assert helper.run_helper({}, make_parameters(), make_args()) is None
    assert not fake_helper.exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"expected_stderr": "error expected\n"},
        {"expected_stdout": "x" * (helper.MAX_EXPECTED_STDOUT + 1)},
        {"stdin": "x" * (helper.MAX_STDIN + 1)},
        {"stdout": "x" * (helper.MAX_STDOUT + 1)},
        {"stderr": "x" * (helper.MAX_STDERR + 1)},
        {"files": []},
        {"files": ["hello.c", "other.c"]},
        {"files": ["missing.c"]},
    ],
)
def test_helper_is_not_run_for_tests_it_can_not_explain(fake_helper, overrides):
    assert (
        helper.run_helper(
            {"t1": failed_test(**overrides)}, make_parameters(), make_args()
        )
        is None
    )
    assert not fake_helper.exists()


def test_helper_is_not_run_for_a_big_source_file(fake_helper, tmp_path):
    (tmp_path / "hello.c").write_text("x" * (helper.MAX_FILE_SIZE + 1))
    assert (
        helper.run_helper({"t1": failed_test()}, make_parameters(), make_args()) is None
    )
    assert not fake_helper.exists()


def test_helper_is_not_run_when_not_on_path(fake_helper, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    assert (
        helper.run_helper({"t1": failed_test()}, make_parameters(), make_args()) is None
    )
    assert not fake_helper.exists()


def test_helper_that_can_not_be_executed_gives_false_and_debug_message(
    fake_helper, tmp_path, capsys
):
    (tmp_path / "bin" / helper.AUTOTEST_HELPER).write_text(
        "#!/nonexistent/interpreter\n"
    )
    assert (
        helper.run_helper({"t1": failed_test()}, make_parameters(), make_args(debug=1))
        is False
    )
    err = capsys.readouterr().err
    assert "running autotest-helper" in err
    assert "No such file or directory" in err


def test_helper_failure_to_execute_is_silent_without_debug(
    fake_helper, tmp_path, capsys
):
    (tmp_path / "bin" / helper.AUTOTEST_HELPER).write_text(
        "#!/nonexistent/interpreter\n"
    )
    assert (
        helper.run_helper({"t1": failed_test()}, make_parameters(), make_args())
        is False
    )
    assert capsys.readouterr().err == ""


def test_as_text_decodes_bytes_without_failing():
    assert helper.as_text(b"\xff\x00a") == "\\xff\x00a"
    assert helper.as_text("s") == "s"
