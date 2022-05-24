# upload results of an autotest via http

import io, os, platform, sys, zipfile


def upload_results_http(tests, parameters, args):
    upload_url = parameters["upload_url"]
    upload_fields = parameters["upload_fields"]
    upload_fields["exercise"] = args.exercise
    upload_fields["hostname"] = platform.node()
    upload_fields["login"] = getlogin()

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
    if args.debug:
        print(r.text, file=sys.stderr)


def zip_files_for_upload(stream, tests, parameters, args):
    # pylint: disable=consider-using-with
    # use of with triggered a bug here - in old python versions
    zf = zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_LZMA)
    bytes_uploaded = 0
    for test in tests.values():
        try:
            zf.writestr(test.label + ".passed", "1" if test.passed else "")
        except AttributeError:
            pass

    # don't zip files that are supplied in autotest
    supplied = os.listdir(parameters["supplied_files_directory"])
    upload_files = set(args.file | args.optional_files).difference(supplied)
    for filename in ["autotest.log"] + list(upload_files):
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
