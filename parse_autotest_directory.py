#!/usr/bin/python3

import codecs, collections, os, re, shlex, subprocess, sys
from run_test import Test


# test line format

# barewords assumed to be label or label, command or label, max_cpu, command or old format

# label - test label

# command - command to be run for test
#		   inferred from label if absent

# program - program being tested  (suffix removed for )
#			inferred from command if absent

# files - source files being tested - may include glob characters for language-agnostic tests
#		  files may be sames as program for scripting language
#		  inferred from command if absent

# compiler_args - compiler args
#		  inferred from files if absent

# shell - if true pass command to shell

# setup_command - command run before test, not shown to users as part of test

# setup_command_shell - if true pass setup_command to shell

# no_expected_output - don't print command expected output

# no_actual_output - don't print command actual output

# no_diff - don't print difference between actual and expected output

# no_reproduce_command - don't print commands to reproduce test

# no_reproduce_command - don't print commands to reproduce test

# description - string to be print to describe test when it is run

# stdin - stdin to be supplied

# stdin_file
#		 inferred from label if absent and stdin absent

# expected_stdout
# expected_stdout_file
#		 defaults to label.expected_stdout

# expected_stderr
# expected_stderr_file
#		 defaults to label.expected_stderr

# expected_file(\w*)_name - name of file that should be created by command

# expected_file(\w+)_file - file containing contents of file that should be created by command
#							 defaults to label.expected_file(\w+)
# ignore_case

# ignore_white_space

# ignore_trailing_white_space - ignore white space on ened of line - default is True

# ignore_blank_lines

# ignore_characters - ignore these characters when comparing output

# compare_only_characters - use only these characters when comparing output

# prediff_filter  - pass output through this command before comparison


def fetch_tests_from_autotest_directory(autotest_dir, tests_filename='tests.txt', debug=0):
	tests = collections.OrderedDict()
	global_variables = {'debug':debug}
	try:
		filename = os.path.join(autotest_dir, tests_filename)
		with open(filename, encoding='UTF-8',errors='replace') as f:
			lines = f.readlines()
		if lines and lines[0][0:2] == '#!':
			command = lines[0][2:].rstrip().split()
			with open(filename) as f:
			  input = f.read()
			p = subprocess.run(command, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
			if p.returncode or p.stderr:
				print('Error executing tests.txt', file=sys.stderr)
				print(p.stderr, file=sys.stderr)
				sys.exit(1)
			lines = p.stdout.splitlines()
		for (line_number, line) in enumerate(lines):
			if line.count('\t') > 1 and 'command=' not in line:
				t = parse_old_test_description(line, autotest_dir, debug=debug)
			else:
				t = parse_test_line(autotest_dir, filename, line_number, line, debug, global_variables)
			if t and t.label in tests:
				print("Duplicate test label: %s" % t.label, file=sys.stderr)
			elif t:
				tests[t.label] = t
	except IOError as e:
		if debug: print(e, file=sys.stderr)
		pass
	return tests

def parse_test_line(autotest_dir, filename, line_number, line, debug, global_variables):
	line_variables = {}
	line_words = []
	current_variable = None
	# crude hack to add back quotes
	m = re.match(r'^(\w+)=(`.*`)\s*$', line)
	if m:
		global_variables[m.group(1)] = m.group(2)
		return
	try:
		for token in shlex.split(line, posix=True, comments=True):
			# https://bugs.python.org/issue21331
			token = codecs.getdecoder("unicode_escape")(token.encode('latin1',errors='backslashreplace'))[0]
			m = re.match(r'^(\w+)=(.*)', token, flags=re.S)
			if m:
				line_variables[m.group(1)] = m.group(2)
				current_variable = m.group(1)
			elif current_variable:
				line_variables[current_variable] = make_list(line_variables[current_variable])
				line_variables[current_variable].append(token)
			else:
				line_words.append(token)
	except ValueError as e:
		print("{}:{} bad test description ({}): {}".format(filename, line_number, e, line))
		return
	if len(line_words) == 1:
		line_variables['label'] = line_words[0]
	elif len(line_words) == 2:
		(line_variables['label'], line_variables['command']) = line_words
	elif len(line_words) == 3:
		(line_variables['label'], line_variables['max_cpu'], line_variables['command']) = line_words
	elif len(line_words) > 3:
		print("%s:%d bad test description line: %s" % (filename, line_number, line), file=sys.stderr)
		return

	# line is just setting variables
	if 'label' not in line_variables:
		global_variables.update(line_variables)
		return

	test_variables = global_variables.copy()
	test_variables.update(line_variables)
	label = test_variables.pop('label')
	try:
		command = test_variables.pop('command')
	except KeyError:
		# if command not specified infer from command
		command = re.sub('[_0-9]*$', '', label)

	command = make_list(command)
	if 'program' in test_variables:
		program = test_variables.pop('program')
	else:
		# if program not specified infer from command
		m = re.search(r'(^|[\s;\&\|\.])\.\/([\w\-\.]+)', command[0])
		if m:
			program = m.group(2)
		else:
			program = re.sub(r'\s.*', '', command[0].strip())

	if 'files' in test_variables:
		test_variables['files'] = make_list(test_variables['files'])
	else:
		# if files not specified infer from program
		if '.' in program:
			test_variables['files'] = [program]
		elif program:
			test_variables['files'] = [program + '.c']
		else:
			test_variables['files'] = []

	if 'files' in test_variables:
		test_variables['files'] = make_list(test_variables['files'])
	if 'optional_files' in test_variables:
		test_variables['optional_files'] = make_list(test_variables['optional_files'])
	expected_files = []
	for parameter in test_variables:
		if not test_variables[parameter]:
			continue
		m = re.match(r'^expected_(file.*)_name', parameter)
		if m:
			expected_files.append(m.group(1))
	test_variables['expected_files'] = expected_files
	for stream in ['stdout', 'stderr'] + expected_files:
		if 'expected_' + stream in test_variables:
			continue
		if 'expected_' + stream + '_file' in test_variables:
			continue
		for potential_filename in [label + '.' + 'expected_' + stream, 'sample' + stream[3:6] + label]:
			if os.path.exists(os.path.join(autotest_dir, potential_filename)):
				if debug > 1:
					print('test_variables[%s]=%s' %('expected_' + stream + '_file',	 potential_filename), file=sys.stderr)
				test_variables['expected_' + stream + '_file'] =  potential_filename
				break
	for stdin_file in [label + '.stdin',  'test' + label]:
		if os.path.exists(os.path.join(autotest_dir, stdin_file)):
			test_variables['stdin_file'] = stdin_file
	if debug > 1:
		 print("%s:%d" % (filename, line_number), label, program, command, test_variables, file=sys.stderr)
	return Test(autotest_dir, label, program, command, **test_variables)

def make_list(x): return x if isinstance(x, list) else [x]

def parse_old_test_description(line, autotest_dir, debug=0):
	fields = re.split(r' *\t *', line.strip())
	assert len(fields) >= 3
	(label, time_limit, command) = fields[0:3]
	time_limit = int(time_limit)
	if len(fields) < 4:
		compare_command = 'diff'
	else:
		compare_command = fields[3]
	if len(fields) <= 5:
		prediff_filter = ''
		description = command
	elif len(fields) == 6:
		prediff_filter = ''
		description = fields[5]
	else:
		prediff_filter = fields[5]
		description = '\t'.join(fields[6:])
	if debug:
		print('from_test_txt_line: (label="%s", time_limit="%s", command="%s", compare_command="%s", prediff_filter="%s", description="%s")' % (label, time_limit, command, compare_command, prediff_filter, description))
	kwargs = {'max_cpu' : time_limit, 'debug' : debug}
	if description:
		kwargs['description'] = description
	expected_stdout_file = 'sampleout' + label
	if os.path.exists(os.path.join(autotest_dir, expected_stdout_file)):
		kwargs['expected_stdout_file'] = expected_stdout_file
	expected_stderr_file = 'sampleerr' + label
	if os.path.exists(os.path.join(autotest_dir, expected_stderr_file)):
		kwargs['expected_stderr_file'] = expected_stderr_file
	stdin_file = 'test' + label
	if os.path.exists(os.path.join(autotest_dir, stdin_file)):
		kwargs['stdin_file'] = stdin_file
	command = command.strip()
	m = re.search(r'(^|[\s;\&\|\.])\.\/([\w\-\.]+)', command)
	if m:
		program = m.group(2)
	else:
		program = re.sub(r'\s.*', '', command.strip())
	if debug:
		print('from_test_txt_line: program="%s"' % program)
	if re.search(r'diff.*-\w*i', compare_command):
		kwargs['ignore_case'] = True
	if re.search(r'diff.*-\w*w', compare_command):
		kwargs['ignore_white_space'] = True
	if re.search(r'diff.*-\w*B', compare_command):
		kwargs['ignore_blank_lines'] = True
	kwargs['prediff_filter'] = prediff_filter
	if debug:
		print('kwargs:', kwargs)
	command = make_list(command)
	return Test(autotest_dir, label, program, command, **kwargs)

def interpolate_backquotes(value):
	v = make_list(value)[0]
	if v.startswith("`"):
		command = v.strip("`")
		(stdout, stderr, returncode) = run(command, shell=True)
		#print(command, "->",  (stdout, stderr, returncode), file=sys.stderr)
		if stderr or returncode:
			print("command failed:",  command, file=sys.stderr)
			print(stderr, file=sys.stderr)
		return stdout.strip().split()
	else:
		return value


import asyncio, glob, shutil, signal
from subprocess_with_resource_limits import run

# generate expected output for tests
if __name__ == '__main__':
	signal.signal(signal.SIGINT, lambda signum, frame:os._exit(1))
	tests_filename='tests.txt'
	if sys.argv[1:]:
		tests_filename='automarking.txt'
	tests = fetch_tests_from_autotest_directory('.', tests_filename=tests_filename)
	for file in glob.glob('*.expected_stdout') + glob.glob('*.expected_stderr'):
		os.unlink(file)
	unlink_files = []
	last_compiler_args = None
	try:
		for test in tests.values():
			unlink_files.append('samp' + test.label)
			unlink_files.append('samperr' + test.label)
			if test.parameters.get('pre_compile_command', ''):
				print('pre_compile_command:', test.parameters['pre_compile_command'], file=sys.stderr)
				subprocess.run(test.parameters['pre_compile_command'], shell=test.parameters.get('pre_compile_command_shell', False))
			if test.parameters.get('setup_command', ''):
				print('setup_command:', test.parameters['setup_command'], file=sys.stderr)
				subprocess.run(test.parameters['setup_command'], shell=test.parameters.get('setup_command_shell', False))
			if not os.path.exists(test.program) or test.parameters.get('pre_compile_command', ''): # handle globs
				for file in test.files:
					if not glob.glob(file):
						alternate_name = os.path.basename(os.path.realpath('..'))
						(root, ext) = os.path.splitext(file)
						alternate_name += ext
						for name in [file, alternate_name]:
							command = ['find', '../solutions', '..', '-name', name]
							print(command)
							p = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
							if p.stdout:
								path = p.stdout.splitlines()[0].decode('ascii')
								b = os.path.basename(file)
								print('cp', path, b, file=sys.stderr)
								shutil.copyfile(path, b)
								shutil.copymode(path, b)
								unlink_files.append(b)
								break
					for test_file in glob.glob(file):
						basename, extension = os.path.splitext(test_file)
						if extension in ['.c', '.h']:
							compiler_args = interpolate_backquotes(test.parameters.get('compiler_args', test.files + ['-o', test.program]))

							if not os.path.exists(test.program) or last_compiler_args != compiler_args:
								last_compiler_args = compiler_args
								command = ['dcc'] + make_list(compiler_args)
								print(" ".join(command))
								subprocess.check_call(command)
								unlink_files.append(test.program)
						elif extension in ['.pl', '.py', '.sh']:
							if not os.path.exists(basename):
								os.link(test_file, basename)
								unlink_files.append(basename)
						elif extension == '.java':
							if not os.path.exists(basename):
								open(basename,"w").write("#!/bin/bash\njava %s $@" % basename)
								os.chmod(basename, 0o700)
								unlink_files.append(basename)
						elif extension == '.js':
							if not os.path.exists(basename):
								open(basename,"w").write("#!/bin/bash\nnode %s $@" % test_file)
								os.chmod(basename, 0o700)
								unlink_files.append(basename)

			# fake expected results
			test.parameters.setdefault('max_stdout_bytes', 100000000)
			test.expected_stdout = ''
			test.expected_stderr = ''
			for expected_file in test.parameters.get('expected_files', []):
				test.parameters['expected_%s_file' % expected_file] = '/dev/null'
			test.run_test()
			expected_stdout_file = test.label + '.expected_stdout'
			if test.stdout:
				with open(expected_stdout_file, 'w')	as f:
					f.write(test.stdout)
			else:
				print('Warning no stdout for test', test, file=sys.stderr)
				unlink_files.append(expected_stdout_file)
			expected_stderr_file = test.label + '.expected_stderr'
			if test.stderr:
				with open(expected_stderr_file, 'w')	as f:
					f.write(test.stderr)
					print('Warning creating', expected_stderr_file, 'with contents:', "'"+test.stderr.rstrip()+"'", file=sys.stderr)
			else:
				unlink_files.append(expected_stderr_file)
			for expected_file in test.parameters.get('expected_files', []):
				filename = test.parameters['expected_%s_name' % expected_file]
				expected_contents_file =  test.label + '.expected_' + expected_file
				print('Creating', expected_contents_file)
				shutil.copyfile(filename, expected_contents_file)
				shutil.copymode(filename, expected_contents_file)
				unlink_files.append(filename)
	except subprocess.CalledProcessError:
		pass
	for pattern in unlink_files:
		for file in glob.glob(pattern):
			if os.path.exists(file):
				print('rm', file, file=sys.stderr)
				os.unlink(file)
	try:
		asyncio.get_event_loop().close()
	except Exception:
		pass
