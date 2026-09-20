# upload results of an autotest via http

import io
import os
import platform
import sys
import zipfile


def upload_results_http(tests, parameters, args):
    """
    POST the test log, the files tested and which tests passed to upload_url.

    Uploading is best effort: it must never change the exit status or the
    output a student sees, so every failure is swallowed unless debugging.
    """
    upload_url = parameters["upload_url"]
    upload_fields = parameters["upload_fields"]
    upload_fields["exercise"] = args.exercise
    upload_fields["hostname"] = platform.node()
    upload_fields["login"] = getlogin()

    buffer = io.BytesIO()
    zip_files_for_upload(buffer, tests, parameters, args)
    buffer.seek(0)
    if args.debug:
        print(upload_url, upload_fields, file=sys.stderr)
    try:
        # requests may not be installed
        import requests

        # 30 s: an unreachable upload host must not hang autotest after the
        # results have been printed
        r = requests.post(
            upload_url, upload_fields, files={"zip": ("zip", buffer)}, timeout=30
        )
    except Exception as e:  # noqa: BLE001 - uploading is best effort (see above)
        if args.debug:
            print(e, file=sys.stderr)
        return
    if args.debug:
        print(r.text, file=sys.stderr)


def zip_files_for_upload(stream, tests, parameters, args):
    """
    Write a zip archive to stream holding the autotest log, the files tested
    and one marker file per test (label.passed containing "1" iff it passed).

    Files supplied by the autotest itself are not uploaded: they are the
    same for every student and would only waste upload_max_bytes.
    """
    # not a with statement: using one here triggered a bug in old
    # Python versions and the file is closed by the caller's stream
    zf = zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_LZMA)
    bytes_uploaded = 0
    for test in tests.values():
        passed = getattr(test, "test_passed", None)
        zf.writestr(test.label + ".passed", "1" if passed else "")

    # don't zip files that are supplied in autotest
    supplied = set()
    supplied_files_directory = parameters.get("supplied_files_directory", "")
    if supplied_files_directory:
        try:
            supplied = set(os.listdir(supplied_files_directory))
        except OSError:
            pass
    upload_files = set(args.file | args.optional_files).difference(supplied)
    for filename in ["autotest.log"] + sorted(upload_files):
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
    except Exception:  # noqa: BLE001, S110 - try the next way  # nosec B110
        pass
    try:
        return os.getlogin()
    except Exception:  # noqa: BLE001, S110 - try the next way  # nosec B110
        pass
    try:
        return (
            os.getenv("LOGNAME", "")
            or os.getenv("USER", "")
            or os.getenv("USERNAME", "")
        )
    except Exception:  # noqa: BLE001 - an empty login name is better than no upload
        return ""
