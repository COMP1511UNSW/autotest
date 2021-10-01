Autotest runs a series of tests on 1 or more programs comparing their behaviour to specified expected behaviour.

Autotest focuses on producing output comprehensible to a novice programmer
perhaps in their first coding course.

The autotest syntax is designed to allow tests to be specified quickly and concisely.

Tests are typically specified in a single file named by default *tests.txt*.

Autotest syntax is designed to allow succinct convenient specification of tests, e.g.:

```
files=is_prime.c

1 stdin="39" expected_stdout="29 is not prime\n"
2 stdin="42" expected_stdout="42 is not prime\n"
3 stdin="47" expected_stdout="47 is prime\n"
```

## Running Autotest

Autotest allows flexible specification of command line arguments, so it can be comfortable
used by novices who little experience with command-line programs.

Autotest will typically be run via a wrapper shell script which
specifies arguments and parameters values appropriate for a class, for example,
specifiying the base directory to search for autotests, e.g:


```bash
#!/bin/sh

parameters="$parameters
	default_compilers = {'c' : [['clang', '-Werror', '-std=gnu11', '-g', '-lm']]}
	upload_url = $autotest_upload_url
	upload_fields = {'zid' : '$LOGNAME'} 
"

exec /usr/local/autotest/autotest.py --exercise_directory /home/class/activities --parameters "$parameters" "$@"
```

Students can then run the wrapper script simply specifying  the particular class exercise they wish to
autotest, perhaps:

```bash
$ autotest.sh is_prime
```

Some useful command-line options are useful when developing test specifications, include:

**-a AUTOTEST_DIRECTORY, --autotest_directory AUTOTEST_DIRECTORY** specify directly the location
of the autotest specification.

**-D DIRECTORY, --directory DIRECTORY** copty files in the specified to the test directory.

**-g, --generate_expected_output** generate expected output for the tests
by executing the supplied files.

for example, this will update the test specification in the directory  `my_autotest` using a 
model solution in `my_solution`

```bash
$ autotest.py --generate_expected_output=update --directory my_solution  --autotest_directory my_autotest
```


## Test Execution Environment

A temporary directory is created for autotests and the program to be 
tested is copied there and compiled there if needed.

By default any other files in the test specification directory are also
copied to the temporary directory (see the `supplied_files_directory` parameter)

By default tests are executed in an environment stripped of most environment variables
but this can be specified with test parameters.

By default tests are executed with resource limits which can be specified with test parameters.


## Tests

A test consists of a label and set of parameter value.

Every test must have a unique label consisting of alphanumeric characters and underscore ([a-zA-Z0-9_]+)

The file is read sequentially and when a test label is reached 
a test is created with the current values of parameters.

Assignments to parameter values apply to any following test or until
a different value is assigned to the parameter.

Except assignments to parameter values on the same line as a test label
are used only for that test. For example in the follow example
the CPU limit for *test1* is 5 seconds and the CPU limit for *test2* is 10 seconds.

```
max_cpu_seconds=10

test1  max_cpu_seconds=5  command=./prime.py 41  expected_stdout="True\n"

test2  command=./prime.py 42  expected_stdout="False\n"
```

If a command is a single string it is passed to a shell for evaluation

A test label may be used multiple times to supply the value of different parameters for the test.

```
max_cpu_seconds=10
program=prime.py

test1  max_cpu_seconds=5  arguments=41 expected_stdout="True\n"

test2  arguments=42  expected_stdout="False\n"
```


## Parameter Assignments

Tests are specified by assigning values to parameters, for example:

```
max_cpu_seconds=10
```

Parameter names  start with an alphabetic letter and can contain
alphanumeric characters and underscore ([a-zA-Z0-9_]+)

The values assigned to parameters can use Python syntax including single-quotes,
double-quotes, triple-quotes and raw-quotes and f-strings.
Values can also be lists or dicts specified in Python syntax.

Triple-quoted strings, lists and dicts can be multi-line.

Parameters specified in previous lines are available as parameters in f-strings.
Parameters specified on the current line are not available in the evaluation of f-strings.

Assignment to a parameter name which is not a builtin parameter listed in the section below,
will produce an error unless the parameter name begins with a single '_'.
Parameter names begining with '_' can be given values to be used in later f-strings.


For convenience, values can also be written as shell-like unquoted strings 
if they contain only non-whitespace ASCII and none of these characters **\\ = [ ] { } " ' **.
So for example, these are equivalent parameter assignments.

```
command=./a.out
command="./a.out"
```

Multiple unquoted strings are aggregated into a list so these are equivalent commands:

```
command=./a.out --print example.txt
command=['./a.out', '--print', 'example.txt']
```

Parameter values are coerced to an appropriate type if possible.
If a boolean type is expected, values are converted to be True or False following
Python rules, so for example . **`0  '' [] {}`** will all become  **`False`**,
except strings with a first characters of '0', 'f' or 'F' are considered  **`False`**

## Examples

```Python
files=prime.c

# specifying command-lines arguments
test1  arguments=41  expected_stdout="41 is prime.\n"

# specifying stdin
test2  stdin="42"  expected_stdout="42 is not prime.\n"

# using files to specify stdin and expected_stdout
test3  stdin=['43.txt']  expected_stdout=['43_expected_output.txt']

# running a  Shell command
test4 command="echo 44 | prime"   expected_stdout="44 is not prime.\n"

# using two line to specifiy test plus triple-quote for a multi-line string
test5  arguments=45
test5  expected_stdout="""45 is not prime.
"""

# specify more flexibility in test acceptance
# by ignoring white space, some punctuation characters (",.!") and extra new lines
test6  ignore_whitespace=True  ignore_blank_lines=True  ignore_characters=",.!"
test6  arguments=46  expected_stdout="46 is not prime.\n"


# make test succeed if it has just right digits in output
test7  arguments=47 compare_only_characters="0123456789" expected_stdout="47 is not prime.\n"
```



## Test Parameters

<!--- start - autogenerated from parameter_descriptions.py --->
### Parameters specifying command to be run

**`program`**


The name of script/binary to run for this test.  
If **`program`** is not specified it is heuristically inferred from **`command`**, if possible.

**`arguments`** = \[\]


Command-line arguments for this test. Used only if **`command`** is not specified.

**`command`**


Command to run for this test.

If **`command`** is a string, it is passed to a shell.  
If **`command`** is a list, it is executed directly.  
If **`command`** is not specified and **`program`** is specified,
**`command`** is set to a list containing **`program`** with **`arguments`** appended.  
Otherwise **`command`** is inferred heuristically from the first filename
specified in  by parameter **`files`**, if possible.


### Parameters specifying files needed for for a test

**`files`**


Input files required to be supplied for a test.  
If **`files`** is not specified it is set to the parameter **`program`**
with a `.c`  appended iff **`program`** does not contain a '.'.  
For example if **`files`** is not specified and **`program`** == **`hello`**, **`files`** will be set to `hello.c`,
but if **`program`** == `hello.sh` **`files`** will be set to `hello.c` 

**`optional_files`** = \[\]


Input files which may be optionally supplied for a test.

### Parameters specifying actions performed prior to test

**`check_hash_bang_line`** = True


Check Perl, Python, Shell scripts have appropriate #! line.

**`pre_compile_command`**


If set **`pre_compile_command`** is executed once before compilation.  
This is invisible to the user, unless **`pre_compile_command`** produces output.  
Compilation does not occur if **`pre_compile_command`** has a non-zero exit-status.  
If **`pre_compile_command`** is a string, it is passed to a shell.  
If **`pre_compile_command`** is a list, it is executed directly.

**`default_checkers`** = {'js': \[\['node', '--check'\]\], 'pl': \[\['perl', '-cw'\]\], 'py': \[\['python3', '-B', '-m', 'py_compile'\]\], 'sh': \[\['bash', '-n'\]\]}


A dict which supplies a default value for the parameter **`checkers`** based on the suffix for the 
the first file specified by  the parameter **`files`**.

**`checkers`**


List of checkers.  Each checker is run once for each file supplied for a test.  The filename is appended as argument.  
Checkers are only run once for a file.  
If checker is a string it is run by passing it to a shell.
Deprocated: if the value is a string containing ':' a list is formed by splitting the string at the ':'s.

**`default_compilers`** = {'c': \[\['dcc', '-o', '%'\]\], 'cc': \[\['g++', '-o', '%'\]\], 'java': \[\['javac'\]\], 'rs': \[\['rustc'\]\]}


A dict which supplies a default value for the parameter **`compilers`** based on the suffix for the 
the first file specified by  the parameter **`files`**.
If '%' is present in a list, it is replaced by the **`program`**.

**`compilers`**


List of compilers + arguments.  
**`files`** are compiled with each member of list and test is run once for each member of the list.  
If compiler is a string it is run by passing it to a shell.  
Deprocated: if the value is a string containing ':' a list is formed by splitting the string at the ':'s.

**`compiler_args`** = \[\]


"List of arguments strings added to every compilation"

**`compile_commands`**


List of compile commands.  
Test is run once for each member of the list.  
If command is a string it is run by passing it to a shell.  
**`compile_commands`** is not normally set directly.  
If not set, it is formed from **`compilers`** and **`compiler_args`** and **`files`**.  
In most cases, set these parameters will be more appropriate.

**`setup_command`**


If set **`setup_command`** is executed once before a test.  
This is invisible to the user, unless **`setup_command`** produces output.  
The test is not run if  **`setup_command`** has a non-zero exit-status.  
If **`setup_command`** is a string, it is passed to a shell.
If **`setup_command`** is a list, it is executed directly.

### Parameters specifying inputs for test

**`supplied_files_directory`**


If set to a non-empty string, any files in this directory are copied to the directory before testing.  
This directory is also prepended to any relative file pathnames in test specifications.  
Its default value is the directory containing the test specification file (`tests.txt`).  
Only one directory is copied for all tests.  This parameter must be set as a global parameter.
It is usually specified in a wrapper shell script via -P.

**`stdin`**


Bytes supplied on stdin for test.  
Deprocated: stdin is not specified and the file *test_label*`.stdin` exists, its contents are used.  
Not yet implemented: if value is a list it is treated as list of pathname of file(s) containing bytes.

**`environment_kept`** = 'ARCH|C_CHECK_.*|DCC_.*|DRYRUN_.*|LANG|LANGUAGE|LC_.*'


Environment variables are by default deleted to avoid them affecting testing.  
Environment variables whose entire name matches this regex are not deleted.  
All other environment variables are deleted.

**`environment_base`**


Dict specifying values for environment variables.  
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

**`environment_set`** = {}


Dict specifying environment variables to be set for this test.  
For example: `environment_set={'answer' : 42 }`  
This is the parameter that should normally be used to manipulate environment variables. 

**`environment`**


Dict specifying all environment variables for this test.  
This parameter should not normally be specified,
**`environment_set`** will serve most purposes.  
By default **`environment`** is formed by taking original environment variables provided to autotest,  
removing all but those matching the regex in **`environment_variables_kept`**,  
setting any variables specified in **`environment_base`** and then  
setting any variables specified in **`environment_set`**.


### Parameters specifying expected output for test

**`expected_stdout`**


Bytes expected on stdout for this test.  
If value is a list it is treated as list of pathname of file(s) containing expected bytes.  
Deprocated: if **`expected_stdout`** is not specified and the file *test_label*`.expected_stdout` exists,
its contents are used.  
Not yet implemented: handling of non-unicode output.  

**`expected_stderr`**


Bytes expected on stderr for this test.  
If value is a list it is treated as list of pathname of file(s) containing expected bytes.  
Deprocated: if **`expected_stderr`** is not specified and the file *test_label*`.stderr` exists,
its contents are used.  
Not yet implemented: handling of non-unicode output.

**`expected_file_name`** = ''


Pathname of file expected to exist after this test.  
Expected contents specified by **`expected_file_contents`**.  
Use **`expected_files`** to specify creation of multiple files.

**`expected_file_contents`** = ''


Bytes expected in file **`expected_file_name`**  expected to exist after this test.  
Not yet implemented: handling of non-unicode output.

**`expected_files`** = {}


Dict specified bytes expected to be written to  files.  
if value is an string, it is specifies bytes expected to be written to that filename.  
If a value is a list it is treated as list of pathname of file(s) containing expected bytes.  
For example: this indicates `a file named `answer.txt` should be created containing `42`.
`expected_files={"answer.txt":"42
"}`  

Not yet implemented: handling of non-unicode output.  



### Parameters specifying resource limits for test

Resource limits are mostly implemented on via `setrlimit` and more information can be found in its documentation.

If a resource limit is exceeded, the test is failed with an explanatory message.


**`max_stdout_bytes`**


Maximum number of bytes that can be written to *stdout*.


**`max_stderr_bytes`**


Maximum number of bytes that can be written to *stderr*.

**`max_real_seconds`**


Maximum elapsed real time in seconds (0 for no limit).  
If not specified, defaults to 20 *  **`max_cpu_seconds`**

**`max_cpu_seconds`** = 60


Maximum CPU time in seconds (0 for no limit).

**`max_core_size`** = 0


Maximum size of any core file written in bytes.  

**`max_stack_bytes`** = 32000000


Maximum stack size in bytes.

**`max_rss_bytes`** = 100000000


Maximum resident set size in bytes.

**`max_file_size_bytes`** = 8192000


Maximum size of any file created in bytes.

**`max_processes`** = 4096


Maximum number of processes the current process may create.
Note: unfortunately this is total per user processes not child processes

**`max_open_files`** = 256


Maximum number of files that can be simultaneously open

## Parameters controlling comparison of expected to actual output

These apply to comparision for stdout, stderr, and files

**`ignore_case`** = False


Ignore case when comparing actual & expected output

**`ignore_whitespace`** = False


Ignore white space when comparing actual & expected output.

**`ignore_trailing_whitespace`** = True


Ignore white space at end of lines when comparing actual & expected output.

**`ignore_blank_lines`** = False


Ignore lines containing only white space when comparing actual & expected output.

**`ignore_characters`** = ''


Ignore these characters when comparing actual & expected output.  
Ignoring "
" has no effect, use **`ignore_blank_lines**` to ignore empty lines.  
Unimplemented: handling of UNICODE. 

**`compare_only_characters`**


Ignore all but these characters and newline when comparing actual & expected output.  
Unimplemented: handling of UNICODE. 

**`postprocess_output_command`**


Pass expected and actual output through this command before comparison.  
If **`command`** is a string, it is passed to a shell.  
If it is a list it is executed directly.

**`allow_unexpected_stderr`** = False


Do not fail a test if there is unexpected output on stderr but other expected outputs are correct.  
This means warning messages don't cause a test to be failed.

### Parameters controlling information printed about test

**`colorize_output`**


If true highlight parts of output using ANSI colour sequences.
Default is true if stdout is a terminal

**`description`**


String describing test printed with its execution - defaults to **`command`**.

**`show_actual_output`** = True


If true, the actual output is included in a test failure explanation.

**`show_expected_output`** = True


If true, the expected output is included in a test failure explanation.

**`show_diff`** = True


If true, a description of the difference between expected output  is included in a test failure explanation.

**`show_stdout_if_errors`** = False


Unless true the actual output is not included in a test failure explanation, when there are unexpected bytes on stderr.

**`show_reproduce_command`** = True


If  true the command to reproduce the test is included  in a test failure explanation

**`show_compile_command`** = True


If true the command to compile the binary for a test is included in the test output.

**`show_stdin`** = True


If  true the stdin is included  in a test failure explanation

**`max_lines_shown`** = 32


Maximum lines included in components of test explanations.
Any further lines are elided.
Likely to be replaced with improved controls.

**`max_line_length_shown`** = 1024


Maximum line lengths included in components of text explanations.
Any further characters are elided.

**`no_replace_semicolon_reproduce_command`** = False


If true semicolons are not replaced with newlines in the command to reproduce the test if it is included  in a test failure explanation.  
Likely to be replaced with improved controls.

**`dcc_output_checking`**


Use dcc's builtin output checking to check for tests's expected output.
This is done by setting several environment variables for the test 

### Miscellaneous parameters

**`upload_url`** = ''


Files tested and the output of the tests are uploaded to this URL using a POST request.  
No more than **`upload_max_bytes`** will be uploaded.
Any field/values specified in  **`upload_max_bytes`** will be included in the POST request.
In addition 'exercise', 'hostname' and 'login' fields are included in the POST request.  
A zip archive containing the files tested is passed as the field **`zip`**.  
This zip archive includes the output of the test in a file named **`autotest.log`**.  
Only one upload is done for all tests.  This parameter must be set as a global parameter.

**`upload_max_bytes`** = 2048000


Maximum number of bytes uploaded if **`upload_url** is set.

**`upload_fields`** = {}


Any specified fields/values are added to upload requests.

**`debug`** = 0


Level of internal debugging output to print.

<!--- end - autogenerated from parameter_descriptions.py --->



## Debugging Autotest

The command line parameter -d/--debug set increasing levels of debug output. 

This can also be done using the environmental variable **`AUTOTEST_DEBUG`** 
Python stack backtraces are only shown if **`AUTOTEST_DEBUG`** is set to a non-zero integer.

