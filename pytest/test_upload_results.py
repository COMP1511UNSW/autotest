"""
Unit tests for upload_results: the best-effort POST of a student's files
and results to upload_url.

A throw-away HTTP server on localhost records the multipart form so the
fields and the zip archive can be checked; the server is stopped so no
thread outlives a test.
"""

import email
import email.message
import http.server
import io
import os
import sys
import threading
import types
import zipfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import upload_results  # noqa: E402

requests = pytest.importorskip(
    "requests", reason="upload_results needs requests to post"
)


class RecordingServer(http.server.HTTPServer):
    """an HTTP server remembering the form fields of every POST it receives"""

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.received: list = []
        self.url = f"http://127.0.0.1:{self.server_port}/upload"


class RecordingHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        # the multipart body is parsed as a MIME message: the form field
        # names are the content-disposition names of its parts
        message = email.message_from_bytes(
            b"Content-Type: "
            + self.headers["Content-Type"].encode()
            + b"\r\n\r\n"
            + body
        )
        parts = message.get_payload()
        assert isinstance(parts, list)
        fields = {}
        for part in parts:
            assert isinstance(part, email.message.Message)
            fields[part.get_param("name", header="content-disposition")] = (
                part.get_payload(decode=True)
            )
        assert isinstance(self.server, RecordingServer)
        self.server.received.append(fields)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"received")

    def log_message(self, *_arguments):
        pass


@pytest.fixture
def server():
    """a local HTTP server recording every POST it receives in server.received"""
    httpd = RecordingServer(("127.0.0.1", 0), RecordingHandler)
    # a short poll interval so shutdown() in teardown does not wait 0.5s
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


@pytest.fixture
def submission(tmp_path, monkeypatch):
    """a test directory holding the log, submitted files and one supplied file"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "autotest.log").write_text("log\n")
    (tmp_path / "hello.c").write_text("int main(void) {}\n")
    (tmp_path / "supplied").mkdir()
    (tmp_path / "supplied" / "checker.sh").write_text("supplied\n")
    (tmp_path / "checker.sh").write_text("supplied\n")
    return tmp_path


def make_tests(**passed):
    return {
        label: types.SimpleNamespace(label=label, test_passed=value)
        for label, value in passed.items()
    }


def make_args(**overrides):
    args = types.SimpleNamespace(
        exercise="lab1", debug=0, file={"hello.c", "checker.sh"}, optional_files=set()
    )
    for name, value in overrides.items():
        setattr(args, name, value)
    return args


def make_parameters(url, **overrides):
    parameters = {
        "upload_url": url,
        "upload_fields": {"zid": "z1"},
        "supplied_files_directory": "supplied",
        "upload_max_bytes": 2048000,
    }
    parameters.update(overrides)
    return parameters


def zip_in(fields):
    return zipfile.ZipFile(io.BytesIO(fields["zip"]))


def test_upload_url_parameter_uploads_the_log_of_the_run(
    server, tmp_path, make_exercise, run_autotest
):
    exercise = make_exercise(
        tmp_path,
        f"files=a.sh\nprogram=./a.sh\nupload_url={server.url}\n"
        '1 expected_stdout="hi\\n"\n',
        files={"a.sh": "#!/bin/sh\necho hi\n"},
    )
    stdout, stderr, status = run_autotest(exercise.args)
    assert status == 0, stdout + stderr
    assert "1 tests passed 0 tests failed" in stdout
    # the server thread records the fields before it answers the POST
    assert len(server.received) == 1
    with zip_in(server.received[0]) as zf:
        assert zf.read("1.passed") == b"1"
        assert b"Test 1 (./a.sh) - passed" in zf.read("autotest.log")


def test_upload_posts_exercise_hostname_login_and_extra_fields(
    server, submission, monkeypatch
):
    monkeypatch.setattr(upload_results.platform, "node", lambda: "host1")
    monkeypatch.setattr(upload_results, "getlogin", lambda: "z1234567")
    upload_results.upload_results_http(
        make_tests(t1=True), make_parameters(server.url), make_args()
    )
    fields = server.received[0]
    assert fields["exercise"] == b"lab1"
    assert fields["hostname"] == b"host1"
    assert fields["login"] == b"z1234567"
    assert fields["zid"] == b"z1"


def test_uploaded_zip_holds_log_submitted_files_and_pass_markers_but_not_supplied_files(
    server, submission
):
    upload_results.upload_results_http(
        make_tests(t1=True, t2=False, t3=None), make_parameters(server.url), make_args()
    )
    with zip_in(server.received[0]) as zf:
        assert sorted(zf.namelist()) == [
            "autotest.log",
            "hello.c",
            "t1.passed",
            "t2.passed",
            "t3.passed",
        ]
        assert zf.read("autotest.log") == b"log\n"
        assert zf.read("hello.c") == b"int main(void) {}\n"
        assert zf.read("t1.passed") == b"1"
        assert zf.read("t2.passed") == b""
        assert zf.read("t3.passed") == b""


def test_optional_files_are_uploaded_and_missing_files_are_skipped(server, submission):
    (submission / "extra.h").write_text("h\n")
    args = make_args(file={"hello.c"}, optional_files={"extra.h", "missing.c"})
    upload_results.upload_results_http(
        make_tests(t1=True), make_parameters(server.url), args
    )
    with zip_in(server.received[0]) as zf:
        assert sorted(zf.namelist()) == [
            "autotest.log",
            "extra.h",
            "hello.c",
            "t1.passed",
        ]


def test_files_are_dropped_once_the_upload_limit_is_reached(server, submission):
    # the size check happens before each file is written, in sorted order:
    # the file that crosses the limit and everything after it are left out
    (submission / "big.c").write_text("x" * 5000)
    args = make_args(file={"big.c", "hello.c"})
    upload_results.upload_results_http(
        make_tests(t1=True), make_parameters(server.url, upload_max_bytes=100), args
    )
    with zip_in(server.received[0]) as zf:
        assert sorted(zf.namelist()) == ["autotest.log", "t1.passed"]


def test_upload_limit_counts_the_log(server, submission):
    (submission / "autotest.log").write_text("x" * 200)
    args = make_args(file={"hello.c"})
    upload_results.upload_results_http(
        make_tests(t1=True), make_parameters(server.url, upload_max_bytes=100), args
    )
    with zip_in(server.received[0]) as zf:
        assert zf.namelist() == ["t1.passed"]


def test_unreadable_supplied_files_directory_uploads_everything(server, submission):
    args = make_args()
    upload_results.upload_results_http(
        make_tests(t1=True),
        make_parameters(server.url, supplied_files_directory="does_not_exist"),
        args,
    )
    with zip_in(server.received[0]) as zf:
        assert "checker.sh" in zf.namelist()


def test_empty_supplied_files_directory_parameter_uploads_everything(
    server, submission
):
    upload_results.upload_results_http(
        make_tests(t1=True),
        make_parameters(server.url, supplied_files_directory=""),
        make_args(),
    )
    with zip_in(server.received[0]) as zf:
        assert "checker.sh" in zf.namelist()


def test_debug_prints_the_request_and_the_response(server, submission, capsys):
    upload_results.upload_results_http(
        make_tests(t1=True), make_parameters(server.url), make_args(debug=1)
    )
    err = capsys.readouterr().err
    assert server.url in err
    assert "'exercise': 'lab1'" in err
    assert "received" in err


def test_connection_refused_is_silently_ignored(submission, capsys):
    httpd = http.server.HTTPServer(("127.0.0.1", 0), RecordingHandler)
    url = f"http://127.0.0.1:{httpd.server_port}/upload"
    httpd.server_close()
    upload_results.upload_results_http(
        make_tests(t1=True), make_parameters(url), make_args()
    )
    assert capsys.readouterr().err == ""


def test_connection_refused_is_reported_only_when_debugging(submission, capsys):
    httpd = http.server.HTTPServer(("127.0.0.1", 0), RecordingHandler)
    url = f"http://127.0.0.1:{httpd.server_port}/upload"
    httpd.server_close()
    upload_results.upload_results_http(
        make_tests(t1=True), make_parameters(url), make_args(debug=1)
    )
    err = capsys.readouterr().err
    assert url in err
    assert "Connection refused" in err or "Failed to establish" in err


def test_missing_requests_module_is_ignored(submission, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "requests", None)
    upload_results.upload_results_http(
        make_tests(t1=True), make_parameters("http://127.0.0.1:1/"), make_args()
    )
    assert capsys.readouterr().err == ""


# getlogin fallbacks


def test_getlogin_prefers_the_password_database(monkeypatch):
    import pwd

    monkeypatch.setattr(
        pwd, "getpwuid", lambda uid: types.SimpleNamespace(pw_name="pwuser")
    )
    monkeypatch.setattr(os, "getlogin", lambda: "ttyuser")
    assert upload_results.getlogin() == "pwuser"


def test_getlogin_falls_back_to_os_getlogin(monkeypatch):
    import pwd

    def no_entry(uid):
        raise KeyError(uid)

    monkeypatch.setattr(pwd, "getpwuid", no_entry)
    monkeypatch.setattr(os, "getlogin", lambda: "ttyuser")
    assert upload_results.getlogin() == "ttyuser"


@pytest.mark.parametrize(
    "environment,expected",
    [
        ({"LOGNAME": "l", "USER": "u", "USERNAME": "n"}, "l"),
        ({"USER": "u", "USERNAME": "n"}, "u"),
        ({"USERNAME": "n"}, "n"),
        ({}, ""),
    ],
)
def test_getlogin_falls_back_to_environment_variables(
    monkeypatch, environment, expected
):
    import pwd

    def no_entry(uid):
        raise KeyError(uid)

    def no_tty():
        raise OSError("no tty")

    monkeypatch.setattr(pwd, "getpwuid", no_entry)
    monkeypatch.setattr(os, "getlogin", no_tty)
    for name in ["LOGNAME", "USER", "USERNAME"]:
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    assert upload_results.getlogin() == expected


def test_upload_post_has_a_timeout(submission, monkeypatch):
    """An unreachable upload host must not hang autotest.

    The results have already been printed by the time the upload is made,
    so a POST without a timeout would leave the student waiting at a
    finished-looking terminal until the network gave up.
    """
    calls = []

    def recording_post(url, *args, **kwargs):
        calls.append((url, kwargs))
        return types.SimpleNamespace(text="ok")

    monkeypatch.setattr(requests, "post", recording_post)
    upload_results.upload_results_http(
        make_tests(t1=True), make_parameters("http://127.0.0.1:1/upload"), make_args()
    )
    assert len(calls) == 1
    timeout = calls[0][1].get("timeout")
    assert isinstance(timeout, (int, float)) and timeout > 0, calls[0]
