# create a temporary directory and copy the files needed for testing to it

import atexit, glob, io, os, pkgutil, re, shutil, subprocess, sys, tarfile, tempfile
from shutil import copy2, copystat
from util import die
from termcolor import colored


def copy_files_to_temp_directory(args, parameters, file=sys.stdout):
    # missing_files = [f for f in parameters["files"] if not glob.glob(f)]
    # if missing_files:
    #     print(
    #         "Could not run tests",
    #         "because these files are missing:",
    #         colored(" ".join(missing_files), "red"),
    #         flush=True,
    #         file=file
    #     )
    #     exit(1)

    temp_dir = tempfile.mkdtemp()
    atexit.register(cleanup, temp_dir=temp_dir, args=args)
    if parameters["supplied_files_directory"]:
        result_supplied_dir = copy_directory(parameters["supplied_files_directory"], temp_dir, parameters["files"], parameters["files"].copy())
        # if not result["success"]:
        #     parameters["missing_files"]["exist"] = True
        #     parameters["missing_files"]["files"] = result["files_not_found"]
        #     print("didn't find these??", result["files_not_found"])
            # error_msg = "Unable to run tests because "
            # error_msg += f"these files were missing: {' '.join(result['files_not_found'])}"
            # print(error_msg)
            # exit(1)
    result_fetch_sub = fetch_submission(temp_dir, args, parameters)
    os.chdir(temp_dir)

    # added for COMP1521 shell assignment but probably a good idea generally
    # os.environ['HOME'] = temp_dir

    for expected_file in glob.glob("*.expected_*"):
        os.chmod(expected_file, 0o400)

# TODO: allow this function to detect if specified 
# required files for the autotest 
# have not been supplied
def fetch_submission(temp_dir, args, parameters):
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
        copy_directory(args.directory, temp_dir, parameters["files"], parameters["files"].copy())
    elif args.git:
        os.chdir(temp_dir)
        if args.commit:
            execute(["git", "clone", "--quiet", args.git, "."])
            execute(["git", "checkout", "--quiet", args.commit])
        else:
            execute(["git", "clone", "--quiet", "--depth", "1", args.git, "."])
        if os.path.isdir(args.exercise) and os.listdir(args.exercise):
            print("cd", args.exercise)
            os.chdir(args.exercise)
    else:
        # FIXME - casn we remove this code
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
                with open(file, "w") as f:
                    f.write(sys.stdin.read())
            except IOError:
                die(f"can not create {file}")
            return
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
                    # don't overwrite files which are supplied by the autotest
                    # can break old autotests
                    if os.path.exists(os.path.join(temp_dir, file)):
                        continue
                    shutil.copy(file, temp_dir)
                    if re.search("\.[pc].?$", file):
                        try:
                            # Kludge to pick up include files
                            with open(file) as f:
                                for line in f:
                                    m = re.search(
                                        r'\b(require|include)\s*[\'"](.*?)[\'"]',
                                        line,
                                        flags=re.I,
                                    )
                                    if m:
                                        files_to_copy.add(m.group(2))
                                    m = re.search(
                                        r"^\s*\b(use|require)\s*(\S+)", line, flags=re.I
                                    )
                                    if m:
                                        files_to_copy.add(m.group(2) + ".pm")
                        except UnicodeDecodeError:
                            die(f"{file} is not a text file")
                except IOError:
                    continue


# exit_status == 0 -> all tests worked
# exit_status == 1 -> 1 or more tests failed
# exit_status >- 2, internal error - testing not completed

# return values -> positive/0 == 
# -1 == Not all values found
def copy_directory(src, dst, expected_files=[], files_not_found=[], symlinks=False, ignore=None):
    names = os.listdir(src)
    if ignore is not None:
        ignored_names = ignore(src, names)
    else:
        ignored_names = set()

    if not (os.path.exists(dst) and os.path.isdir(dst)):
        os.makedirs(dst)
        # we don't want to copy directory permission if the directory exists already
        try:
            copystat(src, dst)
        except (WindowsError, OSError):
            pass
    
    for name in names:
        if name in ignored_names:
            continue
        # check if all "expected_files" are present...
        if name in expected_files:
            try:
                files_not_found.remove(name)
            except:
                pass
        srcname = os.path.join(src, name)
        dstname = os.path.join(dst, name)
        try:
            if symlinks and os.path.islink(srcname):
                linkto = os.readlink(srcname)
                os.symlink(linkto, dstname)
            elif os.path.isdir(srcname):
                result = copy_directory(srcname, dstname, expected_files, files_not_found, symlinks, ignore)
                files_not_found = result["files_not_found"]
            else:
                copy2(srcname, dstname)
        except OSError as why:
            # we don't want to stop if there is an unreadable file - just produce an error
            print("Warning:", why, file=sys.stderr)

    success = len(files_not_found) == 0
    return {"success": success, "files_not_found": files_not_found} 
    


def cleanup(temp_dir=None, args={}):
    if args and args.debug >= 10:
        return
    if temp_dir and temp_dir.startswith("/tmp/"):
        shutil.rmtree(temp_dir)


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
    The script bundle_autotests.sh creates executablkes with embedded autotests.
    """
    tar_data = pkgutil.get_data("embedded_autotests", exercise + ".tar")
    if not tar_data:
        return None
    temp_dir = tempfile.mkdtemp()
    atexit.register(cleanup, temp_dir=temp_dir)
    buffer = io.BytesIO(tar_data)
    with tarfile.open(fileobj=buffer, mode="r|xz") as t:
        t.extractall(temp_dir)
    return os.path.join(temp_dir, "tests.txt")
