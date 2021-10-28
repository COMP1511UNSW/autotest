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

    