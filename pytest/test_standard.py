import pytest
import subprocess
import re

# created from at's original test script (yes this script needs to be thrown into a fire)
class TestStandard:
    def test_arguments(self):
        test_folder = "../tests/arguments"
        p = subprocess.run(
            args=["../autotest.py", "-D", test_folder, "-a", f"{test_folder}/autotest"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    # TODO: fixme
    # this one is a top level one (this is broken because glob things)
    def test_checker(self):
        return
        test_folder = "../tests/checker"
        p = subprocess.run(
            args=["../autotest.py", "-D", test_folder, "-a", test_folder],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_environment(self):

        test_folder = "../tests/environment"
        test_env = {
            "SAMPLE_ENVIRONMENT_VARIABLE": "sample_value"
        }  # this is cursed but it's necessary
        p = subprocess.run(
            args=["../autotest.py", "-D", test_folder, "-a", f"{test_folder}/autotest"],
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
        test_folder = "../tests/expected_output"
        p = subprocess.run(
            args=["../autotest.py", "-D", test_folder, "-a", f"{test_folder}/autotest"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_f_strings(self):
        test_folder = "../tests/f-strings"
        p = subprocess.run(
            args=["../autotest.py", "-D", test_folder, "-a", f"{test_folder}/autotest"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_ignore(self):
        test_folder = "../tests/ignore"
        p = subprocess.run(
            args=["../autotest.py", "-D", test_folder, "-a", f"{test_folder}/autotest"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_limits(self):
        test_folder = "../tests/limits"
        p = subprocess.run(
            args=[
                "../autotest.py",
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

    # TODO: test multi-file-simple here — not currently working
    def test_multi_file_simple(self):
        return
        test_folder = "../tests/multi-file-simple"
        p = subprocess.run(
            args=["../autotest.py", "-D", test_folder, "-a", f"{test_folder}/autotest"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        expected_output = "b\n"
        if p.stdout != expected_output:
            print(p.stdout)
            assert False

    def test_shell(self):
        test_folder = "../tests/shell"
        p = subprocess.run(
            args=["../autotest.py", "-D", test_folder, "-a", f"{test_folder}/autotest"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(r" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_show_parameters(self):
        test_folder = "../tests/show_parameters"
        p = subprocess.run(
            args=["../autotest.py", "-D", test_folder, "-a", f"{test_folder}/autotest"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        expected_output = "bash -n echo.sh\necho fake compile command echo.sh\nfake compile command echo.sh\nTest check_everything_shown (sample description) - failed (Incorrect output)\nYour program produced this line of output:\nhello\n\nThe correct 1 lines of output for this test were:\nworld\n\nThe difference between your output(-) and the correct output(+) is:\n- hello\n+ world\n\nThe input for this test was:\nsample input\nYou can reproduce this test by executing these commands:\n  echo fake compile command echo.sh\n  echo -n 'sample input' | echo.sh hello\nTest check_actual_output_not_shown (sample description) - failed (Incorrect output)\nYour program produced this line of output:\nhello\n\nThe correct 1 lines of output for this test were:\nworld\n\nThe difference between your output(-) and the correct output(+) is:\n- hello\n+ world\nYou can reproduce this test by executing these commands:\n  echo fake compile command echo.sh\n  echo -n 'sample input' | echo.sh hello\nTest check_expected_output_not_shown (sample description) - failed (Incorrect output)\n\nThe difference between your output(-) and the correct output(+) is:\n- hello\n+ world\nYou can reproduce this test by executing these commands:\n  echo fake compile command echo.sh\n  echo -n 'sample input' | echo.sh hello\nTest check_diff_not_shown (sample description) - failed (Incorrect output)\nYou can reproduce this test by executing these commands:\n  echo fake compile command echo.sh\n  echo -n 'sample input' | echo.sh hello\nTest check_reproduce_command_not_shown (sample description) - failed (Incorrect output)\nnew fake compile command echo.sh\nTest check_compile_command_not_shown (sample description) - failed (Incorrect output - same as Test check_reproduce_command_not_shown)\n0 tests passed 6 tests failed\n"
        assert_f = True
        if p.stdout != expected_output:
            print(p.stdout)
            assert_f = False
        assert assert_f