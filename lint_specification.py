"""
Check a test specification without running any test: autotest --lint.

This exists because a broken reference is invisible until a student hits it,
and then it does not look like a broken reference.  Replaying the COMP1511
26T2 activities, exercises reported "could not be run because check failed"
and it took hours to find out why: their checkers are symbolic links into an
_infra submodule which was not checked out.  Nothing said so, and the obvious
reading -- that the specifications were wrong -- was wrong.  Across the three
26T2 courses 37 such references dangle and 366 resolve; 50 of COMP1511's 410
specifications are affected.

What is checked here has to earn its place.  Three rules were designed and
dropped after measuring them against the 2,590 real specifications in
COMP1511, COMP1521 and COMP2041:

  * "a file the submission must supply is also supplied by the autotest" is
    the documented CSE idiom, not a defect: the course supplies the test
    harness and names it in files= so the student must have the copy they
    were given.  61 specifications do it deliberately.
  * "expected output holds one of autotest's own error messages" cannot
    happen: --generate_expected_output turns every limit off before running
    the solution.  0 findings.
  * "a parameter documented as global-only is written on a test line" is
    false for two of the five such parameters -- shared_test_directory and
    supplied_files_directory are read from the test's own parameters and do
    take effect there.  0 findings.

A rule which never fires teaches a reader to ignore the tool.
"""

import os
import shlex
import sys
from collections.abc import Iterator
from typing import Any, Optional

# Parameters whose value names a program to run.  compilers is deliberately
# absent: a compiler is found on PATH, not in the autotest directory, and
# checking whether this host has one is what running the tests does.
COMMAND_PARAMETERS = (
    "checkers",
    "pre_compile_command",
    "setup_command",
    "postprocess_output_command",
)

# checkers holds a LIST of commands; the others hold one command.  The shapes
# collide: ["sh", "-c", "x"] is one argv as a pre_compile_command and three
# shell commands as checkers, and COMP1521's 22t2supp_q10 writes
# checkers=["1521 c_check", "./check-features-used"] meaning two of them.
LISTS_OF_COMMANDS = ("checkers",)


def command_lines(value: Any, many: bool) -> Iterator[Any]:
    """
    The commands in one parameter's value.

    With many, every element of a list is its own command (checkers);
    without it a list is one argv.  An empty element means "no checker" and
    is skipped, which is how run_checkers reads them -- checkers="" is the
    documented way to turn the default off and finalizes to [[]].
    """
    if not value:
        return
    if not isinstance(value, (list, tuple)):
        yield value
    elif many:
        for item in value:
            if item:
                yield item
    else:
        yield list(value)


def program_named(command: Any) -> Optional[str]:
    """
    The program a command runs, or None if it cannot be determined.

    A list command is argv, so the program is its first element.  A string
    is a shell line: only its first word is the program, which is why this
    tokenises rather than looking for a path anywhere in the line.
    "cp ./template.c ./main.c" runs cp, and reporting ./main.c as a missing
    command -- which a regular expression over the whole line would do --
    would be a finding about a file the command is about to create.
    """
    if isinstance(command, (list, tuple)):
        return str(command[0]) if command else None
    try:
        words = shlex.split(str(command))
    except ValueError:  # an unbalanced quote; the parser reports that
        return None
    return words[0] if words else None


def describe_unresolved(directory: str, program: str) -> Optional[str]:
    """
    Why this program cannot be run, or None if it can.

    Only a program named by a path is checked.  A bare name is looked up on
    PATH at run time, and this host's PATH says nothing about the host the
    tests will run on.
    """
    if not program.startswith(("./", "../", "/")):
        return None
    path = program if program.startswith("/") else os.path.join(directory, program)

    if not os.path.lexists(path):
        return "does not exist"
    if not os.path.exists(path):
        target = os.readlink(path) if os.path.islink(path) else "?"
        return (
            f"is a symbolic link to {target}, which does not exist"
            " (an un-checked-out submodule?)"
        )
    if not os.access(path, os.X_OK):
        return "is not executable"
    return None


def check_commands(tests: dict, directory: str) -> Iterator[tuple[str, str, str, str]]:
    """
    Yield (label, parameter, program, problem) for each command that cannot
    be run.

    A specification's parameters are usually global, so the same problem
    would otherwise be reported once per test: COMP1521's cs_chicken has 153
    of them.  Each (parameter, program) is reported once, against the first
    test that uses it.
    """
    seen = set()
    for test in tests.values():
        parameters = getattr(test, "parameters", test)
        label = str(parameters.get("label", "?"))
        for name in COMMAND_PARAMETERS:
            many = name in LISTS_OF_COMMANDS
            for command in command_lines(parameters.get(name), many):
                program = program_named(command)
                if program is None or (name, program) in seen:
                    continue
                seen.add((name, program))
                problem = describe_unresolved(directory, program)
                if problem:
                    yield (label, name, program, problem)


def lint(tests: dict, directory: str, pathname: str, file=None) -> int:
    """
    Report what is wrong with a specification; return the number of findings.

    One finding per line, and nothing at all when there is nothing wrong, so
    that the exit status is the whole verdict and a loop over a course tree
    is usable.
    """
    file = file or sys.stdout
    findings = sorted(check_commands(tests, directory))
    for label, parameter, program, problem in findings:
        print(
            f"{pathname}: unresolved-command: {parameter} of test {label}"
            f" runs '{program}', which {problem}",
            file=file,
        )
    return len(findings)
