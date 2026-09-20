"""
Unit tests for parse_test_specification: the parser every tests.txt goes
through.

They drive parse_string() directly with the small initial parameter set
autotest itself supplies, so each construct a spec author can write (and
each mistake they can make) is pinned without running any program.
"""

import io
import os
import sys
import tokenize

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import parse_test_specification as pts  # noqa: E402
from parse_test_specification import (  # noqa: E402
    IGNORE_TOKENS,
    merge_fstring_tokens,
    output_file_without_parameters,
    output_stream_without_parameters,
    parse_file,
    parse_string,
    stringize,
)
from util import TestSpecificationError as SpecificationError  # noqa: E402

INITIAL_PARAMETERS = {"supplied_files_directory": "."}


def parse(specification, **keywords):
    """parse a tests.txt string the way autotest does, returning (tests, globals)"""
    return parse_string(
        specification, initial_parameters=dict(INITIAL_PARAMETERS), **keywords
    )


def parse_error(specification, **keywords):
    """return the message of the specification error a spec raises"""
    with pytest.raises(SpecificationError) as info:
        parse(specification, **keywords)
    return str(info.value)


# assignments, labels and scoping


def test_global_assignment_applies_to_every_following_test():
    tests, _ = parse("max_cpu_seconds=45\nprogram=hello.py\nt1\nt2\n")
    assert tests["t1"]["max_cpu_seconds"] == 45
    assert tests["t2"]["max_cpu_seconds"] == 45
    assert tests["t2"]["command"] == ["hello.py"]


def test_assignment_on_a_label_line_applies_only_to_that_test():
    tests, _ = parse(
        "max_cpu_seconds=45\nprogram=hello.py\nt1 max_cpu_seconds=10\nt2\n"
    )
    assert tests["t1"]["max_cpu_seconds"] == 10
    assert tests["t2"]["max_cpu_seconds"] == 45


def test_later_global_assignment_changes_only_later_tests():
    tests, _ = parse("program=a\nt1\nprogram=b\nt2\n")
    assert tests["t1"]["program"] == "a"
    assert tests["t2"]["program"] == "b"


def test_tests_are_returned_in_order_of_first_appearance():
    tests, _ = parse("program=a\nzebra\napple\nzebra _x=1\nmango\n")
    assert list(tests) == ["zebra", "apple", "mango"]


def test_label_repeated_on_later_lines_adds_parameters_to_the_test():
    tests, _ = parse("program=a\nt _b=1\nt _c=2\n")
    assert tests["t"]["_b"] == "1"
    assert tests["t"]["_c"] == "2"
    assert tests["t"]["_line_number"] == "2"
    assert tests["t"]["_source_name"] == "<string>"


def test_label_alone_on_a_line_creates_a_test_from_global_parameters():
    tests, _ = parse("files=a.c\nt\n")
    assert tests["t"]["files"] == ["a.c"]
    assert tests["t"]["program"] == "a"


def test_label_keyword_creates_a_test_like_a_bare_label():
    tests, _ = parse("program=a\nlabel=last\n")
    assert list(tests) == ["last"]


def test_label_starting_with_a_digit_is_accepted():
    tests, _ = parse("files=a.c\n4_check command=./a\n42 command=./b\n")
    assert list(tests) == ["4_check", "42"]
    assert tests["4_check"]["command"] == "./a"


def test_private_underscore_variables_are_kept_for_later_use():
    tests, _ = parse("files=a.c\n_answer=42\nt\n")
    assert tests["t"]["_answer"] == "42"


# value syntax


def test_unquoted_words_after_an_assignment_become_a_list():
    tests, _ = parse("files=a.c\nt arguments=hello world\n")
    assert tests["t"]["arguments"] == ["hello", "world"]


def test_python_list_is_equivalent_to_unquoted_words():
    tests, _ = parse('files=a.c\nt arguments=["hello","world"]\n')
    assert tests["t"]["arguments"] == ["hello", "world"]


def test_single_unquoted_word_is_wrapped_into_a_list_parameter():
    tests, _ = parse("files=a.c\nt arguments=hello\n")
    assert tests["t"]["arguments"] == ["hello"]


def test_command_words_keep_quoted_arguments_and_globs_together():
    tests, _ = parse("files=a.c\nt command=hello.py \"arg 1\" 'arg 2' *.c\n")
    assert tests["t"]["command"] == ["hello.py", "arg 1", "arg 2", "*.c"]


def test_adjacent_tokens_merge_like_a_shell():
    tests, _ = parse("files=a.c\nt command=\"./a\"'x y'z\n")
    assert tests["t"]["command"] == "./ax yz"


def test_number_immediately_after_a_string_merges_into_it():
    tests, _ = parse('files=a.c\nt command="./a"3\n')
    assert tests["t"]["command"] == "./a3"


def test_flag_with_equals_sign_needs_quotes_or_is_split_at_the_equals():
    tests, _ = parse("files=a.c\nt arguments='--flag=1'\n")
    assert tests["t"]["arguments"] == ["--flag=1"]
    assert "syntax error in assignment" in parse_error(
        "files=a.c\nt command=./a --flag=1\n"
    )


def test_bare_shell_special_characters_are_allowed_as_words():
    tests, _ = parse("files=a.c\nt command=./a $ ! ? `\n")
    assert tests["t"]["command"] == ["./a", "$", "!", "?", "`"]


def test_numbers_are_kept_as_strings():
    tests, _ = parse("files=a.c\nt arguments=42 1.5 0x1f 1e5\n")
    assert tests["t"]["arguments"] == ["42", "1.5", "0x1f", "1e5"]


def test_empty_quoted_string_is_an_empty_argument():
    tests, _ = parse('files=a.c\nt arguments=""\n')
    assert tests["t"]["arguments"] == [""]


def test_unicode_values_survive_parsing():
    tests, _ = parse("files=a.c\nt arguments=é ü stdin='日本'\n")
    assert tests["t"]["arguments"] == ["é", "ü"]
    assert tests["t"]["stdin"] == "日本"


def test_multi_line_triple_quoted_values_span_lines():
    tests, _ = parse(
        "files=a.c\n\ntest42 expected_stdout='''line 1\nline 2\n''' "
        "expected_stderr='''e1\ne2\n'''\n"
    )
    assert tests["test42"]["expected_stdout"] == "line 1\nline 2\n"
    assert tests["test42"]["expected_stderr"] == "e1\ne2\n"
    assert tests["test42"]["_line_number"] == "3"


def test_lists_and_dicts_can_span_lines():
    tests, _ = parse(
        "files=a.c\n_v3 = [4,5,6]\n_v4 = [7,\n8,\n9]\n_v5 = {'a':'b'}\n"
        "_v6 = {\n'c'  : 'd'\n}\nt\n"
    )
    t = tests["t"]
    assert t["_v3"] == [4, 5, 6]
    assert t["_v4"] == [7, 8, 9]
    assert t["_v5"] == {"a": "b"}
    assert t["_v6"] == {"c": "d"}
    assert t["_line_number"] == "10"


def test_several_multi_line_values_on_one_logical_line():
    tests, _ = parse(
        "files=a.c\nt _a = [\n6] _b = [\n5\n,\n6\n\n] _c=[7,8\n\n] _v1='''1\n2\n"
        "''' _v6 = {\n'c'  : 'd'\n}\n"
    )
    t = tests["t"]
    assert (t["_a"], t["_b"], t["_c"]) == ([6], [5, 6], [7, 8])
    assert t["_v1"] == "1\n2\n"
    assert t["_v6"] == {"c": "d"}
    assert t["_line_number"] == "2"


def test_nested_lists_and_dicts_are_literals():
    tests, _ = parse("files=a.c\nt arguments=[1, [2, 3], {'k': 'v'}]\n")
    assert tests["t"]["arguments"] == [1, [2, 3], {"k": "v"}]
    assert tests["t"]["command"] == ["a", "1", "[2, 3]", "{'k': 'v'}"]


def test_list_followed_by_a_word_gives_both():
    tests, _ = parse("files=a.c\nt _t= [1, 2] x\n")
    assert tests["t"]["_t"] == [[1, 2], "x"]


def test_bracket_not_directly_after_equals_is_a_plain_word():
    tests, _ = parse("files=a.c\nt _t=x [1, 2]\n")
    assert tests["t"]["_t"] == ["x", "[1,", "2]"]


def test_tuple_is_not_a_literal_but_text():
    tests, _ = parse("files=a.c\nt stdin=(1,2)\n")
    assert tests["t"]["stdin"] == "(1,2)"


def test_fstring_can_reference_earlier_parameters_and_private_variables():
    tests, _ = parse(
        "files=a.c\n_x=7\nmax_cpu_seconds=5\n"
        "t arguments=f'answer={int(_x) * 6}' stdin=f'{max_cpu_seconds}s'\n"
    )
    assert tests["t"]["arguments"] == ["answer=42"]
    assert tests["t"]["stdin"] == "5s"


def test_fstring_can_index_earlier_list_and_dict_values():
    tests, _ = parse(
        "files=a.c\n_v=[7, 8]\n_d={'a': 'b'}\nt _u=f'{_v[0]}x' _w=f'{_d[\"a\"]}'\n"
    )
    assert tests["t"]["_u"] == "7x"
    assert tests["t"]["_w"] == "b"


def test_fstring_with_format_spec_conversion_and_double_quotes():
    tests, _ = parse("files=a.c\n_x=7\nt _a=f\"{_x:>3}\" _b=f'{_x!r}' _c=f'{_x}{_x}'\n")
    assert tests["t"]["_a"] == "  7"
    assert tests["t"]["_b"] == "'7'"
    assert tests["t"]["_c"] == "77"


def test_triple_quoted_fstring_spans_lines():
    tests, _ = parse("files=a.c\n_x=7\nt stdin=f'''{_x}\nmulti\n'''\n")
    assert tests["t"]["stdin"] == "7\nmulti\n"


def test_fstring_referencing_an_unknown_name_is_an_error_naming_the_line():
    message = parse_error("files=a.c\nt arguments=f'{unknown_var}'\n")
    assert message.startswith("<string>:2:")
    assert "unknown_var" in message


def test_raw_string_keeps_backslashes_and_normal_string_interprets_them():
    tests, _ = parse("files=a.c\nt _x=r'\\n' _y='\\n'\n")
    assert tests["t"]["_x"] == "\\n"
    assert tests["t"]["_y"] == "\n"


def test_comments_and_blank_lines_are_ignored():
    tests, globals_ = parse(
        "\n# aaaa\t\n\n#unseen_3 _a=b\nfiles=a.c\n\nt command=./a # trailing comment\n"
    )
    assert list(tests) == ["t"]
    assert tests["t"]["command"] == "./a"
    assert "_a" not in globals_


def test_whitespace_around_equals_and_tabs_are_accepted():
    tests, _ = parse("files = a.c\nt\targuments = 1\n")
    assert tests["t"]["arguments"] == ["1"]


def test_compiler_args_with_embedded_equals_is_rewritten_for_backwards_compatibility():
    tests, globals_ = parse(
        "compiler_args=-Dmain=_main autotest_add.c add.c -o add\na\n"
    )
    expected = ["-Dmain=_main", "autotest_add.c", "add.c", "-o", "add"]
    assert globals_["compiler_args"] == expected
    assert tests["a"]["compiler_args"] == expected


# errors a spec author can make


def test_unknown_parameter_is_an_error_with_source_and_line_prefix():
    message = parse_error("files=a.c\nt unknown_param=1\n")
    assert message == "<string>:2: error - unknown parameter 'unknown_param'"


def test_dunder_parameter_names_are_rejected_but_single_underscore_allowed():
    assert "unknown parameter '__dunder'" in parse_error("files=a.c\nt __dunder=1\n")
    tests, _ = parse("files=a.c\nt _private=1\n")
    assert tests["t"]["_private"] == "1"


def test_error_prefix_uses_the_given_source_name():
    message = parse_error("files=a.c\nt unknown_param=1\n", source_name="tests.txt")
    assert message.startswith("tests.txt:2: ")


def test_multiple_assignments_to_one_variable_on_a_line_is_an_error():
    message = parse_error("files=a.c\nt arguments=1 arguments=2\n")
    assert message == "<string>:2: error - multiple assignments to variable 'arguments'"


def test_multiple_labels_on_a_line_is_an_error():
    message = parse_error("files=a.c\nt u command=./a\n")
    assert message == "<string>:2: error - multiple test labels: '(t', 'u')"


def test_respecifying_a_parameter_for_a_label_is_an_error():
    message = parse_error("files=a.c\nt command=./a\nt command=./b\n")
    assert message == (
        "<string>:3: error - multiple parameter specifications for test 't' (command)"
    )


@pytest.mark.parametrize(
    "specification",
    [
        "files=a.c\nt stdin='''abc\n",
        "files=a.c\nt arguments=[1,2\n",
        "files=a.c\nt arguments=[1,2 stdin=5\n",
        "files=a.c\nt _t=\\\n",
    ],
)
def test_incomplete_literal_at_end_of_file_is_reported_at_its_first_line(specification):
    assert parse_error(specification) == "<string>:2: incomplete literal"


@pytest.mark.parametrize(
    "specification",
    [
        "files=a.c\nt =\n",
        "files=a.c\nt ==\n",
        "files=a.c\n= 1\n",
        "files=a.c\nt command==./a\n",
    ],
)
def test_syntax_error_in_assignment_is_reported_with_line(specification):
    message = parse_error(specification)
    assert message.startswith("<string>:2:")
    assert "syntax error in assignment" in message


def test_word_containing_equals_after_a_list_value_is_a_new_assignment():
    assert "unknown parameter 'b'" in parse_error("files=a.c\nt arguments=a b=c\n")


def test_invalid_parameter_value_is_reported_with_line_prefix():
    message = parse_error("files=a.c\nt max_cpu_seconds=abc\n")
    assert (
        message
        == "<string>:2: error invalid value for parameter 'max_cpu_seconds': abc"
    )


def test_test_with_no_program_files_or_command_is_an_error():
    assert "required parameter 'program' not specified" in parse_error("42\n")


# parse_file, parse_string and initial values


def test_parse_file_defaults_supplied_files_directory_to_the_spec_directory(tmp_path):
    spec = tmp_path / "tests.txt"
    spec.write_text("files=a.c\nt command=./a\n")
    tests, globals_ = parse_file(str(spec))
    assert globals_["supplied_files_directory"] == str(tmp_path)
    assert tests["t"]["supplied_files_directory"] == str(tmp_path)
    assert tests["t"]["_source_name"] == str(spec)


def test_parse_file_of_a_bare_filename_uses_the_current_directory(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests.txt").write_text("files=a.c\nt command=./a\n")
    _, globals_ = parse_file("tests.txt")
    assert globals_["supplied_files_directory"] == "."


def test_parse_file_keeps_an_explicit_supplied_files_directory(tmp_path):
    spec = tmp_path / "tests.txt"
    spec.write_text("files=a.c\nt command=./a\n")
    _, globals_ = parse_file(
        str(spec), initial_parameters={"supplied_files_directory": "/elsewhere"}
    )
    assert globals_["supplied_files_directory"] == "/elsewhere"


def test_parse_string_does_not_invent_a_supplied_files_directory():
    _, globals_ = parse_string("max_cpu_seconds=3\n", normalize_global_parameters=False)
    assert globals_ == {"max_cpu_seconds": "3"}


def test_parse_string_does_not_modify_the_callers_initial_parameters():
    initial = {"supplied_files_directory": "."}
    parse_string("files=a.c\nt command=./a\n", initial_parameters=initial)
    assert initial == {"supplied_files_directory": "."}


def test_initial_tests_are_kept_before_parsed_tests():
    initial_tests = {
        "first": {
            "label": "first",
            "files": ["a.c"],
            "command": "./a",
            "supplied_files_directory": ".",
        }
    }
    tests, _ = parse("files=b.c\nsecond\n", initial_tests=initial_tests)
    assert list(tests) == ["first", "second"]
    assert tests["first"]["command"] == "./a"


def test_normalized_global_parameters_get_defaults_but_need_no_program():
    _, globals_ = parse("max_cpu_seconds=3\n")
    assert globals_["max_cpu_seconds"] == 3
    assert globals_["max_real_seconds"] == 60
    assert "program" not in globals_ or not globals_["program"]


def test_unnormalized_global_parameters_are_left_as_written():
    _, globals_ = parse("max_cpu_seconds=3\n", normalize_global_parameters=False)
    assert globals_ == {"supplied_files_directory": ".", "max_cpu_seconds": "3"}


def test_debug_output_does_not_change_the_result(capsys):
    tests, _ = parse("files=a.c\n_v=[1,\n2]\nt command=./a _d={'k': 'v'}\n", debug=10)
    assert tests["t"]["command"] == "./a"
    assert tests["t"]["_v"] == [1, 2]
    assert "parse_literals" in capsys.readouterr().out


# writing a specification back without expected output


def test_output_without_parameters_removes_expected_output_and_keeps_the_rest():
    specification = (
        "# leading comment\nfiles=a.c\n\n"
        "t1 expected_stdout='''a\nb\n''' arguments=1\n"
        't2 expected_stdout="x" expected_stderr="y"\n'
        "t2 arguments=2\n"
        "### generated line\n"
        "t3 arguments=3 # comment kept\n"
        'expected_stdout="global"\n'
        'max_cpu_seconds=5 expected_stderr="g2"\n'
        "\nt4 \n"
    )
    out = io.StringIO()
    output_stream_without_parameters(
        io.StringIO(specification),
        "<s>",
        dict(INITIAL_PARAMETERS),
        None,
        ["expected_stdout", "expected_stderr"],
        0,
        out,
    )
    assert out.getvalue() == (
        "# leading comment\nfiles=a.c\n\n"
        "t1 arguments='1'\n"
        "t2 arguments=2\n"
        "t3 arguments=3 # comment kept\n"
        "max_cpu_seconds='5'\n"
        "\nt4 \n"
    )


def test_output_without_parameters_round_trips_to_the_same_tests():
    specification = (
        "files=a.c\nt1 arguments=1 expected_stdout='''a\nb\n'''\nt2 stdin=x\n"
    )
    out = io.StringIO()
    output_stream_without_parameters(
        io.StringIO(specification),
        "<s>",
        dict(INITIAL_PARAMETERS),
        None,
        ["expected_stdout"],
        0,
        out,
    )
    original, _ = parse(specification)
    stripped, _ = parse(out.getvalue())
    assert list(stripped) == ["t1", "t2"]
    assert stripped["t1"]["arguments"] == original["t1"]["arguments"]
    assert stripped["t1"]["expected_stdout"] == ""
    assert stripped["t2"]["stdin"] == "x"


def test_output_file_without_parameters_defaults_to_removing_expected_streams(tmp_path):
    spec = tmp_path / "tests.txt"
    spec.write_text("files=a.c\nt arguments=1 expected_stdout=x expected_stderr=y\n")
    out = io.StringIO()
    output_file_without_parameters(str(spec), file=out)
    assert out.getvalue() == "files=a.c\nt arguments='1'\n"


def test_output_file_without_parameters_keeps_lines_without_removed_parameters(
    tmp_path,
):
    spec = tmp_path / "tests.txt"
    spec.write_text("files=a.c\nt  arguments=1   # spacing kept\n")
    out = io.StringIO()
    output_file_without_parameters(str(spec), remove_parameters=["stdin"], file=out)
    assert out.getvalue() == "files=a.c\nt  arguments=1   # spacing kept\n"


# helpers


def test_stringize_converts_leaves_but_not_containers():
    assert stringize({"a": [1, 2.5, None, {"b": True}], "c": "s"}) == {
        "a": ["1", "2.5", "None", {"b": "True"}],
        "c": "s",
    }
    assert stringize(5) == "5"
    assert stringize("already") == "already"


def tokens_of(source):
    return [
        (tokenize.tok_name[t.type], t.string)
        for t in merge_fstring_tokens(
            tokenize.generate_tokens(io.StringIO(source).readline), source
        )
        if t.type not in IGNORE_TOKENS
    ]


def test_merge_fstring_tokens_passes_plain_tokens_through():
    assert tokens_of("x = 'plain' 42") == [
        ("NAME", "x"),
        ("OP", "="),
        ("STRING", "'plain'"),
        ("NUMBER", "42"),
    ]


@pytest.mark.skipif(
    pts.FSTRING_START is None, reason="f-strings are single tokens before Python 3.12"
)
def test_merge_fstring_tokens_reassembles_an_fstring_into_one_string_token():
    assert tokens_of("f'a{b}c' d") == [("STRING", "f'a{b}c'"), ("NAME", "d")]


@pytest.mark.skipif(
    pts.FSTRING_START is None, reason="f-strings are single tokens before Python 3.12"
)
def test_merge_fstring_tokens_handles_nested_fstrings():
    assert tokens_of("x=f'{f\"{y}\"}'") == [
        ("NAME", "x"),
        ("OP", "="),
        ("STRING", "f'{f\"{y}\"}'"),
    ]


def test_fstring_escaped_braces_are_literal_braces():
    tests, _ = parse("files=a.c\n_x=7\nt _t=f'{_x} {{literal}}'\n")
    assert tests["t"]["_t"] == "7 {literal}"


def test_fstring_expression_containing_spaces_is_evaluated():
    tests, _ = parse("files=a.c\n_x=7\nt _t=f'{_x if _x else 0}'\n")
    assert tests["t"]["_t"] == "7"


@pytest.mark.parametrize("prefix", ["F", "rf"])
def test_fstring_with_other_valid_prefixes_is_evaluated(prefix):
    tests, _ = parse(f"files=a.c\n_x=7\nt _t={prefix}'{{_x}}'\n")
    assert tests["t"]["_t"] == "7"
