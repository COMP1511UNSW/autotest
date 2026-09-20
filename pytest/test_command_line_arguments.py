"""
Unit tests for command_line_arguments: how "autotest lab03 prime.c" and
friends become a test specification and a set of labels to run.

process_arguments() reads sys.argv, so it is driven by monkeypatching
argv and pointing it at a small exercise tree built under tmp_path.  die()
raises InternalError, which autotest.py turns into the message the student
sees, so tests assert on that exception.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import command_line_arguments as cla  # noqa: E402
from util import InternalError  # noqa: E402

PRIME_SPECIFICATION = """files=prime.c
0 command=./prime 1 expected_stdout="no\\n"
1 command=./prime 2 expected_stdout="yes\\n"
other files=other.py optional_files=helper.py command=./other.py expected_stdout=x
"""


@pytest.fixture
def exercises(tmp_path, monkeypatch):
    """
    an exercise tree: prime/tests.txt and lab/automarking/tests.txt

    argv is reset for every test and AUTOTEST_DEBUG unset so the tests are
    independent of the environment they run in.
    """
    prime = tmp_path / "prime"
    prime.mkdir()
    (prime / "tests.txt").write_text(PRIME_SPECIFICATION)
    (tmp_path / "lab" / "automarking").mkdir(parents=True)
    (tmp_path / "lab" / "automarking" / "tests.txt").write_text(
        "files=a.c\nm command=./a\n"
    )
    monkeypatch.delenv("AUTOTEST_DEBUG", raising=False)
    monkeypatch.setattr(sys, "argv", ["autotest"])
    return tmp_path


def process(*arguments):
    sys.argv = ["autotest"] + list(arguments)
    return cla.process_arguments()


def die_message(*arguments):
    with pytest.raises(InternalError) as info:
        process(*arguments)
    return str(info.value)


# finding the test specification


def test_autotest_directory_option_finds_tests_txt_inside_it(exercises):
    args, tests, parameters = process("-a", str(exercises / "prime"))
    assert sorted(tests) == ["0", "1", "other"]
    assert args.autotest_directory == str(exercises / "prime") + "/"
    assert args.test_specification_pathname == str(exercises / "prime" / "tests.txt")
    assert parameters["supplied_files_directory"] == str(exercises / "prime")
    assert args.exercise == "prime"


def test_autotest_directory_option_may_name_the_specification_file(exercises):
    args, tests, _ = process("-a", str(exercises / "prime" / "tests.txt"))
    assert sorted(tests) == ["0", "1", "other"]
    assert args.autotest_directory == str(exercises / "prime") + "/"
    assert args.test_specification_pathname == str(exercises / "prime" / "tests.txt")


def test_relative_autotest_directory_is_made_absolute(exercises, monkeypatch):
    monkeypatch.chdir(exercises)
    args, _, _ = process("-a", "prime")
    assert args.autotest_directory == str(exercises / "prime") + "/"
    args, _, _ = process("-a", "prime/tests.txt")
    assert args.test_specification_pathname == str(exercises / "prime" / "tests.txt")


def test_exercise_is_looked_up_in_the_exercise_directory(exercises):
    args, tests, _ = process("-E", str(exercises), "-e", "prime")
    assert sorted(tests) == ["0", "1", "other"]
    assert args.autotest_directory == str(exercises / "prime") + "/"
    assert args.exercise == "prime"


def test_first_extra_argument_is_the_exercise_when_none_is_given(exercises):
    args, _, _ = process("-E", str(exercises), "prime")
    assert args.exercise == "prime"
    assert args.extra_arguments == []


@pytest.mark.parametrize(
    "name",
    ["prime.c", "lab03_prime", "week10a_prime", "lab03_prime.c", "wk1_lab03_prime"],
)
def test_exercise_name_variants_find_the_same_autotest(exercises, name):
    args, tests, _ = process("-E", str(exercises), name)
    assert sorted(tests) == ["0", "1", "other"]
    assert args.exercise == name


def test_exercise_is_searched_in_the_current_directory_by_default(
    exercises, monkeypatch
):
    monkeypatch.chdir(exercises)
    args, tests, _ = process("prime")
    assert sorted(tests) == ["0", "1", "other"]
    assert args.autotest_directory == str(exercises / "prime") + "/"


def test_several_exercise_directories_are_searched_in_order(exercises, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    args, _, _ = process("-E", str(empty), "-E", str(exercises), "prime")
    assert args.autotest_directory == str(exercises / "prime") + "/"


def test_marking_looks_for_the_automarking_specification(exercises):
    args, tests, _ = process("-E", str(exercises), "-m", "lab")
    assert list(tests) == ["m"]
    assert args.autotest_directory == str(exercises / "lab" / "automarking") + "/"


def test_without_marking_the_automarking_specification_is_not_found(exercises):
    assert die_message("-E", str(exercises), "lab") == "no autotest found for lab"


def test_marking_with_autotest_directory_uses_automarking_txt(exercises):
    (exercises / "prime" / "automarking.txt").write_text(
        "files=a.c\nmarking command=./a\n"
    )
    _, tests, _ = process("-a", str(exercises / "prime"), "-m")
    assert list(tests) == ["marking"]


def test_marking_with_autotest_directory_falls_back_to_tests_txt(exercises):
    _, tests, _ = process("-a", str(exercises / "prime"), "-m")
    assert sorted(tests) == ["0", "1", "other"]


def test_tarfile_and_exercise_pair_is_accepted_positionally(exercises):
    args, tests, _ = process("-E", str(exercises), "submission.tar", "prime")
    assert args.tarfile == "submission.tar"
    assert args.exercise == "prime"
    assert sorted(tests) == ["0", "1", "other"]


def test_specification_without_tests_dies(exercises):
    (exercises / "empty_exercise").mkdir()
    (exercises / "empty_exercise" / "tests.txt").write_text("max_cpu_seconds=1\n")
    assert (
        die_message("-E", str(exercises), "empty_exercise")
        == "no tests found for empty_exercise"
    )


# selecting tests from extra arguments


def selection(exercises, *arguments):
    args, _, _ = process("-a", str(exercises / "prime"), *arguments)
    return args


def test_no_selection_runs_every_test(exercises):
    args = selection(exercises)
    assert args.labels == ["0", "1", "other"]
    assert args.file == {"prime.c", "other.py"}
    assert args.programs == {"prime", "other.py"}
    assert args.optional_files == {"helper.py"}


def test_labels_option_selects_tests_and_their_files(exercises):
    args = selection(exercises, "-l", "0")
    assert args.labels == ["0"]
    assert args.file == {"prime.c"}
    assert args.programs == {"prime"}
    assert args.optional_files == set()


@pytest.mark.parametrize("argument", ["prime.c", "./prime.c"])
def test_filename_argument_selects_tests_using_that_file(exercises, argument):
    args = selection(exercises, argument)
    assert args.labels == ["0", "1"]
    assert args.file == {"prime.c"}


def test_program_name_argument_selects_tests_for_that_program(exercises):
    args = selection(exercises, "prime")
    assert args.labels == ["0", "1"]
    assert args.file == {"prime.c"}


def test_program_name_with_another_suffix_selects_the_program_and_adds_the_file(
    exercises,
):
    args = selection(exercises, "prime.py")
    assert args.labels == ["0", "1"]
    assert args.file == {"prime.py"}


def test_label_argument_selects_that_test(exercises):
    args = selection(exercises, "0")
    assert args.labels == ["0"]


def test_regex_argument_selects_matching_labels(exercises):
    assert selection(exercises, "^1$").labels == ["1"]
    assert selection(exercises, "[0-9]").labels == ["0", "1"]


def test_substring_of_a_program_selects_its_tests(exercises):
    assert selection(exercises, "prime_thing").labels == ["0", "1"]


@pytest.mark.parametrize("argument", ["sub.tar", "sub.tar.gz", "sub.tar.xz"])
def test_tar_argument_becomes_the_tarfile(exercises, argument):
    args = selection(exercises, argument)
    assert args.tarfile == argument
    assert args.labels == ["0", "1", "other"]


def test_git_url_argument_becomes_the_repository(exercises):
    args = selection(exercises, "gitlab@github.com:x/y.git")
    assert args.git == "gitlab@github.com:x/y.git"


def test_plain_git_at_url_argument_becomes_the_repository(exercises):
    args = selection(exercises, "git@github.com:x/y.git")
    assert args.git == "git@github.com:x/y.git"


def test_existing_file_argument_becomes_an_optional_file(exercises):
    extra = exercises / "extra.txt"
    extra.write_text("x")
    args = selection(exercises, str(extra))
    assert str(extra) in args.optional_files
    assert args.labels == ["0", "1", "other"]


def test_unexpected_argument_dies_listing_filenames_and_labels(exercises):
    message = die_message("-a", str(exercises / "prime"), "zzz")
    lines = message.splitlines()
    assert lines[0] == "unexpected argument 'zzz'"
    assert lines[1].startswith("Specify 1+ of these filenames: ")
    assert set(lines[1].split(": ")[1].split()) == {"prime.c", "other.py"}
    assert lines[2].startswith("Or 1+ of these individual tests: ")
    assert set(lines[2].split(": ")[1].split()) == {"0", "1", "other"}


def test_unexpected_argument_is_tolerated_when_runtests_pl_exists(exercises):
    (exercises / "prime" / "runtests.pl").write_text("")
    args = selection(exercises, "zzz")
    assert args.labels == ["0", "1", "other"]


def test_programs_option_selects_all_tests_for_the_program(exercises):
    assert selection(exercises, "-p", "prime").labels == ["0", "1"]
    assert selection(exercises, "-p", "other.py").labels == ["other"]


def test_programs_option_with_no_matching_tests_runs_everything(exercises):
    assert selection(exercises, "-p", "other").labels == ["0", "1", "other"]


def test_file_option_selects_tests_whose_files_match_exactly(exercises):
    args = selection(exercises, "-f", "other.py")
    assert args.labels == ["other"]
    assert args.file == {"other.py"}


def test_file_option_falls_back_to_tests_sharing_any_file(exercises):
    args = selection(exercises, "-f", "prime.c", "other.py")
    assert args.labels == ["0", "1", "other"]


def test_file_option_with_no_matching_tests_runs_everything_with_that_file(exercises):
    args = selection(exercises, "-f", "nomatch.c")
    assert args.labels == ["0", "1", "other"]
    assert args.file == {"nomatch.c"}


# global parameter conveniences


def test_parameters_jobs_and_no_sandbox_become_initial_parameters(exercises):
    args = selection(
        exercises, "-j", "3", "--no_sandbox", "-P", "max_cpu_seconds=5", "-P", "_x=1"
    )
    assert args.initial_parameters["max_cpu_seconds"] == "5"
    assert args.initial_parameters["_x"] == "1"
    assert args.initial_parameters["parallel_tests"] == 3
    assert args.initial_parameters["sandbox"] is False
    assert args.initial_parameters["debug"] == 0


def test_initial_parameters_reach_the_tests(exercises):
    _, tests, parameters = process(
        "-a", str(exercises / "prime"), "-j", "2", "-P", "max_cpu_seconds=5"
    )
    assert tests["0"].parameters["max_cpu_seconds"] == 5
    assert parameters["parallel_tests"] == 2


def test_debug_option_counts_and_prints_arguments(exercises, capsys):
    args = selection(exercises, "-d", "-d")
    assert args.debug == 2
    assert args.initial_parameters["debug"] == 2
    err = capsys.readouterr().err
    assert "raw args:" in err
    assert "normalized args:" in err


def test_debug_comes_from_the_environment_when_not_given(
    exercises, monkeypatch, capsys
):
    monkeypatch.setenv("AUTOTEST_DEBUG", "3")
    args, _, _ = process("-E", str(exercises), "prime")
    assert args.debug == 3
    assert "looking for test specification in" in capsys.readouterr().out


def test_commit_with_git_is_accepted(exercises):
    args = selection(exercises, "-c", "abc123", "-G", "gitlab@host:repo.git")
    assert args.commit == "abc123"
    assert args.git == "gitlab@host:repo.git"


def test_mutually_exclusive_sources_are_rejected_by_argparse(exercises, capsys):
    with pytest.raises(SystemExit):
        selection(exercises, "-S", "-D", "x")
    assert "not allowed with argument" in capsys.readouterr().err


# helpers


def test_regex_matching_labels_ignores_invalid_regexes_and_sorts_matches():
    assert cla.regex_matching_labels("[", {"a"}) == []
    assert cla.regex_matching_labels("^b", {"a", "bb", "ba"}) == ["ba", "bb"]


def test_find_autotest_dir_returns_the_first_existing_path(exercises):
    path = cla.find_autotest_dir(
        [str(exercises)], "prime.c", ["missing.txt", "tests.txt"]
    )
    assert path == os.path.join(str(exercises), "prime", "tests.txt")


def test_find_autotest_dir_dies_without_an_exercise(exercises):
    with pytest.raises(InternalError, match=r"^no autotest found$"):
        cla.find_autotest_dir([str(exercises)], "", ["tests.txt"])
