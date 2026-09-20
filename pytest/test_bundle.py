"""
Tests of bundle_autotests.sh: the single-file executable it builds (a "#!"
line followed by a zip of the sources and xz-compressed tars of the
embedded autotests) runs an embedded exercise by name, still runs an
ordinary autotest given with -a, and reports an unknown exercise.

The bundle is run through the interpreter running pytest, not its "#!"
line, so it works where the system python3 lacks termcolor.
"""

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.needs_tools("bash", "zip", "tar", "xz")

EXERCISE = "lab1"
SPEC = (
    "files=a.sh\nprogram=./a.sh\n"
    '1 command="./a.sh; cat extra.txt" expected_stdout="hi\\nEXTRA\\n"\n'
)


@pytest.fixture(scope="module")
def bundle(tmp_path_factory, make_exercise):
    """(bundle pathname, submission directory) built once for the module"""
    base = tmp_path_factory.mktemp("bundle")
    # the exercise is embedded from a directory named after it, containing
    # an autotest sub-directory, the layout bundle_autotests.sh derives the
    # exercise name from
    exercise = make_exercise(
        base,
        SPEC,
        files={"a.sh": "#!/bin/sh\necho hi\n"},
        supplied={"extra.txt": "EXTRA\n"},
        name=EXERCISE,
    )
    executable = str(base / "autotest.pyz")
    p = subprocess.run(
        ["bash", os.path.join(REPO_ROOT, "bundle_autotests.sh"), executable]
        + [os.path.join(str(base), EXERCISE)],
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    return executable, exercise.submission


def run_bundle(executable, *arguments, timeout=60):
    p = subprocess.run(
        [sys.executable, executable] + list(arguments),
        cwd=REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return p.stdout, p.stderr, p.returncode


def test_bundle_starts_with_a_hash_bang_line_and_is_executable(bundle):
    executable, _ = bundle
    with open(executable, "rb") as f:
        assert f.readline() == b"#!/usr/bin/env python3\n"
        assert f.read(2) == b"PK"
    assert os.access(executable, os.X_OK)


def test_bundle_runs_an_embedded_exercise_by_name(bundle):
    executable, submission = bundle
    stdout, stderr, status = run_bundle(executable, EXERCISE, "-D", submission)
    assert status == 0, stdout + stderr
    assert "internal error" not in stderr
    assert stdout.endswith(
        "Test 1 ('./a.sh; cat extra.txt') - passed\n1 tests passed 0 tests failed \n"
    )


def test_bundle_removes_the_extracted_embedded_autotest(bundle, tmp_path):
    executable, submission = bundle
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    p = subprocess.run(
        [sys.executable, executable, EXERCISE, "-D", submission],
        cwd=REPO_ROOT,
        env=dict(os.environ, TMPDIR=str(tmpdir)),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        timeout=60,
    )
    assert p.returncode == 0, p.stdout
    assert os.listdir(tmpdir) == []


def test_bundle_runs_a_non_embedded_autotest_given_with_a(bundle):
    executable, _ = bundle
    stdout, stderr, status = run_bundle(
        executable, "-a", "tests/ignore/autotest", "-D", "tests/ignore"
    )
    assert status == 0, stdout + stderr
    assert "7 tests passed 0 tests failed" in stdout


def test_bundle_reports_an_unknown_exercise_without_an_internal_error(bundle):
    executable, submission = bundle
    _, stderr, status = run_bundle(executable, "nosuch", "-D", submission)
    assert status == 2
    assert "internal error" not in stderr, stderr
    assert "no autotest found for nosuch" in stderr


def test_bundle_without_arguments_says_no_exercise(bundle):
    executable, _ = bundle
    _, stderr, status = run_bundle(executable)
    assert status == 2
    assert "no exercise specified" in stderr


def test_bundling_a_directory_without_tests_txt_fails(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    p = subprocess.run(
        [
            "bash",
            os.path.join(REPO_ROOT, "bundle_autotests.sh"),
            str(tmp_path / "out.pyz"),
            str(empty),
        ],
        capture_output=True,
        encoding="utf-8",
        timeout=60,
    )
    assert p.returncode == 1
    assert "no tests.txt found" in p.stderr
    assert not (tmp_path / "out.pyz").exists()
