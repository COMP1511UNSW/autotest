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
        if not re.search(" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

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
        if not re.sedarch(" tests passed 0 tests failed *$", p.stdout):
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
        if not re.search(" tests passed 0 tests failed *$", p.stdout):
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
        if not re.search(" tests passed 0 tests failed *$", p.stdout):
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
        if not re.search(" tests passed 0 tests failed *$", p.stdout):
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
        if not re.search(" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    def test_limits(self):
        test_folder = "../tests/limits"
        p = subprocess.run(
            args=["../autotest.py", "-D", test_folder, "-a", f"{test_folder}/autotest"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        expected_output = "bash -n echo.sh\nTest max_open_files_should_pass (/bin/true) - passed\nTest max_open_files_should_fail ('/bin/true 3>3 4>4 5>5 6>6 7>7 8>8') - failed (errors)\nYour program produced these errors:\n/bin/sh: 1: cannot create 6: Too many open files\nYou can reproduce this test by executing these commands:\n  /bin/true 3>3 4>4 5>5 6>6 7>7 8>8\nTest max_cpu_seconds_should_fail ('while true; do :; done') - failed (errors)\nYour program produced these errors:\nError: CPU limit of 1 seconds exceeded\nYou can reproduce this test by executing these commands:\n  while true\n  do :\n  done\nTest max_file_size_bytes_should_fail ('yes >out') - failed (errors)\nYour program produced these errors:\nFile size limit exceeded\nYou can reproduce this test by executing these commands:\n  yes >out\nTest max_stdout_bytes_should_fail (yes) - failed (errors)\nYour program produced these errors:\n\nError too much output - maximum stdout bytes of 32 exceeded.\nYour program produced these 32 bytes of output before it was terminated:\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\nYou can reproduce this test by executing these commands:\n  yes\nTest max_stderr_bytes_should_fail ('yes 1>&2') - failed (errors)\nYour program produced these errors:\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\nYou can reproduce this test by executing these commands:\n  yes 1>&2\n1 tests passed 5 tests failed\n"
        if p.stdout != expected_output:
            print(p.stdout)
            assert False

    # TODO: test multi-file-simple here

    def test_shell(self):
        test_folder = "../tests/shell"
        p = subprocess.run(
            args=["../autotest.py", "-D", test_folder, "-a", f"{test_folder}/autotest"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            encoding="utf-8",
        )
        if not re.search(" tests passed 0 tests failed *$", p.stdout):
            print(p.stdout)
            assert False

    # TODO: test show_parameters here
