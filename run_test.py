# run a single test
#
# This code needs extensive rewriting.
# Much of the code can be moved to parameter_descriptions.py

import codecs, os, re, shlex, subprocess, time
from subprocess_with_resource_limits import run
from explain_output_differences import explain_output_differences, sanitize_string
from termcolor import colored as termcolor_colored


class InternalError(Exception):
    pass


class _Test:
    def __init__(self, autotest_dir, **parameters):
        debug = parameters["debug"]
        self.autotest_dir = autotest_dir

        # FIXME implement UNICODE handling
        # ignore all characters but those specified
        if parameters.get("compare_only_characters", ""):
            mapping = dict.fromkeys([chr(v) for v in range(0, 256)], None)
            if debug:
                print("compare_only_characters", parameters["compare_only_characters"])
            for c in parameters["compare_only_characters"] + "\n":
                mapping.pop(c, None)
        else:
            mapping = dict.fromkeys(parameters["ignore_characters"], None)
        # 		mapping['\r'] = '\n'

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

    def run_test(self, compile_command=""):
        if self.debug > 1:
            print(
                f'run_test(compile_command="{compile_command}", command="{self.command}")\n'
            )

        self.set_environ()

        for attempt in range(3):
            if self.debug > 1:
                print("run_test attempt", attempt)
            (stdout, stderr, self.returncode) = run(**self.parameters)
            if stdout or stderr or self.returncode == 0 or not self.expected_stdout:
                break
            if self.debug > 1:
                print("run_test retry", (stdout, stderr, self.returncode))
            # ugly work-around for
            # weird termination with non-zero exit status seen on some CSE servers
            # ignore this execution and try again
            time.sleep(1)

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
        for (pathname, expected_contents) in self.parameters["expected_files"].items():
            try:
                if self.parameters["unicode_files"]:
                    with open(pathname, encoding="UTF-8", errors="replace") as f:
                        actual_contents = f.read()
                else:
                    with open(pathname, mode="rb") as f:
                        actual_contents = f.read()
            except IOError:
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

    def check_stream(self, actual, expected, name):
        if self.debug:
            print("name:", name)
            print("actual:", actual[0:256] if actual else "")
            print("expected:", expected[0:256] if expected else "")
        if actual:
            if expected:
                # TODO: Make this better. May require new params.
                # FIXME.
                # probably only need to check for bytearray; I've put both for completeness.

                # Handling non-unicode IO
                if type(actual) in (bytearray, bytes) or type(expected) in (
                    bytearray,
                    bytes,
                ):
                    if actual == bytearray(expected):
                        return None
                    else:
                        return "Your non-unicode output is not correct"
                # handling unicode input
                if self.compare_strings(actual, expected):
                    return None
                else:
                    return "Incorrect " + name
            else:
                if name == "stderr":
                    return "errors"
                elif name == "output":
                    return name + " produced when none expected"
                else:
                    return name + " should be empty and was not"
        else:
            if expected:
                if name.lower().startswith("file"):
                    return f"File {name} is empty"
                else:
                    return f"No {name} produced"
            else:
                return None

    # TODO: get this to work with non-unicode input
    def make_string_canonical(self, raw_str, keep_all_lines=False):
        s = re.sub("\r\n?", "\n", raw_str)
        filter = self.parameters.get("postprocess_output_command", None)

        if filter:
            if self.debug:
                print(f"postprocess_output_command={filter} str='{s}'")
            p = subprocess.run(
                filter,
                stdout=subprocess.PIPE,
                input=s,
                stderr=subprocess.PIPE,
                shell=isinstance(filter, str),
                universal_newlines=True,
            )
            if p.stderr:
                raise InternalError(
                    "error from postprocess_output_command: " + p.stderr
                )
            if p.returncode:
                raise InternalError(
                    "non-zero exit status from postprocess_output_command"
                )
            s = p.stdout
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

    def stdin_file_name(self):
        return ""
        # fix-me for reproduce commands we should generate a filename in some circumstances
        if not self.stdin_file:
            return self.stdin_file
        if self.stdin_file[0] == "/":
            return self.stdin_file
        path = os.path.realpath(self.autotest_dir + "/" + self.stdin_file)
        path = re.sub(r"/tmp_amd/\w+/export/\w+/\d/(\w+)", r"/home/\1", path)
        return path

    # TODO: make this work better (and fully, as it is incomplete) with non-unicode IO
    def get_long_explanation(self):
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
            else lambda x, *a, **kw: x
        )
        # 		colored = lambda x,*a,**kw: x # disable use of blue below - hard to read, replace with more readable color
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
                    self.long_explanation += self.report_bit_differences(expected_bits, actual_bits)

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
            bad_characters = False
            if self.parameters["unicode_stdout"]:
                bad_characters = self.check_bad_characters(
                    self.stdout, expected=self.expected_stdout
                )
            if bad_characters:
                self.long_explanation += bad_characters
                self.parameters["show_diff"] = False
            # report output differences in a easily readable manner
            # if we don't have unicode input.
            # TODO: improve non-unicode handling
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
                self.long_explanation += self.report_bit_differences(expected_bits, actual_bits)


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
                self.long_explanation += self.report_bit_differences(expected_bits, actual_bits)

        std_input = self.stdin
        # we don't want to consider newlines when dealing with non-unicode output
        if self.parameters["unicode_stdin"]:
            n_input_lines = std_input.count("\n")
        else:
            # TODO: add *proper* else case for non-unicode input
            return self.long_explanation

        if self.parameters["show_stdin"]:
            if std_input and n_input_lines < 32:
                self.long_explanation += (
                    f"\nThe input for this test was:\n{colored(std_input, 'yellow')}\n"
                )
                if std_input[-1] != "\n" and "\n" in std_input[:-2]:
                    self.long_explanation += (
                        "Note: last character in above input is not '\\n'\n\n"
                    )

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
                echo_command = echo_command_for_string(input)
                if not self.stdin_file_name() or len(echo_command) < 128:
                    if "shell" in self.parameters and (
                        ";" in command or "&" in command or "|" in command
                    ):
                        command = "(" + command + ")"
                    command = f"{echo_command} | {command}"
                else:
                    command += " <" + self.stdin_file_name()
                command = indent + command
            else:
                if "shell" in self.parameters and not self.parameters.get(
                    "no_replace_semicolon_reproduce_command", ""
                ):
                    command = re.sub(r"\s*;\s*", "\n" + indent, command)
                command = indent + command

            self.long_explanation += colored(command + "\n", "blue")
        return self.long_explanation

    # TODO: make this work with non-unicode input
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

    # TODO: complete this
    def report_bit_differences(self, expected, actual):
        feedback = ""

        # compare bit length
        expected_len = len(expected)
        actual_len = len(actual)
        if expected_len != actual_len:
            feedback = f"Your output was {actual_len} bytes long. "
            feedback += f"It should have been {expected} bytes long."
            return feedback
            
        # TODO: fix this algorithm...
        # get number of bit differences 
        n_different = 0
        expected = int(expected.hex(), base=16)
        actual = int(actual.hex(), base=16)
        different_bytes = expected ^ actual
        for i in range(len(str(different_bytes))):
            if not different_bytes & (1 << i):
                n_different += 1
        
        feedback += f"There were {n_different} different bits between your output and the expected output\n"

        return feedback

    def check_bad_characters(self, str, expected=""):
        if re.search(r"[\x00-\x08\x14-\x1f\x7f-\xff]", expected):
            return None
        colored = (
            termcolor_colored
            if self.parameters["colorize_output"]
            else lambda x, *a, **kw: x
        )
        for (line_number, line) in enumerate(str.splitlines()):
            m = re.search(r"^(.*?)([\x00-\x08\x14-\x1f\x7f-\xff])", line)
            if not m:
                continue
            (prefix, offending_char) = m.groups()
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

    def is_true(self, parameter):
        if parameter not in self.parameters:
            return None
        value = self.parameters[parameter]
        return value and value[0] not in "0fF"

    def set_environ(self):
        test_environ = self.parameters["environment"]
        if os.environ != test_environ:
            os.environ.clear()
            os.environ.update(test_environ)


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
