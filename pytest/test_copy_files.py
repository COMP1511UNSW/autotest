"""
Tests of copy_files_to_temp_directory.py: every way a submission can reach
the temporary test directory (-D, --tarfile local or http, --stdin, --git,
and the default glob copy from the current directory with its
include/require kludge), the files the autotest supplies winning in each
mode, the *.expected_* files being made read-only, and the registry of
temporary directories which cleanup removes.

Already covered elsewhere and not repeated here: -D with a symlink to a
private file not being readable inside the sandbox (test_integration),
supplied files winning over a submission given with -D (test_fixes),
--git without --commit and --stdin with a relative -a (test_fixes), and
the temporary directory being removed on exit and on SIGINT (test_fixes,
test_parallel).
"""

import functools
import http.server
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import types

import copy_files_to_temp_directory as temp_directories
import pytest

# the exercise every submission mode is checked against: the submission's
# a.sh and extra.txt are tested, and extra.txt is also supplied by the
# autotest, whose copy must be the one the test sees
SPEC = (
    "files=a.sh\nprogram=./a.sh\n"
    '1 command="./a.sh; cat extra.txt" expected_stdout="sub\\nSUPPLIED\\n"\n'
)
SUBMISSION = {"a.sh": "#!/bin/sh\necho sub\n", "extra.txt": "SUBMITTED\n"}
SUPPLIED = {"extra.txt": "SUPPLIED\n"}
PASSED = "Test 1 ('./a.sh; cat extra.txt') - passed\n1 tests passed 0 tests failed \n"


@pytest.fixture(scope="module")
def exercise(tmp_path_factory, make_exercise):
    return make_exercise(
        tmp_path_factory.mktemp("modes"), SPEC, files=SUBMISSION, supplied=SUPPLIED
    )


def make_tar(path, submission, mode):
    with tarfile.open(path, mode) as tar:
        for name in sorted(os.listdir(submission)):
            tar.add(os.path.join(submission, name), arcname=name)
    return path


@pytest.mark.parametrize("suffix,mode", [(".tar", "w"), (".tar.gz", "w:gz")])
def test_tarfile_submission_is_extracted_and_supplied_files_win(
    tmp_path, exercise, run_autotest, suffix, mode
):
    tar = make_tar(str(tmp_path / ("s" + suffix)), exercise.submission, mode)
    stdout, stderr, status = run_autotest(["-t", tar, "-a", exercise.autotest])
    assert status == 0, stdout + stderr
    assert stdout.endswith(PASSED)


def test_tarfile_given_as_an_extra_argument(tmp_path, exercise, run_autotest):
    tar = make_tar(str(tmp_path / "s.tar.gz"), exercise.submission, "w:gz")
    stdout, stderr, status = run_autotest(["-a", exercise.autotest, tar])
    assert status == 0, stdout + stderr
    assert stdout.endswith(PASSED)


def test_missing_tarfile_dies_with_a_message(tmp_path, exercise, run_autotest):
    _, stderr, status = run_autotest(
        ["-t", str(tmp_path / "missing.tar"), "-a", exercise.autotest]
    )
    assert status == 2
    assert "autotest: tar failed" in stderr
    assert "Traceback" not in stderr


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """serves tmp_path without logging each request to stderr"""

    def log_message(self, format, *args):  # noqa: A002 - the base class's name
        pass


@pytest.mark.needs_tools("wget")
def test_http_tarfile_is_fetched_with_wget(tmp_path, exercise, run_autotest):
    make_tar(str(tmp_path / "s.tar.gz"), exercise.submission, "w:gz")
    handler = functools.partial(QuietHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/s.tar.gz"
        stdout, stderr, status = run_autotest(["-t", url, "-a", exercise.autotest])
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert status == 0, stdout + stderr
    assert stdout.startswith(
        f"wget -O submission.tar {url}\ntar -x -f submission.tar\n"
    )
    assert stdout.endswith(PASSED)


def test_stdin_submission_is_the_single_file_the_tests_need(
    tmp_path, make_exercise, run_autotest
):
    single = make_exercise(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\n1 command="./a.sh; cat extra.txt"'
        ' expected_stdout="stdin\\nSUPPLIED\\n"\n',
        supplied=SUPPLIED,
    )
    stdout, stderr, status = run_autotest(
        ["-a", single.autotest, "--stdin"], stdin="#!/bin/sh\necho stdin\n"
    )
    assert status == 0, stdout + stderr
    assert "1 tests passed 0 tests failed" in stdout


def test_stdin_submission_is_refused_when_tests_need_several_files(
    tmp_path, make_exercise, run_autotest
):
    several = make_exercise(
        tmp_path,
        'files=a.sh b.sh\nprogram=./a.sh\n1 expected_stdout="x\\n"\n',
    )
    stdout, stderr, status = run_autotest(
        ["-a", several.autotest, "--stdin"], stdin="#!/bin/sh\n"
    )
    assert status == 1
    assert stderr == "--stdin specified but tests requires multiple files\n"
    assert stdout == ""


def git_repository_with_two_commits(tmp_path, submission):
    """a bare repository whose first commit is submission and whose second
    changes a.sh; returns (url, first commit hash)"""
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.com",
    )
    git = functools.partial(
        subprocess.run, env=env, check=True, stdout=subprocess.PIPE, encoding="utf-8"
    )
    work = tmp_path / "work"
    shutil.copytree(submission, work)
    git(["git", "init", "--quiet"], cwd=work)
    git(["git", "add", "."], cwd=work)
    git(["git", "commit", "--quiet", "-m", "first"], cwd=work)
    first = git(["git", "rev-parse", "HEAD"], cwd=work).stdout.strip()
    (work / "a.sh").write_text("#!/bin/sh\necho second\n")
    git(["git", "commit", "--quiet", "-am", "second"], cwd=work)
    bare = tmp_path / "bare.git"
    git(["git", "clone", "--quiet", "--bare", str(work), str(bare)])
    return "file://" + str(bare), first


@pytest.mark.needs_tools("git")
def test_git_commit_option_tests_that_commit_not_the_head(
    tmp_path, exercise, run_autotest
):
    url, first = git_repository_with_two_commits(tmp_path, exercise.submission)
    stdout, stderr, status = run_autotest(["-a", exercise.autotest, "--git", url])
    assert status == 1, stdout + stderr
    assert (
        "Your program produced these 2 lines of output:\nsecond\nSUPPLIED\n" in stdout
    )
    stdout, stderr, status = run_autotest(
        ["-a", exercise.autotest, "--git", url, "--commit", first]
    )
    assert status == 0, stdout + stderr
    assert stdout.startswith(
        f"git clone --quiet {url} .\ngit checkout --quiet {first}\n"
    )
    assert stdout.endswith(PASSED)


# ---- the default copy from the current directory


def test_default_copy_takes_the_files_the_tests_name_and_their_includes(
    tmp_path, make_exercise, run_autotest
):
    """
    #include "x.h" in a submitted .c file copies x.h too, and use Foo in a
    .pl file copies Foo.pm; an absolute or parent-directory include is
    never copied (it could name any file the user running autotest can
    read).  ls shows exactly what reached the test directory.
    """
    # the files of every test are copied before any test runs
    spec = (
        "files=m.c m.pl\ncompilers=[]\ncheckers=[]\n"
        '1 command="ls" expected_stdout="Foo.pm\\nh.h\\nm.c\\nm.pl\\ntests.txt\\n"\n'
    )
    ex = make_exercise(
        tmp_path,
        spec,
        files={
            "m.c": '#include "h.h"\n#include "/etc/hostname"\n#include "../secret.h"\n',
            "h.h": "/* header */\n",
            "m.pl": "use Foo qw();\nprint 1;\n",
            "Foo.pm": "package Foo; 1;\n",
            "unrelated.txt": "not copied\n",
        },
    )
    (tmp_path / "exercise" / "secret.h").write_text("/* outside the submission */\n")
    # no -D: the submission is the current directory
    stdout, stderr, status = run_autotest(["-a", ex.autotest], cwd=ex.submission)
    assert status == 0, stdout + stderr
    assert "1 tests passed 0 tests failed" in stdout


def test_default_copy_takes_a_perl_module_used_with_a_semicolon(
    tmp_path, make_exercise, run_autotest
):
    ex = make_exercise(
        tmp_path,
        'files=m.pl\ncheckers=[]\n1 command="ls" expected_stdout="Foo.pm\\nm.pl\\ntests.txt\\n"\n',
        files={"m.pl": "use Foo;\nprint 1;\n", "Foo.pm": "package Foo; 1;\n"},
    )
    stdout, stderr, status = run_autotest(["-a", ex.autotest], cwd=ex.submission)
    assert status == 0, stdout + stderr


def test_default_copy_of_a_binary_c_file_dies_with_a_message(
    tmp_path, make_exercise, run_autotest
):
    ex = make_exercise(
        tmp_path,
        'files=bin.c\n1 command="ls" expected_stdout=""\n',
        files={"bin.c": b"\xff\xfe\x00 not text\n"},
    )
    _, stderr, status = run_autotest(["-a", ex.autotest], cwd=ex.submission)
    assert status == 2
    assert "autotest: bin.c is not a text file" in stderr
    assert "Traceback" not in stderr


# ---- -D


def test_directory_submission_copies_symlinks_as_links(
    tmp_path, make_exercise, run_autotest
):
    """
    a link inside the submission still works; a link to a file outside it
    is copied as a link (not followed), so the target is read only where the
    program could read it anyway (here: no sandbox)
    """
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE\n")
    ex = make_exercise(
        tmp_path,
        "files=a.sh\nprogram=./a.sh\n"
        '1 command="cat inside.txt; readlink outside.txt"'
        f' expected_stdout="INSIDE\\n{outside}\\n"\n',
        files={"a.sh": "#!/bin/sh\n", "real.txt": "INSIDE\n"},
    )
    os.symlink("real.txt", os.path.join(ex.submission, "inside.txt"))
    os.symlink(str(outside), os.path.join(ex.submission, "outside.txt"))
    stdout, stderr, status = run_autotest(ex.args + ["--no_sandbox"])
    assert status == 0, stdout + stderr


def test_expected_files_supplied_by_the_autotest_are_made_read_only(
    tmp_path, make_exercise, run_autotest
):
    ex = make_exercise(
        tmp_path,
        'files=a.sh\nprogram=./a.sh\n1 command="stat -c %a x.expected_out"'
        ' expected_stdout="400\\n"\n',
        files={"a.sh": "#!/bin/sh\n"},
        supplied={"x.expected_out": "hi\n"},
    )
    stdout, stderr, status = run_autotest(ex.args)
    assert status == 0, stdout + stderr


def test_nonexistent_submission_directory_is_an_internal_error(
    tmp_path, make_exercise, run_autotest
):
    ex = make_exercise(tmp_path, SPEC)
    _, stderr, status = run_autotest(["-D", str(tmp_path / "nope"), "-a", ex.autotest])
    assert status == 2
    assert "autotest: internal error: FileNotFoundError" in stderr


def test_an_embedded_autotest_can_not_extract_outside_its_directory(
    tmp_path, monkeypatch
):
    """A bundled autotest is a tar explode into a temporary directory.

    The archive is autotest's own (bundle_autotests.sh made it), so this is
    hardening rather than a live hole - but the extraction is the one place
    in the program where an archive member decides a pathname, and without
    the data filter a member named ../escape.txt writes there.
    """
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w|xz") as t:
        for name, contents in (("tests.txt", b"1\n"), ("../escape.txt", b"escaped\n")):
            info = tarfile.TarInfo(name)
            info.size = len(contents)
            t.addfile(info, io.BytesIO(contents))
    tar_data = archive.getvalue()
    monkeypatch.setattr(
        temp_directories.pkgutil, "get_data", lambda package, name: tar_data
    )
    extract_root = tmp_path / "extract"
    extract_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(extract_root))
    known = len(temp_directories._temp_directories)
    try:
        with pytest.raises(tarfile.TarError):
            temp_directories.load_embedded_autotest("exercise")
    finally:
        for directory in list(temp_directories._temp_directories[known:]):
            temp_directories.remove_temp_directory(directory)
    assert not (extract_root / "escape.txt").exists()
    assert not (tmp_path / "escape.txt").exists()


# ---- the temporary directory registry


def test_debug_level_10_keeps_the_temporary_directory(
    tmp_path, make_exercise, run_autotest
):
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    ex = make_exercise(tmp_path, SPEC, files=SUBMISSION, supplied=SUPPLIED)
    stdout, stderr, status = run_autotest(
        ex.args + ["-" + "d" * 10], env=dict(os.environ, TMPDIR=str(tmpdir))
    )
    assert status == 0, stdout + stderr
    kept = os.listdir(tmpdir)
    assert len(kept) == 1, kept
    contents = sorted(os.listdir(tmpdir / kept[0]))
    assert "autotest" in contents
    assert any(name.startswith(".test-") for name in contents), contents
    assert not any(name.startswith(".sandbox-root-") for name in contents), contents
    # what a normal run leaves: nothing
    stdout, stderr, status = run_autotest(
        ex.args, env=dict(os.environ, TMPDIR=str(tmpdir))
    )
    assert status == 0, stdout + stderr
    assert os.listdir(tmpdir) == kept
    shutil.rmtree(tmpdir / kept[0])


@pytest.fixture
def registry(monkeypatch, tmp_path):
    """the module's registry emptied, and mkdtemp pointed at tmp_path"""
    monkeypatch.setattr(temp_directories, "_temp_directories", [])
    # tempfile caches the directory it chose, so TMPDIR would be ignored
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return temp_directories._temp_directories


def test_cleanup_all_removes_the_registered_directory_and_nothing_else(
    registry, tmp_path
):
    ours = temp_directories.new_temp_directory()
    with open(os.path.join(ours, "file"), "w") as f:
        f.write("x")
    theirs = tmp_path / "not_ours"
    theirs.mkdir()
    assert registry == [ours]
    temp_directories.cleanup_all()
    assert registry == []
    assert not os.path.exists(ours)
    assert theirs.is_dir()


def test_cleanup_all_removes_every_registered_directory(registry):
    # a bundle registers two: the extracted embedded autotest and the test
    # directory, and SIGINT must remove both (remove_temp_directory takes
    # each out of the registry, so cleanup_all must not iterate it directly)
    ours = [temp_directories.new_temp_directory() for _ in range(3)]
    temp_directories.cleanup_all()
    assert registry == []
    assert not any(os.path.exists(directory) for directory in ours)


def test_remove_temp_directory_ignores_a_directory_it_did_not_create(
    registry, tmp_path
):
    theirs = tmp_path / "not_ours"
    theirs.mkdir()
    temp_directories.remove_temp_directory(str(theirs))
    temp_directories.cleanup(temp_dir=str(theirs), args=types.SimpleNamespace(debug=0))
    assert theirs.is_dir()
    assert registry == []


def test_cleanup_keeps_the_directory_when_debugging_at_level_10(registry):
    directory = temp_directories.new_temp_directory()
    temp_directories.cleanup(temp_dir=directory, args=types.SimpleNamespace(debug=10))
    assert os.path.isdir(directory)
    assert registry == [directory]
    temp_directories.cleanup(temp_dir=directory, args=types.SimpleNamespace(debug=9))
    assert not os.path.exists(directory)
    assert registry == []


def test_is_within_submission_refuses_absolute_and_parent_paths():
    assert temp_directories.is_within_submission("x.h")
    assert temp_directories.is_within_submission("lib/x.h")
    assert temp_directories.is_within_submission("..x.h")
    assert not temp_directories.is_within_submission("/etc/passwd")
    assert not temp_directories.is_within_submission("../x.h")
    assert not temp_directories.is_within_submission("lib/../../x.h")


# Both of the following were found by replaying the COMP1511, COMP1521 and
# COMP2041 26T2 activity sets against their own model solutions.


def test_a_supplied_file_that_can_not_be_read_does_not_delete_the_submitted_one(
    tmp_path,
):
    """
    A dangling symlink in an autotest directory must not take a file away.

    Real course material contains symlinks into sibling activities, and some of
    them do not resolve.  Removing the destination before attempting the copy
    left the submission's own file deleted and nothing in its place, so tests
    needing it failed with no indication why.
    """
    supplied = tmp_path / "supplied"
    supplied.mkdir()
    (supplied / "data.txt").symlink_to("../nowhere/data.txt")
    (supplied / "real.txt").write_text("supplied\n")

    working = tmp_path / "working"
    working.mkdir()
    (working / "data.txt").write_text("submitted\n")

    temp_directories.copy_directory(str(supplied), str(working))

    assert (working / "data.txt").read_text() == "submitted\n"
    assert (working / "real.txt").read_text() == "supplied\n"
    assert not list(working.glob("*autotest-incoming*"))


def test_a_supplied_file_still_replaces_a_read_only_submitted_file(tmp_path):
    """The reason the destination is replaced at all: a submission may ship a
    read-only file of the same name as a checker or an expected output."""
    supplied = tmp_path / "supplied"
    supplied.mkdir()
    (supplied / "checker.sh").write_text("staff\n")

    working = tmp_path / "working"
    working.mkdir()
    submitted = working / "checker.sh"
    submitted.write_text("student\n")
    submitted.chmod(0o444)

    temp_directories.copy_directory(str(supplied), str(working))

    assert submitted.read_text() == "staff\n"
