"""
The replay's oracle, shrunk to fixtures so it runs in the suite.

Every defect the parallel work introduced was found by replaying real course
material and none by this suite, which was green throughout -- including
while a chmod race was silently marking a permissions exercise on the wrong
permissions.  Each of those defects now has its own regression test, but each
was written after the replay found it.  This is the net for the next one.

Two properties, checked over specifications built to exercise the machinery
that has actually broken:

  * a run is reproducible: the same specification twice produces the same
    output, so a specification cannot quietly depend on what a previous run
    left behind;
  * parallelism is invisible: -j produces the same output as serial, which is
    the promise the parallel work makes and the one that a shared-state
    defect breaks.

  * the same work is done: a cached support command runs the same number of
    times either way, which output equality does not show -- COMP1521's
    pacman ran its checker 140 times under -j and printed the same verdicts.

None of the three needs to know what could go wrong, which is the point.
Verified by reintroducing the defects into a copy of the tree: the chmod
race fails "parallelism is invisible", and moving the checkers back onto a
worker thread without the per-command lock fails "the same work is done"
(one run serial, four under -j 8).  Two that these would NOT have caught, and
which keep their own targeted tests, are the per-test pre_compile_command
(wrong in serial too, so both sides agree) and the sandbox's missing DNS
(nothing here uses the network).
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SH = "#!/bin/sh\n"


# A compilation that needs no compiler: the "compile command" writes the
# program, so these specifications run anywhere the suite does.
def build(word):
    return f"\"sh -c 'echo echo {word} > prog; chmod 755 prog'\""


SPECIFICATIONS = {
    # two distinct pre_compile_commands put every test on the per-test
    # preparation path, which is where preparation has broken three times
    "per_test_preparation": (
        (
            "files=marker.txt\nprogram=prog\n"
            'one pre_compile_command="echo first > built.txt"\n'
            f"one compile_commands={build('one')}\n"
            'one command="cat built.txt; ./prog" expected_stdout="first\\none\\n"\n'
            'two pre_compile_command="sed -i s/first/second/ built.txt"\n'
            f"two compile_commands={build('two')}\n"
            'two command="cat built.txt; ./prog" expected_stdout="second\\ntwo\\n"\n'
        ),
        {"marker.txt": ""},
    ),
    # a cached support command: it must run once and be reported once,
    # wherever the tests run
    "checkers": (
        (
            "files=marker.txt\nprogram=prog\n"
            'checkers=[["sh", "-c", "echo checked"]]\n'
            # enough tests, on the per-test preparation path, for a support
            # command running on a worker thread to race: with three it never
            # did, and COMP1521's pacman ran its checker 140 times
        )
        + "".join(
            f'{n} pre_compile_command="true {n % 2}"\n'
            f'{n} command="echo {n}" expected_stdout="{n}\\n"\n'
            for n in range(1, 25)
        ),
        {"marker.txt": ""},
    ),
    # a file whose mode forbids reading, in a test that inspects modes:
    # copying it widens the mode of a file every other test is copying
    "unreadable_file": (
        (
            "files=show.sh\nprogram=./show.sh\n"
            'pre_compile_command="chmod 223 unreadable >unreadable"\n'
            "pre_compile_command_shell=1\n"
        )
        + "".join(f'{n} expected_stdout="--w--w--wx\\n"\n' for n in range(1, 9)),
        {"show.sh": '#!/bin/sh\nls -l unreadable | cut -d" " -f1\n'},
    ),
    # setup_command runs per test and writes into the test's directory
    "setup_command": (
        (
            "files=show.sh\nprogram=./show.sh\n"
            'setup_command="printf x >>counted"\n'
            + "".join(
                f'{n} command="wc -c <counted" expected_stdout="1\\n"\n'
                for n in range(1, 7)
            )
        ),
        {"show.sh": SH},
    ),
    # expected_files, checked in the test's own directory
    "expected_files": (
        (
            "files=show.sh\nprogram=./show.sh\n"
            + "".join(
                f'{n} command="echo {n} >made.txt" expected_files={{"made.txt": "{n}\\n"}}\n'
                for n in range(1, 6)
            )
        ),
        {"show.sh": SH},
    ),
    # one test, several compile commands, all producing the same program name
    "several_compilations": (
        (
            "files=marker.txt\nprogram=prog\n"
            f"compile_commands=[{build('x')}, {build('x')}]\n"
            + "".join(
                f'{n} command="./prog" expected_stdout="x\\n"\n' for n in range(1, 5)
            )
        ),
        {"marker.txt": ""},
    ),
    # the opt-out: every test in one directory, using what the last left
    "shared_test_directory": (
        (
            "files=show.sh\nprogram=./show.sh\n"
            "shared_test_directory=1\n"
            'maker setup_command="touch left_behind" command="ls left_behind"\n'
            'maker expected_stdout="left_behind\\n"\n'
            'user command="ls left_behind" expected_stdout="left_behind\\n"\n'
        ),
        {"show.sh": SH},
    ),
}


def normalise(output):
    """Remove what legitimately differs between two runs of one tree."""
    output = re.sub(r"/tmp/[A-Za-z0-9_./-]+", "TMP", output)
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    return re.sub(r"0x[0-9a-f]+", "0xADDRESS", output)


@pytest.fixture(params=sorted(SPECIFICATIONS))
def specification(request, tmp_path, make_exercise):
    tests_txt, files = SPECIFICATIONS[request.param]
    exercise = make_exercise(tmp_path, tests_txt, files=files)
    return request.param, exercise


def test_a_run_is_reproducible(specification, run_autotest):
    """
    The same specification twice produces the same output.

    A specification that depends on what a previous run left behind is
    marking students on it.
    """
    name, exercise = specification
    first, _stderr, first_status = run_autotest(exercise.args + ["--no_sandbox"])
    second, _stderr, second_status = run_autotest(exercise.args + ["--no_sandbox"])
    assert normalise(first) == normalise(second), name
    assert first_status == second_status


@pytest.mark.parametrize("jobs", ["4", "8"])
def test_parallelism_is_invisible(specification, run_autotest, jobs):
    """
    -j produces what serial produces.

    This is the promise the parallel work makes, and the property that a
    defect in shared state breaks: the chmod race, the checkers running once
    per test, and the dangling program link each broke it.
    """
    name, exercise = specification
    serial, _stderr, serial_status = run_autotest(exercise.args + ["--no_sandbox"])
    parallel, _stderr, parallel_status = run_autotest(
        exercise.args + ["--no_sandbox", "-j", jobs]
    )
    assert normalise(serial) == normalise(parallel), name
    assert serial_status == parallel_status


def test_the_same_work_is_done(tmp_path, make_exercise, run_autotest):
    """
    A cached support command runs the same number of times, serial or not.

    Output equality does not show this: checking the cache, running the
    command and storing the result were three steps with no lock across
    them, so every worker that looked before anyone stored ran the command
    too -- 140 runs of pacman's checker where a serial run did one, with the
    same verdicts printed either way.
    """
    counter = tmp_path / "support_runs"
    tests = "".join(
        f'{n} pre_compile_command="true {n % 2}"\n'
        f'{n} command="echo {n}" expected_stdout="{n}\\n"\n'
        for n in range(1, 25)
    )
    exercise = make_exercise(
        tmp_path,
        "files=marker.txt\nprogram=prog\n"
        f'checkers=[["sh", "-c", "printf x >>{counter}"]]\n' + tests,
        files={"marker.txt": ""},
    )

    counts = []
    for extra in ([], ["-j", "8"]):
        counter.write_text("")
        stdout, stderr, status = run_autotest(exercise.args + ["--no_sandbox", *extra])
        assert status == 0, stdout + stderr
        counts.append(len(counter.read_text()))
    assert counts[0] == counts[1] == 1, counts


def test_every_specification_here_passes_its_own_tests(specification, run_autotest):
    """
    A comparison that passes because nothing happened is worse than one that
    fails, so each specification must actually pass.
    """
    name, exercise = specification
    stdout, stderr, status = run_autotest(exercise.args + ["--no_sandbox"])
    assert "tests failed" in stdout, (name, stdout, stderr)
    assert " 0 tests failed" in stdout.replace("  ", " "), (name, stdout)
    assert status == 0, (name, stdout, stderr)
