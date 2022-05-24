import os, pickle, sys, subprocess

SANDBOX_NAME = "../sandbox.pickle"


def run_tests_in_sandbox(argv0_realpath, tests, args, parameters):
    """
    run unshare to create mount and other namespaces for a sandbox
    deliberate escape from sandbox is possible.
    variable values are saved in a file for recovery after invocation of unshare
    """
    with open(SANDBOX_NAME, "wb") as f:
        pickle.dump((tests, args, parameters), f)
    command = parameters.get("sandbox_command", [])
    command += [argv0_realpath, "--inside_sandbox"]
    if args.debug:
        print(" ".join(command), file=sys.stderr)
    p = subprocess.run(command, check=False)
    if args.debug:
        print("leaving sandbox with exit status", p.returncode, file=sys.stderr)
    return p.returncode


def continue_inside_sandbox():
    """
    continue execution after invocation in separate name space via unshare
    recover variable values from a file
    """
    with open(SANDBOX_NAME, "rb") as f:
        (tests, args, parameters) = pickle.load(f)
    os.unlink(SANDBOX_NAME)
    create_file_system(args, parameters)
    return (tests, args, parameters)


def create_file_system(args, parameters):
    """
    create a filesystem using bind mounts and chroot to it

    invoked via unshare creating a mount namespace
    which makes mounts possible for unprivileged user
    """
    initial_dir = os.getcwd()
    os.mkdir("../root", 0o755)
    if args.debug:
        print("chdir", "../root", file=sys.stderr)
    os.chdir("../root")

    os.mkdir("tmp", 0o1777)
    run_command(["mount", "-t", "tmpfs", "tmpfs", "tmp"], args)

    os.mkdir("proc", 0o755)
    run_command(["mount", "-t", "proc", "proc", "proc"], args)

    os.mkdir("sys", 0o755)
    if parameters.get("sandbox_network", True):
        # only possible if a network namespace has been created
        run_command(["mount", "-t", "sysfs", "none", "sys"], args)
    else:
        run_command(["mount", "--rbind", "/sys", "sys"], args)

    os.mkdir("dev", 0o755)
    run_command(["mount", "--rbind", "/dev", "dev"], args)

    ro_mounts = parameters.get("sandbox_read_only_mount_base", [])
    ro_mounts += parameters.get("sandbox_read_only_mount", [])

    rw_mounts = parameters.get("sandbox_read_write_mount", [])
    rw_mounts += [initial_dir]

    for mount in ro_mounts:
        do_mount(mount, args, read_only=True)
    for mount in rw_mounts:
        do_mount(mount, args, read_only=False)

    if args.debug:
        print("chroot", file=sys.stderr)
    os.chroot(".")
    if args.debug:
        print("chdir", initial_dir, file=sys.stderr)
    os.chdir(initial_dir)


def do_mount(mount, args, read_only=False):
    """
    bind mount a file/or directory relative to the current directory

    if mount is a string it is assumed to be a pathname to be mounted at the
    same position relative to the current directory


    otherwise mount[0] should be a pathname and mount[1] a mount point
    """
    if isinstance(mount, str):
        mount_point = mount.lstrip(os.sep)
        pathname = os.sep + mount_point
    else:
        # mount is a pair specifying a different mount-point inside sandbox
        pathname = os.sep + mount[0].lstrip(os.sep)
        mount_point = mount[1].lstrip(os.sep)

    if os.path.isdir(pathname):
        os.makedirs(mount_point, 0o755, exist_ok=True)
    else:
        # bind mount of single file requires file to exist
        os.makedirs(os.path.dirname(mount_point), 0o755, exist_ok=True)
        with open(mount_point, "wb"):
            pass
    mount_command = ["mount", "--bind"]
    if read_only:
        mount_command += ["-o", "ro"]
    mount_command += [pathname, mount_point]
    run_command(mount_command, args)


def run_command(command, args):
    if args.debug:
        print(" ".join(command), file=sys.stderr)
    subprocess.run(command, check=True)
