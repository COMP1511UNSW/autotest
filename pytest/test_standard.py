import re
import subprocess
import sys

import pytest

# created from at's original test script (yes this script needs to be thrown into a fire)
# don't forget to add `sys.executable` for every subprocess call to ensure same python interpreter is used


def run_autotest(test_folder, *extra_arguments, env=None):
    """
    Run ./autotest.py on one of the directories under tests/ and return its
    combined stdout+stderr, so every test compares the same thing a student
    would see in a terminal.
    """
    p = subprocess.run(
        args=[
            sys.executable,
            "./autotest.py",
            "-D",
            test_folder,
            "-a",
            f"{test_folder}/autotest",
        ]
        + list(extra_arguments),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
        encoding="utf-8",
    )
    # on a host without unprivileged user namespaces autotest prints one
    # line saying so before anything else; the tests here are about what a
    # student's program produced, so that line is dropped rather than
    # making every exact comparison host-dependent
    return re.sub(r"^autotest: running WITHOUT a sandbox:.*\n", "", p.stdout)


ALL_TESTS_PASS_FOLDERS = [
    "tests/arguments",
    "tests/expected_output",
    "tests/f-strings",
    "tests/ignore",
    "tests/multi-file-simple",
    "tests/non_unicode_stdout",
    "tests/non_unicode_stderr",
    "tests/non_unicode_stdin",
    "tests/shell",
]


class TestStandard:
    @pytest.mark.parametrize("test_folder", ALL_TESTS_PASS_FOLDERS)
    def test_all_tests_pass(self, test_folder):
        output = run_autotest(test_folder)
        assert re.search(r" tests passed 0 tests failed *$", output), output

    def test_checker(self):
        output = run_autotest("tests/checker")
        expected_stdout = "bash -n hello.sh\nTest 0 (hello.sh) - passed\nchecker.sh hello.sh\nchecker.sh hello.sh\npre_compile autotest checker.sh hello.sh pre_compile.sh tests.txt\nTest 1 (hello.sh) - could not be run because check failed\n1 tests passed 0 tests failed  1 tests could not be run\n"
        assert output == expected_stdout

    def test_environment(self):
        test_env = {
            "SAMPLE_ENVIRONMENT_VARIABLE": "sample_value"
        }  # this is cursed but it's necessary
        output = run_autotest("tests/environment", env=test_env)
        assert re.search(r" tests passed 0 tests failed *$", output), output

    def test_limits(self):
        output = run_autotest(
            "tests/limits",
            "--parameters",
            "ignore_blank_lines=1\nignore_case=1\ncompare_only_characters=abcdefghijklmnopqrstuvwxyz",
        )
        # Peform a series of greps to find if we have the correct output.
        # Yes, this feels very brittle. No, I don't have a better solution.
        # More greps could be added to ensure that this is more effective.
        expected_lines = [
            r"Test max_open_files_should_pass \(/bin/true\) - passed",
            r"Test max_open_files_should_fail \('/bin/true 3>3 4>4 5>5 6>6 7>7 8>8'\) - failed \(errors\)",
            r"Test max_cpu_seconds_should_fail \('while true; do :; done'\) - failed \(errors\)",
            r"""Test max_cpu_seconds_python_should_fail \("python3 -c 'while 1: pass'"\) - failed \(errors\)""",
            r"Test max_file_size_bytes_should_fail \('yes >out'\) - failed \(errors\)",
            r"Test max_stdout_bytes_should_fail \(yes\) - failed \(errors\)",
            r"Test max_stderr_bytes_should_fail \('yes 1>&2'\) - failed \(errors\)",
            r"1 tests passed 6 tests failed",
        ]
        for expected_line in expected_lines:
            assert re.search(expected_line, output), output

    def test_non_unicode_file_output(self):
        output = run_autotest("tests/non_unicode_file_output")
        expected_output = r"Test test_incorrect_output \(not_unicode_files\) - failed \(Your non-unicode output is not correct\)\n"
        expected_output += r"Your non-unicode files had incorrect output\n"
        expected_output += r"File test_file2 had the following error:\n"
        expected_output += r"expected: 0xa571ffffa57f actual: 0xa571ffffa571\n"
        expected_output += (
            r"There were 3 different bits between your output and the expected output\n"
        )
        assert re.search(expected_output, output), output
        assert re.search(r"1 tests passed 1 tests failed", output), output

    def test_show_parameters(self):
        # every test here fails deliberately: each show_* parameter must hide
        # exactly one part of the failure explanation
        output = run_autotest("tests/show_parameters")
        assert re.search(r"0 tests passed 6 tests failed", output), output
        blocks = re.split(
            r"^(?=Test \w+ \(sample description\) - failed)", output, flags=re.MULTILINE
        )
        explanations = {}
        for block in blocks:
            label = re.match(r"Test (\w+)", block)
            if label:
                explanations[label.group(1)] = block
        actual = "Your program produced this line of output:\nhello\n"
        expected = "The correct 1 lines of output for this test were:\nworld\n"
        diff = "The difference between your output(-) and the correct output(+) is:\n- hello\n+ world\n"
        stdin = "The input for this test was:\nsample input\n"
        reproduce = "You can reproduce this test by executing these commands:\n  echo fake compile command echo.sh\n  echo -n 'sample input' | echo.sh hello\n"
        everything = explanations["check_everything_shown"]
        for part in [actual, expected, diff, stdin, reproduce]:
            assert part in everything, everything
        assert stdin not in explanations["check_actual_output_not_shown"]
        assert expected not in explanations["check_expected_output_not_shown"]
        assert diff not in explanations["check_diff_not_shown"]
        assert reproduce not in explanations["check_reproduce_command_not_shown"]
        assert (
            "fake compile command echo.sh\nfake compile command echo.sh\n" in output
        ), output
        assert "echo new fake compile command echo.sh" not in output, output
