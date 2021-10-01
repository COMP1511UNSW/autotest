#!/usr/bin/python3

# parse a "tests.txt" file specifying an autotest

import ast, io, collections, copy, os, pprint, re, sys, tokenize
from parameter_descriptions import check_valid_parameter_name, normalize_parameters
from util import TestSpecificationError

IGNORE_TOKENS = set([tokenize.COMMENT, tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER, tokenize.INDENT, tokenize.NEWLINE, tokenize.NL])

ASSIGNMENT = None


def parse_file(pathname, initial_parameters={},  initial_tests={}, normalize_global_parameters=True, debug=0):
	"""
		return tuple of tests and global parameter values specified in file
		tests are as an OrderedDict of dicts
		key is test label, value is dict of parameter values for each test 
		ordered by first appearance in file
	"""
	if 'supplied_files_directory' not in initial_parameters:
		initial_parameters['supplied_files_directory'] = os.path.dirname(pathname) or '.'
	with open(pathname, 'r') as f:
		return parse_stream(f, pathname, initial_parameters, initial_tests, normalize_global_parameters, debug)


def parse_string(string, source_name="<string>", initial_parameters={},  initial_tests={}, normalize_global_parameters=True, debug=0):
	"""
		equivalent of parse_file for string
	"""
	return parse_stream(io.StringIO(string), source_name, initial_parameters, initial_tests, normalize_global_parameters, debug)	

		
def parse_stream(stream, source_name="?", initial_parameters={},  initial_tests={}, normalize_global_parameters=True, debug=0):
	"""
		equivalent of parse_file for stream
	"""
	tests = collections.OrderedDict(initial_tests)
	global_parameters = dict(initial_parameters)
	test_local_parameters = collections.defaultdict(set)
	for (line_number, values, source_lines) in get_line_literals(stream, source_name, global_parameters, debug=debug):
		if values:
			try:
				process_line(source_name, line_number, values, global_parameters, tests, test_local_parameters, debug=debug)
			except TestSpecificationError as e:
				raise TestSpecificationError(f"{source_name}:{line_number}: {e}")
			except Exception as e:
				raise TestSpecificationError(f"{source_name}:{line_number}: internal error - {e}")

	if debug > 2:
		print('*** pre-nomalize_parameters **')
		print_tests(tests)
	
	for test in tests.values():	
		normalize_parameters(test)

	if debug > 1:
		print('*** normalized_parameters **')
		print_tests(tests)
		
	if normalize_global_parameters:
		normalize_parameters(global_parameters, check_required_parameters_set=False)

	return tests, global_parameters


def print_tests(tests):
#	last_parameters = {}
	for (label, parameters) in tests.items():
		print('***', label, end=' = ')
		p = dict((k,v) for (k,v) in parameters.items() if  k != '__environment_original' and v)
#		p = dict((k,v) for (k,v) in parameters.items() if v != last_parameters.get(v, None) and)
		pprint.pprint(p)


def process_line(source_name, line_number, values, global_parameters, tests, test_local_parameters, debug=0):
	"""
		process a line updating: global_parameters, tests, test_local_parameters
		return parameters specified in line
	"""
	local_parameters = parse_line_assignments(values, debug=debug)

	for parameter_name in local_parameters:
		if not check_valid_parameter_name(parameter_name):
			raise TestSpecificationError(f"error - unknown parameter '{parameter_name}'")

	if 'label' not in local_parameters:
		global_parameters.update(local_parameters)
		return local_parameters

	label = local_parameters['label']

	respecified_parameters = test_local_parameters[label] & local_parameters.keys()
	respecified_parameters.discard('label')
	if respecified_parameters:
		respecified = ', '.join(respecified_parameters)
		raise TestSpecificationError(f"error - parameter specification multiples for test '{label}' ({respecified})")

	if label not in tests:
		tests[label] = copy.deepcopy(global_parameters)
		tests[label]['_source_name'] = str(source_name)
		tests[label]['_line_number'] = str(line_number)
	tests[label].update(copy.deepcopy(local_parameters))
	test_local_parameters[label].update(local_parameters.keys())		

	return local_parameters	


def parse_line_assignments(values, debug=0):
	"""
		return a dict containing the parameters specified by values
		a singleton value is converted to a value for the parameter 'label'
	"""
	if debug > 3:
		print(f'parse_line_assignments({values}')
	assignments = collections.OrderedDict()
	last_word = None
	
	while values:
		value = values.pop(0)
		if value == ASSIGNMENT:

			if (not last_word or
				not last_word.isidentifier() or
				not(values) or
				values[0] == ASSIGNMENT):
				raise TestSpecificationError("syntax error in assignment")

			if last_word in assignments:
				raise TestSpecificationError(f"error - multiple assignments to variable '{last_word}'")

			if len(values) == 1 or values[2:3] == [ASSIGNMENT]:
				assignment_rhs = values.pop(0)
			else:
				assignment_rhs = []
				while values and values[1:2] != [ASSIGNMENT]:
					assignment_rhs.append(values.pop(0))

			assignments[last_word] = assignment_rhs
			last_word = None
		elif isinstance(value, str): 
			if last_word:
				if 'label' in assignments:
					labels = f"'({assignments['label']}', '{last_word}')"
					raise TestSpecificationError(f"error - multiple test labels: {labels}")
				assignments['label'] = last_word
			last_word = value
		else:
			raise TestSpecificationError("error - invalid test specification")
	if last_word:
		if 'label' in assignments:
			labels = f"'({assignments['label']}', '{last_word}')"
			raise TestSpecificationError(f"error - multiple test labels: {labels}")
		assignments['label'] = last_word
	return assignments
																
		
def get_line_literals(stream, source_name, parameters, debug=0):
	"""
		yields a (line_number, literal_list, source_lines) tuple
		where literal_list is the literals parsed from lines starting at line_number
		source_lines is the lines as a string
		
		literal_list can contain strings, lists, dicts and the special literal ASSIGNMENT
		list or dicts in literal_list will only not contain any other type than strings, lists or dicts 
		
		shell-like bare-words and merge of adjacent tokens is implemented
		
		triple-quoted strings, lists & dict can span multiple lines - which is handled in an brittle, inefficient and ugly manner	
	"""
	source_lines = ''
	start_lines_number = 1
	for (line_number, line) in enumerate(stream, 1):

		if source_lines == '':
			start_lines_number = line_number

			# hack for backwards compatiblity change lines like this:
			# compiler_args=-Dmain=_main autotest_add.c add.c -o add
			# to
			# compiler_args='-Dmain=_main' autotest_add.c add.c -o add
			if line.startswith('compiler_args='):
				line = re.sub(r'([ =])(\S+=\S+)', r"\1'\2'", line)
		
		source_lines += line
		try:
			yield (start_lines_number, parse_literals(source_lines, parameters, debug=debug), source_lines)
			source_lines = ''
		except (tokenize.TokenError, SyntaxError, ValueError):
			# assume this exceptions results from a multi-line string, strings, lists & dict
			# continue adding more lines until string/expression is complete 
			pass
		except Exception as e:
			raise TestSpecificationError(f"{source_name}:{line_number}:{e}")

	if source_lines:
		raise TestSpecificationError(f"{source_name}:{start_lines_number}: incomplete literal")


def parse_literals(combined_lines, parameters, debug=0):
	"""
		parse 1 or more lines into literals
	"""
	if debug > 3:
		print(f'parse_literals({repr(combined_lines)})')
	last_token = None
	literals = []

	for token in tokenize.generate_tokens(io.StringIO(combined_lines).readline):
		if debug > 3:
			print(f'token {token}')

		if token.type == tokenize.ERRORTOKEN:
			# allow bare $ ! ` ? characters
			if (
				len(token.string) == 1 and
				token.string in " \t"
				):
					continue
			elif (
				len(token.string) == 1 and
				token.string in "$!`?"
				):
					token = FakeToken(token)
			else:
				raise TestSpecificationError(f"{token.start[1]}: syntax error unexpected '{token.string}'")
		
		if not token.string or token.type in IGNORE_TOKENS:
			continue
		
		if token.type != tokenize.STRING and '=' in token.string and  len(token.string) > 1:
			raise TestSpecificationError(f"{token.start[1]}: syntax error in assignment")
		
		# chop up string in reverse  attempting to find something
		# that parses as a Python literal list or dict - ugly and brittle
		if token.string in '[{' and literals and literals[-1] == ASSIGNMENT and not last_token:
			closing_ch = ']' if token.string == '[' else '}'
			(start_line, start_ch) = token.start
			remaining_lines = "\n".join(combined_lines.splitlines()[start_line-1:])[start_ch:]
			if debug > 3:
				print(f'list/dict parsing remaining_lines={repr(remaining_lines)}')
				print(token)
			for end in range(len(remaining_lines)):
				if remaining_lines[end] == closing_ch:
					try:
						if debug > 3:
							print(f'eval({repr(remaining_lines[:end+1])})')
						literal = ast.literal_eval(remaining_lines[:end+1])
						# break up remainder of string into literals
						return literals + [literal] + parse_literals(remaining_lines[end+1:], parameters, debug=debug)
					except (ValueError, SyntaxError):
						pass
			raise ValueError("unclosed list or dict")

		if not last_token or (last_token.end != token.start or token.string == '='):
			if last_token:
				literals.append(get_token_characters(last_token, parameters))
			if token.string == '=':
				literals.append(ASSIGNMENT)
				last_token = None
			else:
				last_token = token
		else:
			last_token = FakeToken(last_token, token, parameters) # shell-like merging of adjacent strings
			
	if last_token:
		literals.append(get_token_characters(last_token, parameters))
	return literals
	

def stringize(x):
	"""convert to str all sub-objects in x which are not dicts and list"""
	if isinstance(x, dict):
		for (k,v) in x.items():
			x[k] = stringize(v)
	elif isinstance(x, list):
		for i in range(len(x)):
			x[i] = stringize(x[i])
	elif not isinstance(x, str):
		x = str(x)
	return x


def get_token_characters(token, parameters):
	"""return characters of token"""
	if token.type == tokenize.STRING:
		if token.string[0] == 'f':
			return eval(token.string, globals(), parameters)
		else:
			return ast.literal_eval(token.string)
	else:
		return token.string

		
class FakeToken:
	"""return a tokenize.TokenInfo look-like formed from 2 adjacent tokens"""
	def __init__(self, token1, token2=None, parameters={}):
		self.type = tokenize.STRING
		self.start = token1.start
		if token2 is None:
			self.end = token1.end
			self.string = repr(get_token_characters(token1, parameters))
		else:
			self.end = token2.end
			self.string = repr(get_token_characters(token1, parameters) + get_token_characters(token2, parameters))
	def __str__(self):
			return self.string


def output_file_without_parameters(pathname, initial_parameters={},  initial_tests={}, remove_parameters=['expected_stdin','expected_stdout'], debug=0, file=sys.stdout):
	"""
		read a test specification file and output it to the stream file
		with specified parameters removed
		labels are not printed if all parameters are removed
		comments and white-space are preserved for lines that don't include the specified parameters
		
	"""
	with open(pathname, 'r') as f:
		return output_stream_without_parameters(f, pathname, initial_parameters, initial_tests, remove_parameters, debug, file)


def output_stream_without_parameters(stream, source_name, initial_parameters, initial_tests, remove_parameters, debug, file):
	"""
		equivalent of output_file_without_parameters for stream
	"""
	tests = collections.OrderedDict(initial_tests)
	global_parameters = dict(initial_parameters)
	test_local_parameters = collections.defaultdict(set)
	for (line_number, values, source_lines) in get_line_literals(stream, source_name, global_parameters, debug=debug):
		parameters = process_line(source_name, line_number, values, global_parameters, tests, test_local_parameters, debug=0)
		output_lines_without_parameters(source_lines, parameters, remove_parameters, file)


def output_lines_without_parameters(source_lines, parameters, remove_parameters, file):
	"""
		output_stream_without_parameters helper
	"""
	if not (set(remove_parameters) & set(parameters)):
		print(source_lines, end='', file=file) 
		return

	if 'label' not in parameters:
		for (k,v) in parameters.items():
			if k not in remove_parameters:
				print(f'{k}={repr(v)}', file=file)
		return

	# don't print a bare label
	if (set(parameters) & set(remove_parameters)) and len(set(parameters) - set(remove_parameters)) < 2:
		return
		
	print(parameters['label'], end='', file=file)	
	for (k,v) in parameters.items():
		if k not in remove_parameters and k != 'label':
			print(f' {k}={repr(v)}', end='', file=file)
	print(file=file)	
				
		
TEST_STRINGS = [
"""

# aaaa	

max_cpu_seconds=45

program=hello.py
files=hello.py

test1 max_cpu_seconds=10 expected_stdout=kkk command=hello.py "arg 1" 'arg 2' *.c

test2 stdin=5

v1='''1
2
3
'''

v2='''a
b
c
'''

v3 = [4,5,6]

v4 = [7,
8,
9]

v5 = {'a':'b'}

v6 = {
'c'  : 'd'
}

label=last
""",
"""

test42 expected_stdout='''line 1
line 2
line 3
''' expected_stderr='''e1
e2
e3
'''
""",
"""

x=7
f_string_test y=f'answer={int(x) * 6}'
""",
"""

r_string_test x=r'\\n'
""",

"""
a b=1
a c=2
""",
"""
#unseen_3 a=b
""",
"""
compiler_args=-Dmain=_main autotest_add.c add.c -o add
a
""",

"""
a = [
6] b = [
5
,
6

] c=[7,8

] v1='''1
2
3
''' v2='''a
b
c
''' v3 = [4,5,6] v4 = [7,
8,
9] v5 = {'a':'b'} v6 = {
'c'  : 'd'
} label
"""

]

if __name__ == "__main__":
	if sys.argv[1:]:
		for pathname in sys.argv[1:]:
			pprint.pprint(parse_file(pathname))
	else:
		for test_string in TEST_STRINGS:
			print(test_string)
			pprint.pprint(parse_string(test_string, debug=10))