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
#execute cat examples/wrapper.sh
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
#execute cat examples/simple_C/tests.txt
```



## Test Parameters

#execute parameter_descriptions.py



## Debugging Autotests

The command line parameter -d/--debug set increasing levels of debug output.

This can also be done using the environmental variable **`AUTOTEST_DEBUG`**
Python stack backtraces are only shown if **`AUTOTEST_DEBUG`** is set to a non-zero integer.



## Embedding Autotests

The script *`bundle_autotests.sh`* generates a single executable for autotest.

It can also embed specified autotests in the executable allowing
distribution of a single file containing a set of autotests and the code to run them.


```
$ bundle_autotests.sh my_autotest exercise1/autotest exercise2/autotest exercise3/autotest
$ ./my_autotest exercise2
Test 0 (./prime 42) - passed
1 tests passed 0 tests failed
```
