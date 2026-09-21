"""
End-to-end tests of the failure explanations run_test.py produces: each
test here pins the text a student sees for one kind of failure, or that a
comparison parameter makes a test pass, by running ./autotest.py on a
small shell-based exercise.

Already covered elsewhere and not repeated here: the show_* parameters
(test_standard.test_show_parameters), non-unicode expected_files
(test_standard.test_non_unicode_file_output), resource limits
(test_standard.test_limits), "same as Test X" de-duplication
(test_integration), postprocess_output_command failures (test_integration)
and the ignore_* parameters when they make tests/ignore pass
(test_standard).

Parameters written on a test's label line apply to that test only, so the
failing cases share one tests.txt and one autotest run (a module fixture);
each test then examines the block printed for its label.
"""

import pytest

SH = "#!/bin/sh\n"

# every test fails in a different way (so none is reported as "same as" an
# earlier one) and pins one explanation path of get_long_explanation
FAILING_SPEC = r"""files=a.sh
program=./a.sh
stderr_wrong command="echo out; echo bad >&2" expected_stdout="out\n" expected_stderr="good\n"
stderr_bytes_wrong unicode_stderr=False command="printf '\\xfa\\xfb' >&2" expected_stderr=b"\xfa\xfa"
stderr_bytes_short unicode_stderr=False command="printf '\\xfa' >&2" expected_stderr=b"\xfa\xfa"
stdout_bytes_wrong unicode_stdout=False command="printf '\\xff\\x0f'" expected_stdout=b"\xff\xff"
dcc_stopped dcc_output_checking=1 command="echo line1; echo line2; echo 'Execution stopped because of a runtime error' >&2; exit 1" expected_stdout="line1\nline2\nline3\n"
stdin_no_newline command="cat" stdin="a\nb\nc" expected_stdout="x\n"
stdin_newline command="cat" stdin="a\nb\n" expected_stdout="y\n"
stdin_bytes unicode_stdin=False command="cat" stdin=b"\xaa\x01" expected_stdout="x\n"
semicolons command="echo a; echo b ; echo c" expected_stdout="x\n"
semicolons_kept no_replace_semicolon_reproduce_command=1 command="echo a; echo b" expected_stdout="x\n"
piped_stdin command="cat; echo b" stdin="in\n" expected_stdout="x\n"
stderr_with_correct_stdout command="echo ok; echo warning >&2" expected_stdout="ok\n"
stderr_hides_stdout command="echo bad; echo warning >&2" expected_stdout="ok\n"
stderr_shows_stdout show_stdout_if_errors=1 command="echo worse; echo warning >&2" expected_stdout="ok\n"
zero_byte command="printf 'ab\\0cd\\n'" expected_stdout="abcd\n"
non_ascii_byte command="printf 'ab\\xc3\\xa9cd\\n'" expected_stdout="abcd\n"
control_character command="printf 'ab\\007cd\\n'" expected_stdout="abcd\n"
compare_only_characters compare_only_characters="abc" command="echo xaxbxb" expected_stdout="abc\n"
ignore_characters ignore_characters="x" command="echo xaxbxcy" expected_stdout="abc\n"
ignore_blank_lines command="printf '\\na\\n\\nb\\n'" expected_stdout="a\nb\n"
ignore_trailing_whitespace ignore_trailing_whitespace=0 command="echo 'a  '" expected_stdout="a\n"
ignore_case command="echo HeLLo" expected_stdout="hello\n"
postprocess_list postprocess_output_command=["tr", "a-z", "A-Z"] command="echo hello" expected_stdout="HELLX\n"
file_missing command="echo x" expected_stdout="x\n" expected_files={"out/one.txt": "1\n"}
file_wrong command="mkdir out; echo 2 >out/one.txt; echo x" expected_stdout="x\n" expected_files={"out/one.txt": "1\n"}
file_empty command="touch one.txt; echo x" expected_stdout="x\n" expected_files={"one.txt": "1\n"}
file_unexpected command="echo 1 >one.txt; echo x" expected_stdout="x\n" expected_files={"one.txt": ""}
"""

# the same parameters, each set so its test passes
PASSING_SPEC = r"""files=a.sh
program=./a.sh
allow_stderr allow_unexpected_stderr=1 command="echo ok; echo warning >&2" expected_stdout="ok\n"
compare_only_characters compare_only_characters="abc" command="echo xaxbxc" expected_stdout="abc\n"
ignore_characters ignore_characters="x" command="echo xaxbxc" expected_stdout="abc\n"
ignore_blank_lines ignore_blank_lines=1 command="printf '\\na\\n\\nb\\n'" expected_stdout="a\nb\n"
ignore_trailing_whitespace ignore_trailing_whitespace=1 command="echo 'a  '" expected_stdout="a\n"
ignore_case ignore_case=1 command="echo HeLLo" expected_stdout="hello\n"
ignore_whitespace ignore_whitespace=1 command="echo 'h e l l o'" expected_stdout="hello\n"
postprocess_list postprocess_output_command=["tr", "a-z", "A-Z"] command="echo hello" expected_stdout="HELLO\n"
file_in_directory command="mkdir out; echo 1 >out/one.txt; echo x" expected_stdout="x\n" expected_files={"out/one.txt": "1\n"}
"""


@pytest.fixture(scope="module")
def failures(tmp_path_factory, make_exercise, run_autotest, split_test_output):
    """the per-test output of one run of FAILING_SPEC"""
    exercise = make_exercise(
        tmp_path_factory.mktemp("failures"), FAILING_SPEC, files={"a.sh": SH}
    )
    stdout, stderr, status = run_autotest(exercise.args)
    assert status == 1, stdout + stderr
    assert "internal error" not in stderr, stderr
    blocks = split_test_output(stdout)
    # no explanation was replaced by "same as Test X": each is examined
    assert not any("same as Test" in block for block in blocks.values()), stdout
    return blocks


@pytest.fixture(scope="module")
def passes(tmp_path_factory, make_exercise, run_autotest):
    """the output of one run of PASSING_SPEC"""
    exercise = make_exercise(
        tmp_path_factory.mktemp("passes"), PASSING_SPEC, files={"a.sh": SH}
    )
    stdout, stderr, status = run_autotest(exercise.args)
    assert status == 0, stdout + stderr
    return stdout


# ---- stderr


def test_wrong_stderr_is_explained_like_wrong_output(failures):
    block = failures["stderr_wrong"]
    assert "- failed (Incorrect stderr)\n" in block
    assert "Your program produced this line of stderr:\nbad\n" in block
    assert "The correct 1 lines of stderr for this test were:\ngood\n" in block
    assert (
        "The difference between your stderr(-) and the correct stderr(+) is:\n"
        "- bad\n+ good\n" in block
    )


def test_wrong_non_unicode_stderr_is_shown_as_hex_with_bit_count(failures):
    block = failures["stderr_bytes_wrong"]
    assert "- failed (Your non-unicode output is not correct)\n" in block
    assert "You had 0xfafb as stderr. You should have 0xfafa\n" in block
    assert (
        "There were 1 different bits between your output and the expected output\n"
        in block
    )


def test_non_unicode_stderr_of_wrong_length_reports_lengths_not_bits(failures):
    block = failures["stderr_bytes_short"]
    assert "You had 0xfa as stderr. You should have 0xfafa\n" in block
    assert "Your output was 1 bytes long. It should have been 2 bytes long. " in block
    assert "different bits" not in block


def test_wrong_non_unicode_stdout_is_shown_as_hex_with_bit_count(failures):
    block = failures["stdout_bytes_wrong"]
    assert "You had 0xff0f as stdout. You should have 0xffff\n" in block
    assert (
        "There were 4 different bits between your output and the expected output\n"
        in block
    )


def test_dcc_execution_stopped_message_replaces_the_diff(failures):
    block = failures["dcc_stopped"]
    assert "- failed (incorrect output)\n" in block
    assert (
        "Your program produced these 2 lines of output before it was terminated:\n"
        "line1\nline2\nExecution stopped because of a runtime error\n" in block
    )
    assert "The difference between" not in block


def test_stderr_with_correct_stdout_says_output_was_correct(failures):
    block = failures["stderr_with_correct_stdout"]
    assert "- failed (errors)\n" in block
    assert (
        "Your program's output was correct but errors occurred:\nwarning\n"
        "Apart from the above errors, your program's output was correct.\n" in block
    )


def test_allow_unexpected_stderr_passes_a_test_with_correct_stdout(passes):
    assert "Test allow_stderr ('echo ok; echo warning >&2') - passed\n" in passes


def test_errors_hide_wrong_stdout_unless_show_stdout_if_errors(failures):
    hidden = failures["stderr_hides_stdout"]
    assert "Your program produced these errors:\nwarning\n" in hidden
    assert "Your program produced this line of output:" not in hidden
    assert "The correct 1 lines of output" not in hidden
    shown = failures["stderr_shows_stdout"]
    assert "Your program produced these errors:\nwarning\n" in shown
    assert "Your program produced this line of output:\nworse\n" in shown
    assert "The correct 1 lines of output for this test were:\nok\n" in shown


# ---- stdin


def test_stdin_without_trailing_newline_gets_a_note_and_echo_n(failures):
    block = failures["stdin_no_newline"]
    assert "\nThe input for this test was:\na\nb\nc\n" in block
    assert "Note: last character in above input is not '\\n'\n" in block
    assert "  echo -n -e 'a\\nb\\nc' | cat\n" in block


def test_stdin_with_trailing_newline_has_no_note(failures):
    block = failures["stdin_newline"]
    assert "\nThe input for this test was:\na\nb\n\n" in block
    assert "Note: last character" not in block
    assert "  echo -e 'a\\nb' | cat\n" in block


def test_non_unicode_stdin_is_shown_as_hex_and_reproduced_with_echo_ne(failures):
    block = failures["stdin_bytes"]
    assert "\nThe input for this test was:\n0xaa01\n" in block
    assert "  echo -ne '\\xaa\\x01' | cat\n" in block


# ---- the reproduce command


def test_semicolons_in_a_shell_command_become_separate_lines(failures):
    block = failures["semicolons"]
    assert (
        "You can reproduce this test by executing these commands:\n"
        "  echo a\n  echo b\n  echo c\n" in block
    )


def test_no_replace_semicolon_reproduce_command_keeps_the_command_intact(failures):
    block = failures["semicolons_kept"]
    assert (
        "You can reproduce this test by executing these commands:\n"
        "  echo a; echo b\n" in block
    )


def test_piped_stdin_parenthesises_a_compound_command(failures):
    assert "  echo in | (cat; echo b)\n" in failures["piped_stdin"]


# ---- bad characters in the output


@pytest.mark.parametrize(
    "label,description,escaped",
    [
        ("zero_byte", "a zero byte ('\\0')", "ab\\x00cd"),
        ("non_ascii_byte", "a non-ascii byte \\xe9", "ab\\xe9cd"),
        ("control_character", "a non-printable character \\x07", "ab\\x07cd"),
    ],
)
def test_bad_character_is_located_and_the_diff_suppressed(
    failures, label, description, escaped
):
    block = failures[label]
    assert f"Byte 3 of line 1 of your program's output is {description}\n" in block
    assert (
        "Here is line 1 with non-printable characters replaced with "
        f"backslash-escaped equivalents:\n\n{escaped}\n\n" in block
    )
    assert "The correct 1 lines of output for this test were:\nabcd\n" in block
    # check_bad_characters turns show_diff off: the diff would repeat the point
    assert "The difference between" not in block


# ---- comparison parameters, each alone


@pytest.mark.parametrize(
    "label,actual",
    [
        ("compare_only_characters", "xaxbxb"),
        ("ignore_characters", "xaxbxcy"),
        ("ignore_case", "HeLLo"),
    ],
)
def test_comparison_parameter_does_not_hide_a_real_difference(failures, label, actual):
    block = failures[label]
    assert "- failed (Incorrect output)\n" in block
    assert f"Your program produced this line of output:\n{actual}\n" in block


def test_blank_lines_count_unless_ignore_blank_lines(failures):
    block = failures["ignore_blank_lines"]
    assert "- failed (Incorrect output)\n" in block
    assert "Your program produced these 4 lines of output:\n" in block


def test_trailing_whitespace_counts_when_ignore_trailing_whitespace_is_off(failures):
    block = failures["ignore_trailing_whitespace"]
    assert "- failed (Incorrect output)\n" in block
    assert "- a  \n+ a\n" in block


@pytest.mark.parametrize(
    "label",
    [
        "compare_only_characters",
        "ignore_characters",
        "ignore_blank_lines",
        "ignore_trailing_whitespace",
        "ignore_case",
        "ignore_whitespace",
    ],
)
def test_comparison_parameter_makes_the_test_pass(passes, split_test_output, label):
    block = split_test_output(passes)[label]
    assert block.endswith(") - passed\n"), block


# ---- postprocess_output_command


def test_postprocess_output_command_list_filters_the_output(
    passes, failures, split_test_output
):
    assert split_test_output(passes)["postprocess_list"].endswith(") - passed\n")
    block = failures["postprocess_list"]
    assert "- failed (Incorrect output)\n" in block
    # the raw output is shown, the filtered text is what was compared
    assert "Your program produced this line of output:\nhello\n" in block
    assert "The correct 1 lines of output for this test were:\nHELLX\n" in block


# ---- expected_files


def test_missing_expected_file_is_reported_by_name(failures):
    block = failures["file_missing"]
    assert "- failed (File file: out/one.txt is empty)\n" in block
    assert (
        "Your program was expected to create a file named 'out/one.txt' and did not\n"
        in block
    )


def test_expected_file_in_a_subdirectory_is_compared_relative_to_the_test_directory(
    passes, failures, split_test_output
):
    assert split_test_output(passes)["file_in_directory"].endswith(") - passed\n")
    block = failures["file_wrong"]
    assert "- failed (Incorrect file: out/one.txt)\n" in block
    assert "Your program produced this line of out/one.txt:\n2\n" in block
    assert "The correct 1 lines of out/one.txt for this test were:\n1\n" in block
    assert (
        "The difference between your out/one.txt(-) and the correct out/one.txt(+) is:\n"
        "- 2\n+ 1\n" in block
    )


def test_empty_expected_file_is_reported_as_no_output_in_the_file(failures):
    block = failures["file_empty"]
    assert "- failed (File file: one.txt is empty)\n" in block
    assert "Your program produced no output in one.txt\n" in block
    assert "\nThe correct one.txt for this test was:\n1\n" in block


def test_unexpected_file_contents_are_reported(failures):
    block = failures["file_unexpected"]
    assert "- failed (file: one.txt should be empty and was not)\n" in block
    assert (
        "No one.txt was expected for this test and your program produced 1 lines of one.txt\n"
        in block
    )


# ---- colour


COLOUR_SPEC = (
    'files=a.sh\nprogram=./a.sh\n1 command="echo bad" expected_stdout="good\\n"\n'
)


def test_colorize_output_adds_escape_sequences(tmp_path, make_exercise, run_autotest):
    exercise = make_exercise(tmp_path, COLOUR_SPEC, files={"a.sh": SH})
    stdout, _, status = run_autotest(exercise.args + ["-P", "colorize_output=1"])
    assert status == 1
    assert "\x1b[31mfailed\x1b[0m (Incorrect output)" in stdout
    assert "\x1b[31m- bad\x1b[0m\n\x1b[32m+ good\x1b[0m\n" in stdout
    assert "\x1b[31m0 tests passed\x1b[0m \x1b[31m1 tests failed\x1b[0m\n" in stdout


def test_output_is_not_coloured_when_stdout_is_not_a_terminal(
    tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(tmp_path, COLOUR_SPEC, files={"a.sh": SH})
    stdout, _, status = run_autotest(exercise.args)
    assert status == 1
    assert "\x1b[" not in stdout
    assert "0 tests passed 1 tests failed\n" in stdout


def test_expected_files_does_not_follow_a_symbolic_link(
    tmp_path, make_exercise, run_autotest
):
    """
    A file the test checks was created by the submitted program, so it may be
    a link the program made.

    Following one reads whatever the account running autotest can read -- and
    check_files prints what it read back to the student in the difference, so
    a link named as an expected_file is an arbitrary read.  The sandbox puts
    those files out of reach; a student on their own account has only this.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("the solution\n")
    spec = (
        "files=a.sh\nprogram=./a.sh\n"
        f'1 command="ln -s {secret} out.txt; echo x" expected_stdout="x\\n"'
        ' expected_files={"out.txt": "1\\n"}\n'
    )
    exercise = make_exercise(tmp_path, spec, files={"a.sh": SH})
    stdout, stderr, status = run_autotest(exercise.args + ["--no_sandbox"])

    assert status == 1, stdout + stderr
    assert "the solution" not in stdout + stderr, stdout + stderr
    assert "created a symbolic link" in stdout, stdout
