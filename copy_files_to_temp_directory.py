# create a temporary directory and copy the files needed for testing to it

import atexit
import glob
import io
import os
import pkgutil
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from shutil import copy2, copystat

from util import die

INITIAL_DIR_NAME = "autotest"

# Every temporary directory this module has created and not yet removed.
# A registry (rather than "anything under /tmp") is what lets cleanup
# remove exactly our directories whatever TMPDIR is set to, and lets a
# SIGINT handler remove them all at once.
_temp_directories: list[str] = []


def copy_files_to_temp_directory(args, parameters):
    """
    Create the directory the tests run in, populate it and chdir into it.
    Returns its absolute pathname.

    The student's submission is fetched first and the files supplied by the
    autotest are copied in afterwards, so a submission can never replace a
    file the autotest relies on (a checker, expected output, ...).  It also
    means git can clone into an empty directory.
    """
    # resolved before anything chdirs: fetching a --stdin, --git or http
    # --tarfile submission changes directory, and supplied_files_directory
    # is relative when tests.txt was given as a relative -a pathname
    supplied_files_directory = parameters["supplied_files_directory"]
    if supplied_files_directory:
        supplied_files_directory = os.path.abspath(supplied_files_directory)

    temp_dir = new_temp_directory()
    atexit.register(cleanup, temp_dir=temp_dir, args=args)

    initial_dir = os.path.join(temp_dir, INITIAL_DIR_NAME)
    os.mkdir(initial_dir, 0o700)

    working_dir = fetch_submission(initial_dir, args)

    if supplied_files_directory:
        copy_directory(supplied_files_directory, working_dir)

    os.chdir(working_dir)

    # done last so nothing copied afterwards has to write over a read-only file
    for expected_file in glob.glob("*.expected_*"):
        os.chmod(expected_file, 0o400)

    return os.path.abspath(working_dir)


def fetch_submission(  # noqa: C901, PLR0912, PLR0915 - every submission source in one function
    temp_dir, args
):
    """
    Put the files being tested into temp_dir and return the directory the
    tests should run in: temp_dir, or the exercise sub-directory of a cloned
    repository when there is one.
    """
    if args.debug:
        print(f"fetch_submission({temp_dir})", file=sys.stderr)
    if args.tarfile:
        # FIXME handle xz compression
        if re.search(r"^https?://.*\.tar(.[a-z]+)?", args.tarfile):
            os.chdir(temp_dir)
            execute(["wget", "-O", "submission.tar", args.tarfile])
            execute(["tar", "-x", "-f", "submission.tar"])
        else:
            execute(
                ["tar", "-x", "-C", temp_dir, "-f", args.tarfile],
                print_command=args.debug,
            )
    elif args.directory:
        # symbolic links are copied as links, never followed: following them
        # would copy the target's contents as the user running autotest, so a
        # submission could read any file that user can (another submission,
        # a solution, a private key) via a link the sandbox can not stop
        copy_directory(args.directory, temp_dir, symlinks=True)
    elif args.git:
        os.chdir(temp_dir)
        if args.commit:
            execute(["git", "clone", "--quiet", args.git, "."])
            execute(["git", "checkout", "--quiet", args.commit])
        else:
            execute(["git", "clone", "--quiet", "--depth", "1", args.git, "."])
        if os.path.isdir(args.exercise) and os.listdir(args.exercise):
            print("cd", args.exercise)
            return os.path.join(temp_dir, args.exercise)
    else:
        # FIXME - can we remove this code
        if (
            os.path.isdir(".git")
            and os.path.isdir(args.exercise)
            and os.listdir(args.exercise)
            and os.path.realpath(args.autotest_directory)
            != os.path.realpath(args.exercise)
        ):
            print("cd", args.exercise)
            os.chdir(args.exercise)
        files_to_copy = set(args.file | args.optional_files)
        if args.stdin:
            if len(files_to_copy) != 1:
                print(
                    "--stdin specified but tests requires multiple files",
                    file=sys.stderr,
                )
                sys.exit(1)
            os.chdir(temp_dir)
            file = files_to_copy.pop()
            try:
                with open(file, "w", encoding="utf-8") as f:
                    f.write(sys.stdin.read())
            except OSError:
                die(f"can not create {file}")
            return temp_dir
        if args.debug:
            print("files_to_copy:", files_to_copy, file=sys.stderr)
        copied = set()
        while files_to_copy:
            file_pattern = files_to_copy.pop()
            if file_pattern in copied:
                continue
            copied.add(file_pattern)
            for file in glob.glob(file_pattern):
                try:
                    shutil.copy(file, temp_dir)
                    if re.search(r"\.[pc].?$", file):
                        try:
                            # Kludge to pick up include files
                            with open(file, encoding="utf-8") as f:
                                for line in f:
                                    m = re.search(
                                        r'\b(require|include)\s*[\'"](.*?)[\'"]',
                                        line,
                                        flags=re.IGNORECASE,
                                    )
                                    if m and is_within_submission(m.group(2)):
                                        files_to_copy.add(m.group(2))
                                    # the module name ends at whitespace or
                                    # at the statement's ";" (use Foo;)
                                    m = re.search(
                                        r"^\s*\b(use|require)\s*([^\s;]+)",
                                        line,
                                        flags=re.IGNORECASE,
                                    )
                                    if m and is_within_submission(m.group(2)):
                                        files_to_copy.add(m.group(2) + ".pm")
                        except UnicodeDecodeError:
                            die(f"{file} is not a text file")
                except OSError:
                    continue
    return temp_dir


def is_within_submission(pathname):
    """
    True iff an include/require name may be copied from the submission:
    a relative pathname which does not leave the directory.  Anything else
    (`#include "/home/marker/.ssh/id_rsa"`) would copy that file into the
    test directory, where the program could read it; such includes could
    not have worked from a flat copy of the submission anyway.
    """
    return not os.path.isabs(pathname) and ".." not in pathname.split("/")


# exit_status == 0 -> all tests worked
# exit_status == 1 -> 1 or more tests failed
# exit_status >- 2, internal error - testing not completed


def copy_directory(src, dst, symlinks=False, ignore=None):
    """
    Recursively copy src into dst, replacing any files already in dst.

    A file supplied by the autotest must win over a submitted file of the same
    name, and the destination may be read-only because a submission can contain
    anything, so a plain copy2 over it would fail.

    Each file is therefore copied beside its destination and moved into place
    only once the copy has succeeded.  Removing the destination first and then
    copying looks simpler and is wrong: a source that cannot be read -- a
    dangling symlink in the autotest directory is the case seen in real course
    material -- would leave the destination deleted and not replaced, silently
    taking away a file the submission provided.
    """
    names = os.listdir(src)
    ignored_names = ignore(src, names) if ignore is not None else set()

    if not (os.path.exists(dst) and os.path.isdir(dst)):
        os.makedirs(dst)
        # we don't want to copy directory permission if the directory exists already
        try:
            copystat(src, dst)
        except OSError:
            pass
    for name in names:
        if name in ignored_names:
            continue
        srcname = os.path.join(src, name)
        dstname = os.path.join(dst, name)
        try:
            if symlinks and os.path.islink(srcname):
                copy_into_place(srcname, dstname, as_symlink=True)
            elif os.path.isdir(srcname):
                if os.path.lexists(dstname) and not os.path.isdir(dstname):
                    remove_existing(dstname)
                copy_directory(srcname, dstname, symlinks, ignore)
            else:
                copy_into_place(srcname, dstname)
        except OSError as why:
            # we don't want to stop if there is an unreadable file - just produce an error
            print("Warning:", why, file=sys.stderr)


def copy_into_place(srcname, dstname, as_symlink=False):
    """
    Copy srcname to dstname, leaving dstname alone if the copy fails.

    The copy is made beside dstname and moved into place, so a source that
    turns out to be unreadable cannot destroy a file that is already there.
    os.replace is used rather than copying onto dstname because dstname may be
    read-only; what matters is the permission on the directory, which autotest
    owns.
    """
    temporary = dstname + ".autotest-incoming"
    remove_existing(temporary)
    try:
        if as_symlink:
            os.symlink(os.readlink(srcname), temporary)
        else:
            copy2(srcname, temporary)
        remove_existing(dstname)
        os.replace(temporary, dstname)
    finally:
        remove_existing(temporary)


def remove_existing(pathname):
    """remove whatever is at pathname (file, symlink or directory) if anything"""
    if os.path.isdir(pathname) and not os.path.islink(pathname):
        shutil.rmtree(pathname)
    elif os.path.lexists(pathname):
        os.unlink(pathname)


def new_temp_directory():
    """create a temporary directory which cleanup/cleanup_all will remove"""
    temp_dir = tempfile.mkdtemp()
    _temp_directories.append(temp_dir)
    return temp_dir


def cleanup(temp_dir=None, args=None):
    """
    remove a temporary directory created by this module
    (kept for inspection when debugging at level 10 or above)
    """
    if args and args.debug >= 10:
        return
    remove_temp_directory(temp_dir)


def cleanup_all():
    """
    remove every temporary directory created by this module
    (for the SIGINT handler, which then exits without running atexit)
    """
    # iterate over a copy: remove_temp_directory() shrinks the list
    for temp_dir in list(_temp_directories):  # noqa: PERF101 - the list shrinks
        remove_temp_directory(temp_dir)
        # a test on another thread may have been creating its directory
        # while this one was being removed: give it a moment and try again
        for _ in range(3):
            if not os.path.lexists(temp_dir):
                break
            time.sleep(0.05)
            shutil.rmtree(temp_dir, ignore_errors=True)


def remove_temp_directory(temp_dir):
    """remove temp_dir iff this module created it; other directories are never touched"""
    if temp_dir not in _temp_directories:
        return
    _temp_directories.remove(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


def execute(command, print_command=True):
    if print_command:
        print(" ".join(command))
    if subprocess.call(command) != 0:
        die(f"{command[0]} failed")


def load_embedded_autotest(exercise):
    """
    if exercise is found as an embedded tar file
    explode the tarfile to a temporary directory
    and return the pathname for tests.txt
    The script bundle_autotests.sh creates executables with embedded autotests.
    """
    try:
        tar_data = pkgutil.get_data("embedded_autotests", exercise + ".tar")
    except OSError:
        # zipimport raises for a member the bundle does not hold: that is
        # an exercise which is not embedded, not a fault
        tar_data = None
    if not tar_data:
        return None
    temp_dir = new_temp_directory()
    atexit.register(cleanup, temp_dir=temp_dir)
    buffer = io.BytesIO(tar_data)
    with tarfile.open(fileobj=buffer, mode="r|xz") as t:
        # the data filter (Python 3.12+, backported to 3.8.17+) refuses
        # archive members which would escape the directory
        if hasattr(tarfile, "data_filter"):
            t.extractall(temp_dir, filter="data")
        else:
            # the archive is autotest's own, embedded by bundle_autotests.sh
            t.extractall(temp_dir)  # noqa: S202 - our own archive  # nosec B202
    return os.path.join(temp_dir, "tests.txt")
