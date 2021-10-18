# upload results of an autotest via http

import io, os, platform, sys, zipfile
from run_tests import run_tests


def run_tests_and_upload_results(tests, parameters, args):
    class Tee(object):
        def __init__(self, stream):
            self.stream = stream
            self.fileno = stream.fileno

        def flush(self):
            sys.stdout.flush()
            self.stream.flush()

        def write(self, message):
            sys.stdout.write(message)
            self.stream.write(message)

    upload_url = parameters["upload_url"]
    upload_fields = parameters["upload_fields"]
    upload_fields["exercise"] = args.exercise
    upload_fields["hostname"] = platform.node()
    upload_fields["login"] = getlogin()

    with open("autotest.log", "w") as f:
        exit_status = run_tests(tests, parameters, args, file=Tee(f))

    buffer = io.BytesIO()
    zip_files_for_upload(buffer, tests, parameters, args)
    buffer.seek(0)
    if args.debug:
        print(upload_url, upload_fields)
    try:
        # requests may not be installed
        import requests

        r = requests.post(upload_url, upload_fields, files={"zip": ("zip", buffer)})
    except Exception as e:
        if args.debug:
            print(e, file=sys.stderr)
        return exit_status
    if args.debug:
        print(r.text, file=sys.stderr)
    return exit_status


def zip_files_for_upload(stream, tests, parameters, args):
    zf = zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_LZMA)
    bytes_uploaded = 0
    for test in tests.values():
        try:
            zf.writestr(test.label + ".passed", "1" if test.passed else "")
        except AttributeError:
            pass
    for filename in ["autotest.log"] + list(set(args.file | args.optional_files)):
        try:
            bytes_uploaded += os.path.getsize(filename)
            if bytes_uploaded > parameters["upload_max_bytes"]:
                break
            zf.write(filename)
        except OSError:
            pass
    zf.close()


def getlogin():
    """
    attempt to get username robustly whatever the platform
    """
    try:
        import pwd

        return pwd.getpwuid(os.geteuid()).pw_name
    except Exception:
        pass
    try:
        return os.getlogin()
    except Exception:
        pass
    try:
        return (
            os.getenv("LOGNAME", "")
            or os.getenv("USER", "")
            or os.getenv("USERNAME", "")
        )
    except Exception:
        return ""
