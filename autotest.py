#!/usr/bin/python3 -I

# try to catch keyboard interrupt in imports 
import fnmatch, os, signal
if __name__ == '__main__':
	signal.signal(signal.SIGINT, lambda signum, frame:os._exit(2))

import  argparse, atexit, copy, glob, io, json, re, shutil, subprocess, sys, tempfile, traceback, zipfile
from collections import OrderedDict
from shutil import copy2, copystat

# add autotest directory to module path
if __name__ == '__main__':
	sys.path.append(os.path.dirname(os.path.realpath(__file__)))

from course_configuration import course_configuration
from parse_autotest_directory import fetch_tests_from_autotest_directory,interpolate_backquotes,make_list
from termcolor import colored as termcolor_colored
from subprocess_with_resource_limits import run

class InternalError(Exception):
	pass

default_c_compilers = 'dcc:dcc --valgrind'
#default_c_compilers = 'gcc -std=c99 -Wall -Wno-unused -O -lm'
suffixes = "pl py sh java cgi rb js".split();
debug = 0
my_name = 'autotest'

extra_help = """
Examples:

autotest lab06                                 # all tests for lab06
autotest lab07 -p count_word.pl total_words.pl # test specified programs
autotest lab08 -l lectures_3 lectures_4        # run specified tests
autotest ass1 --marking                       # run the auto-marking tests after they are released
autotest lab08 -G                              # test files in your gitlab.cse.unsw.edu.au repo
autotest lab08 -c 8f1e69bd                     # use this commit instead of latest commit
autotest lab08 -s z5555555                     # test files in student z5555555 gitlab.cse.unsw.edu.au's repo
"""

def process_arguments():
	parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,epilog=extra_help)
	parser.add_argument("-a", "--autotest_directory", help="directory containing test specifications")
	parser.add_argument("-c", "--commit", help="test files from COMMIT instead of latest commit")
	parser.add_argument("-C", "--c_compilers", default=os.environ.get('AUTOTEST_C_COMPILERS', os.environ.get('DRYRUN_COMPILERS', default_c_compilers)), help="test C programs using these compilers")
	parser.add_argument("--c_checkers", default=os.environ.get('AUTOTEST_C_CHECKERS', ''), help="check C programs using these compilers")
	parser.add_argument("-d", "--debug", action='count', help="print debug information")
	parser.add_argument("-e", "--exercise",  help="run tests for EXERCISE")
	parser.add_argument("-f", "--file", nargs='+', default=[], help="add a copy of this file to the test directory ")
	parser.add_argument("-j", "--json", action="store_true", help="output JSON")
	parser.add_argument("-l", "--labels", nargs='+', default=[], help="execute tests with these LABELS")
	parser.add_argument("-m", "--marking", action="store_true", help="run automarking tests")
	parser.add_argument("--colorize", action="store_true", dest="colorize", default=sys.stdout.isatty(), help="show test input")
	parser.add_argument("--no_colorize", action="store_false", dest="colorize",  help="show test input")
	parser.add_argument("--no_show_input", action="store_false", dest="show_input", default=True, help="show test input")
	parser.add_argument("--no_show_expected", action="store_false", dest="show_expected", default=True, help="don't show expected output")
	parser.add_argument("--no_show_actual", action="store_false", dest="show_actual", default=True, help="don't show actual output")
	parser.add_argument("--no_show_diff", action="store_false", dest="show_diff", default=True, help="don't show diff between actual and expected output")
	parser.add_argument("--no_show_reproduce_command", action="store_false", dest="show_reproduce_command", default=True, help="don't show command to reproduce test")
	parser.add_argument("--no_check_hash_bang_line", action="store_false", dest="check_hash_bang_line", default=True, help="don't check #! line")
	parser.add_argument("--no_fail_tests_for_errors", action="store_false", dest="fail_tests_for_errors", default=True, help="don't fail tests with correct stdout and errors on stderr")
	parser.add_argument("--print_test_names", action="store_true", help="print names of tests and files")
	parser.add_argument("--show_stdout_if_errors", action="store_true", dest="show_stdout_if_errors", default=False, help="shor command stdout when there are errors")
	parser.add_argument("--ssh_upload_url", help="URL for upload of results")
	parser.add_argument("--ssh_upload_host", help="HOST for ssh upload of results")
	parser.add_argument("--ssh_upload_username", help="USERNAME for ssh upload of results")
	parser.add_argument("--ssh_upload_keyfile", help="KEYFILE for ssh upload of results")
	parser.add_argument("--ssh_upload_key", help="KEYFILE for ssh upload of results")
	parser.add_argument("--ssh_upload_max_bytes", default=2048000, type=int, help="MAX bytes for ssh upload of results")
	parser.add_argument("-p", "--programs", nargs='+', default=[], help="execute tests for PROGRAMS")
	parser.add_argument("extra_arguments",  nargs='*', default=[], help="")

	source_args = parser.add_mutually_exclusive_group()
	source_args.add_argument("-G", "--gitlab_cse", action="store_true", help="test files from gitlab.cse.unsw.edu.au")
	source_args.add_argument("-g", "--git", help="test files from this git repository")
	source_args.add_argument("-S", "--stdin", action="store_true", help="test file supplied on standard input")
	source_args.add_argument("-t", "--tarfile", help="test files from this tarfile, can be http URL")
	source_args.add_argument("-s", "--student", help="test files from STUDENT's repository on gitlab.cse.unsw.edu.au")
	source_args.add_argument("-D", "--directory", help="test files from this directory")

	args = parser.parse_args()
	global debug
	debug = int(os.environ.get('DEBUG', 0) or args.debug or 0)
	if args.colorize:
		# is there a better way to do this
		os.environ['DCC_COLORIZE_OUTPUT'] = 'true'
		os.environ['C_CHECK_COLORIZE_OUTPUT'] = 'true'
	if debug: print('raw args:', args, file=sys.stderr)
	if len(args.extra_arguments) == 2 and re.search(r'\.tar$', args.extra_arguments[0]):
		# give calls dryrun this way
		args.tarfile = args.extra_arguments[0]
		args.exercise = args.extra_arguments[1]
		args.extra_arguments = []
	if not args.exercise and not args.autotest_directory:
		if args.extra_arguments:
			args.exercise = args.extra_arguments.pop(0)
		else:
			print("%s: no exercise specified" % (my_name), file=sys.stderr)
			sys.exit(1)
	if not args.exercise and args.autotest_directory:
		args.exercise = os.path.basename(args.autotest_directory)
	return (parser, args)

def normalize_arguments(parser, args, tests):
	test_labels = set(list(tests.keys()))
	programs = list(set(tests[t].program for t in tests))
	files = list(set(f for t in tests for f in tests[t].files))
	unknown_labels = set(args.labels) - test_labels
	if unknown_labels:
		print("%s: unknown labels:" % (my_name), " ".join(unknown_labels), file=sys.stderr)
		sys.exit(1)

	args.optional_files = []
	for arg in args.extra_arguments:
		p = re.sub(r'^\./', '', arg)
		basename_p = re.sub(r'\.[a-z]{1,4}$', '', p)
		if any(fnmatch.fnmatch(arg, f) for f in files):
			args.file += [arg]
		elif p in programs:
			args.programs += [p]
		elif basename_p in programs:
			args.programs += [basename_p]
			args.file += [p]
		elif arg in test_labels:
			args.labels += [arg]
		elif re.search(r'.*\.tar(.[a-z]+)?$', arg) and not args.tarfile:
			args.tarfile = arg
		elif re.search(r'^git\w+@', arg) and not args.git:
			args.git = arg
		elif os.path.isfile(arg):
			args.optional_files += [arg]
		else:
			matching_labels = [t for t in tests if (arg in t) or (arg in tests[t].program) or (tests[t].program in arg)]
			if not matching_labels:
				print("%s: unexpected argument '%s'" % (my_name, arg), file=sys.stderr)
				print("Specify 1+ of these filenames:", " ".join(files), file=sys.stderr)
				print("Or 1+ of these individual tests:", " ".join(test_labels), file=sys.stderr)
				sys.exit(1)
			args.labels += matching_labels
	# if programs are specified run all the tests for them
	if debug: print('programs:', args.programs, file=sys.stderr)
	if args.programs:
		args.labels += [label for label in tests if tests[label].program in args.programs]
	if args.file:
		args.file = set(args.file)
		extra_labels = [label for label in tests if set(tests[label].files) == args.file]
		if not extra_labels:
			extra_labels = [label for label in tests if set(tests[label].files).intersection(args.file)]
		args.labels += extra_labels
		args.file = set(args.file)
	if debug: print('labels:', args.labels, file=sys.stderr)
	# if no labels or programs, run all the tests for the exercise
	if not args.labels:
		args.labels = list(tests.keys())
	args.programs = set(tests[label].program for label in args.labels)
	if not args.file:
		args.file = set(f for label in args.labels for f in tests[label].files)
	args.optional_files += [f for label in args.labels for f in tests[label].parameters.get('optional_files', [])]
	args.optional_files = set(args.optional_files)
	if (args.gitlab_cse or args.commit or args.student) and not args.git:
		args.git = repository_name(args.exercise, account=args.student)
	if debug: print('normalized args:', args, file=sys.stderr)

def main():
	# strip most environment variables to avoid problems with complex tests
	keep_variables = "PATH DEBUG DRYRUN_DIR DCC_COLORIZE_OUTPUT C_CHECK_COLORIZE_OUTPUT DRYRUN_COMPILERS LANG LANGUAGE LC_COLLATE LC_NUMERIC LC_ALL".split()
	for variable in list(os.environ.keys()):
		if variable not in keep_variables and not variable.startswith("AUTOTEST_"):
			os.environ.pop(variable, None)
#	os.environ['LANG'] = 'en_AU.utf8'
#	os.environ['LANGUAGE'] = 'en_AU:en'
	os.environ['LC_COLLATE'] = 'POSIX'
	os.environ['LC_NUMERIC'] = 'POSIX'
	os.environ['PERL5LIB'] = '.'
	os.environ['PATH'] = course_configuration['PATH'] + ':' + os.environ.get('PATH', '.')
	
	(parser, args) = process_arguments()
	if not args.autotest_directory and 'DRYRUN_DIR' in os.environ:
		args.autotest_directory = os.environ['DRYRUN_DIR'] + '/'
	elif not args.autotest_directory:
		if args.marking:
			 args.autotest_directory = find_autotest_dir(args.exercise, autotest_subdir=['automarking'], tests_filename='automarking.txt')
		else:
			 args.autotest_directory = find_autotest_dir(args.exercise)
	if args.autotest_directory[-1] != '/':
		args.autotest_directory += '/'
	if debug:
		print('autotest_dir:',  args.autotest_directory, file=sys.stderr)
	# These historically were used by compile or run scripts
	os.environ['TESTFILE_DIR'] = args.autotest_directory
	os.environ['TEST_COMPILERS'] = args.c_compilers
	# FIXME
	tests_filename = 'tests.txt'
	if args.marking and os.path.exists(os.path.join(args.autotest_directory, 'automarking.txt')):
		tests_filename = 'automarking.txt'
	tests = fetch_tests_from_autotest_directory(args.autotest_directory, tests_filename=tests_filename, debug=debug)
	if not tests:
		die("autotest not available for %s" % args.exercise)
	if args.print_test_names:
		test_groups = OrderedDict()
		for test in tests.values():
			files = tuple(sorted(test.files))
			test_groups.setdefault(files, []).append(test.label)
		print(json.dumps([{'files':files,'labels':labels} for (files,labels) in test_groups.items()]))
		sys.exit(0)
	normalize_arguments(parser, args, tests)
	temp_dir = tempfile.mkdtemp()
	atexit.register(cleanup, temp_dir=temp_dir)
	copy_directory(args.autotest_directory, temp_dir)
	fetch_submission(temp_dir, args)
#    subprocess.check_call(['rsync', '-rptgoDL', '--copy-unsafe-links',  args.autotest_directory, './', '--exclude', '*.sh'])
	os.chdir(temp_dir)
	for expected_file in glob.glob('*.expected_*'):
		os.chmod(expected_file, 0o400)
	if debug > 2:
		subprocess.call("ls -l;pwd", shell=True)
#    if args.json:
#        return_values = {'debug': ''}
#        run_tests_json(tests, args, return_values)
#        print(json.dumps(return_values))
#    else:
#   if args.ssh_upload_url or (args.ssh_upload_host and args.ssh_upload_username and (args.ssh_upload_keyfile or args.ssh_upload_key)):
	if args.ssh_upload_url:
		zid = re.sub(r'^z', '', get_zid())
		if zid and args.exercise:
			sys.exit(run_tests_and_upload_results(tests, args, zid))

	sys.exit(run_tests(tests, args))

def get_zid(account = ''):
	if not account:
		try:
			account = os.getlogin()
		except OSError:
			pass
	if account == 'andrewt':
		return 'z9300162'
	if account:
		m = re.search(r'\bz?(\d{7})$', account)
		if m:
			return 'z' + m.group(1)
	command = ['acc']
	if account:
		command += [account]
	try:
		output = subprocess.check_output(command, stderr=subprocess.STDOUT, universal_newlines=True)
		m = re.search(r'\bz?(\d{7})\b', output)
		if m:
			return 'z' + m.group(1)
	except (subprocess.CalledProcessError,OSError):
		pass
	return ''

def run_tests_and_upload_results(tests, args, zid):
	class Tee(object):
		def __init__(self, stream):
			self.stream = stream
		def flush(self):
			sys.stdout.flush()
			self.stream.flush()
		def write(self, message):
			sys.stdout.write(message)
			self.stream.write(message)
	with open("autotest.log", "w") as f:
		exit_status = run_tests(tests, args, file=Tee(f))
#   if args.ssh_upload_url:
	buffer = io.BytesIO()
	zip_files_for_upload(buffer, tests, args)
	buffer.seek(0)
	if debug:
		print(args.ssh_upload_url, {"zid":zid, "exercise":args.exercise})
	try:
		# requests may not be installed 
		import requests
		r = requests.post(args.ssh_upload_url, data={"zid":zid, "exercise":args.exercise}, files={"zip": ("zip", buffer)})
	except Exception as e:
		if debug:
			print(e, file=sys.stderr)
		return exit_status
	if debug:
		print(r.text, file=sys.stderr)
	return exit_status
#   else:
#       client = paramiko.SSHClient()
#       client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
#       if args.ssh_upload_key:
#           f = io.BytesIO()
#           f.write(args.ssh_upload_key)
#           f.seek(0)
#           key = paramiko.RSAKey.from_private_key_file(f)
#       else:
#           key = paramiko.RSAKey.from_private_key_file(args.ssh_upload_keyfile)
#       client.connect(args.ssh_upload_host, username=args.ssh_upload_username, allow_agent=False, look_for_keys=False, pkey=key)
#       stdin,stdout,stderr = client.exec_command(' '.join([args.exercise, zid]))
#       zip_files_for_upload(stdin, tests, args)

def zip_files_for_upload(stream, tests, args):
	zf = zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_LZMA)
	bytes_uploaded = 0
	for test in tests.values():
		try:
			zf.writestr(test.label + '.passed', '1' if test.passed else '')
		except AttributeError:
			pass
	for filename in ["autotest.log"] + list(set(args.file | args.optional_files)):
		try:
			bytes_uploaded += os.path.getsize(filename)
			if bytes_uploaded >args.ssh_upload_max_bytes:
				break
			zf.write(filename)
		except OSError:
			pass
	zf.close()

def run_tests(tests, args, file=sys.stdout):
	colored = termcolor_colored if args.colorize else lambda x,*a,**kw: x
	compile_commands = {}
	pre_execute_commands = {}
	if os.path.exists('./compile.sh'):
		if subprocess.call(['./compile.sh'] + args.programs) != 0:
			die("compilation failed")
		for program in args.programs:
			compile_commands[program] = ['']
			pre_execute_commands[program] = ['']
	if os.path.exists('./runtests.pl'):
		sys.exit(subprocess.call(['./runtests.pl']))
	if not tests:
		die("autotest not available for %s" % args.exercise)
	if not args.labels:
		die("nothing to test")
	n_tests_failed = 0
	n_tests_not_run = 0
	n_tests_passed = 0
	compiler_parameters = {}
	previous_errors = {}
	for test_label in tests:
		if test_label in args.labels:
			test = tests[test_label]
			test_compiler_parameters = (test.parameters.get('compilers', None), test.parameters.get('compiler_args', None))
			if 'pre_compile_command' in test.parameters:
				if debug:
					print('pre_compile_command:', test.parameters['pre_compile_command'], file=sys.stderr)
				subprocess.run(test.parameters['pre_compile_command'], shell=test.parameters.get('pre_compile_command_shell'))
			if compiler_parameters.get(test.program, None) != test_compiler_parameters:
				compiler_parameters[test.program] = test_compiler_parameters
				(output, compile_commands[test.program], pre_execute_commands[test.program]) = compile_program(test, args)
#               if not compile_commands[test.program]:
				print(output, file=file)
			missing_files = [f for f in test.files if not glob.glob(f)]
			if missing_files:
				print("Test %s (%s) - "% (test.label, test.description), colored("could not be run", 'red'),  "because these files are missing:" , colored(" ".join(missing_files), 'red'), flush=True, file=file)
				n_tests_not_run += 1
				continue
			if compile_commands[test.program]:
				print("Test %s (%s) -" % (test.label, test.description), end=' ', file=file)
				# run individual test for each compiler
				individual_tests = []
				for (i, pre_execute_command) in enumerate(pre_execute_commands[test.program]):
					individual_test = copy.copy(test)
					if 'setup_command' in test.parameters:
						if debug:
							print('setup_command:', test.parameters['setup_command'], file=sys.stderr)
						run(test.parameters['setup_command'], shell=test.parameters.get('setup_command_shell', False))
					pre_execute_command()
					individual_test.run_test(compile_commands[test.program][i], fail_tests_for_errors=args.fail_tests_for_errors)
					individual_tests.append(individual_test)
					if not individual_test.stderr_ok and args.fail_tests_for_errors:
						break

				failed_individual_tests = [it for it in individual_tests if not it.test_passed]
				test.passed = not failed_individual_tests
				if test.passed:
					print(colored("passed", 'green'), flush=True, file=file)
					n_tests_passed += 1
					continue
				# pick the best failed test to report
				# if we have errors then should be more informative than incorrect output except memory leaks
				if not failed_individual_tests[-1].stderr_ok and 'free not called' not in failed_individual_tests[-1].stderr:
					individual_test = failed_individual_tests[-1]
				else:
					individual_test = failed_individual_tests[0]
				long_explanation = individual_test.get_long_explanation(show_input=args.show_input, show_reproduce_command=args.show_reproduce_command,
					show_expected=args.show_expected and not test.parameters.get('no_expected_output', ''),
					show_actual=args.show_actual and not test.parameters.get('no_actual_output', ''),
					show_diff=args.show_diff and not test.parameters.get('no_diff', ''),
					show_stdout_if_errors=args.show_stdout_if_errors, colorize=args.colorize)
				#remove hexadecimal constace
				reduced_long_explanation = re.sub(r'0x[0-9a-f]+', '', long_explanation, flags=re.I)
				if reduced_long_explanation in previous_errors:
					print(colored("failed", 'red'), "({} - same as Test {})".format(individual_test.short_explanation, previous_errors[reduced_long_explanation]), flush=True, file=file)
				else:
					print(colored("failed", 'red'), "(%s)" % individual_test.short_explanation, file=file)
					if long_explanation:
						#print(file=file)
						print(long_explanation, flush=True, file=file, end='')
					previous_errors.setdefault(reduced_long_explanation, test_label)
				n_tests_failed += 1
			else:
				n_tests_not_run += 1
	if debug > 2:
		subprocess.call("ls -l;pwd", shell=True)
	if n_tests_passed:
		print(colored(str(n_tests_passed) + ' tests passed', 'green'), end=' ', file=file)
	else:
		print(colored('0 tests passed', 'red'), end=' ')
	if n_tests_failed:
		print(colored(str(n_tests_failed) + ' tests failed', 'red'), end='', file=file)
	else:
		print(colored('0 tests failed', 'green'), end=' ', file=file)
	if n_tests_not_run:
		print('', n_tests_not_run, 'tests could not be run', end='', file=file)
	print(file=file)
	return 1 if n_tests_failed + n_tests_not_run else 0


#def run_tests_json(tests, args, return_values):
#    return_values['compiled_successfully'] = True
#    return_values['debug'] += 'test_labels_to_run:' + str(args.labels)
#    program_compile_commands = {}
#    if os.path.exists('./compile.sh'):
#        (output, exit_status) = system(['./compile.sh', args.programs])
#        if exit_status == 0:
#            return_values['compiled_successfully'] = False
#            return
#        for program in args.programs:
#            program_compile_commands[program] = ['']
#    else:
#        for test_label in args.labels:
#            test = tests[test_label]
#            if test.program not in program_compile_commands:
#                (output, program_compile_commands[test.program], pre_execute_commands) = compile_program(test.program, args)
#                if not program_compile_commands[test.program]:
#                    return_values['compiled_successfully'] = False
#                    return_values['compiler_output'] = output
#                    return
#    test_results = {}
#    test_details = {}
#    n_tests_passed = 0
#    n_tests_failed = 0
#    test_output =''
#    for test_label in args.labels:
#        test = tests[test_label]
#        test_output += "Test %s (%s) -" % (test.label, test.description)
#        # UPDATE THIS
#        if test.run_test_old(program_compile_commands[test.program]):
#            test_output += "passed\n"
#            test_results[test.label] = "passed"
#            n_tests_passed += 1
#        else:
#            n_tests_failed += 1
#            test_output += "failed (%s)\n" % test.short_explanation
#            test_output += '\n' + test.get_long_explanation() + '\n'
#            test_results[test.label] = "failed"
#            test_details[test.label] = {
#                'label': test.label,
#                'command': test.command,
#                'description': test.description,
#                'stdin': test.get_stdin(),
#                'short_explanation': test.short_explanation,
#                'long_explanation': test.get_long_explanation(),
#                'stdout': test.stdout,
#                'expected_stdout': test.expected_stdout,
#                'stdout_ok': test.stdout_ok,
#                'stderr': test.stderr,
#                'expected_stderr': test.expected_stderr,
#                'stderr_ok': test.stderr_ok,
#            }
##            if not test.stdout_ok and test.stdout and test.expected_stdout:
##                d = difflib.HtmlDiff(linejunk=None, charjunk=lambda c:c in test.ignore_chars)
##                test_details[test.label]['stdout_htmldiff'] = d.make_table(test.stdout, test.expected_stdout, "Your Output", "Expected Output", True, 3)
##            if not test.stderr_ok and test.stderr and test.expected_stderr:
##                d = difflib.HtmlDiff(linejunk=None, charjunk=lambda c:c in test.ignore_chars)
##                test_details[test.label]['stderr_htmldiff'] = d.make_table(test.stderr, test.expected_stderr, "Your Stderr", "Expected Stderr", True, 3)
#    return_values['tests_run'] = args.labels
#    return_values['test_output'] = test_output
#    return_values['test_results'] = test_results
#    return_values['test_details'] = test_details
#    return_values['n_tests_passed'] = n_tests_passed
#    return_values['n_tests_failed'] = n_tests_failed

def repository_name(submission_name, account=None):
	zid = get_zid(account)
	if re.search(r'^lab', submission_name):
		submission_name = 'labs'
	c = course_configuration['course_code'].lower()
	return "gitlab@gitlab.cse.unsw.EDU.AU:%s/%s-%s-%s" % (zid, course_configuration['unsw_session'], c, submission_name)

def cleanup(temp_dir=None):
	if temp_dir and re.search('^/tmp/', temp_dir) and debug < 10:
		shutil.rmtree(temp_dir)

def warn(message):
	print("%s: %s" % (my_name, message), file=sys.stderr)

def die(message):
	raise InternalError(message)

def execute(command, print_command=True):
	if print_command or debug:
		print(" ".join(command))
	if subprocess.call(command) != 0:
		die("%s failed"%command[0])

#
# Is this needed?
#
def check_program(program):
	if not re.search(r'\.', program):
		program += ".c"
	for p in glob.glob(program):
		m = re.match(r'\.([a-z]+)$', program)
		if m and m not in suffixes:
			continue
		try:
			os.chmod(p, 0o700)
			with open(p, "rb") as f:
				program_bytes = f.read(1000000)
			if '\0' in program_bytes:
				die("%s is not a text file.\n You must correct this problem and re-submit." % p)
			correct_first_line = { 'sh' : '/bin/sh', 'pl' : '/usr/bin/perl -w', 'py': '/usr/bin/python'}
			m = re.search(r'\.([a-z]{1,3})$', program)
			if m and program_bytes[0:2] != '#!' and m.group(1) in correct_first_line:
				die("%s needs a #! line such as \"%s\" as its first line.\n You must correct this problem and re-submit." % (program, correct_first_line[ m.group(1)]))
		except (IOError, OSError):
			warn("could not open %s" % program)
			return False
	return True

def create_link(file, i):
	try:
		if os.path.exists(file):
			os.unlink(file)
	except OSError:
		pass
	target = file +  "." + str(i)
	if debug:
		print("os.link(%s,%s)" % (target, file), file=sys.stderr)
	os.link(target, file)

def compile_program(test, args):
	program = test.program
	if debug: print('compile_program(%s)' % program)
#    if program.lower() == "makefile":
#        return ('', [''], [''])
	pre_execute_commands = [lambda:None]
	basename, extension = os.path.splitext(program)
	if debug: print('extension:', extension, file=sys.stderr)
	if not extension and test.files:
		basename, extension = os.path.splitext(test.files[0])
		if not program:
			program = test.files[0]
		if extension == '.h':
			extension = '.c'
	if not extension or extension == '.*':
		for lang in ['pl', 'py', 'sh', 'c', 'cc', 'java', 'js']:
			suffix = '.' + lang
			if os.path.exists(basename  + suffix):
				if debug:  print('found:', basename  + suffix, file=sys.stderr)
				extension = suffix
				if lang in ['pl', 'py', 'sh']:
					pre_execute_commands = [lambda basename=basename,lang=lang:create_link(basename, lang)]
					program = basename  +  suffix
				elif lang in ['java']:
					pre_execute_commands = [lambda basename=basename:open(basename,"w").write("#!/bin/bash\njava %s $@" % basename) and os.chmod(basename, 0o700)]
					program = basename
				elif lang in ['js']:
					program = basename  +  suffix
					pre_execute_commands = [lambda basename=basename:open(basename,"w").write("#!/bin/bash\nnode %s $@" % program) and os.chmod(basename, 0o700)]
				elif lang in ['c','cc']:
					program = basename
	if debug: print('program', program, 'basename', basename, 'extension:', extension, file=sys.stderr)
	if extension in ['.pl', '.py', '.sh']:
		if os.path.exists(program):
			os.chmod(program, 0o700)
		else:
			return (program + " not found", [], [])

		try:
			with open(program) as f:
				program_bytes = f.read(1000000)
				if '\0' in program_bytes:
					return ("%s is not a text file." % program, [], [])
				program_first_line = program_bytes.partition('\n')[0]
		except UnicodeDecodeError:
			return ("%s is not a text file." % program, [], [])

		if args.check_hash_bang_line:
			correct_first_line = { 'sh' : '/bin/sh', 'pl' : '/usr/bin/perl -w', 'py': '/usr/bin/python'}
			if extension[1:] in correct_first_line:
				if not program_first_line.startswith('#!'):
					return ("%s missing a #! line such as \"#!%s\" as its first line." % (program, correct_first_line[extension[1:]]), [], [])
				m = re.match(r'#!(\S+)', program_first_line)
				if not m or not os.path.exists(m.group(1)):
					return ("%s bad #! line: \"%s\" try: #!%s" % (program, program_first_line, correct_first_line[extension[1:]]), [], [])
	# run syntax check or compilers
	run = ['']
	compile_commands = ['']
	glob_lists = [glob.glob(g) for g in test.files]
	test_files = [item for sublist in glob_lists for item in sublist]
	if extension == ".c":
		compiler_args = make_list(interpolate_backquotes(test.parameters.get('compiler_args', test_files + ['-o', program])))
		compilers = test.parameters.get('compilers', args.c_compilers)
		compile_commands = [compiler.split() + compiler_args for compiler in compilers.split(':')]
		# if multiple compilers - suffix executable with .n
		if len(compile_commands) > 1:
			run = [r[0:-1] + [r[-1] + '.' + str(i)] for (i, r) in enumerate(compile_commands)]	
			compile_target = program
			if '-o' in compiler_args[0:-1]:
				compile_target = compiler_args[compiler_args.index('-o') + 1]
			pre_execute_commands = [lambda basename=basename,i=i:create_link(compile_target, i) for (i, c) in enumerate(compile_commands)]
		else:
			run = list(compile_commands)
		checkers = test.parameters.get('checkers', args.c_checkers)
		if isinstance(checkers, list):
			checkers = ' '.join(checkers)
		if checkers:
			check_commands = [checker.split() + test_files for checker in checkers.split(':')]
			run += check_commands
	elif extension == ".cc":
		compiler_args = test.parameters.get('compiler_args', test_files + ['-o', program])
		run = [['g++'] + compiler_args]
	elif extension == ".java":
		compiler_args = test.parameters.get('compiler_args', test_files)
		run = [['javac'] + compiler_args]
	elif extension == ".js":
		compiler_args = test.parameters.get('compiler_args', test_files)
		run = [['node', '--check'] + compiler_args]
	elif extension == ".pl":
		run = [['perl', '-cw ', program]]
	elif extension == ".py":
		if 'python3' in program_first_line:
			run = [['python3', '-B', '-m', 'py_compile', program]]
		else:
			run = [['python', '-B', '-m', 'py_compile', program]]
	elif extension == ".sh":
		run = [['bash', '-n', program]]
	if debug: print('run:', run, file=sys.stderr)
	if debug: print('compile_commands:', compile_commands, file=sys.stderr)
	output = ''
	for (i, command) in enumerate(run):
		if command:
			if debug: print(" ".join(command), file=sys.stderr)
			(errors, exit_status) = system(command)
			if i < len(compile_commands):
				if compile_commands[i] and args.show_reproduce_command:
					output += " ".join(compile_commands[i]) + "\n" 
				output += errors
			else:
				if args.show_reproduce_command:
					output += " ".join(run[i]) + "\n"
				output += errors
			if debug: print('exit_status', exit_status, file=sys.stderr)
			if exit_status != 0:
				return (output, [], [])
	return (output, compile_commands, pre_execute_commands)

# run command merging stdin & stdout
def system(command, **kwargs):
	if debug:
		print("system(%s)" % (command), file=sys.stderr)
	try:
#       doesn't work with old CGI servers
#        kwargs['stderr'] = kwargs.get('stderr', sys.stdout.fileno())
#        output = subprocess.check_output(command, **kwargs)
		kwargs['stderr'] = subprocess.PIPE
		kwargs['stdout'] = subprocess.PIPE
		kwargs['universal_newlines'] = True
		process = subprocess.Popen(command, **kwargs)
		(output,error) = process.communicate()
		if debug > 1: print('Popen.communicate() output=%s, error=%s'%(output,error), file=sys.stderr)
		output = error + output
		exit_status = process.returncode
	except subprocess.CalledProcessError as err:
		(output, exit_status) = (err.output, err.returncode)
	return (output, exit_status)

def fetch_submission(temp_dir, args):
	if debug:
		print("fetch_submission(%s)" % (temp_dir), file=sys.stderr)
	if args.tarfile:
		# FIXME handle xz compression
		if re.search(r'^https?://.*\.tar(.[a-z]+)?', args.tarfile):
			os.chdir(temp_dir)
			execute(['wget', '-O', 'submission.tar', args.tarfile])
			execute(['tar', '-x',  '-f', 'submission.tar'])
		else:
			execute(['tar', '-x', '-C', temp_dir, '-f', args.tarfile], print_command=False)
	elif args.directory:
		copy_directory(args.directory, temp_dir)
	elif args.git:
		os.chdir(temp_dir)
		if args.commit:
			execute(['git', 'clone', '--quiet', args.git, '.'])
			execute(['git', 'checkout', '--quiet', args.commit])
		else:
			execute(['git', 'clone', '--quiet', '--depth', '1', args.git, '.'])
		if os.path.isdir(args.exercise) and os.listdir(args.exercise):
			print('cd', args.exercise)
			os.chdir(args.exercise)
	else:
		if os.path.isdir(".git") and os.path.isdir(args.exercise) and os.listdir(args.exercise):
			print('cd', args.exercise)
			os.chdir(args.exercise)
		files_to_copy = set(args.file | args.optional_files)
#        basename_files_to_copy = [re.sub(r'\.[a-z]{1,4}$', '', p) for p in files_to_copy]
#        if debug:
#            print('basename_files_to_copy:', basename_files_to_copy)
#        for program in args.programs:
#            if not re.search(r'(akefile|\.[a-z]{1,3})$', program):
#                files_to_copy.add(program+'.c')
#            elif program in basename_files_to_copy:
#                continue
#            else:
#                files_to_copy.add(program)
#        for program in all_programs:
#            if '*' in program:
#                basename_program = re.sub(r'\.[^.]{1,4}$', '', program)
#                if basename_program not in basename_files_to_copy:
#                    files_to_copy.update(glob.glob(program))
		if args.stdin:
			if len(files_to_copy) != 1:
				print("--stdin specified but tests requires multiple files", file=sys.stderr)
				sys.exit(1)
			os.chdir(temp_dir)
			file = files_to_copy.pop()
			try:
				with open(file, 'w') as f:
					f.write(sys.stdin.read())
			except IOError:
				die("can not create %s" % file)
			return
		if debug:
			print('files_to_copy:', files_to_copy, file=sys.stderr)
		copied = set()
		while files_to_copy:
			file_pattern = files_to_copy.pop()
			if file_pattern in copied:
				continue
			copied.add(file_pattern)
			for file in glob.glob(file_pattern):
				try:
					shutil.copy(file, temp_dir)
					if re.search('\.[pc].?$', file):
						try:
							# Kludge to pick up include files
							with open(file) as f:
								for line in f:
									m = re.search(r'\b(require|include)\s*[\'"](.*?)[\'"]', line, flags=re.I)
									if m:
										files_to_copy.add(m.group(2))
									m = re.search(r'^\s*\b(use|require)\s*(\S+)', line, flags=re.I)
									if m:
										files_to_copy.add(m.group(2) + '.pm')
						except UnicodeDecodeError:
							die("%s is not a text file" % file)
				except IOError:
					continue

def find_autotest_dir(submission_name, autotest_subdir=['autotest','dryrun'], tests_filename='tests.txt'):
	d = course_configuration['base_directory']
	# Ugly hacks - they should be moved to per class configuration
	names = [submission_name]
	if '.' in submission_name:
		names.append(re.sub(r'\..*', '', submission_name))
	m = re.match(r'(\w+?\d{1,2}_)?(.*)', submission_name)
	if m:
		names.append(re.sub(r'\..*', '', m.group(2)))
	m = re.match(r'(\w+\d{1,2}_)?(.*)', submission_name)
	if m:
		names.append(re.sub(r'\..*', '', m.group(2)))

	if 'AUTOTEST_DIRECTORY' in os.environ:
		for activity in names:
			path = os.path.join(os.environ['AUTOTEST_DIRECTORY'], activity)
			if os.path.exists(os.path.join(path, tests_filename)):
				return path

	for activity in names:
		path = os.path.join(d, 'activities', activity)
		if debug > 1:
			print('Searching for autotest in', path, file=sys.stderr)
		if os.path.exists(os.path.join(path, tests_filename)):
			return path
		path = os.path.join(d, 'activities', activity, 'autotest')
		if debug > 1:
			print('Searching for autotest in', path, file=sys.stderr)
		if os.path.exists(os.path.join(path, tests_filename)):
			return path
	m = re.match(r'^(\D+)(\d+)$', submission_name)
	if m:
		names.append(os.path.join(m.group(2), m.group(1)))
		names.append(os.path.join(m.group(1), m.group(2)))
		if m.group(1) in ['tut', 'lab']:
			names.append(os.path.join('tlb', m.group(2)))
	for dir in ['.',  os.path.join(d, 'activities'), d, os.path.join(d, '*'), os.path.join(d, '*', '*')]:
		for name in names:
			for sub_dir in autotest_subdir:
				glob2 = os.path.join(dir, name + '?' + sub_dir)
				glob1 = os.path.join(dir, name, sub_dir)
				if debug > 1:
					print('Searching for autotest in', glob1, glob2, file=sys.stderr)
				for path in glob.glob(glob1) + glob.glob(glob2):
					if os.path.isdir(path):
						return path
	return os.path.join(course_configuration['work_directory'], submission_name, 'tests')

# exit_status == 0 -> all tests worked
# exit_status == 1 -> 1 or more tests failed
# exit_status >- 2, internal error - testing not completed

def copy_directory(src, dst, symlinks=False, ignore=None):
	names = os.listdir(src)
	if ignore is not None:
		ignored_names = ignore(src, names)
	else:
		ignored_names = set()

	if not (os.path.exists(dst) and os.path.isdir(dst)):
		os.makedirs(dst)
		# we don't want to copy directory permission if the directory exists already
		try:
			copystat(src, dst)
		except (WindowsError, OSError):
			pass
	for name in names:
		if name in ignored_names:
			continue
		srcname = os.path.join(src, name)
		dstname = os.path.join(dst, name)
		try:
			if symlinks and os.path.islink(srcname):
				linkto = os.readlink(srcname)
				os.symlink(linkto, dstname)
			elif os.path.isdir(srcname):
				copy_directory(srcname, dstname, symlinks, ignore)
			else:
				copy2(srcname, dstname)
		except OSError as why:
			# we don't want to stop if there is an unreadable file - just produce an error
			print('Warning:', why, file=sys.stderr)

if __name__ == '__main__':
	my_name = re.sub(r'\.py$', '', os.path.basename(sys.argv[0]))
	# there may be other threads running so use os._exit(1) to terminate entire program on interrupt
	if not debug:
		signal.signal(signal.SIGINT, lambda signum, frame:os._exit(2))
	try:
		main()
	except InternalError as e:
		print("%s: %s" % (my_name, str(e)), file=sys.stderr)
		if debug:
			traceback.print_exc(file=sys.stderr)
		sys.exit(2)
	except Exception:
		etype, evalue, etraceback = sys.exc_info()
		eformatted = "\n".join(traceback.format_exception_only(etype, evalue))
		print("%s: internal error: %s" % (my_name, eformatted), file=sys.stderr)
		if debug:
			traceback.print_exc(file=sys.stderr)
		sys.exit(2)
