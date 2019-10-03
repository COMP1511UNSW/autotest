#!/usr/bin/python3

import codecs, os, re, shlex, subprocess, time
from subprocess_with_resource_limits import run
from explain_output_differences import explain_output_differences, sanitize_string
from termcolor import colored as termcolor_colored

class InternalError(Exception):
	pass

class Test():
	def __init__(self, autotest_dir, label, program, command, files=None, description=None, stdin=None, stdin_file=None, expected_stdout=None, expected_stdout_file=None, expected_stderr=None, expected_stderr_file=None, ignore_case=False, ignore_white_space=False, ignore_trailing_white_space=True, ignore_blank_lines=False, ignore_characters='', compare_only_characters='', prediff_filter=None, debug=0, **parameters):
		self.autotest_dir = autotest_dir
		self.label = label
		self.program = program
		self.command = command
		if files is None:
			if '.' in program:
				self.files = [program]
			else:
				self.files = [program + '.c']

		else:
			self.files = files
		if description is None:
			self.description = " ".join(command) if isinstance(command, list) else command
		else:
			self.description = description
		self.ignore_case = ignore_case
		self.ignore_blank_lines = ignore_blank_lines
		self.ignore_characters = ignore_characters
		self.ignore_trailing_white_space = ignore_trailing_white_space
		self.compare_only_characters = compare_only_characters
		self.ignore_white_space = ignore_white_space

		# ignore all characters but those specified
		if ignore_white_space:
			self.ignore_characters += " \t"
		if compare_only_characters:
			mapping = dict.fromkeys([chr(v) for v in range(0,256)], None)
			if debug: print('compare_only_characters', compare_only_characters)
			for c in compare_only_characters + "\n":
				mapping.pop(c, None)
		else:
			mapping = dict.fromkeys(self.ignore_characters, None)
		mapping['\r'] = '\n'
		self.canonical_translator = "".maketrans(mapping)
		self.stdin = stdin
		self.stdin_file = stdin_file
		self.expected_stdout = expected_stdout
		self.expected_stdout_file = expected_stdout_file
		self.expected_stderr = expected_stderr
		self.expected_stderr_file = expected_stderr_file
		self.explanation = None
		self.prediff_filter = prediff_filter
		self.parameters = parameters.copy()
		self.debug = debug
		self.test_passed = None
		self.using_dcc_output_checking = False

	def __str__(self):
		return "Test(%s, %s, %s)" % (self.label, self.program, self.command)

	def run_test(self, compiler='', fail_tests_for_errors=True):
		if self.debug > 1:
			print('run_test(compiler=%s)\n' % compiler)
		# load input expected_stdout & expected expected_stderr from files
		# if needed
		if self.expected_stdout is None:
			if self.expected_stdout_file:
				with open(self.expected_stdout_file) as f:
					self.expected_stdout = f.read()
			else:
				self.expected_stdout = ''
			if self.debug: print("expected_stdout_file='%s' expected_stdout='%s'" % (self.expected_stdout_file, self.expected_stdout))
		if self.expected_stderr is None:
			if self.expected_stderr_file:
				with open(self.expected_stderr_file) as f:
					self.expected_stderr = f.read()
			else:
				self.expected_stderr = ''

		max_stdout_bytes = min(10000000, 10 * len(self.expected_stdout))
		max_stdout_bytes = max(10000, max_stdout_bytes)
		max_stdout_bytes = max(2 * len(self.expected_stdout), max_stdout_bytes)
		self.parameters.setdefault('max_stdout_bytes', max_stdout_bytes)
		if (
			self.is_true('use_dcc_output_checking') or
			(
				'use_dcc_output_checking' not in self.parameters and
				os.environ.get('AUTOTEST_USE_DCC_OUTPUT_CHECKING', '') and
				not self.expected_stderr and
				not self.prediff_filter and
				'compiler_args' not in self.parameters
			)):
			os.environ['DCC_EXPECTED_STDOUT'] = self.expected_stdout
			os.environ['DCC_IGNORE_CASE'] = str(self.ignore_case)
			os.environ['DCC_COMPARE_ONLY_CHARACTERS'] = str(self.compare_only_characters)
			os.environ['DCC_IGNORE_CHARACTERS'] = str(self.ignore_characters)
			os.environ['DCC_IGNORE_TRAILING_WHITE_SPACE'] = str(self.ignore_trailing_white_space)
			os.environ['DCC_IGNORE_WHITE_SPACE'] = str(self.ignore_white_space)
			os.environ['DCC_IGNORE_EMPTY_LINES'] = str(self.ignore_blank_lines)
			os.environ['DCC_MAX_STDOUT_BYTES'] = str(max_stdout_bytes)
			self.using_dcc_output_checking = True
			
		for attempt in range(3):
			input = None
			stdin = None
			if self.stdin:
				input = self.stdin
			elif self.stdin_file:
				stdin = open(self.stdin_file)
			if self.debug > 1:
				print('run_test attempt', attempt)
			(stdout, stderr, self.returncode) = run(self.command, input=input, stdin=stdin, debug=self.debug, **self.parameters)
			if stdout or stderr or self.returncode == 0 or not	self.expected_stdout:
				break
			if self.debug > 1:
				print('run_test retry', (stdout, stderr, self.returncode))
			# ugly work-around for
			# weird termination with non-zero exit status seen on some CSE servers
			# ignore this execution and try again
			time.sleep(1)
		if stdin:
			stdin.close()
		self.stdout = codecs.decode(stdout, 'UTF-8', errors='replace')
		self.stderr = codecs.decode(stderr, 'UTF-8', errors='replace')
		self.short_explanation = None
		self.long_explanation = None

		stdout_short_explanation = self.check_stream(self.stdout, self.expected_stdout, "output")
		if fail_tests_for_errors or stdout_short_explanation:
			if self.using_dcc_output_checking and 'Execution stopped because' in self.stderr:
				self.short_explanation = "incorrect output"
			else:
				self.short_explanation = self.check_stream(self.stderr, self.expected_stderr, "stderr")

		self.stderr_ok = not self.short_explanation

		self.stdout_ok = not stdout_short_explanation

		if not self.short_explanation:
			self.short_explanation = stdout_short_explanation

		if not self.short_explanation:
			self.short_explanation = self.check_files()

		self.test_passed = not self.short_explanation
		if not self.test_passed:
			self.failed_compiler = " ".join(compiler)
		return self.test_passed

	def check_files(self):
		for expected_file in self.parameters.get('expected_files', []):
			filename = self.parameters['expected_%s_name' % expected_file]
			expected_contents_file = self.parameters['expected_%s_file' % expected_file]
			try:
				with open(filename) as f:
					actual_contents = f.read()
			except IOError:
				self.long_explanation = "Your program was expected to create a file named %s and did not\n" % filename
				return filename + " not created"
			with open(expected_contents_file) as f:
				expected_contents = f.read()
			short_explanation = self.check_stream(actual_contents, expected_contents, "file: "+filename)
			if short_explanation:
				self.file_not_ok = filename
				self.file_expected = expected_contents
				self.file_actual = actual_contents
				return short_explanation

	def check_stream(self, actual, expected, name):
		if self.debug:
			print('name:', name)
			print('actual:', actual[0:256] if actual else '')
			print('expected:', expected[0:256] if expected else '')
		if actual:
			if expected:
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
					return "File %s is empty" % name
				else:
					return "No %s produced" % name
			else:
				return None

	def make_string_canonical(self, raw_str, keep_all_lines=False):
		str = re.sub('\r\n?', '\n', raw_str)
		if self.prediff_filter:
			if self.debug: print("prediff_filter=%s str='%s'" % (self.prediff_filter, str))
			p = subprocess.Popen(self.prediff_filter, stdout=subprocess.PIPE, stdin=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, universal_newlines=True)
			(str, stderr) = p.communicate(input=str)
			if stderr:
				raise InternalError('error from pre-diff filter: ' + stderr)
			if self.debug: print("after filter str='%s'"%str)
		if self.ignore_case:
			str = str.lower()
		str = str.translate(self.canonical_translator)
		if self.ignore_blank_lines and not keep_all_lines:
			str = re.sub(r'\n\s*\n', '\n', str)
			str = re.sub(r'^\n+', '', str)
		if self.ignore_trailing_white_space:
			str = re.sub(r'[ \t]+\n', '\n', str)
		if self.debug > 1:
			print("self.ignore_characters='%s'" % (self.ignore_characters))
			print("make_string_canonical('%s') -> '%s'" % (raw_str, str))
		return str

	def compare_strings(self, actual, expected):
		return self.make_string_canonical(actual) == self.make_string_canonical(expected)

	def stdin_file_name(self):
		if not self.stdin_file:
			return self.stdin_file
		if self.stdin_file[0] == '/':
			return self.stdin_file
		path = os.path.realpath(self.autotest_dir + "/" + self.stdin_file)
		path = re.sub('/tmp_amd/\w+/export/\w+/\d/(\w+)', r'/home/\1', path)
		return path

	def get_long_explanation(self,	show_input=True, show_reproduce_command=True, show_expected=True, show_actual=True, show_diff=True, show_stdout_if_errors=False, colorize=False):
		if self.debug: print('get_long_explanation(', show_input, show_reproduce_command, show_expected, show_actual, show_diff, ') short_explanation=', self.short_explanation, 'long_explanation=', self.long_explanation, 'stderr_ok=', self.stderr_ok, 'expected_stderr=', self.expected_stderr)
		if self.long_explanation:
			return self.long_explanation
		colored = termcolor_colored if colorize else lambda x,*a,**kw: x
#		colored = lambda x,*a,**kw: x # disable use of blue below - hard to read, replace with more readable color
		self.long_explanation = ""
		if not self.stderr_ok:
			if self.expected_stderr:
				self.long_explanation += self.report_difference("stderr", self.expected_stderr, self.stderr, show_expected=show_expected, show_actual=show_actual, show_diff=show_diff)
			elif self.using_dcc_output_checking and 'Execution stopped because' in self.stderr:
				self.long_explanation += "Your program produced these %d lines of output before it was terminated:\n" % (len(self.stdout.splitlines()))
				self.long_explanation += colored(sanitize_string(self.stdout, max_lines_shown=16, max_line_length_shown=256, colorize=colorize), 'cyan')
				self.long_explanation += self.stderr + "\n"
			else:
				errors = sanitize_string(self.stderr, leave_tabs=True, leave_colorization=True, **self.parameters)
				if '\x1b' not in self.long_explanation:
					 errors = colored(errors, 'red')
				if 'Error too much output' in self.stderr:
					errors += "Your program produced these %d bytes of output before it was terminated:\n" % (len(self.stdout))
					errors += colored(sanitize_string(self.stdout, max_lines_shown=16, max_line_length_shown=256, colorize=colorize), 'yellow')
				if self.stdout_ok and self.expected_stdout:
					self.long_explanation = "Your program's output was correct but errors occurred:\n"
					self.long_explanation += errors
					self.long_explanation += "Apart from the above errors, your program's output was correct.\n"
				else:
					self.long_explanation = "Your program produced these errors:\n"
					self.long_explanation += errors
		if not self.stdout_ok and (show_stdout_if_errors or self.stderr_ok):
			bad_characters = self.check_bad_characters(self.stdout, colorize=colorize, expected=self.expected_stdout)
			if bad_characters:
				self.long_explanation += bad_characters
				show_diff = False
			self.long_explanation += self.report_difference("output", self.expected_stdout, self.stdout, show_expected=show_expected, show_actual=show_actual, show_diff=show_diff, colorize=colorize)
		if self.stdout_ok and self.stderr_ok and self.file_not_ok:
			self.long_explanation = self.report_difference(self.file_not_ok, self.file_expected,self.file_actual, show_expected=show_expected, show_actual=show_actual, show_diff=show_diff, colorize=colorize)
		input = self.get_stdin()
		n_input_lines = input.count('\n')
		if show_input:
			if input and n_input_lines < 16:
				self.long_explanation += "\nThe input for this test was:\n%s\n" % colored(input, 'yellow')
				if input[-1] != '\n' and '\n' in input[0:-2]:
					self.long_explanation += "Note: last character in above input is not '\\n'\n\n"
					
		if show_reproduce_command and not self.parameters.get('no_reproduce_command', ''):
			self.long_explanation += "You can reproduce this test by executing these commands:\n"
			if self.failed_compiler:
				self.long_explanation += colored(self.failed_compiler + "\n", 'yellow')
			command = " ".join(self.command)
			if input:
				echo_command = echo_command_for_string(input)
				if not self.stdin_file_name() or len(echo_command) < 128:
					command = '%s | %s' % (echo_command, command)
				else:
					command += ' <' + self.stdin_file_name()
			self.long_explanation += colored(command + '\n', 'yellow')
		return self.long_explanation

	def get_stdin(self):
		if self.stdin is not None:
			return self.stdin
		elif self.stdin_file:
			with open(self.stdin_file) as f:
				return f.read()
		else:
			return ''

	def report_difference(self, name, expected, actual, show_expected=True, show_actual=True, show_diff=True, colorize=False):
		if self.debug: print("report_difference(%s, '%s', '%s')" % (name, expected, actual))
		canonical_expected = self.make_string_canonical(expected)
		canonical_actual = self.make_string_canonical(actual)
		canonical_actual_plus_newlines = self.make_string_canonical(actual,keep_all_lines=True)
		canonical_expected_plus_newlines = self.make_string_canonical(expected,keep_all_lines=True)
		return explain_output_differences(name, expected, canonical_expected, canonical_expected_plus_newlines, actual, canonical_actual, canonical_actual_plus_newlines, show_expected=show_expected, show_actual=show_actual, show_diff=show_diff, colorize=colorize, debug=self.debug, **self.parameters)

	def check_bad_characters(self, str, colorize=False, expected=''):
		if re.search(r'[\x00-\x08\x14-\x1f\x7f-\xff]', expected):
			return None
		colored = termcolor_colored if colorize else lambda x,*a,**kw: x
		for (line_number, line) in enumerate(str.splitlines()):
			m = re.search(r'^(.*?)([\x00-\x08\x14-\x1f\x7f-\xff])', line)
			if not m:
				continue
			(prefix, offending_char) = m.groups()
			offending_value = ord(offending_char)
			if offending_value == 0:
				description = "zero byte ('" + colored('\\0', 'red') + "')"
			elif offending_value > 127:
				description = "non-ascii byte " + colored("\\x%02x" % (offending_value), 'red')
			else:
				description = "non-printable character " + colored("\\x%02x" % (offending_value), 'red')
			column = len(prefix)
			explanation =  "Byte %d of line %d of your program's output is a %s\n" % (column + 1, line_number + 1, description)
			explanation += "Here is line %d with non-printable characters replaced with backslash-escaped equivalents:\n\n" % (line_number + 1)
			line = line.encode('unicode_escape').decode('ascii') + '\n\n'
			line = re.sub(r'(\\x[0-9a-f][0-9a-f])', colored(r'\1', 'red'), line)
			explanation += line
			return explanation
		return None

	def is_true(self, parameter):
		if parameter not in self.parameters:
			return None
		value = self.parameters[parameter]
		return value and value[0] not in "0fF"
		
def echo_command_for_string(input):
	options = []
	if input and input[-1] == '\n':
		input = input[0:-1]
	else:
		options += ['-n']
	echo_string = shlex.quote(input)
	if '\n' in input[0:-1]:
		echo_string = echo_string.replace('\\', '\\\\')
		options += ['-e']
	echo_string = echo_string.replace('\n','\\n')
	command = 'echo '
	if options:
		command += ' '.join(options) + ' '
	return command + echo_string
