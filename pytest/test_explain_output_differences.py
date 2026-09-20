"""
Unit tests for explain_output_differences: the wording a student sees when
their output is wrong.

explain_output_differences() is called directly with small literal inputs
(the canonical forms are the same as the raw ones unless a test says
otherwise) and the tests assert on the exact phrases a novice reads.
"""

import os
import sys
from collections import defaultdict

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from explain_output_differences import (  # noqa: E402
    create_diff,
    explain_output_differences,
    sanitize_string,
)

RED = "\x1b[31m"
GREEN = "\x1b[32m"
RESET = "\x1b[0m"


def explain(
    expected,
    actual,
    name="stdout",
    canonical_expected=None,
    canonical_actual=None,
    **overrides,
):
    """explain a difference the way run_test does, with colour off unless asked"""
    canonical_expected = expected if canonical_expected is None else canonical_expected
    canonical_actual = actual if canonical_actual is None else canonical_actual
    parameters = {"colorize_output": False}
    parameters.update(overrides)
    return explain_output_differences(
        name,
        expected,
        canonical_expected,
        canonical_expected,
        actual,
        canonical_actual,
        canonical_actual,
        **parameters,
    )


def numbered_lines(count, suffix=""):
    return "".join(f"{i}{suffix}\n" for i in range(count))


# no output at all


def test_no_output_in_stdout_shows_the_correct_output():
    assert explain("hello\n", "") == (
        "Your program produced no output in stdout\n\nThe correct stdout for this test was:\nhello\n"
    )


def test_no_output_on_stderr_uses_on_instead_of_in():
    assert explain("hello\n", "", name="stderr").startswith(
        "Your program produced no output on stderr\n"
    )


def test_no_output_for_the_generic_output_name_is_a_single_line():
    assert explain("hello\n", "", name="output") == "Your program produced no output\n"


def test_no_output_without_show_expected_output_omits_the_correct_output():
    assert (
        explain("hello\n", "", show_expected_output=False)
        == "Your program produced no output in stdout\n"
    )


# one character out at the end


@pytest.mark.parametrize(
    "expected,actual,missing",
    [
        ("hello world\n", "hello world", "'\\n'"),
        ("hi.\n", "hi.", "'\\n'"),
        ("hi \n", "hi ", "'\\n'"),
        ("hello world.", "hello world", "'.'"),
    ],
)
def test_missing_trailing_character_is_named(expected, actual, missing):
    assert explain(expected, actual) == (
        f"Your program's stdout was correct except it was missing a {missing} character on the end.\n"
    )


def test_missing_trailing_character_is_not_used_for_a_short_output():
    explanation = explain("abc", "ab")
    assert "missing a" not in explanation
    assert "- ab\n+ abc\n" in explanation


@pytest.mark.parametrize(
    "expected,actual,extra",
    [
        ("hello world", "hello world\n", "'\\n'"),
        ("hi.", "hi.\n", "'\\n'"),
        ("hello world\n", "hello world\nx", "'x'"),
    ],
)
def test_extra_trailing_character_is_named(expected, actual, extra):
    assert explain(expected, actual) == (
        f"Your program's stdout was correct except it had an extra {extra} character on the end.\n"
    )


def test_extra_replacement_character_hints_at_printing_eof():
    assert explain("hello world\n", "hello world\n\ufffd") == (
        "Your program's stdout was correct except it had an extra '\\xff' character on the end.\n"
        "This can result from printing the EOF value returned by getchar.\n"
    )


def test_extra_newline_after_trailing_spaces_stripped_by_canonicalisation():
    # the expected output ends in spaces which canonicalisation strips
    assert explain(
        "hello world  ", "hello world\n", canonical_expected="hello world"
    ).startswith(
        "Your program's stdout was correct except it had an extra '\\n' character"
    )


# output where none was expected


def test_no_output_expected_lists_what_was_produced():
    assert explain("", "a\nb\n") == (
        "No stdout was expected for this test and your program produced 2 lines of stdout\n"
        "Your program produced these 2 lines of stdout:\na\nb\n"
    )


def test_no_output_expected_single_line_wording():
    assert explain("", "a\n") == (
        "No stdout was expected for this test and your program produced 1 lines of stdout\n"
        "Your program produced this line of stdout:\na\n"
    )


def test_no_output_expected_without_show_actual_output_omits_the_listing():
    assert explain("", "a\n", show_actual_output=False) == (
        "No stdout was expected for this test and your program produced 1 lines of stdout\n"
    )


# lines in the wrong order


def test_correct_lines_in_wrong_order_are_reported():
    explanation = explain("1\n2\n3\n", "3\n2\n1\n")
    assert explanation.startswith(
        "\nYour program produced the correct stdout lines but in the wrong order.\n"
    )
    assert (
        "The difference between your stdout(-) and the correct stdout(+) is:\n+ 1\n+ 2\n  3\n- 2\n- 1\n"
        in explanation
    )


def test_wrong_order_is_not_reported_for_two_lines():
    assert "wrong order" not in explain("1\n2\n", "2\n1\n")


# a single character substituted or inserted


def test_replacing_every_occurrence_of_one_character_would_fix_the_output():
    explanation = explain("hello world, hello\n", "hellx wxrld, hellx\n")
    assert explanation.endswith(
        "\nYour program's stdout would be correct if you replaced all 'x' characters with 'o' characters.\n"
    )
    assert "The difference between" not in explanation


def test_replacing_one_character_would_fix_the_output():
    explanation = explain("a\nb\nc\nd\ne\n", "a\nb\nX\nd\ne\n")
    assert explanation.endswith(
        "\nYour program's stdout would be correct if you replaced a 'X' with a 'c' character.\n"
    )


def test_removing_one_character_would_fix_the_output():
    assert explain("hello world\n", "hel-lo world\n").endswith(
        "Your program's stdout would be correct if you removed a '-' character.\n"
    )


def test_removing_every_occurrence_of_one_character_would_fix_the_output():
    assert explain("hello world\n", "hel-lo wor-ld\n").endswith(
        "Your program's stdout would be correct if you removed all '-' characters.\n"
    )


def test_transliteration_hint_is_not_used_for_short_output():
    explanation = explain("abcd\n", "abcx\n")
    assert "would be correct" not in explanation
    assert "- abcx\n" in explanation


# the difference listing


def test_difference_listing_marks_wrong_and_correct_lines():
    assert explain("abc\ndef\n", "abd\ndef\n") == (
        "Your program produced these 2 lines of stdout:\nabd\ndef\n\n"
        "The correct 2 lines of stdout for this test were:\nabc\ndef\n\n"
        "The difference between your stdout(-) and the correct stdout(+) is:\n- abd\n+ abc\n  def\n"
    )


def test_column_markers_are_shown_when_lines_are_unchanged_by_canonicalisation():
    explanation = explain("hello world!!\n", "hello world!?\n")
    assert "- hello world!?\n?             ^\n" in explanation
    assert "+ hello world!!\n?             ^\n" in explanation


def test_column_markers_are_omitted_when_canonicalisation_changed_the_line():
    explanation = explain("ABC\ndef\n", "abd\ndef\n", canonical_expected="abc\ndef\n")
    assert "- abd\n+ ABC\n  def\n" in explanation
    assert "?" not in explanation


def test_single_line_output_is_described_in_the_singular():
    assert explain("abcd\n", "abcx\n").startswith(
        "Your program produced this line of stdout:\nabcx\n"
    )


def test_missing_last_line_and_extra_last_line():
    assert explain("a\nb\nc\n", "a\nb\n").endswith("is:\n...\n  b\n+ c\n")
    assert explain("a\nb\nc\n", "a\nb\nc\nd\n").endswith("is:\n...\n  c\n- d\n")


def test_matching_prefix_and_suffix_are_elided_leaving_one_line_of_context():
    lines = [f"line {i}" for i in range(10)]
    expected = "\n".join(lines) + "\n"
    actual = "\n".join(lines[:5] + ["WRONG"] + lines[6:]) + "\n"
    assert explain(expected, actual).endswith(
        "is:\n...\n  line 4\n- WRONG\n+ line 5\n  line 6\n...\n"
    )


def test_differences_at_both_ends_keep_no_elision_markers():
    lines = [f"line {i}" for i in range(10)]
    expected = "\n".join(lines) + "\n"
    actual = "\n".join(["WRONG"] + lines[1:9] + ["WRONG2"]) + "\n"
    assert explain(expected, actual).endswith(
        "is:\n- WRONG\n+ line 0\n  line 1\n  line 8\n- WRONG2\n+ line 9\n"
    )


def test_more_than_128_differing_lines_are_truncated():
    explanation = explain(numbered_lines(300), numbered_lines(300, "x"))
    assert explanation.startswith(
        "Your program produced these 300 lines of stdout:\n0x\n"
    )
    assert "31x\n...\n" in explanation
    assert "200x" not in explanation
    assert explanation.count("...") >= 2


def test_max_lines_shown_elides_listings_and_the_diff():
    explanation = explain(
        numbered_lines(10), numbered_lines(10, "x"), max_lines_shown=3
    )
    assert (
        "Your program produced these 10 lines of stdout:\n0x\n1x\n2x\n...\n"
        in explanation
    )
    assert (
        "The correct 10 lines of stdout for this test were:\n0\n1\n2\n...\n"
        in explanation
    )
    assert "9x" not in explanation


def test_max_lines_shown_limits_the_difference_listing():
    explanation = explain(
        numbered_lines(20, "q"), numbered_lines(20, "zz"), max_lines_shown=3
    )
    assert explanation.endswith("is:\n- 0zz\n- 1zz\n")


def test_show_all_lines_never_elides():
    explanation = explain(
        numbered_lines(10),
        numbered_lines(10, "x"),
        max_lines_shown=3,
        show_all_lines=True,
    )
    assert "..." not in explanation
    assert "9x\n" in explanation
    assert "\n9\n" in explanation


def test_show_all_lines_keeps_every_context_line_in_the_diff():
    explanation = explain(
        "a\nb\nc\nd\n", "a\nb\nX\nd\n", show_all_lines=True, show_diff=True
    )
    assert explanation.endswith("is:\n  a\n  b\n- X\n+ c\n  d\n")


def test_show_diff_false_omits_the_difference_and_hints():
    explanation = explain("hello world\n", "hel-lo wor-ld\n", show_diff=False)
    assert explanation == (
        "Your program produced this line of stdout:\nhel-lo wor-ld\n\n"
        "The correct 1 lines of stdout for this test were:\nhello world\n"
    )


def test_show_actual_output_false_omits_both_listings():
    explanation = explain("hello world\n", "hel-lo wor-ld\n", show_actual_output=False)
    assert (
        explanation
        == "Your program's stdout would be correct if you removed all '-' characters.\n"
    )


def test_show_expected_output_false_omits_the_correct_output():
    explanation = explain("abc\ndef\n", "abd\ndef\n", show_expected_output=False)
    assert "The correct" not in explanation
    assert explanation.startswith(
        "Your program produced these 2 lines of stdout:\nabd\ndef\n"
    )
    assert "- abd\n+ abc\n" in explanation


def test_unterminated_last_line_is_noted():
    explanation = explain("abc\ndef\n", "abd\ndef")
    assert (
        "abd\ndef\nLast line of output above was not terminated with a newline('\\n') character\n\n"
        in explanation
    )


def test_unterminated_last_line_is_not_noted_when_expected_is_also_unterminated():
    assert "not terminated" not in explain("abc\ndef", "abd\ndef")


def test_debug_prints_the_call(capsys):
    explain("abc\n", "abd\n", debug=True)
    assert (
        capsys.readouterr().out
        == "explain_output_differences(stdout, 'abc\n', 'abd\n')\n"
    )


# colour


def test_colour_highlights_the_missing_character():
    assert explain("hello world\n", "hello world", colorize_output=True) == (
        f"Your program's stdout was correct except it was missing a {RED}'\\n'{RESET} character on the end.\n"
    )


def test_colour_marks_no_output_red_and_expected_green():
    assert explain("hello\n", "", colorize_output=True) == (
        f"{RED}Your program produced no output in stdout\n{RESET}\nThe correct stdout for this test was:\n{GREEN}hello\n{RESET}"
    )


def test_colour_marks_wrong_lines_red_and_matching_lines_green():
    explanation = explain("abc\ndef\n", "abd\ndef\n", colorize_output=True)
    assert (
        f"Your program produced these 2 lines of stdout:\n{RED}abd{RESET}\n{GREEN}def{RESET}\n"
        in explanation
    )
    assert (
        f"The correct 2 lines of stdout for this test were:\n{GREEN}abc\ndef\n{RESET}"
        in explanation
    )
    assert (
        f"The difference between your stdout({RED}-{RESET}) and the correct stdout({GREEN}+{RESET}) is:\n"
        in explanation
    )
    assert f"{RED}- abd{RESET}\n{GREEN}+ abc{RESET}\n  def\n" in explanation


def test_colour_marks_unexpected_output_red():
    assert explain("", "a\n", colorize_output=True) == (
        f"{RED}No stdout was expected for this test and your program produced 1 lines of stdout\n{RESET}"
        f"Your program produced this line of stdout:\n{RED}a{RESET}\n"
    )


def test_colour_marks_the_characters_in_the_transliteration_hint():
    explanation = explain(
        "hello world, hello\n", "hellx wxrld, hellx\n", colorize_output=True
    )
    assert explanation.endswith(
        f"replaced all '{RED}x{RESET}' characters with '{GREEN}o{RESET}' characters.\n"
    )
    explanation = explain("hello world\n", "hel-lo world\n", colorize_output=True)
    assert explanation.endswith(f"removed a '{RED}-{RESET}' character.\n")


def test_colour_marks_wrong_order_message_red():
    assert explain("1\n2\n3\n", "3\n2\n1\n", colorize_output=True).startswith(
        f"{RED}\nYour program produced the correct stdout lines but in the wrong order.\n{RESET}"
    )


# sanitize_string


def test_control_characters_tabs_and_escapes_are_made_visible():
    assert (
        sanitize_string("a\tb\x01c\x1b[31mred\\\n", colorize_output=False)
        == "a\\tb\\x01c\\x1b[31mred\\\\\n"
    )


def test_non_ascii_characters_are_escaped():
    assert sanitize_string("é\n", colorize_output=False) == "\\xe9\n"


def test_leave_tabs_keeps_tabs_and_backslashes():
    assert (
        sanitize_string("a\tb\\\n", leave_tabs=True, colorize_output=False)
        == "a\tb\\\n"
    )


def test_leave_colorization_keeps_ansi_sequences():
    assert sanitize_string(
        "\x1b[31mred\x1b[0m\n", leave_colorization=True, colorize_output=False
    ) == ("\x1b[31mred\x1b[0m\n")


def test_long_lines_are_truncated_with_an_ellipsis():
    assert (
        sanitize_string("abcdefghij\n", max_line_length_shown=4, colorize_output=False)
        == "abcd ...\n"
    )


def test_lines_beyond_max_lines_shown_are_elided():
    assert (
        sanitize_string(numbered_lines(10), max_lines_shown=3, colorize_output=False)
        == "0\n1\n2\n...\n\n"
    )


def test_show_all_lines_keeps_every_line():
    assert sanitize_string(
        numbered_lines(10),
        max_lines_shown=3,
        show_all_lines=True,
        colorize_output=False,
    ) == numbered_lines(10)


def test_repeated_last_line_is_compressed():
    assert sanitize_string(
        "a\n" + "same\n" * 40, max_lines_shown=10, colorize_output=False
    ) == ("a\nsame\n<last line repeated 40 times>\n")
    assert sanitize_string(
        "same\n" * 40, max_lines_shown=10, colorize_output=False
    ) == ("same\n<last line repeated 40 times>\n")


def test_repeat_message_is_red_when_colouring():
    assert sanitize_string("same\n" * 40, max_lines_shown=10, colorize_output=True) == (
        f"same\n{RED}<last line repeated 40 times>{RESET}\n"
    )


def test_repeats_are_not_compressed_when_the_rest_would_still_be_elided():
    assert sanitize_string(
        numbered_lines(20) + "same\n" * 6, max_lines_shown=10, colorize_output=False
    ) == ("0\n1\n2\n3\n4\n5\n6\n7\n8\n9\n...\n\n")


def test_few_repeats_are_compressed_when_everything_else_fits():
    assert sanitize_string(
        numbered_lines(4) + "same\n" * 6, max_lines_shown=10, colorize_output=False
    ) == ("0\n1\n2\n3\nsame\n<last line repeated 6 times>\n")


def test_line_color_colours_only_the_given_lines():
    line_color = defaultdict(lambda: "")
    line_color[1] = "red"
    assert (
        sanitize_string("a\nb\nc\n", line_color=line_color, colorize_output=False)
        == f"a\n{RED}b{RESET}\nc\n"
    )


def test_empty_and_unterminated_strings_end_with_one_newline():
    assert sanitize_string("", colorize_output=False) == "\n"
    assert sanitize_string("no newline", colorize_output=False) == "no newline\n"


def test_string_parameters_are_accepted_for_limits():
    assert (
        sanitize_string(
            "abcdef\n",
            max_line_length_shown="3",
            max_lines_shown="5",
            colorize_output=False,
        )
        == "abc ...\n"
    )


# create_diff


def no_color(text, *_args, **_keywords):
    return text


def test_create_diff_survives_line_counts_that_disagree(capsys):
    # a postprocess command can change the number of lines so the raw
    # lines run out before the canonical ones: the partial diff is kept
    result = create_diff(
        ["a", "b", "c"],
        ["a", "x", "c"],
        ["a"],
        ["a"],
        "stdout",
        no_color,
        32,
        False,
        True,
        defaultdict(lambda: "green"),
    )
    assert result == [
        "The difference between your stdout(-) and the correct stdout(+) is:",
        "  a",
    ]
    assert "IndexError: unexpected diff output" in capsys.readouterr().out


def test_create_diff_does_not_print_without_debug(capsys):
    create_diff(
        ["a", "b", "c"],
        ["a", "x", "c"],
        ["a"],
        ["a"],
        "stdout",
        no_color,
        32,
        False,
        False,
        defaultdict(lambda: "green"),
    )
    assert capsys.readouterr().out == ""


def test_create_diff_drops_matching_empty_first_and_last_lines():
    assert create_diff(
        ["", "b"],
        ["", "x"],
        ["", "b"],
        ["", "x"],
        "stdout",
        no_color,
        32,
        False,
        False,
        defaultdict(lambda: "green"),
    )[1:] == ["...", "- b", "+ x"]
    assert create_diff(
        ["b", ""],
        ["x", ""],
        ["b", ""],
        ["x", ""],
        "stdout",
        no_color,
        32,
        False,
        False,
        defaultdict(lambda: "green"),
    )[1:] == ["- b", "+ x", "..."]


def test_create_diff_records_which_actual_lines_are_wrong():
    actual_line_color: defaultdict = defaultdict(lambda: "green")
    create_diff(
        ["a", "b", "c", "d"],
        ["a", "x", "c", "d"],
        ["a", "b", "c", "d"],
        ["a", "x", "c", "d"],
        "stdout",
        no_color,
        32,
        False,
        False,
        actual_line_color,
    )
    assert actual_line_color[1] == "red"
    assert actual_line_color[0] == "green"
    assert actual_line_color[2] == "green"


def test_create_diff_of_nothing_is_just_the_heading():
    assert create_diff(
        [],
        [],
        [],
        [],
        "stdout",
        no_color,
        32,
        False,
        False,
        defaultdict(lambda: "green"),
    ) == ["The difference between your stdout(-) and the correct stdout(+) is:"]
