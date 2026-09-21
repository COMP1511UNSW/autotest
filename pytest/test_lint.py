"""
Tests for autotest --lint, which checks a specification without running it.

Every rule here was measured against the 2,590 real specifications in the
COMP1511, COMP1521 and COMP2041 26T2 material before it was written; three
others were designed and dropped because they reported a documented idiom or
never fired.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SH = "#!/bin/sh\n"


def exercise_with(tmp_path, tests_txt, autotest_files=()):
    spec = tmp_path / "spec"
    spec.mkdir(exist_ok=True)
    (spec / "tests.txt").write_text("files=a.sh\nprogram=./a.sh\n" + tests_txt)
    for name, contents, mode in autotest_files:
        path = spec / name
        path.write_text(contents)
        path.chmod(mode)
    submission = tmp_path / "sub"
    submission.mkdir(exist_ok=True)
    a = submission / "a.sh"
    a.write_text(SH)
    a.chmod(0o700)
    return ["-D", str(submission), "-a", str(spec), "--lint"]


def test_lint_reports_a_checker_that_is_not_there(run_autotest, tmp_path):
    args = exercise_with(
        tmp_path, 'pre_compile_command="./check_it.sh a.sh"\n1 command="true"\n'
    )
    stdout, _stderr, status = run_autotest(args)
    assert "unresolved-command" in stdout, stdout
    assert "./check_it.sh" in stdout, stdout
    assert "does not exist" in stdout, stdout
    assert status == 1


def test_lint_says_when_a_reference_is_a_dangling_symbolic_link(run_autotest, tmp_path):
    """
    This is the real case, and the reason the message is specific: a checker
    that is a link into a submodule nobody checked out reports the same
    "check failed" as a checker that fails, and the difference is hours.
    """
    spec = tmp_path / "spec"
    spec.mkdir()
    os.symlink("../nowhere/check_it.sh", spec / "check_it.sh")
    args = exercise_with(
        tmp_path, 'pre_compile_command="./check_it.sh a.sh"\n1 command="true"\n'
    )
    stdout, _stderr, status = run_autotest(args)
    assert "symbolic link" in stdout, stdout
    assert "submodule" in stdout, stdout
    assert status == 1


def test_lint_reports_a_checker_that_is_not_executable(run_autotest, tmp_path):
    args = exercise_with(
        tmp_path,
        'pre_compile_command="./check_it.sh a.sh"\n1 command="true"\n',
        autotest_files=[("check_it.sh", SH, 0o600)],
    )
    stdout, _stderr, status = run_autotest(args)
    assert "is not executable" in stdout, stdout
    assert status == 1


def test_lint_is_silent_and_succeeds_on_a_sound_specification(run_autotest, tmp_path):
    args = exercise_with(
        tmp_path,
        'pre_compile_command="./check_it.sh a.sh"\n1 command="true"\n',
        autotest_files=[("check_it.sh", SH, 0o700)],
    )
    stdout, stderr, status = run_autotest(args)
    assert stdout == "", stdout
    assert status == 0, stderr


def test_lint_does_not_judge_a_command_found_on_the_path(run_autotest, tmp_path):
    """This host's PATH says nothing about the host the tests will run on."""
    args = exercise_with(
        tmp_path,
        'pre_compile_command="definitely_not_installed_anywhere a.sh"\n'
        '1 command="true"\n',
    )
    stdout, _stderr, status = run_autotest(args)
    assert stdout == "", stdout
    assert status == 0


def test_lint_looks_only_at_the_program_a_command_runs(run_autotest, tmp_path):
    """
    "cp ./template.c ./main.c" runs cp. Reporting ./main.c -- which a regular
    expression over the whole line would -- would be a finding about a file
    the command is about to create.
    """
    args = exercise_with(
        tmp_path, 'setup_command="cp ./a.sh ./made.sh"\n1 command="true"\n'
    )
    stdout, _stderr, status = run_autotest(args)
    assert stdout == "", stdout
    assert status == 0


def test_lint_checks_every_checker_not_only_the_first(run_autotest, tmp_path):
    """
    checkers is a list of commands. COMP1521's 22t2supp_q10 writes
    checkers=["1521 c_check", "./check-features-used"]; treating that as one
    argv would inspect "1521" and never look at the reference that matters.
    """
    args = exercise_with(
        tmp_path,
        'checkers=["true", "./check_second.sh"]\n1 command="true"\n',
    )
    stdout, _stderr, status = run_autotest(args)
    assert "./check_second.sh" in stdout, stdout
    assert status == 1


def test_lint_reports_a_shared_problem_once(run_autotest, tmp_path):
    """
    A global parameter would otherwise be reported once per test, and
    COMP1521's cs_chicken has 153 of them.
    """
    tests = "".join(f'{n} command="true"\n' for n in range(1, 20))
    args = exercise_with(tmp_path, 'pre_compile_command="./check_it.sh a.sh"\n' + tests)
    stdout, _stderr, status = run_autotest(args)
    assert stdout.count("unresolved-command") == 1, stdout
    assert status == 1


def test_lint_creates_nothing_and_runs_nothing(run_autotest, tmp_path):
    """Safe to loop over a whole course tree."""
    args = exercise_with(
        tmp_path,
        'setup_command="touch ./made_by_setup"\n1 command="touch ./made_by_test"\n',
    )
    before = sorted(p.name for p in tmp_path.rglob("*"))
    run_autotest(args)
    assert sorted(p.name for p in tmp_path.rglob("*")) == before
