#!/usr/bin/python3

#
# This file specifies for all test parameters
# 	1) default value - possible computed from other parameter values
#   2) description in markdown used to generate parameter documentation
# 	3) required_type - the required type of this parameters (optional)
# 	4) code to finalize and update the parameter value possible depending on other parameters
# 	5) where this parameter must be set for all tests

import collections, copy, os, re, string, sys
from util import TestSpecificationError

# parameters are set to any values explicity set in the specification
#
# all unspecified parameters with non-callable default values are then set to this default value
#
# then all unspecified parameters with callable default values are set (evaluated) in the order in this list
# - this allows them to depend on the values of parameters  with non-callable default values
# - and also to depend on the values of parameters  with callable default values preceding them in the list
#
# finally the finalize method is called for all parameters which provide it
# it may normalize the parameter value, or update the parameter value base don other parameters


class Parameter:
    def __init__(
        self,
        name,
        default=None,
        description="",
        required_type=None,
        finalize=None,
        must_be_set=None,
        show_in_documentation=True,
    ):
        self.name = name
        self.default = default
        self.description = description
        self.required_type = required_type
        self.finalize = finalize
        self.must_be_set = must_be_set
        self.show_in_documentation = show_in_documentation
        if default is not None and required_type is None:
            t = type(default)
            if t in [int, bool, float, str, list]:
                self.required_type = t

    def get_default(self, paramaters):
        return self.default(paramaters) if callable(self.default) else self.default

    def description_markdown(self):
        markdown = f"**`{self.name}`**"
        if not callable(self.default) and self.default is not None:
            escaped_default = repr(self.default)
            escaped_default = escaped_default.replace("[", r"\[")
            escaped_default = escaped_default.replace("]", r"\]")
            markdown += f" = {escaped_default}"
        markdown += "\n\n"
        if self.description:
            d = self.description
            d = re.sub(r"^[ \t]*", "", d, flags=re.MULTILINE)
            # convert line-breaks marked with <br> to trailing spaces for markdown
            d = re.sub(r"<br>\n?", r"  \n", d, flags=re.MULTILINE)
            markdown += d
        return markdown


PARAMETER_LIST = []


def heuristically_infer_program(parameters):
    """
    heuristically infer program being run from other parameters
    """
    command = parameters.get("command", "")
    command_word = command
    if isinstance(command_word, list):
        command_word = str(command_word[0])
    command_word = re.sub(r"\s.*", "", str(command_word))
    bare_command_word = re.sub(r".*/", "", command_word)
    files = parameters.get("files", [])
    if not isinstance(files, list):
        files = [files]

    # this unfortunate code is tweaked in attempt to supply backwards compatibility
    # it is not documented and its behaviour should not be relied on
    # instead the parameter program should be specified

    if bare_command_word and [f for f in files if f.startswith(bare_command_word)]:
        return bare_command_word
    elif bare_command_word in parameters.get("compiler_args", ""):
        return bare_command_word
    elif re.match(r"^./[\w\-]+$", command_word):
        return command_word[2:]
    elif files:
        return re.sub(r"\.(c|cc|h)?$", "", str(files[0]), flags=re.I)
    elif bare_command_word:
        return bare_command_word
    else:
        # use of label for backwards compatibility only
        return re.sub("[_0-9]*$", "", str(parameters.get("label", "")))


PARAMETER_LIST += [
    "### Parameters specifying command to be run",
    Parameter(
        "program",
        default=heuristically_infer_program,
        must_be_set=True,
        description="""
			The name of script/binary to run for this test.<br>
			If **`program`** is not specified it is heuristically inferred from **`command`**, if possible.
		""",
    ),
    Parameter(
        "arguments",
        default=[],
        description="""
				Command-line arguments for this test. Used only if **`command`** is not specified.
			""",
    ),
]


def finalize_command(parameter_name, value, parameters):
    if isinstance(value, list):
        if parameter_name == "command":
            deprocated_shell_parameter_name = "shell"
        else:
            deprocated_shell_parameter_name = parameter_name + "_shell"
        l = [str(a) for a in value]
        if parameters.get(deprocated_shell_parameter_name, None):
            return " ".join(l)
        return l
    elif isinstance(value, str):
        return value
    elif value is None:
        return []
    raise TestSpecificationError(
        f"invalid value for parameter '{parameter_name}': {value}"
    )


PARAMETER_LIST += [
    Parameter(
        "command",
        default=lambda parameters: [parameters["program"]] + parameters["arguments"],
        finalize=finalize_command,
        must_be_set=True,
        description="""
			Command to run for this test.

			If **`command`** is a string, it is passed to a shell.<br>
			If **`command`** is a list, it is executed directly.<br>
			If **`command`** is not specified and **`program`** is specified,
			**`command`** is set to a list containing **`program`** with **`arguments`** appended.<br>
			Otherwise **`command`** is inferred heuristically from the first filename
			specified in  by parameter **`files`**, if possible.
			
		""",
    ),
    Parameter(
        "shell",
        default=False,
        show_in_documentation=False,
        description="""
			Deprocated: if **`shell`** is true, **`command`** is run by passing it to a shell.
		""",
    ),
]


def finalize_list_of_strings(name, value, parameters):
    if not isinstance(value, list):
        raise TestSpecificationError(f"invalid value for parameter '{name}': {value}")
    return [str(v) for v in value]


PARAMETER_LIST += [
    "### Parameters specifying files needed for for a test",
    Parameter(
        "files",
        default=lambda parameters: [parameters["program"] + ".c"]
        if "." not in parameters["program"]
        else [parameters["program"]],
        finalize=finalize_list_of_strings,
        required_type=list,
        must_be_set=True,
        description="""
			Input files required to be supplied for a test.<br>
			If **`files`** is not specified it is set to the parameter **`program`**
			with a `.c`  appended iff **`program`** does not contain a '.'.<br>
			For example if **`files`** is not specified and **`program`** == **`hello`**, **`files`** will be set to `hello.c`,
			but if **`program`** == `hello.sh` **`files`** will be set to `hello.c` 
		""",
    ),
    Parameter(
        "optional_files",
        default=[],
        finalize=finalize_list_of_strings,
        required_type=list,
        description="""
			Input files which may be optionally supplied for a test.
		""",
    ),
    "### Parameters specifying actions performed prior to test",
    Parameter(
        "check_hash_bang_line",
        default=True,
        description="""
			Check Perl, Python, Shell scripts have appropriate #! line.
		""",
    ),
    Parameter(
        "pre_compile_command",
        finalize=finalize_command,
        description="""
			If set **`pre_compile_command`** is executed once before compilation.<br>
			This is invisible to the user, unless **`pre_compile_command`** produces output.<br>
			Compilation does not occur if **`pre_compile_command`** has a non-zero exit-status.<br>
			If **`pre_compile_command`** is a string, it is passed to a shell.<br>
			If **`pre_compile_command`** is a list, it is executed directly.
		""",
    ),
    Parameter(
        "pre_compile_command_shell",
        show_in_documentation=False,
        default=False,
        description="""
			Deprocated: execute **`pre_compile_command`** by passing it to a shell.<br>
		""",
    ),
]


def finalize_compiler_checker_list(name, compilers_or_checkers, parameters):
    """
    verify list of checker or compilers for command
    if not set use default based on file suffix of first file in test
    """
    if compilers_or_checkers is None:
        files = parameters["files"]
        if files:
            suffix = os.path.splitext(files[0])[1]
            if suffix.startswith("."):
                suffix = suffix[1:]
            compilers_or_checkers = parameters["default_" + name].get(suffix, [])
        else:
            compilers_or_checkers = []

    # backwards compatibility
    if isinstance(compilers_or_checkers, str):
        return [s.split() for s in compilers_or_checkers.split(":")]

    if not isinstance(compilers_or_checkers, list):
        raise TestSpecificationError(
            f"invalid value for parameter '{name}': {compilers_or_checkers}"
        )
    program = parameters["program"]
    for (index, command) in enumerate(compilers_or_checkers):
        if not command:
            raise TestSpecificationError(
                f"invalid value for parameter '{name}': {compilers_or_checkers}"
            )
        if isinstance(command, str):
            continue
        if not isinstance(command, list):
            raise TestSpecificationError(
                f"invalid value for parameter '{name}': {compilers_or_checkers}"
            )

        if isinstance(command[0], list):
            command = select_command_from_alternatives(command)
            if not command:
                raise TestSpecificationError(
                    f"parameter '{name}' no alternative found: {compilers_or_checkers}"
                )
        compilers_or_checkers[index] = [
            program if a == "%" else str(a) for a in command
        ]
    return compilers_or_checkers


def select_command_from_alternatives(alternatives):
    """
    given a list of alternative commands,
    return first that exists in PATH
    None is returned if no command can be found in $PATH
    """
    for alternative in alternatives:
        if not alternative:
            return None
        if isinstance(alternative, str):
            executable = alternative.split()[0]
        elif isinstance(alternative, list):
            executable = alternative[0]
        else:
            return None
        if search_path(executable):
            return alternative
    return None


def search_path(program):
    """
    return first occurence of program as executable in $PATH
    return NONE if not found in PATH
    """
    for path in os.environ["PATH"].split(os.pathsep):
        full_path = os.path.join(path, program)
        if os.path.isfile(full_path) and os.access(full_path, os.X_OK):
            return full_path
    return None


PARAMETER_LIST += [
    Parameter(
        "default_checkers",
        default={
            "js": [["node", "--check"]],
            "pl": [["perl", "-cw"]],
            "py": [["python3", "-B", "-m", "py_compile"]],
            "sh": [["bash", "-n"]],
        },
        description="""
			A dict which supplies a default value for the parameter **`checkers`** based on the suffix for the 
			the first file specified by  the parameter **`files`**.
		""",
    ),
    Parameter(
        "checkers",
        finalize=finalize_compiler_checker_list,
        description="""
			List of checkers.  Each checker is run once for each file supplied for a test.  The filename is appended as argument.<br>
			Checkers are only run once for a file.<br>
			If checker is a string it is run by passing it to a shell.
			Deprocated: if the value is a string containing ':' a list is formed by splitting the string at the ':'s.
		""",
    ),
    Parameter(
        "default_compilers",
        default={
            "c": [[["dcc"], ["clang", "-Wall"], ["gcc", "-Wall"]]],
            "cc": [["g++", "-Wall"]],
            "java": [["javac"]],
            "rs": [["rustc"]],
        },
        description="""
			A dict which supplies a default value for the parameter **`compilers`** based on the suffix for the 
			the first file specified by  the parameter **`files`**.
			If '%' is present in a list, it is replaced by the **`program`**.
		""",
    ),
    Parameter(
        "compilers",
        finalize=finalize_compiler_checker_list,
        description="""
			List of compilers + arguments.<br>
			**`files`** are compiled with each member of list and test is run once for each member of the list.<br>
			For example, given:
			```
			# run all tests twice once compiled with gcc -fsanitize=address, once with clang -fsanitize=memory
			compilers = [['gcc', '-fsanitize=address'], ['clang', '-fsanitize=memory']] 
			```
			Element of the list of compilers can themselves be a list specifying a list of alternative compilers.<br>
			For example:
			```
			# run all tests twice once compiled with gcc -fsanitize=address, once with clang -fsanitize=memory
			compilers = [[['dcc'], ['clang', '-Wall'], ['gcc', -Wall]]]
			```
			The first element of this sub-list where the compiler can be found in PATH is used.<br>
			If compiler is a string it is run by passing it to a shell.<br>
			Deprocated: if the value is a string containing ':' a list is formed by splitting the string at the ':'s.
		""",
    ),
    Parameter(
        "default_compiler_args",
        default={
            "c": [["-o", "%"]],
            "cc": [["-o", "%"]],
        },
        description="""
			A dict which supplies a default value for the parameter **`compilers`** based on the suffix for the 
			the first file specified by  the parameter **`files`**.
			If '%' is present in a list, it is replaced by the **`program`**.
		""",
    ),
    Parameter(
        "compiler_args",
        default=[],
        finalize=finalize_compiler_checker_list,
        description="""
			"List of arguments strings added to every compilation"
			If '%' is present in a list, it is replaced by the **`program`**.
		""",
    ),
]


def finalize_compile_commands(name, value, parameters):

    # parameter has been set directly
    if isinstance(value, str):
        return [str]

    if isinstance(value, list):
        compile_commands = []
        for compile_command in value:
            if isinstance(compile_command, list):
                compile_command = [str(a) for a in compile_command]
            else:
                compile_command = str(compile_command)
            compile_commands.append(compile_command)
        return compile_commands

    compiler_args = parameters["compiler_args"]
    program = parameters["program"]

    compile_commands = []
    for command in parameters["compilers"]:

        command_as_list = command
        if isinstance(command, str):
            command_as_list = command.split()

        compiler = command_as_list[0]

        # for convenience and backwards compatiblity
        # add -o if it looks like a C compile without -o
        # and remove one -o if it looks like its specified twice
        #
        # is there is a better way to do this

        if "cc" in compiler or "clang" in compiler:
            if compiler_args:
                if "-o" in command_as_list and "-o" in compiler_args:
                    index = command_as_list.index("-o")
                    command = command_as_list[0:index] + command_as_list[index + 2 :]
            else:
                if "-o" not in command_as_list and program not in command_as_list:
                    compiler_args = ["-o", program]

        if isinstance(command, str):
            command += " ".join(compiler_args)
        else:
            command += compiler_args

        compile_commands.append(command)
    return compile_commands


PARAMETER_LIST += [
    Parameter(
        "compile_commands",
        finalize=finalize_compile_commands,
        description="""
			List of compile commands.<br>
			Test is run once for each member of the list.<br>
			If command is a string it is run by passing it to a shell.<br>
			**`compile_commands`** is not normally set directly.<br>
			If not set, it is formed from **`compilers`** and **`compiler_args`** and **`files`**.<br>
			In most cases, set these parameters will be more appropriate.
		""",
    ),
    Parameter(
        "setup_command",
        finalize=finalize_command,
        description="""
			If set **`setup_command`** is executed once before a test.<br>
			This is invisible to the user, unless **`setup_command`** produces output.<br>
			The test is not run if  **`setup_command`** has a non-zero exit-status.<br>
			If **`setup_command`** is a string, it is passed to a shell.
			If **`setup_command`** is a list, it is executed directly.
		""",
    ),
    Parameter(
        "setup_command_shell",
        show_in_documentation=False,
        default=False,
        description="""
			Deprocated: execute **`setup_command_shell`** by passing it to a shell.<br>
		""",
    ),
    "### Parameters specifying inputs for test",
    Parameter(
        "supplied_files_directory",
        required_type=str,
        description="""
			If set to a non-empty string, any files in this directory are copied to the directory before testing.<br>
			This directory is also prepended to any relative file pathnames in test specifications.<br>
			Its default value is the directory containing the test specification file (`tests.txt`).<br>
			Only one directory is copied for all tests.  This parameter must be set as a global parameter.
			It is usually specified in a wrapper shell script via -P.
		""",
    ),
]


def finalize_stream(parameter_name, stream_contents, parameters):
    """
    handle specifications of stdin, expected_stdin, expected_stdout
    All three can be specified as a string or as list of filename.

    For backwards compatibility
    The deprocated parameters stdin_file, expected_stdin_file, expected_stdout_file are also handled.
    As checking for a file named test_label.stdin etc
    """
    stream_name = parameter_name.replace("expected_", "")
    deprocated_file_name = parameters.get(f"{parameter_name}_file", "")
    if not stream_contents and deprocated_file_name:
        stream_contents = [deprocated_file_name]
    if not stream_contents and "label" in parameters:
        filename = f'{parameters["label"]}.{stream_name}'
        if os.path.exists(
            os.path.join(parameters["supplied_files_directory"], filename)
        ):
            stream_contents = [filename]
    return interpolate_file(stream_contents, parameter_name, parameters)


def interpolate_file(e, parameter_name, parameters):
    """
    where a list of filenames is specified instead of a string
    return a string formed from concatenating the files
    """
    if not e:
        return ""
    if isinstance(e, str):
        return e
    if not isinstance(e, list):
        raise TestSpecificationError("invalid type for value in {parameter_name}")
    contents = ""
    for pathname in e:
        if not isinstance(pathname, str):
            raise TestSpecificationError("invalid type for value in {parameter_name}")
        contents += read_file(pathname, parameters)
    return contents


def read_file(pathname, parameters):
    if not os.path.isabs(pathname):
        pathname = os.path.join(parameters["supplied_files_directory"], pathname)
    try:
        with open(pathname) as f:
            return f.read()
    except OSError as e:
        raise TestSpecificationError(f"{pathname}: {e}")


PARAMETER_LIST += [
    Parameter(
        "stdin",
        finalize=finalize_stream,
        description="""
			Bytes supplied on stdin for test.<br>
			Deprocated: stdin is not specified and the file *test_label*`.stdin` exists, its contents are used.<br>
			Not yet implemented: if value is a list it is treated as list of pathname of file(s) containing bytes.
		""",
    ),
    Parameter(
        "stdin_file",
        default="",
        show_in_documentation=False,
        description="""
			Deprocated: file supplied on stdin for test.<br>
		""",
    ),
    Parameter(
        "__environment_original",
        default=lambda p: dict((k, v) for (k, v) in os.environ.items()),
        show_in_documentation=False,
        description="""
			Internal variable holding original environment.
		""",
    ),
    Parameter(
        "environment_kept",
        # guess at variables whose absence might break programs
        default="ARCH|C_CHECK_.*|DCC_.*|DRYRUN_.*|LANG|LANGUAGE|LC_.*|LOGNAME|USER",
        description="""
			Environment variables are by default deleted to avoid them affecting testing.<br>
			Environment variables whose entire name matches this regex are not deleted.<br>
			All other environment variables are deleted.
		""",
    ),
    Parameter(
        "__environment_filtered",
        default=lambda p: dict(
            (k, v)
            for (k, v) in os.environ.items()
            if re.fullmatch(p["environment_kept"], k)
        ),
        show_in_documentation=False,
        description="""
			Internal variable holding filtered original environment.
		""",
    ),
]


def finalize_dict_of_strings(name, value, parameters):
    if not isinstance(value, dict):
        raise TestSpecificationError(f"invalid value for parameter '{name}': {value}")
    return dict((str(k), str(v)) for (k, v) in value.items())


PARAMETER_LIST += [
    Parameter(
        "environment_base",
        default=lambda p: {
            "LC_COLLATE": "POSIX",
            "LC_NUMERIC": "POSIX",
            "PERL5LIB": ".",
            "HOME": ".",
            "PATH": "/bin:/usr/bin:/usr/local/bin:.:"
            + p["__environment_original"].get("PATH", ""),
        },
        finalize=finalize_dict_of_strings,
        description="""
			Dict specifying values for environment variables.<br>
			Default:

			```
			{
				'LC_COLLATE' : 'POSIX',
				'LC_NUMERIC' : 'POSIX',
				'PERL5LIB' : '.',
				'HOME' : '.',
				'PATH' : '/bin:/usr/bin:/usr/local/bin:.:$PATH',
				}, 
			```
			where `$PATH` is the original value of `PATH`.
			 
			The environment  variables in **`environment_base`** are set and then,
			environment  variables specified in **`environment_set`** are set.<bt>
			This parameter should not normally be used.<bt>
			The parameter **`environment_set`** should normally be used instead of this parameter.<bt>
			It is only necessary to specify **`environment_base`** if these variables need to be unset rather than given different values for a test.<bt>
		""",
    ),
    Parameter(
        "environment_set",
        default={},
        finalize=finalize_dict_of_strings,
        description="""
			Dict specifying environment variables to be set for this test.<br>
			For example: `environment_set={'answer' : 42 }`<br>
			This is the parameter that should normally be used to manipulate environment variables. 
		""",
    ),
    Parameter(
        "environment",
        #       better but needs python 3.9
        # 		default = lambda test: test['__environment_filtered'] | test['environment_base'] | test['environment_set'],
        default=lambda test: {
            **test["__environment_filtered"],
            **test["environment_base"],
            **test["environment_set"],
        },
        finalize=finalize_dict_of_strings,
        description="""
			Dict specifying all environment variables for this test.<br>
			This parameter should not normally be specified,
			**`environment_set`** will serve most purposes.<br>
			By default **`environment`** is formed by taking original environment variables provided to autotest,<br>
			removing all but those matching the regex in **`environment_variables_kept`**,<br>
			setting any variables specified in **`environment_base`** and then<br>
			setting any variables specified in **`environment_set`**.
			
		""",
    ),
    "### Parameters specifying expected output for test",
    Parameter(
        "expected_stdout",
        finalize=finalize_stream,
        description="""
			Bytes expected on stdout for this test.<br>
			If value is a list it is treated as list of pathname of file(s) containing expected bytes.<br>
			Deprocated: if **`expected_stdout`** is not specified and the file *test_label*`.expected_stdout` exists,
			its contents are used.<br>
			Not yet implemented: handling of non-unicode output.<br>
		""",
    ),
    Parameter(
        "expected_stdout_file",
        show_in_documentation=False,
        default="",
        description="""
			Deprocated: pathname of file containing bytes expected on stdout for this test.
		""",
    ),
    Parameter(
        "expected_stderr",
        finalize=finalize_stream,
        description="""
			Bytes expected on stderr for this test.<br>
			If value is a list it is treated as list of pathname of file(s) containing expected bytes.<br>
			Deprocated: if **`expected_stderr`** is not specified and the file *test_label*`.stderr` exists,
			its contents are used.<br>
			Not yet implemented: handling of non-unicode output.
		""",
    ),
    Parameter(
        "expected_stderr_file",
        show_in_documentation=False,
        default="",
        description="""
			Deprocated: pathname of file containing bytes expected on stderr for this test.
		""",
    ),
    Parameter(
        "expected_file_name",
        default="",
        description="""
			Pathname of file expected to exist after this test.<br>
			Expected contents specified by **`expected_file_contents`**.<br>
			Use **`expected_files`** to specify creation of multiple files.
		""",
    ),
    Parameter(
        "expected_file_contents",
        default="",
        description="""
			Bytes expected in file **`expected_file_name`**  expected to exist after this test.<br>
			Not yet implemented: handling of non-unicode output.
		""",
    ),
]


def finalize_expected_files(name, expected_files, parameters):
    name = "expected_files"
    if not isinstance(expected_files, dict):
        raise TestSpecificationError(
            f"error invalid value for parameter '{name}': {expected_files}"
        )

    efn = parameters.get("expected_file_name", "")
    if efn:
        efc = parameters.get("expected_file_contents", "")
        if not efc and "label" in parameters:
            filename = f'{parameters["label"]}.expected_file'
            if os.path.exists(
                os.path.join(parameters["supplied_files_directory"], filename)
            ):
                efc = [filename]
        expected_files[efn] = efc

    for (k, v) in list(expected_files.items()):
        if not isinstance(k, str):
            raise TestSpecificationError(
                f"error invalid type for parameter '{name}' key: {k}"
            )
        expected_files[k] = interpolate_file(v, "expected_files", parameters)

    return expected_files


PARAMETER_LIST += [
    Parameter(
        "expected_files",
        default={},
        finalize=finalize_expected_files,
        description="""
			Dict specified bytes expected to be written to  files.<br>
			if value is an string, it is specifies bytes expected to be written to that filename.<br>
			If a value is a list it is treated as list of pathname of file(s) containing expected bytes.<br>
			For example: this indicates `a file named `answer.txt` should be created containing `42`.
			`expected_files={"answer.txt":"42\n"}`<br>
			
			Not yet implemented: handling of non-unicode output.<br>

		""",
    ),
    Parameter(
        "missing_files",
        default=[],
        description="""
 			List of any files missing that are required for all tests.
            Is filled in by the `copy_files_to_temp_directory` function.
 		""",
    ),
    """
		### Parameters specifying resource limits for test
		
		Resource limits are mostly implemented on via `setrlimit` and more information can be found in its documentation.
	
		If a resource limit is exceeded, the test is failed with an explanatory message.
	""",
]


def finalize_max_bytes(name, value, parameters):
    stream = name.replace("max_", "").replace("_bytes", "")
    expected_stream = "expected_" + stream
    len_expected = len(parameters.get(expected_stream, ""))
    if value is None:
        return max(min(10000000, 10 * len_expected), 10000, 2 * len_expected)
    else:
        return max(len_expected, int(value))


PARAMETER_LIST += [
    Parameter(
        "max_stdout_bytes",
        finalize=finalize_max_bytes,
        description="""
			Maximum number of bytes that can be written to *stdout*.
			
		""",
    ),
    Parameter(
        "max_stderr_bytes",
        finalize=finalize_max_bytes,
        description="""
			Maximum number of bytes that can be written to *stderr*.
		""",
    ),
    Parameter(
        "max_real_seconds",
        default=lambda test: test["max_cpu_seconds"] * 20,
        required_type=int,
        description="""
			Maximum elapsed real time in seconds (0 for no limit).<br>
			If not specified, defaults to 20 *  **`max_cpu_seconds`**
		""",
    ),
    Parameter(
        "max_cpu_seconds",
        default=60,
        description="""
			Maximum CPU time in seconds (0 for no limit).
		""",
    ),
    Parameter(
        "max_core_size",
        default=0,
        description="""
			Maximum size of any core file written in bytes.  
		""",
    ),
    Parameter(
        "max_stack_bytes",
        default=32000000,
        description="""
			Maximum stack size in bytes.
		""",
    ),
    Parameter(
        "max_rss_bytes",
        default=100000000,
        description="""
			Maximum resident set size in bytes.
		""",
    ),
    Parameter(
        "max_file_size_bytes",
        default=8192000,
        description="""
			Maximum size of any file created in bytes.
		""",
    ),
    Parameter(
        "max_processes",
        default=4096,
        description="""
			Maximum number of processes the current process may create.
			Note: unfortunately this is total per user processes not child processes
		""",
    ),
    Parameter(
        "max_open_files",
        default=256,
        description="""
			Maximum number of files that can be simultaneously open
		""",
    ),
    " ## Parameters controlling comparison of expected to actual output",
    "These apply to comparision for stdout, stderr, and files",
    Parameter(
        "ignore_case",
        default=False,
        description="""
			Ignore case when comparing actual & expected output
		""",
    ),
    Parameter(
        "ignore_whitespace",
        default=False,
        description="""
			Ignore white space when comparing actual & expected output.
		""",
    ),
    Parameter(
        "ignore_trailing_whitespace",
        default=True,
        description="""
			Ignore white space at end of lines when comparing actual & expected output.
		""",
    ),
    Parameter(
        "ignore_blank_lines",
        default=False,
        description="""
			Ignore lines containing only white space when comparing actual & expected output.
		""",
    ),
    Parameter(
        "ignore_characters",
        default="",
        finalize=lambda name, value, parameters: "".join(
            set(value + string.whitespace if parameters["ignore_whitespace"] else value)
            - set("\n")
        ),
        description="""
			Ignore these characters when comparing actual & expected output.<br>
			Ignoring "\n" has no effect, use **`ignore_blank_lines**` to ignore empty lines.<br>
			Unimplemented: handling of UNICODE. 
		""",
    ),
    Parameter(
        "compare_only_characters",
        description="""
			Ignore all but these characters and newline when comparing actual & expected output.<br>
			Unimplemented: handling of UNICODE. 
		""",
    ),
    Parameter(
        "postprocess_output_command",
        finalize=finalize_command,
        description="""
			Pass expected and actual output through this command before comparison.<br>
			If **`command`** is a string, it is passed to a shell.<br>
			If it is a list it is executed directly.
		""",
    ),
    Parameter(
        "allow_unexpected_stderr",
        default=False,
        description="""
			Do not fail a test if there is unexpected output on stderr but other expected outputs are correct.<br>
			This means warning messages don't cause a test to be failed.
		""",
    ),
    "### Parameters controlling information printed about test",
    Parameter(
        "colorize_output",
        default=lambda parameters: sys.stdout.isatty(),
        required_type=bool,
        description="""
			If true highlight parts of output using ANSI colour sequences.
			Default is true if stdout is a terminal
		""",
    ),
]


def default_description(parameters):
    command = parameters["command"]
    if isinstance(command, list):
        return " ".join(drepr(p) for p in command)
    else:
        return drepr(command)


def drepr(s):
    if isinstance(s, str) and s.isascii() and s.isprintable() and " " not in s:
        return s
    else:
        return repr(s)


PARAMETER_LIST += [
    Parameter(
        "description",
        default=default_description,
        description="""
			String describing test printed with its execution - defaults to **`command`**.
		""",
    ),
    Parameter(
        "show_actual_output",
        default=True,
        description="""
			If true, the actual output is included in a test failure explanation.
		""",
    ),
    Parameter(
        "show_expected_output",
        default=True,
        description="""
			If true, the expected output is included in a test failure explanation.
		""",
    ),
    Parameter(
        "show_diff",
        default=True,
        description="""
			If true, a description of the difference between expected output  is included in a test failure explanation.
		""",
    ),
    Parameter(
        "show_stdout_if_errors",
        default=False,
        description="""
			Unless true the actual output is not included in a test failure explanation, when there are unexpected bytes on stderr.
		""",
    ),
    Parameter(
        "show_reproduce_command",
        default=True,
        description="""
			If  true the command to reproduce the test is included  in a test failure explanation
		""",
    ),
    Parameter(
        "show_compile_command",
        default=True,
        description="""
			If true the command to compile the binary for a test is included in the test output.
		""",
    ),
    Parameter(
        "show_stdin",
        default=True,
        description="""
			If  true the stdin is included  in a test failure explanation
		""",
    ),
    Parameter(
        "max_lines_shown",
        default=32,
        description="""
			Maximum lines included in components of test explanations.
			Any further lines are elided.
			Likely to be replaced with improved controls.
		""",
    ),
    Parameter(
        "max_line_length_shown",
        default=1024,
        description="""
			Maximum line lengths included in components of text explanations.
			Any further characters are elided.
		""",
    ),
    Parameter(
        "no_replace_semicolon_reproduce_command",
        default=False,
        description="""
			If true semicolons are not replaced with newlines in the command to reproduce the test if it is included  in a test failure explanation.<br>
			Likely to be replaced with improved controls.
		""",
    ),
]


def finalize_dcc_output_checking(name, value, parameters):
    # dcc_output_checking explicitly set to False
    if value is not None and not value_to_bool(value):
        return False

    # dcc_output_checking not explicitly set
    # so use it if it is a simple execution of a C binary
    # more tests needed here
    if (value is None or value == "" or str(value).lower()[0:1] == "a") and (
        len(parameters["files"]) != 1
        or not parameters["files"][0].endswith(".c")
        or parameters["expected_stderr"]
        or parameters.get("postprocess_output_command", "")
        or parameters.get("compiler_args", "")
        or (
            isinstance(parameters["command"], str)
            and (set(";|&") & set(parameters["command"]))
        )
    ):
        return False

    for (
        p
    ) in "expected_stdout ignore_case compare_only_characters ignore_characters ignore_trailing_whitespace ignore_whitespace ignore_blank_lines max_stdout_bytes".split():
        dcc_equivalent = "DCC_" + p.upper().replace("WHITESPACE", "WHITE_SPACE")
        value = str(parameters.get(p, ""))
        parameters["environment"][dcc_equivalent] = value

    return True


PARAMETER_LIST += [
    Parameter(
        "dcc_output_checking",
        finalize=finalize_dcc_output_checking,
        description="""
			Use dcc's builtin output checking to check for tests's expected output.
			This is done by setting several environment variables for the test 
		""",
    ),
    "### Miscellaneous parameters",
    Parameter(
        "upload_url",
        default="",
        description="""
			Files tested and the output of the tests are uploaded to this URL using a POST request.<br>
			No more than **`upload_max_bytes`** will be uploaded.
			Any field/values specified in  **`upload_max_bytes`** will be included in the POST request.
			In addition 'exercise', 'hostname' and 'login' fields are included in the POST request.<br>
			A zip archive containing the files tested is passed as the field **`zip`**.<br>
			This zip archive includes the output of the test in a file named **`autotest.log`**.<br>
			Only one upload is done for all tests.  This parameter must be set as a global parameter.
		""",
    ),
    Parameter(
        "upload_max_bytes",
        default=2048000,
        description="""
			Maximum number of bytes uploaded if **`upload_url** is set.
		""",
    ),
    Parameter(
        "upload_fields",
        default={},
        finalize=finalize_dict_of_strings,
        description="""
			Any specified fields/values are added to upload requests.
		""",
    ),
    Parameter(
        "debug",
        default=0,
        description="""
			Level of internal debugging output to print.
		""",
    ),
]


PARAMETERS = collections.OrderedDict(
    (bv.name, bv) for bv in PARAMETER_LIST if isinstance(bv, Parameter)
)


PARAMETER_ALIASES = {
    # backwards compatibility & typo-squatting
    "ignore_white_space": "ignore_whitespace",
    "ignore_trailing_white_space": "ignore_trailing_whitespace",
    "prediff_filter": "postprocess_output_command",
    "post_process_output_command": "postprocess_output_command",
    "use_dcc_output_checking": "dcc_output_checking",
    "cpu": "max_cpu_seconds",
    "max_cpu": "max_cpu_seconds",
    "max_wall_clock": "max_real_seconds",
    "timeout": "max_real_seconds",
    "colorise_output": "colorize_output",
}


# backwards compatibility only
NEGATED_PARAMETER_ALIASES = {
    "no_reproduce_command": "show_reproduce_command",
    "no_expected_output": "show_expected_output",
    "no_actual_output": "show_actual_output",
    "no_diff": "show_diff",
}


def normalize_parameters(parameters, check_required_parameters_set=True, debug=0):
    """
    check supplied parameters values appropriate
    and add default values for parameters which haven't been supplied
    """
    error_prefix = (
        f"{parameters.get('_source_name', '?')}:{parameters.get('_line_number', '?')}"
    )
    try:
        normalize_parameters1(
            parameters,
            check_required_parameters_set=check_required_parameters_set,
            debug=debug,
        )
    except TestSpecificationError as e:
        raise TestSpecificationError(f"{error_prefix}: {e}")
    except Exception as e:
        raise TestSpecificationError(f"{error_prefix}: {e}")


def normalize_parameters1(parameters, check_required_parameters_set=True, debug=0):
    set_parameter_aliases(parameters, debug=debug)
    # this allows these to be used in calculated default values or finalize
    coerce_parameter_types(parameters, debug=debug)
    set_parameter_constant_defaults(parameters, debug=debug)
    set_parameter_calculated_defaults(parameters, debug=debug)
    finalize_parameters(parameters, debug=debug)
    if check_required_parameters_set:
        check_parameters_set(parameters, debug=debug)


def set_parameter_aliases(parameters, debug=0):
    """
    set any any parameters which have non-calculated default values
    """
    for (parameter, value) in list(parameters.items()):
        alias = PARAMETER_ALIASES.get(parameter, "")
        if alias:
            if alias[0] == "?":
                continue
                raise TestSpecificationError(
                    f"error - not yet implemented parameter '{parameter}'"
                )
            parameters[alias] = value
        negated_alias = NEGATED_PARAMETER_ALIASES.get(parameter, "")
        if negated_alias:
            parameters[negated_alias] = not value


def set_parameter_constant_defaults(parameters, debug=0):
    for p in PARAMETERS.values():
        if (
            p.name not in parameters
            and p.default is not None
            and not callable(p.default)
        ):
            parameters[p.name] = copy.deepcopy(p.default)


def set_parameter_calculated_defaults(parameters, debug=0):
    for p in PARAMETERS.values():
        if p.name not in parameters:
            default = p.get_default(parameters)
            if default is not None:
                parameters[p.name] = default


def coerce_parameter_types(parameters, debug=0):
    for p in PARAMETERS.values():
        if p.name in parameters:
            value = parameters[p.name]
            if p.required_type is not None and not isinstance(value, p.required_type):
                try:
                    if p.required_type == list:
                        new_value = [value]
                    elif p.required_type == bool:
                        new_value = value_to_bool(value)
                    else:
                        new_value = p.required_type(value)
                    parameters[p.name] = new_value
                except (ValueError, TypeError):
                    raise TestSpecificationError(
                        f"error invalid value for parameter '{p.name}': {value}"
                    )


def value_to_bool(value):
    return bool((value[0:1] not in "0fF") if isinstance(value, str) else value)


def finalize_parameters(parameters, debug=0):
    for p in PARAMETERS.values():
        if p.finalize:
            value = p.finalize(p.name, parameters.get(p.name, None), parameters)
            if value is not None:
                parameters[p.name] = value


def check_parameters_set(parameters, debug=0):
    for p in PARAMETERS.values():
        if p.must_be_set and not parameters[p.name]:
            raise TestSpecificationError(f"required parameter '{p.name}' not specified")


def check_valid_parameter_name(name):
    return (
        name in PARAMETERS
        or name in PARAMETER_ALIASES
        or name in NEGATED_PARAMETER_ALIASES
        or (name.startswith("_") and not name.startswith("__"))
        or name == "label"
    )


def print_markdown():
    print("<!--- start - autogenerated from parameter_descriptions.py --->")
    for p in PARAMETER_LIST:
        if isinstance(p, Parameter):
            if p.show_in_documentation:
                print(p.description_markdown())
        else:
            print(re.sub(r"^[ \t]*", "", p, flags=re.MULTILINE) + "\n")
    print("<!--- end - autogenerated from parameter_descriptions.py --->")


if __name__ == "__main__":
    print_markdown()
