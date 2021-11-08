import pytest
import subprocess
import re
import sys

# created from at's original test script (yes this script needs to be thrown into a fire)
# don't forget to add `sys.exectuable` for every subprocess call to ensure same python interpreter is used
class TestStandard:
    def test_arguments(self):
        test_folder = "tests/arguments"
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_checker(self):
        test_folder = "tests/checker"
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        expected_stdout = "bash -n hello.sh\nTest 0 (hello.sh) - passed\nchecker.sh hello.sh\nchecker.sh hello.sh\npre_compile autotest checker.sh hello.sh pre_compile.sh tests.txt\nTest 1 (hello.sh) - could not be run because check failed\n1 tests passed 0 tests failed  1 tests could not be run\n"
        assert p.stdout == expected_stdout

    def test_environment(self):

        test_folder = "tests/environment"
        test_env = {
            "SAMPLE_ENVIRONMENT_VARIABLE": "sample_value"
        }  # this is cursed but it's necessary
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
            ],
            env=test_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_expected_output(self):
        test_folder = "tests/expected_output"
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_f_strings(self):
        test_folder = "tests/f-strings"
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_ignore(self):
        test_folder = "tests/ignore"
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_limits(self):
        test_folder = "tests/limits"
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
                "--parameters",
                "ignore_blank_lines=1\nignore_case=1\ncompare_only_characters=abcdefghijklmnopqrstuvwxyz",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        # Peform a series of greps to find if we have the correct output.
        # Yes, this feels very brittle. No, I don't have a better solution.
        # More greps could be added to ensure that this is more effective.
        success = True
        if not re.search(
            r"Test max_open_files_should_pass \(/bin/true\) - passed", p.stdout
        ):
            success = False
        if not re.search(
            r"Test max_open_files_should_fail \('/bin/true 3>3 4>4 5>5 6>6 7>7 8>8'\) - failed \(errors\)",
            p.stdout,
        ):
            success = False
        if not re.search(
            r"Test max_cpu_seconds_should_fail \('while true; do :; done'\) - failed \(errors\)",
            p.stdout,
        ):
            success = False
        if not re.search(
            r"Test max_file_size_bytes_should_fail \('yes >out'\) - failed \(errors\)",
            p.stdout,
        ):
            success = False
        if not re.search(
            r"Test max_stdout_bytes_should_fail \(yes\) - failed \(errors\)", p.stdout
        ):
            success = False
        if not re.search(
            r"Test max_stderr_bytes_should_fail \('yes 1>&2'\) - failed \(errors\)",
            p.stdout,
        ):
            success = False
        if not re.search(r"1 tests passed 5 tests failed", p.stdout):
            success = False

        if success is False:
            print(p.stdout)

        assert success

    def test_multi_file_simple(self):
        test_folder = "tests/multi-file-simple"
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not p.stdout != re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_non_unicode_stdout(self):
        test_folder = "tests/non_unicode_stdout"
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_non_unicode_stdout(self):
        test_folder = "tests/non_unicode_stderr"
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_non_unicode_stderr(self):
        test_folder = "tests/non_unicode_stderr"
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_non_unicode_stdin(self):
        test_folder = "tests/non_unicode_stdin"
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_non_unicode_multi_file_output(self):
        test_folder = "tests/non_unicode_multi_file_output"
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        expected_output = r"Test test_incorrect_output \(not_unicode_mult\) - failed \(Your non-unicode output is not correct.\)\n"
        expected_output += r"Your non-unicode files had incorrect output\n"
        expected_output += r"File test_file2 had the following error:\n"
        expected_output += r"expected: 0xa573bfffa571 actual: 0xa571ffffa571\n"
        print(expected_output)
        if not re.search(expected_output, p.stdout):
            print(p.stdout)
            assert False

    def test_shell(self):
        test_folder = "tests/shell"
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_show_parameters(self):
        test_folder = "tests/show_parameters"
        p = subprocess.run(
            args=[
                sys.executable,
                "./autotest.py",
                "-D",
                test_folder,
                "-a",
                f"{test_folder}/autotest",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not p.stdout != re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False
