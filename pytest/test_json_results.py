"""
Tests for --json, the machine-readable description of a run.

There was no way to ask "which test did the cohort fail most?" without
parsing coloured terminal output: upload_results.py posts a zip to an HTTP
endpoint, which is telemetry for a server rather than something course staff
or CI can read.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SH = "#!/bin/sh\n"
SPEC = (
    "files=a.sh\nprogram=./a.sh\n"
    'good command="echo hi" expected_stdout="hi\\n"\n'
    'bad command="echo bye" expected_stdout="hi\\n"\n'
    'crash command="sh -c \'kill -SEGV $$\'" expected_stdout=""\n'
)


def run_with_json(tmp_path, make_exercise, run_autotest, extra=()):
    exercise = make_exercise(tmp_path, SPEC, files={"a.sh": SH})
    out = tmp_path / "results.json"
    stdout, stderr, status = run_autotest(
        exercise.args + ["--no_sandbox", "--json", str(out), *extra]
    )
    return json.loads(out.read_text()), stdout, stderr, status


def test_json_describes_every_test_in_specification_order(
    tmp_path, make_exercise, run_autotest
):
    document, _stdout, _stderr, _status = run_with_json(
        tmp_path, make_exercise, run_autotest
    )
    assert document["version"] == 1
    assert [t["label"] for t in document["tests"]] == ["good", "bad", "crash"]
    assert document["summary"] == {
        "passed": 2,
        "failed": 1,
        "could_not_be_run": 0,
        "unstable": 0,
    }


def test_json_explanation_is_null_where_there_is_nothing_to_explain(
    tmp_path, make_exercise, run_autotest
):
    """
    TestOutcome starts short_explanation as "" and only a failure sets it, so
    a consumer testing `explanation is None` to find the passes would
    otherwise find nothing.
    """
    document, _stdout, _stderr, _status = run_with_json(
        tmp_path, make_exercise, run_autotest
    )
    by_label = {t["label"]: t for t in document["tests"]}
    assert by_label["good"]["explanation"] is None
    assert by_label["bad"]["explanation"] == "Incorrect output"


def test_json_reports_a_signal_as_a_signal(tmp_path, make_exercise, run_autotest):
    """returncode follows Popen, where a signal is negative; a consumer
    should not have to know that."""
    document, _stdout, _stderr, _status = run_with_json(
        tmp_path, make_exercise, run_autotest
    )
    crash = next(t for t in document["tests"] if t["label"] == "crash")
    assert crash["signal"] == 11
    assert crash["exit_status"] is None


def test_json_order_does_not_depend_on_parallel_tests(
    tmp_path, make_exercise, run_autotest
):
    """Records are appended by report_outcome on the main thread, which runs
    in test order whatever parallel_tests is."""
    document, _stdout, _stderr, _status = run_with_json(
        tmp_path, make_exercise, run_autotest, extra=["-j", "8"]
    )
    assert [t["label"] for t in document["tests"]] == ["good", "bad", "crash"]


def test_json_carries_the_resources_when_stats_asked(
    tmp_path, make_exercise, run_autotest
):
    document, _stdout, _stderr, _status = run_with_json(
        tmp_path, make_exercise, run_autotest, extra=["--stats"]
    )
    assert all(t["resources"] is not None for t in document["tests"])
    assert all(t["resources"]["real_seconds"] >= 0 for t in document["tests"]), document


def test_json_is_absent_unless_asked_for(tmp_path, make_exercise, run_autotest):
    exercise = make_exercise(tmp_path, SPEC, files={"a.sh": SH})
    stdout, _stderr, _status = run_autotest(exercise.args + ["--no_sandbox"])
    assert "version" not in stdout, stdout
    assert not list(tmp_path.glob("*.json")), list(tmp_path.glob("*.json"))


def test_json_to_stdout(tmp_path, make_exercise, run_autotest):
    exercise = make_exercise(tmp_path, SPEC, files={"a.sh": SH})
    stdout, _stderr, _status = run_autotest(
        exercise.args + ["--no_sandbox", "--json", "-"]
    )
    document = json.loads(stdout[stdout.index("{\n") :])
    assert document["summary"]["passed"] == 2


def test_a_json_path_that_can_not_be_written_still_runs_the_tests(
    tmp_path, make_exercise, run_autotest
):
    """
    The document is written after the helper and the upload, so a mistyped
    path costs the student neither -- and their results are still printed.
    """
    exercise = make_exercise(tmp_path, SPEC, files={"a.sh": SH})
    stdout, _stderr, status = run_autotest(
        exercise.args
        + ["--no_sandbox", "--json", str(tmp_path / "no" / "such" / "dir.json")]
    )
    assert "2 tests passed 1 tests failed" in stdout, stdout
    assert status == 2
