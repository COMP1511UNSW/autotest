"""
End-to-end tests of run_tests.py: which tests run, what happens when they
can not, how their results are summarised, and the command-line ways of
selecting tests and generating expected output.

Already covered elsewhere and not repeated here: setup_command running for
every test and its output placement (test_parallel), a compile.sh supplied
by the autotest being run and one planted by a submission being ignored
(test_integration), tests sharing a compile command compiling once
(test_integration), C compilation with files=hello.* (test_integration),
-g output being identical to main (test_integration), and unknown extra
arguments / regexes (test_fixes).

Compilers here are shell scripts supplied by the autotest, so no C
compiler is needed and the output is the same on every host.
"""

import json
import os
import time

import pytest

SH = "#!/bin/sh\n"
SPEC = 'files=a.sh\nprogram=./a.sh\n1 command="echo x" expected_stdout="x\\n"\n'

# a "compiler" which writes program a so that it prints its own first
# argument: compile_commands=["./cc.sh good"] gives an a printing good
FAKE_COMPILER = "#!/bin/sh\nprintf '#!/bin/sh\\necho %s\\n' \"$1\" >a\nchmod +x a\n"


# ---- tests which can not be run


def test_all_tests_missing_a_file_stops_before_running_any(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(
        tmp_path, SPEC + '2 command="echo y" expected_stdout="y\\n"\n', files={}
    )
    stdout, stderr, status = run_autotest(exercise.args)
    assert status == 1, stderr
    assert stdout == "Unable to run tests because these files were missing: a.sh\n"


def test_a_test_missing_its_own_file_is_reported_and_the_rest_run(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(
        tmp_path,
        SPEC + 'files=b.sh\n2 command="echo y" expected_stdout="y\\n"\n',
        files={"a.sh": SH},
    )
    stdout, _, status = run_autotest(exercise.args)
    assert status == 1
    assert "Test 1 ('echo x') - passed\n" in stdout
    assert (
        "Test 2 ('echo y') - could not be run because these files are missing: b.sh\n"
        in stdout
    )
    assert stdout.endswith("1 tests passed 0 tests failed  1 tests could not be run\n")


def test_failing_checker_runs_once_and_stops_every_test_using_it(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\ncheckers=["./check.sh"]\n'
        '1 command="echo x" expected_stdout="x\\n"\n'
        '2 command="echo y" expected_stdout="y\\n"\n',
        files={"a.sh": SH},
        supplied={"check.sh": "#!/bin/sh\necho checking $1\nexit 1\n"},
    )
    stdout, _, status = run_autotest(exercise.args)
    assert status == 1
    assert stdout == (
        "./check.sh a.sh\nchecking a.sh\n"
        "Test 1 ('echo x') - could not be run because check failed\n"
        "Test 2 ('echo y') - could not be run because check failed\n"
        "0 tests passed 0 tests failed  2 tests could not be run\n"
    )


def test_failing_pre_compile_command_is_reported_as_check_failed(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\npre_compile_command="echo pre; false"\n'
        '1 command="echo x" expected_stdout="x\\n"\n',
        files={"a.sh": SH},
    )
    stdout, _, status = run_autotest(exercise.args)
    assert status == 1
    assert stdout == (
        "bash -n a.sh\npre\n"
        "Test 1 ('echo x') - could not be run because check failed\n"
        "0 tests passed 0 tests failed  1 tests could not be run\n"
    )


def test_compilation_failure_is_shared_by_tests_with_the_same_compile_command(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(
        tmp_path,
        'files=a.c\ncompile_commands=["./cc.sh"]\n'
        '1 command=./a expected_stdout="x\\n"\n'
        '2 command=./a expected_stdout="y\\n"\n'
        '3 command=./a expected_stdout="y\\n" compile_commands=["./cc.sh other"]\n',
        files={"a.c": ""},
        supplied={"cc.sh": "#!/bin/sh\necho COMPILING $*\nexit 1\n"},
    )
    stdout, _, status = run_autotest(exercise.args)
    assert status == 1
    # the shared compile command ran once; test 3's different one ran too
    assert stdout == (
        "./cc.sh a.c\nCOMPILING a.c\n"
        "Test 1 (./a) - could not be run because compilation failed\n"
        "Test 2 (./a) - could not be run because compilation failed\n"
        "./cc.sh other a.c\nCOMPILING other a.c\n"
        "Test 3 (./a) - could not be run because compilation failed\n"
        "0 tests passed 0 tests failed  3 tests could not be run\n"
    )


# ---- compilers


def test_second_compiler_alternative_is_used_when_the_first_is_not_on_path(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(
        tmp_path,
        'files=a.c\ncompilers=[[["nonexistent_cc_xyz"], ["true"]]]\n'
        '1 command="echo x" expected_stdout="x\\n"\n',
        files={"a.c": ""},
    )
    stdout, stderr, status = run_autotest(exercise.args)
    assert status == 0, stdout + stderr
    assert stdout.startswith("true a.c\n")
    assert "nonexistent_cc_xyz" not in stdout


def test_no_compiler_alternative_on_path_is_a_specification_error(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(
        tmp_path,
        'files=a.c\ncompilers=[[["nonexistent_cc_xyz"], ["nonexistent_cc_abc"]]]\n'
        '1 command="echo x" expected_stdout="x\\n"\n',
        files={"a.c": ""},
    )
    _, stderr, status = run_autotest(exercise.args)
    assert status == 2
    assert "parameter 'compilers' no alternative found" in stderr


def test_compiler_which_can_not_be_started_is_reported_on_its_own_line(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(
        tmp_path,
        'files=a.c\ncompile_commands=[["nonexistent_cc_xyz", "-o", "a"]]\n'
        '1 command=./a expected_stdout="x\\n"\n',
        files={"a.c": ""},
    )
    stdout, _, status = run_autotest(exercise.args)
    assert status == 1
    assert stdout == (
        "nonexistent_cc_xyz -o a a.c\n"
        "No such file or directory: 'nonexistent_cc_xyz'\n"
        "Test 1 (./a) - could not be run because compilation failed\n"
        "0 tests passed 0 tests failed  1 tests could not be run\n"
    )


def test_two_compile_commands_run_each_test_twice_and_report_the_wrong_binary(
    tmp_path, make_exercise, run_autotest, split_test_output
):
    exercise = make_exercise(
        tmp_path,
        'files=a.c\ncompile_commands=["./cc.sh good", "./cc.sh bad"]\n'
        '1 command=./a expected_stdout="good\\n"\n'
        '2 command=./a expected_stdout="bad\\n"\n',
        files={"a.c": ""},
        supplied={"cc.sh": FAKE_COMPILER},
    )
    stdout, stderr, status = run_autotest(exercise.args)
    assert status == 1, stdout + stderr
    assert stdout.startswith("./cc.sh good a.c\n./cc.sh bad a.c\n")
    blocks = split_test_output(stdout)
    # test 1: the first binary was right, the second wrong, so it is the
    # second compilation which is shown in the reproduce command
    assert "Your program produced this line of output:\nbad\n" in blocks["1"]
    assert "  ./cc.sh bad a.c\n  ./a\n" in blocks["1"]
    # test 2: the first binary was wrong (and produced no errors)
    assert "Your program produced this line of output:\ngood\n" in blocks["2"]
    assert "  ./cc.sh good a.c\n  ./a\n" in blocks["2"]


# ---- legacy hooks supplied by the autotest


def test_supplied_runtests_pl_replaces_the_tests_and_sets_the_exit_status(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(
        tmp_path,
        SPEC,
        files={"a.sh": SH},
        supplied={"runtests.pl": "#!/bin/sh\necho RUNTESTS $*\nexit 3\n"},
    )
    stdout, stderr, status = run_autotest(exercise.args + ["somearg"])
    assert status == 3, stdout + stderr
    assert stdout == "RUNTESTS somearg\n"
    assert "tests passed" not in stdout


def test_supplied_compile_sh_gets_the_program_names_and_its_failure_is_fatal(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(
        tmp_path,
        'files=a.sh b.sh\nprogram=./b.sh\n1 expected_stdout="y\\n"\n'
        'program=./a.sh\n2 expected_stdout="x\\n"\n',
        files={"a.sh": "#!/bin/sh\necho x\n", "b.sh": "#!/bin/sh\necho y\n"},
        supplied={"compile.sh": "#!/bin/sh\necho COMPILE $*\nexit 1\n"},
    )
    stdout, stderr, status = run_autotest(exercise.args)
    assert status == 2, stdout + stderr
    assert stdout == "COMPILE ./a.sh ./b.sh\n"
    assert "autotest: compilation failed" in stderr
    assert "Traceback" not in stderr


# ---- provide_multi_language_support: files=hello.*


@pytest.mark.parametrize(
    "filename,source",
    [
        ("hello.py", "#!/usr/bin/env python3\nprint('hi')\n"),
        ("hello.sh", "#!/bin/sh\necho hi\n"),
        ("hello.pl", '#!/usr/bin/perl\nprint "hi\\n";\n'),
    ],
)
def test_script_submission_for_a_glob_is_run_under_the_program_name(
    tmp_path, make_exercise, run_autotest, filename, source
):
    # the program is a hard link to the script in the shared directory; a
    # test sees an identical, executable copy
    exercise = make_exercise(
        tmp_path,
        "files=hello.*\nprogram=hello\n"
        f'1 command="cmp hello {filename} && ./hello" expected_stdout="hi\\n"\n',
        files={filename: source},
    )
    stdout, stderr, status = run_autotest(exercise.args)
    assert status == 0, stdout + stderr
    assert "1 tests passed 0 tests failed" in stdout


@pytest.mark.parametrize(
    "filename,interpreter",
    [("hello.js", "node"), ("hello.java", "java")],
)
def test_java_and_js_submissions_get_a_wrapper_script(
    tmp_path, make_exercise, run_autotest, filename, interpreter
):
    # the wrapper is checked rather than run: java/node need not be installed
    exercise = make_exercise(
        tmp_path,
        "files=hello.*\nprogram=hello\n"
        '1 command="test -x hello && cat hello"'
        f" expected_stdout='#!/bin/bash\\n{interpreter} hello \"$@\"'\n",
        files={filename: "// not run\n"},
    )
    stdout, stderr, status = run_autotest(exercise.args)
    assert status == 0, stdout + stderr


# ---- summary lines and exit status


def test_a_test_directory_is_removed_as_soon_as_its_result_is_printed(
    tmp_path, make_exercise, run_autotest
):
    """Only the running test's directory may exist while a test runs.

    A 40-test exercise would otherwise hold 40 copies of the submission at
    once.  Checking after autotest exits proves nothing, because the
    temporary tree is removed whole at exit; the second test counts the
    test directories from inside the run instead.  --no_sandbox so that ..
    is the temporary root rather than the sandbox's.
    """
    counter = "#!/bin/sh\nls -a .. | grep -c '^[.]test-'\n"
    exercise = make_exercise(
        tmp_path,
        "files=a.sh\nprogram=./a.sh\n"
        '1 command="echo first" expected_stdout="first\\n"\n'
        '2 command="./a.sh" expected_stdout="1\\n"\n',
        files={"a.sh": counter},
    )
    stdout, stderr, status = run_autotest(exercise.args + ["--no_sandbox", "-j", "1"])
    assert status == 0, stdout + stderr
    assert "2 tests passed 0 tests failed" in stdout, stdout


def test_all_passed_summary_and_exit_status_0(tmp_path, make_exercise, run_autotest):
    exercise = make_exercise(tmp_path, SPEC, files={"a.sh": SH})
    stdout, _, status = run_autotest(exercise.args)
    assert status == 0
    assert stdout.endswith("1 tests passed 0 tests failed \n")


def test_all_failed_summary_and_exit_status_1(tmp_path, make_exercise, run_autotest):
    exercise = make_exercise(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\n1 command="echo y" expected_stdout="x\\n"\n',
        files={"a.sh": SH},
    )
    stdout, _, status = run_autotest(exercise.args)
    assert status == 1
    assert stdout.endswith("0 tests passed 1 tests failed\n")


def test_could_not_be_run_summary_and_exit_status_1(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\ncheckers=["false"]\n' + SPEC,
        files={"a.sh": SH},
    )
    stdout, _, status = run_autotest(exercise.args)
    assert status == 1
    assert stdout.endswith("0 tests passed 0 tests failed  1 tests could not be run\n")


# ---- selecting tests


SELECTION_SPEC = """files=a.sh b.sh
program=./a.sh
alpha1 expected_stdout="x\\n"
alpha2 arguments=2 expected_stdout="x\\n"
program=./b.sh
beta expected_stdout="y\\n"
files=b.sh
gamma expected_stdout="y\\n"
"""


@pytest.fixture(scope="module")
def selection(tmp_path_factory, make_exercise):
    return make_exercise(
        tmp_path_factory.mktemp("selection"),
        SELECTION_SPEC,
        files={"a.sh": "#!/bin/sh\necho x\n", "b.sh": "#!/bin/sh\necho y\n"},
    )


def labels_run(stdout):
    return [
        line.split(" ")[1] for line in stdout.splitlines() if line.startswith("Test ")
    ]


def test_print_test_names_groups_labels_by_their_files(selection, run_autotest):
    stdout, _, status = run_autotest(selection.args + ["--print_test_names"])
    assert status == 0
    assert json.loads(stdout) == [
        {"files": ["a.sh", "b.sh"], "labels": ["alpha1", "alpha2", "beta"]},
        {"files": ["b.sh"], "labels": ["gamma"]},
    ]


def test_labels_option_runs_only_those_tests(selection, run_autotest):
    stdout, _, status = run_autotest(selection.args + ["-l", "beta", "alpha2"])
    assert status == 0, stdout
    assert labels_run(stdout) == ["alpha2", "beta"]


def test_unknown_label_dies(selection, run_autotest):
    _, stderr, status = run_autotest(selection.args + ["-l", "nope"])
    assert status == 2
    assert "autotest: unknown labels: nope" in stderr


def test_programs_option_runs_the_tests_of_those_programs(selection, run_autotest):
    stdout, _, status = run_autotest(selection.args + ["-p", "./b.sh"])
    assert status == 0, stdout
    assert labels_run(stdout) == ["beta", "gamma"]


def test_extra_argument_regex_selects_matching_labels(selection, run_autotest):
    stdout, _, status = run_autotest(selection.args + ["^alpha"])
    assert status == 0, stdout
    assert labels_run(stdout) == ["alpha1", "alpha2"]


def test_extra_argument_naming_a_label_selects_it(selection, run_autotest):
    stdout, _, status = run_autotest(selection.args + ["gamma"])
    assert status == 0, stdout
    assert labels_run(stdout) == ["gamma"]


# ---- --generate_expected_output


GENERATE_SPEC = (
    'files=a.sh\nprogram=./a.sh\n1 command="echo x"\n2 command="echo y; echo err >&2"\n'
)
GENERATED_LINES = (
    "1 expected_stdout='x\\n'\n2 expected_stdout='y\\n'\n2 expected_stderr='err\\n'\n"
)


def test_generate_expected_output_prints_the_specification_with_outputs(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(tmp_path, GENERATE_SPEC, files={"a.sh": SH})
    stdout, stderr, status = run_autotest(exercise.args + ["-g"])
    assert status == 0, stderr
    assert stdout == (
        GENERATE_SPEC + "### generated by: autotest --generate_expected_output"
        " - see https://github.com/COMP1511UNSW/autotest\n" + GENERATED_LINES
    )
    # tests.txt itself is untouched
    with open(exercise.tests_txt) as f:
        assert f.read() == GENERATE_SPEC


def test_generate_expected_output_other_value_prints_only_the_outputs(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(tmp_path, GENERATE_SPEC, files={"a.sh": SH})
    stdout, stderr, status = run_autotest(exercise.args + ["-g", "outputs_only"])
    assert status == 0, stderr
    assert stdout == GENERATED_LINES


def test_generate_expected_output_update_rewrites_tests_txt_only_when_it_changes(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(tmp_path, GENERATE_SPEC, files={"a.sh": SH})
    stdout, stderr, status = run_autotest(exercise.args + ["-g", "update"])
    assert status == 0, stderr
    assert stdout == ""
    with open(exercise.tests_txt) as f:
        assert f.read().endswith(GENERATED_LINES)
    written = os.stat(exercise.tests_txt)
    # a second run generates the same file, which must not be rewritten
    # (a marker's tests.txt under version control would otherwise churn)
    time.sleep(0.05)
    stdout, stderr, status = run_autotest(exercise.args + ["-g", "update"])
    assert status == 0, stderr
    unchanged = os.stat(exercise.tests_txt)
    assert (written.st_mtime_ns, written.st_ctime_ns) == (
        unchanged.st_mtime_ns,
        unchanged.st_ctime_ns,
    )


# ---- debugging output


def test_debug_output_does_not_crash_and_reports_the_sandbox_decision(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(tmp_path, SPEC, files={"a.sh": SH})
    for flags in (["-d"], ["-dd"], ["-dd", "--no_sandbox"]):
        stdout, stderr, status = run_autotest(exercise.args + flags)
        assert status == 0, stdout + stderr
        assert "Traceback" not in stderr
        assert "1 tests passed 0 tests failed" in stdout
        assert "raw args:" in stderr
    # -dd says, once, whether the sandbox is on, off or unavailable
    assert "sandbox: off (sandbox=False)" in stderr
    stdout, stderr, status = run_autotest(exercise.args + ["-dd"])
    assert status == 0
    assert (
        "sandbox: on SandboxConfig(" in stderr or "running WITHOUT a sandbox" in stderr
    )


def test_a_test_can_inspect_a_file_it_is_not_allowed_to_read(run_autotest, tmp_path):
    """
    A per-test directory copy must carry files whose mode forbids reading.

    COMP1521's file_modes exercise creates a file with mode 223 and then has a
    test list its permissions.  Copying each test its own directory failed on
    that file, so the test saw nothing to report.
    """
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "tests.txt").write_text(
        "files=show.sh\n"
        "program=./show.sh\n"
        'pre_compile_command="chmod 223 unreadable >unreadable"\n'
        "pre_compile_command_shell=1\n"
        '1 expected_stdout="--w--w--wx\\n"\n'
    )
    submission = tmp_path / "sub"
    submission.mkdir()
    show = submission / "show.sh"
    show.write_text('#!/bin/sh\nls -l unreadable | cut -d" " -f1\n')
    show.chmod(0o700)

    stdout, _stderr, status = run_autotest(
        ["-D", str(submission), "-a", str(spec), "--no_sandbox"]
    )
    assert "1 tests passed 0 tests failed" in stdout, stdout
    assert status == 0


def test_each_test_gets_its_own_pre_compile_command(run_autotest, tmp_path):
    """
    A pre_compile_command which differs between tests writes to that test's
    own directory, not to one shared by all of them.

    COMP1521's 25t2final_q4 found this: two of its tests have
    pre_compile_commands which write different contents to the same temp.s.
    Preparing once in a shared directory let whichever ran last decide what
    both tests saw, and one of them failed.
    """
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "tests.txt").write_text(
        "files=show.sh\n"
        "program=./show.sh\n"
        'one pre_compile_command="echo first > chosen.txt"\n'
        'one command="cat chosen.txt" expected_stdout="first\\n"\n'
        'two pre_compile_command="echo second > chosen.txt"\n'
        'two command="cat chosen.txt" expected_stdout="second\\n"\n'
    )
    submission = tmp_path / "sub"
    submission.mkdir()
    show = submission / "show.sh"
    show.write_text("#!/bin/sh\n")
    show.chmod(0o700)

    for extra in ([], ["-j", "4"]):
        stdout, _stderr, status = run_autotest(
            ["-D", str(submission), "-a", str(spec), "--no_sandbox", *extra]
        )
        assert "2 tests passed 0 tests failed" in stdout, (extra, stdout)
        assert status == 0


def test_one_pre_compile_command_shared_by_every_test_runs_once(run_autotest, tmp_path):
    """The shared case keeps preparing once, which is what lets tests share a
    compilation."""
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "tests.txt").write_text(
        "files=show.sh\n"
        "program=./show.sh\n"
        "pre_compile_command=\"sh -c 'echo x >> counted.txt'\"\n"
        'one command="wc -l < counted.txt" expected_stdout="1\\n"\n'
        'two command="wc -l < counted.txt" expected_stdout="1\\n"\n'
    )
    submission = tmp_path / "sub"
    submission.mkdir()
    show = submission / "show.sh"
    show.write_text("#!/bin/sh\n")
    show.chmod(0o700)

    stdout, _stderr, status = run_autotest(
        ["-D", str(submission), "-a", str(spec), "--no_sandbox"]
    )
    assert "2 tests passed 0 tests failed" in stdout, stdout
    assert status == 0


def test_a_sparse_file_is_not_filled_in_when_each_test_is_given_a_copy(
    run_autotest, tmp_path
):
    """
    COMP1521's file_sizes creates files of 420GB and 1TB with dd seek= and has
    a test report their sizes.  They occupy almost no disk, but copying them
    byte by byte into each test's own directory never finished and would have
    filled it.
    """
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "tests.txt").write_text(
        "files=show.sh\n"
        "program=./show.sh\n"
        'pre_compile_command="dd status=none seek=64G bs=1 count=1 </dev/zero >big"\n'
        "pre_compile_command_shell=1\n"
        'one command="stat -c %s big" expected_stdout="68719476737\\n"\n'
        'two command="stat -c %s big" expected_stdout="68719476737\\n"\n'
    )
    submission = tmp_path / "sub"
    submission.mkdir()
    show = submission / "show.sh"
    show.write_text("#!/bin/sh\n")
    show.chmod(0o700)

    stdout, _stderr, status = run_autotest(
        ["-D", str(submission), "-a", str(spec), "--no_sandbox"], timeout=120
    )
    assert "2 tests passed 0 tests failed" in stdout, stdout
    assert status == 0
