# run a single test
#
# This code needs extensive rewriting.
# Much of the code can be moved to parameter_descriptions.py

import codecs
import errno
import os
import re
import shlex
import tempfile
from typing import Any, Optional, Union

from termcolor import colored as termcolor_colored

from explain_output_differences import explain_output_differences, sanitize_string
from subprocess_with_resource_limits import run
from util import InternalError


def _open_no_symlink(path: str, flags: int) -> int:
    """
    open() a path, refusing to follow a symbolic link in its last component.

    A test's files are created by the submitted program, so any of them may be
    a link the program made.  Following one reads whatever the account running
    autotest can read -- another submission, a solution, a private key -- and
    check_files prints what it read back to the student in the difference.
    The sandbox puts those files out of reach, but a test run without one
    (a student on their own account, --no_sandbox) has only this.
    """
    return os.open(path, flags | os.O_NOFOLLOW)


# Output limits for support commands (compilers, checkers, setup and
# postprocess commands).  They are not resource-limited like a test, but a
# runaway compiler must not be able to exhaust autotest's memory.
SUPPORT_COMMAND_MAX_OUTPUT_BYTES = 10_000_000


class CommandRunner:
    """
    Runs every command that executes on the student's behalf: the test
    command itself and, when sandbox_support_commands is true, the compilers,
    checkers, setup and postprocess commands.

    One runner is shared by every test of a run.  It owns the sandbox
    decision (config is None when the sandbox is off) so that no test or
    support command can forget to apply it, and it builds one Sandbox per
    command because a Sandbox is single use.

    temp_root is where the empty mountpoint directory for each sandbox root
    is created: a sibling of the working directory, so it is neither inside
    the work directory (which is copied per test) nor an ancestor of it (the
    sandbox forbids that).  All callers pass an absolute work_dir and never
    depend on the process's cwd, so tests can run on threads.
    """

    def __init__(self, config, temp_root, debug=0, note_sandbox=None):
        self.config = config
        self.temp_root = temp_root
        self.debug = debug
        self.note_sandbox = note_sandbox

    def sandboxed(self, parameters, support_command=False):
        """True iff this command must run inside a sandbox"""
        if self.config is None:
            return False
        return not support_command or bool(
            parameters.get("sandbox_support_commands", True)
        )

    def run(
        self, command, parameters, work_dir, env, support_command=False, stdin=None
    ):
        """
        run command with the test's parameters (resource limits, stdin, ...)
        in work_dir, with environment env (None inherits autotest's own),
        returning (stdout, stderr, returncode) from subprocess_with_resource_limits.run

        A support command (compiler, checker, setup or postprocess command) is
        not resource-limited like a test and does not read the test's stdin:
        it gets stdin (a str, bytes or None for no input), which is how the
        postprocess command receives the output it filters.
        """
        run_parameters = dict(parameters)
        run_parameters.update(command=command, cwd=work_dir, env=env, sandbox=None)
        if support_command:
            # their output is still bounded so a runaway compiler can not
            # exhaust memory
            run_parameters.update(
                stdin=stdin,
                unicode_stdin=not isinstance(stdin, (bytes, bytearray)),
                max_real_seconds=0,
                max_cpu_seconds=0,
                max_stack_bytes=0,
                max_rss_bytes=0,
                max_file_size_bytes=0,
                max_processes=0,
                max_open_files=0,
                max_stdout_bytes=SUPPORT_COMMAND_MAX_OUTPUT_BYTES,
                max_stderr_bytes=SUPPORT_COMMAND_MAX_OUTPUT_BYTES,
                # --stats reports what a TEST cost; a compiler's memory is
                # not something a specification author can act on
                report_resource_usage=False,
            )
        if not self.sandboxed(parameters, support_command):
            return run(**run_parameters)

        # imported here so autotest still works on platforms where the
        # sandbox module can not be imported and the sandbox is off
        from sandbox import Sandbox

        # a shell resolves a string command itself, so only a list command's
        # argv[0] can be checked inside the sandbox
        argv0 = None if isinstance(command, str) else str(command[0])
        root_dir = tempfile.mkdtemp(prefix=".sandbox-root-", dir=self.temp_root)
        sandbox = None
        try:
            sandbox = Sandbox(
                self.config,
                work_dir,
                root_dir,
                debug=self.debug,
                executable_check=argv0,
                env=env,
            )
            if self.note_sandbox:
                self.note_sandbox(sandbox)
            run_parameters["sandbox"] = sandbox
            return run(**run_parameters)
        finally:
            if sandbox is not None:
                sandbox.close()
            # the sandbox's root is mounted only in the child's private mount
            # namespace so the directory is still empty on the host
            try:
                os.rmdir(root_dir)
            except OSError:
                pass


class _Test:
    # set by run_test() and check_files(); declared without a value so that a
    # test which could not be run has no stdout attribute, which is how
    # print_expected_output() tells
    stdout: Any
    stderr: Any
    file_expected: Any
    file_actual: Any

    def __init__(self, autotest_dir, **parameters):
        debug = parameters["debug"]
        self.autotest_dir = autotest_dir

        # FIXME implement UNICODE handling
        # ignore all characters but those specified
        if parameters.get("compare_only_characters", ""):
            mapping = dict.fromkeys([chr(v) for v in range(256)], None)
            if debug:
                print("compare_only_characters", parameters["compare_only_characters"])
            for c in parameters["compare_only_characters"] + "\n":
                mapping.pop(c, None)
        else:
            mapping = dict.fromkeys(parameters["ignore_characters"], None)

        self.canonical_translator = "".maketrans(mapping)
        self.command = parameters["command"]
        self.debug = parameters["debug"]
        self.files = parameters["files"]
        self.expected_stdout = parameters["expected_stdout"]
        self.expected_stderr = parameters["expected_stderr"]
        self.explanation = None
        self.label = parameters["label"]
        self.parameters = parameters
        self.program = parameters["program"]
        self.stdin = parameters["stdin"]

        self.test_passed = None

    def __str__(self):
        return f"Test({self.label}, {self.program}, {self.command})"

    def run_test(self, work_dir, runner=None, compile_command=""):
        """
        run this test's command in work_dir (an absolute pathname) using
        runner (a CommandRunner, or None to run without a sandbox from the
        current directory) and return True iff the test passed

        Every relative pathname in the test (expected files, the postprocess
        command's cwd) is resolved against work_dir, never the process's cwd:
        tests can run concurrently in their own directories.
        """
        if self.debug > 1:
            print(
                f'run_test(compile_command="{compile_command}", command="{self.command}")\n'
            )
        self.work_dir = work_dir
        self.runner = runner or CommandRunner(None, os.path.dirname(work_dir))

        result = self.runner.run(
            self.command, self.parameters, work_dir, self.parameters["environment"]
        )
        stdout, stderr, self.returncode = result
        # None unless report_resource_usage asked for it: see --stats
        self.resource_usage = getattr(result, "usage", None)

        if self.parameters["unicode_stdout"]:
            self.stdout = codecs.decode(stdout, "UTF-8", errors="replace")
        else:
            self.stdout = stdout

        if self.parameters["unicode_stderr"]:
            self.stderr = codecs.decode(stderr, "UTF-8", errors="replace")
        else:
            self.stderr = stderr

        self.short_explanation = None
        self.long_explanation = None

        stdout_short_explanation = self.check_stream(
            self.stdout, self.expected_stdout, "output"
        )
        if not self.parameters["allow_unexpected_stderr"] or stdout_short_explanation:
            if (
                self.parameters["dcc_output_checking"]
                and "Execution stopped because" in self.stderr
            ):
                self.short_explanation = "incorrect output"
            else:
                self.short_explanation = self.check_stream(
                    self.stderr, self.expected_stderr, "stderr"
                )

        self.stderr_ok = not self.short_explanation

        self.stdout_ok = not stdout_short_explanation

        if not self.short_explanation:
            self.short_explanation = stdout_short_explanation

        if not self.short_explanation:
            self.short_explanation = self.check_files()

        self.test_passed = not self.short_explanation
        if not self.test_passed:
            self.failed_compiler = (
                " ".join(compile_command)
                if isinstance(compile_command, list)
                else str(compile_command)
            )
        return self.test_passed

    def check_files(self):
        for pathname, expected_contents in self.parameters["expected_files"].items():
            path = os.path.join(self.work_dir, pathname)
            try:
                if self.parameters["unicode_files"]:
                    with open(
                        path,
                        encoding="UTF-8",
                        errors="replace",
                        opener=_open_no_symlink,
                    ) as f:
                        actual_contents: Union[str, bytes] = f.read()
                else:
                    with open(path, mode="rb", opener=_open_no_symlink) as f:
                        actual_contents = f.read()
            except OSError as e:
                if e.errno == errno.ELOOP:
                    # the program put a link where the file should be, and
                    # following it would read, and then print back in the
                    # difference, whatever the account running autotest can
                    # read: another submission, a solution, a private key
                    self.long_explanation = (
                        f"Your program was expected to create a file named '{pathname}'"
                        " and created a symbolic link instead\n"
                    )
                else:
                    self.long_explanation = f"Your program was expected to create a file named '{pathname}' and did not\n"
                actual_contents = ""
            short_explanation = self.check_stream(
                actual_contents, expected_contents, f"file: {pathname}"
            )
            if short_explanation:
                self.file_not_ok = pathname
                self.file_expected = expected_contents
                self.file_actual = actual_contents
                return short_explanation
        return None

    def check_stream(  # noqa: C901, PLR0911 - one early return per way a stream can differ
        self, actual, expected, name
    ):
        if self.debug:
            print("name:", name)
            print("actual:", actual[0:256] if actual else "")
            print("expected:", expected[0:256] if expected else "")
        if actual:
            if expected:
                # Handling non-unicode IO
                if type(actual) in (bytearray, bytes) or type(expected) in (
                    bytearray,
                    bytes,
                ):
                    if actual == bytearray(expected):
                        return None
                    return "Your non-unicode output is not correct"
                # handling unicode input
                if self.compare_strings(actual, expected):
                    return None
                return "Incorrect " + name
            if name == "stderr":
                return "errors"
            if name == "output":
                return name + " produced when none expected"
            return name + " should be empty and was not"
        if expected:
            if name.lower().startswith("file"):
                return f"File {name} is empty"
            return f"No {name} produced"
        return None

    def make_string_canonical(self, raw_str, keep_all_lines=False):
        s = re.sub("\r\n?", "\n", raw_str)
        output_filter = self.parameters.get("postprocess_output_command", None)

        if output_filter:
            if self.debug:
                print(f"postprocess_output_command={output_filter} str='{s}'")
            # the filter runs like the other support commands: in the
            # test's directory with the test's environment, fed the text
            stdout, stderr, returncode = self.runner.run(
                output_filter,
                self.parameters,
                self.work_dir,
                self.parameters["environment"],
                support_command=True,
                stdin=s,
            )
            if stderr:
                raise InternalError(
                    "error from postprocess_output_command: "
                    + codecs.decode(stderr, "UTF-8", errors="replace")
                )
            if returncode:
                raise InternalError(
                    "non-zero exit status from postprocess_output_command"
                )
            s = re.sub("\r\n?", "\n", codecs.decode(stdout, "UTF-8", errors="replace"))
            if self.debug:
                print(f"after filter s='{s}'")

        if self.parameters["ignore_case"]:
            s = s.lower()
        s = s.translate(self.canonical_translator)
        if self.parameters["ignore_blank_lines"] and not keep_all_lines:
            s = re.sub(r"\n\s*\n", "\n", s)
            s = re.sub(r"^\n+", "", s)
        if self.parameters["ignore_trailing_whitespace"]:
            s = re.sub(r"[ \t]+\n", "\n", s)
        if self.debug > 1:
            print(f"make_string_canonical('{raw_str}') -> '{s}'")
        return s

    def compare_strings(self, actual, expected):
        return self.make_string_canonical(actual) == self.make_string_canonical(
            expected
        )

    def get_long_explanation(  # noqa: C901, PLR0912, PLR0915 - the explanation a novice reads, built case by case
        self,
    ):
        if self.debug:
            print(
                "get_long_explanation() short_explanation=",
                self.short_explanation,
                "long_explanation=",
                self.long_explanation,
                "stderr_ok=",
                self.stderr_ok,
                "expected_stderr=",
                self.expected_stderr,
            )
        if self.long_explanation:
            return self.long_explanation
        colored = (
            termcolor_colored
            if self.parameters["colorize_output"]
            else lambda x, *_args, **_kwargs: x
        )
        self.long_explanation = ""
        if not self.stderr_ok:
            if self.expected_stderr:
                if self.parameters["unicode_stderr"]:
                    self.long_explanation += self.report_difference(
                        "stderr", self.expected_stderr, self.stderr
                    )
                else:
                    self.long_explanation = f"You had 0x{self.stderr.hex()} as stderr. "
                    self.long_explanation += (
                        f"You should have 0x{self.expected_stderr.hex()}\n\n"
                    )
                    expected_bits = self.expected_stderr
                    actual_bits = self.stderr
                    self.long_explanation += self.report_bit_differences(
                        expected_bits, actual_bits
                    )

            elif (
                self.parameters["dcc_output_checking"]
                and "Execution stopped because" in self.stderr
            ):
                n_output_lines = len(self.stdout.splitlines())
                self.long_explanation += f"Your program produced these {n_output_lines} lines of output before it was terminated:\n"
                self.long_explanation += colored(
                    sanitize_string(self.stdout, **self.parameters), "cyan"
                )
                self.long_explanation += self.stderr + "\n"
            else:
                errors = sanitize_string(
                    self.stderr,
                    leave_tabs=True,
                    leave_colorization=True,
                    **self.parameters,
                )
                if "\x1b" not in self.long_explanation:
                    errors = colored(errors, "red")
                if "Error too much output" in self.stderr:
                    errors += f"Your program produced these {len(self.stdout)} bytes of output before it was terminated:\n"
                    errors += colored(
                        sanitize_string(self.stdout, **self.parameters), "yellow"
                    )
                if self.stdout_ok and self.expected_stdout:
                    self.long_explanation = (
                        "Your program's output was correct but errors occurred:\n"
                    )
                    self.long_explanation += errors
                    self.long_explanation += "Apart from the above errors, your program's output was correct.\n"
                else:
                    self.long_explanation = "Your program produced these errors:\n"
                    self.long_explanation += errors
        if not self.stdout_ok and (
            self.parameters["show_stdout_if_errors"] or self.stderr_ok
        ):
            # If we don't have unicode in out stdout, we should check for bad characters
            bad_characters: Optional[str] = None
            if self.parameters["unicode_stdout"]:
                bad_characters = self.check_bad_characters(
                    self.stdout, expected=self.expected_stdout
                )
            if bad_characters:
                self.long_explanation += bad_characters
                self.parameters["show_diff"] = False
            # report output differences in a easily readable manner
            # if we don't have unicode input.
            if self.parameters["unicode_stdout"]:
                self.long_explanation += self.report_difference(
                    "output", self.expected_stdout, self.stdout
                )
            else:
                self.long_explanation = f"You had 0x{self.stdout.hex()} as stdout. "
                self.long_explanation += (
                    f"You should have 0x{self.expected_stdout.hex()}\n\n"
                )
                expected_bits = self.expected_stdout
                actual_bits = self.stdout
                self.long_explanation += self.report_bit_differences(
                    expected_bits, actual_bits
                )

        if self.stdout_ok and self.stderr_ok and self.file_not_ok:
            if self.parameters["unicode_files"]:
                self.long_explanation = self.report_difference(
                    self.file_not_ok, self.file_expected, self.file_actual
                )
            else:
                self.long_explanation = "Your non-unicode files had incorrect output\n"
                self.long_explanation += (
                    f"File {self.file_not_ok} had the following error:\n"
                )
                self.long_explanation += f"expected: 0x{self.file_expected.hex()} "
                self.long_explanation += f"actual: 0x{self.file_actual.hex()}\n"
                expected_bits = self.file_expected
                actual_bits = self.file_actual
                self.long_explanation += self.report_bit_differences(
                    expected_bits, actual_bits
                )

        std_input = self.stdin
        unicode_stdin = self.parameters["unicode_stdin"]

        # we don't want to consider newlines when dealing with non-unicode output
        if self.parameters["unicode_stdin"]:
            n_input_lines = std_input.count("\n")

        if self.parameters["show_stdin"]:
            if unicode_stdin and std_input and n_input_lines < 32:
                self.long_explanation += (
                    f"\nThe input for this test was:\n{colored(std_input, 'yellow')}\n"
                )
                if std_input[-1] != "\n" and "\n" in std_input[:-2]:
                    self.long_explanation += (
                        "Note: last character in above input is not '\\n'\n\n"
                    )
            elif (not unicode_stdin) and std_input:
                self.long_explanation += f"\nThe input for this test was:\n{colored('0x' + std_input.hex(), 'yellow')}\n"

        if self.parameters["show_reproduce_command"]:
            indent = "  "
            self.long_explanation += (
                "You can reproduce this test by executing these commands:\n"
            )
            if self.failed_compiler:
                self.long_explanation += colored(
                    indent + self.failed_compiler + "\n", "blue"
                )
            command = (
                " ".join(self.command)
                if isinstance(self.command, list)
                else self.command
            )
            if std_input:
                if unicode_stdin:
                    echo_command = echo_command_for_string(std_input)
                else:
                    echo_command = (
                        "echo -ne '" + self.insert_hex_slash_x(std_input.hex()) + "'"
                    )

                if "shell" in self.parameters and (
                    ";" in command or "&" in command or "|" in command
                ):
                    command = "(" + command + ")"
                command = f"{echo_command} | {command}"
                command = indent + command
            else:
                if "shell" in self.parameters and not self.parameters.get(
                    "no_replace_semicolon_reproduce_command", ""
                ):
                    command = re.sub(r"\s*;\s*", "\n" + indent, command)
                command = indent + command

            self.long_explanation += colored(command + "\n", "blue")
        return self.long_explanation

    def report_difference(self, name, expected, actual):
        if self.debug:
            print(f"report_difference({name}, '{expected}', '{actual}')")
        canonical_expected = self.make_string_canonical(expected)
        canonical_actual = self.make_string_canonical(actual)
        canonical_actual_plus_newlines = self.make_string_canonical(
            actual, keep_all_lines=True
        )
        canonical_expected_plus_newlines = self.make_string_canonical(
            expected, keep_all_lines=True
        )
        return explain_output_differences(
            name,
            expected,
            canonical_expected,
            canonical_expected_plus_newlines,
            actual,
            canonical_actual,
            canonical_actual_plus_newlines,
            **self.parameters,
        )

    def report_bit_differences(self, expected, actual):
        feedback = ""

        # compare bit length
        expected_len = len(expected)
        actual_len = len(actual)
        if expected_len != actual_len:
            feedback = f"Your output was {actual_len} bytes long. "
            feedback += f"It should have been {expected_len} bytes long. "
            return feedback

        # int.from_bytes copes with empty bytes, unlike int(b.hex(), 16)
        different_bits = int.from_bytes(bytes(expected), "big") ^ int.from_bytes(
            bytes(actual), "big"
        )
        n_different = bin(different_bits).count("1")

        feedback += f"There were {n_different} different bits between your output and the expected output\n"

        return feedback

    def check_bad_characters(self, string, expected=""):
        if re.search(r"[\x00-\x08\x0e-\x1f\x7f-\xff]", expected):
            return None
        colored = (
            termcolor_colored
            if self.parameters["colorize_output"]
            else lambda x, *_args, **_kwargs: x
        )
        for line_number, line in enumerate(string.splitlines()):
            m = re.search(r"^(.*?)([\x00-\x08\x0e-\x1f\x7f-\xff])", line)
            if not m:
                continue
            prefix, offending_char = m.groups()
            offending_value = ord(offending_char)
            if offending_value == 0:
                description = "zero byte ('" + colored(r"\0", "red") + "')"
            elif offending_value > 127:
                description = "non-ascii byte " + colored(
                    r"\x" + f"{offending_value:02x}", "red"
                )
            else:
                description = "non-printable character " + colored(
                    r"\x" + f"{offending_value:02x}", "red"
                )
            column = len(prefix)
            explanation = f"Byte {column + 1} of line {line_number + 1} of your program's output is a {description}\n"
            explanation += f"Here is line {line_number + 1} with non-printable characters replaced with backslash-escaped equivalents:\n\n"
            line = line.encode("unicode_escape").decode("ascii") + "\n\n"
            line = re.sub(r"(\\x[0-9a-f][0-9a-f])", colored(r"\1", "red"), line)
            explanation += line
            return explanation
        return None

    # inserts \x into a hex string, useful for printing sometimes
    def insert_hex_slash_x(self, string):
        return "\\x" + "\\x".join(string[i : i + 2] for i in range(0, len(string), 2))


def echo_command_for_string(test_input):
    options = []
    if test_input and test_input[-1] == "\n":
        test_input = test_input[:-1]
    else:
        options += ["-n"]
    echo_string = shlex.quote(test_input)
    if "\n" in test_input[:-1]:
        echo_string = echo_string.replace("\\", r"\\")
        options += ["-e"]
    echo_string = echo_string.replace("\n", r"\n")
    command = "echo "
    if options:
        command += " ".join(options) + " "
    return command + echo_string
