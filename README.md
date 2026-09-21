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

Autotest allows flexible specification of command line arguments, so it can be comfortably
used by novices who have little experience with command-line programs.

Autotest will typically be run via a wrapper shell script which
specifies arguments and parameters values appropriate for a class, for example,
specifying the base directory to search for autotests, e.g:


```bash
#!/bin/sh

# Marking wrappers should require the sandbox: with sandbox = True autotest
# stops on a host which can not create it instead of running student code
# unconfined.  Leave it out (the default is auto) for a wrapper students run
# on their own work.
#
# parallel_tests = 0 runs one test per CPU (or use -j N on the command line).

parameters="
	default_compilers = {'c' : [['clang', '-Werror', '-std=gnu11', '-g', '-lm']]}
	upload_url = https://example.com/autotest.cgi
	sandbox = True
	# parallel_tests = 0
"

exec /usr/local/autotest/autotest.py --exercise_directory /home/class/activities --parameters "$parameters" "$@"
```

Students can then run the wrapper script simply specifying  the particular class exercise they wish to
autotest, perhaps:

```bash
$ autotest.sh is_prime
```

Some command-line options useful when developing test specifications include:

**-a AUTOTEST_DIRECTORY, --autotest_directory AUTOTEST_DIRECTORY** specify directly the location
of the autotest specification.

**-D DIRECTORY, --directory DIRECTORY** copy files in the specified directory to the test directory.

**--lint** report commands the specification names (`checkers`, `setup_command`,
`pre_compile_command`, `postprocess_output_command`) which can not be run, without
running any test.  Nothing is copied and nothing is executed, so it can be run over a
whole course tree; it exits non-zero if it found anything.

**--stats** print what each test cost: peak memory and wall clock
(sets the parameter **`report_resource_usage`**).

**--check_stability=N** run each test N times (default 2) and report any test whose
result is not the same every time (sets the parameter **`stability_runs`**).
A test which does not reach the same result twice is marking students on a coin flip.

**--json FILE** write a machine-readable description of the run to FILE (`-` for stdout):
a versioned document with a summary and one record per test.  Nothing is written if no
test ran.

**-g, --generate_expected_output** generate expected output for the tests
by executing the supplied files.

for example, this will update the test specification in the directory  `my_autotest` using a
model solution in `my_solution`

```bash
$ autotest.py --generate_expected_output=update --directory my_solution  --autotest_directory my_autotest
```


## Test Execution Environment

A temporary directory is created for each run of autotest.
The program to be tested (the submission) is copied there first,
then any other files in the test specification directory are copied over it
(see the `supplied_files_directory` parameter).
Supplied files win, so a submission can not replace a file the autotest relies on,
such as a checker, an expected output file or a supplied header file.
Programs are compiled in this directory if needed.

Each test then runs in its own copy of this directory,
so a test never sees files created or modified by an earlier test.
Anything a test needs which is not in the test specification directory
must be created by its `setup_command`, unless the specification sets
`shared_test_directory`, which runs every test in one directory as earlier versions did
(those tests then can not run concurrently).

Tests can be run in parallel by setting the parameter `parallel_tests`
or with the command-line option `-j N`/`--jobs N` (`0` means one test per CPU).
Output is printed in test order regardless of the order in which tests finish.

By default tests are executed in an environment stripped of most environment variables
but this can be specified with test parameters (see `environment_base`, `environment_set`, ...).
The default `PATH` is `/bin:/usr/bin:/usr/local/bin:$PATH:.` - the current directory is searched last.

By default tests are executed with resource limits which can be specified with test parameters
(`max_cpu_seconds`, `max_rss_bytes`, `max_stdout_bytes`, ...).
A limit of `0` means no limit, except that **`max_stdout_bytes`** and **`max_stderr_bytes`**
are never set below the length of the expected output, and `max_core_size` (default `0`) means no core file.

### Sandbox

Every command which executes student code - the test `command` and, by default,
compilers, checkers, `pre_compile_command`, `setup_command` and `postprocess_output_command`
(see `sandbox_support_commands`) - runs in an unprivileged Linux user-namespace sandbox.
No root privileges or setuid helper are needed: the sandbox is built from
what an unprivileged process is allowed to do on Linux.
It adds about 10 ms to each command.

The global parameter `sandbox` has three settings:

* `auto` (the default): the sandbox is used when the host supports it,
  otherwise a one-line warning is printed and programs run without a sandbox.
* a true value (`1`, `yes`, `required`, ...): the sandbox is required,
  autotest refuses to run if it can not be created.
  Marking wrappers should set `sandbox = True`.
* a false value (`0`, `no`, `off`, ...) or the command-line option `--no_sandbox`:
  no sandbox, programs run with all the privileges of the user running autotest.
  `--no_sandbox` is refused if `sandbox` was set true with `-P`, so a marking wrapper which
  passes `sandbox = True` and forwards its arguments can not be talked out of its sandbox.

Inside the sandbox:

* the uid and gid are those of the user running autotest (it does not appear to be root);
* the system directories `/bin`, `/etc`, `/lib*`, `/opt`, `/sbin` and `/usr` are visible read-only
  (see `sandbox_read_only_mount_base` and `sandbox_read_only_mount`);
* the test directory is the current directory and is read-write
  (more directories can be made visible with `sandbox_read_write_mount`);
* nothing else on the host exists: not the home directory of the user running autotest,
  not `/home`, `/var`, `/root` or `/run`;
* `HOME` and `PATH` are exactly what the `environment_*` parameters specify;
* `/tmp` and `/dev/shm` are private in-memory directories of limited size
  (`sandbox_tmp_bytes`, `sandbox_shm_bytes`);
* `/dev` is minimal (`null`, `zero`, `random`, ...) and `/proc` is fresh:
  only the processes of the command being run are visible;
* there is no network except loopback (unless `sandbox_network=False`);
* on x86_64 a seccomp filter denies creating nested user namespaces, `mount`, `setns`,
  `io_uring`, `bpf`, kernel module loading and similar system calls (`sandbox_seccomp`);
  debugging tools (`gdb`, `valgrind`, `strace`, dcc) still work;
* Landlock rules are applied as a backstop where the kernel supports them (`sandbox_landlock`);
* every process started by the command is killed when the command finishes.

The sandbox does not provide:

* CPU accounting beyond `setrlimit` (`max_cpu_seconds`, ...) - cgroups are not used.
  `max_rss_bytes` is enforced, but by autotest sampling the test's process group
  rather than by the kernel, so a program can briefly exceed it;
* a disk quota - the test directory is on the host's filesystem and `max_file_size_bytes` limits
  the size of each file but not their number, so a program can fill the disk (for the duration of its test:
  the directory is removed afterwards); only `/tmp` and `/dev/shm` are size-limited, and being in memory,
  `N` concurrent tests can use up to `N` x (`sandbox_tmp_bytes` + `sandbox_shm_bytes`) of RAM;
* isolation from the other processes and files of the user running autotest beyond what the kernel gives -
  the program can not see them by name, but it runs as that user
  and anything it can reach (the test directory, `sandbox_read_write_mount` directories) is written as that user.

Some hosts do not allow unprivileged user namespaces, so the sandbox can not be created:

* Docker containers with Docker's default seccomp profile;
* Ubuntu 24.04 and later with `kernel.apparmor_restrict_unprivileged_userns=1` (the default);
* some hardened kernels (`kernel.unprivileged_userns_clone=0` or `user.max_user_namespaces=0`).

On such a host `unshare -Ur true` fails with "Operation not permitted"; on a host where the sandbox works it succeeds silently.
With `sandbox=auto` autotest prints a warning explaining this and runs without a sandbox;
with `sandbox=True` it stops.

### Security model

Test specifications are trusted: `tests.txt` can run arbitrary commands
and its f-strings are evaluated as Python, with the privileges of the user running autotest and outside the sandbox.
A test specification must only ever come from staff, never from a submission.
Files in the autotest directory are copied over the submission, so a submission can not replace a checker,
an expected output file or anything else the autotest supplies.
A specification named `tests.txt` or `automarking.txt` is not copied: those two names are left behind,
so the program being tested is not handed every expected output,
and a student running an exercise's own tests is not handed its marking tests.
A specification given to `-a` under any other name is copied like any other file in its directory.
Everything else in the autotest directory is copied and can be read by the program being tested,
so a sample solution or anything else students should not see must be kept elsewhere.

Nothing here hides a specification from a student running autotest on their own account:
autotest reads it as them, so whatever it can read they can read.
What this changes is what the *program being tested* is handed, and what a marking run - which runs as an account
the student does not control - leaves within reach.
For that reason `dcc_output_checking` is off by default when marking (`-m`):
it passes the expected output to the test in `DCC_EXPECTED_STDOUT`, and a program can read its own environment.
A specification that wants dcc's diff while marking can set `dcc_output_checking=1`.

Submissions are not trusted. With the sandbox a submitted program can:

* run arbitrary code as the user running autotest, within the resource limits;
* read the system directories and read and write its copy of the test directory;
* fork, use threads, use loopback networking, and run debuggers on itself.

It can not:

* read or modify any other file of the user running autotest (or anybody else's) -
  a submission fetched with `--directory`, `--git` or `--tarfile` is copied without following
  symbolic links, so a link can not smuggle a file in.  Files copied by name from the current
  directory, when no submission option is given, do follow links, which is why a marking run
  must use one of those options;
* see, signal or trace processes outside the sandbox, including other tests;
* use the network;
* gain privileges via nested user namespaces, `mount` or `setns`;
* leave anything behind - the temporary directories are deleted when autotest finishes,
  and any process it started is killed.

With `sandbox=False` (or `--no_sandbox`) none of this holds: the submitted program has all the privileges
of the user running autotest, which is acceptable when students test their own work on their own account
but not for marking.
Marking wrappers should set `sandbox = True` so that a host which can not sandbox stops
instead of silently running student code unconfined,
and should run autotest as an account with no access to anything a student should not see.

### Changes in behaviour

For maintainers upgrading from an earlier version of autotest:

* `tests.txt` and `automarking.txt` are no longer copied into the test directory, so a checker
  or `setup_command` which read the specification from there no longer finds it;
* `dcc_output_checking` is off by default under `-m`, because it passes the expected output to the
  program in `DCC_EXPECTED_STDOUT`; a marking specification which wants dcc's diff must set
  `dcc_output_checking=1`;
* files supplied by the autotest now win over same-named submitted files in every submission mode
  (directory, `--stdin`, `--tarfile`, `--git`, ...); previously submitted files could replace them;
* each test runs in its own copy of the test directory - a test no longer sees files
  created by an earlier test unless its `setup_command` creates them;
* tests can run concurrently (`parallel_tests`, `-j`); output order is unchanged;
* programs which exit non-zero with no output are no longer silently re-run up to 3 times;
* the default `PATH` is now `/bin:/usr/bin:/usr/local/bin:$PATH:.` - the current directory is searched last;
* a resource limit of `0` (`max_cpu_seconds`, `max_real_seconds`, `max_rss_bytes`, ...) now really means no limit;
* `max_rss_bytes` now limits memory. Linux ignores `RLIMIT_RSS`, so it never did before,
  and a test whose program uses a lot of memory may fail where it used to pass.
  The default was raised to 1GB to leave room for the heaviest work seen in real
  course material; lower it for an exercise that has no reason to use much;
* the sandbox parameter `sandbox` defaults to `auto`; `sandbox_command` is deprecated and ignored; `--inside_sandbox` is gone;
* symbolic links in a submission given with `--directory` are copied as links, not followed
  (a link to a file outside the submission does not resolve inside the sandbox);
* `--gitlab_cse` and `--student` are gone: use `--git URL`; `--commit` requires `--git`;
* obsolete command-line options (`--colorize`, `--no_show_diff`, `--c_compilers`, ...) now stop with a message
  naming the parameter to use instead of being silently ignored;
* boolean parameter values `no`, `n`, `off` and `None` are now false (as well as `0`, `false` and the empty string);
* fixes: a bare `git@host:repo` argument is taken as the `--git` URL; `-m` with `-a DIR` finds `DIR/automarking.txt`;
  `%` in `compiler_args` is replaced by the program, as documented; `use Foo;` in a Perl submission copies `Foo.pm`;
  a `sandbox_read_only_mount` directory under `/tmp` is visible; on Python 3.12+ an f-string in `tests.txt` keeps
  its `{{` and the spaces inside `{...}`, and `F'...'` and `rf'...'` are f-strings too;
  a bundle asked for an exercise it does not embed says so instead of reporting an internal error.


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
Parameter names beginning with '_' can be given values to be used in later f-strings.


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
Python rules, so for example **`0  '' [] {}`** will all become  **`False`**,
except that the empty string, `off` and strings starting with `0`, `f`, `F`, `n` or `N` (such as `false`, `no`, `None`) are considered  **`False`**

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

Boolean parameters accept any value.
The empty string, and strings starting with `0`, `f` or `n` (`0`, `false`, `no`, `n`, ...)
and `off` (ignoring case) are false; any other string is true.


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
but if **`program`** == `hello.sh` **`files`** will be set to `hello.sh`

**`optional_files`** = \[\]


Input files which may be optionally supplied for a test.

### Parameters specifying actions performed prior to test

**`check_hash_bang_line`** = True


Check Perl, Python, Shell scripts have appropriate #! line.

**`pre_compile_command`**


If set **`pre_compile_command`** is executed before compilation, in
the test's own copy of the test directory.  
When the tests being run have different **`pre_compile_command`**s,
every distinct command appearing earlier in the specification runs
there first, so one command can run many times in a run: write it
so that running it again is harmless.  
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
Deprecated: if the value is a string containing ':' a list is formed by splitting the string at the ':'s.

**`default_compilers`** = {'c': \[\[\['dcc'\], \['clang', '-Wall'\], \['gcc', '-Wall'\]\]\], 'cc': \[\['g++', '-Wall'\]\], 'java': \[\['javac'\]\], 'rs': \[\['rustc'\]\]}


A dict which supplies a default value for the parameter **`compilers`** based on the suffix for the
the first file specified by  the parameter **`files`**.
If '%' is present in a list, it is replaced by the **`program`**.

**`compilers`**


List of compilers + arguments.  
**`files`** are compiled with each member of list and test is run once for each member of the list.  
For example, given:
```
# run all tests twice once compiled with gcc -fsanitize=address, once with clang -fsanitize=memory
compilers = [['gcc', '-fsanitize=address'], ['clang', '-fsanitize=memory']]
```
Element of the list of compilers can themselves be a list specifying a list of alternative compilers.  
For example:
```
# run all tests twice once compiled with gcc -fsanitize=address, once with clang -fsanitize=memory
compilers = [[['dcc'], ['clang', '-Wall'], ['gcc', -Wall]]]
```
The first element of this sub-list where the compiler can be found in PATH is used.  
If compiler is a string it is run by passing it to a shell.  
Deprecated: if the value is a string containing ':' a list is formed by splitting the string at the ':'s.

**`default_compiler_args`** = {'c': \[\['-o', '%'\]\], 'cc': \[\['-o', '%'\]\]}


A dict which supplies a default value for the parameter **`compilers`** based on the suffix for the
the first file specified by  the parameter **`files`**.
If '%' is present in a list, it is replaced by the **`program`**.

**`compiler_args`** = \[\]


"List of arguments strings added to every compilation"
If '%' is present in a list, it is replaced by the **`program`**.

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
The exit status of **`setup_command`** is ignored: the test is run regardless.  
It runs in the test's own copy of the test directory (and in the sandbox, see **`sandbox_support_commands`**).  
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
Deprecated: stdin is not specified and the file *test_label*`.stdin` exists, its contents are used.  
Not yet implemented: if value is a list it is treated as list of pathname of file(s) containing bytes.

**`unicode_stdin`** = True


Whether or not the specified stdin should be treated as unicode.
Default is True.

**`unicode_stdout`** = True


Whether or not the program's stdout should be treated as unicode.
Default is True.

**`unicode_stderr`** = True


Whether or not the program's stderr should be treated as unicode.
Default is True.

**`unicode_files`** = True


Describes whether or not output files should be treated as unicode.
Default is True.

**`environment_kept`** = 'ARCH|C_CHECK_.*|DCC_.*|DRYRUN_.*|LANG|LANGUAGE|LC_.*|LOGNAME|USER'


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
'PATH' : '/bin:/usr/bin:/usr/local/bin:$PATH:.',
},
```
where `$PATH` is the original value of `PATH`.  
The test directory (`.`) is searched last, so a file supplied for a test
can not shadow a program found elsewhere in `PATH`.

The environment  variables in **`environment_base`** are set and then,
environment  variables specified in **`environment_set`** are set.  
This parameter should not normally be used.  
The parameter **`environment_set`** should normally be used instead of this parameter.  
It is only necessary to specify **`environment_base`** if these variables need to be unset rather than given different values for a test.  

**`environment_set`** = {}


Dict specifying environment variables to be set for this test.  
For example: `environment_set={'answer' : 42 }`  
This is the parameter that should normally be used to manipulate environment variables.

**`environment`**


Dict specifying all environment variables for this test.  
This parameter should not normally be specified,
**`environment_set`** will serve most purposes.  
By default **`environment`** is formed by taking original environment variables provided to autotest,  
removing all but those matching the regex in **`environment_kept`**,  
setting any variables specified in **`environment_base`** and then  
setting any variables specified in **`environment_set`**.


### Parameters specifying expected output for test

**`expected_stdout`**


Bytes expected on stdout for this test.  
If value is a list it is treated as list of pathname of file(s) containing expected bytes.  
Deprecated: if **`expected_stdout`** is not specified and the file *test_label*`.stdout` exists,
its contents are used.  
Not yet implemented: handling of non-unicode output.  

**`expected_stderr`**


Bytes expected on stderr for this test.  
If value is a list it is treated as list of pathname of file(s) containing expected bytes.  
Deprecated: if **`expected_stderr`** is not specified and the file *test_label*`.stderr` exists,
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
A value below the length of **`expected_stdout`** is raised to it,
so `0` means no limit only when no output is expected.  
If not specified, a limit is chosen based on the size of **`expected_stdout`**.

**`max_stderr_bytes`**


Maximum number of bytes that can be written to *stderr*.  
A value below the length of **`expected_stderr`** is raised to it,
so `0` means no limit only when no output is expected.  
If not specified, a limit is chosen based on the size of **`expected_stderr`**.

**`max_real_seconds`**


Maximum elapsed real time in seconds (0 for no limit).  
If not specified, defaults to 20 *  **`max_cpu_seconds`**

**`max_cpu_seconds`** = 60


Maximum CPU time in seconds (0 for no limit).

**`max_core_size`** = 0


Maximum size of any core file written in bytes.

**`max_stack_bytes`** = 32000000


Maximum stack size in bytes (0 for no limit).

**`report_resource_usage`** = False


If true, autotest measures what each test cost and prints a table
after the results.  The command-line option is **`--stats`**.  
Peak memory is the largest total autotest saw across the test's
whole process group, which is the same quantity **`max_rss_bytes`**
is enforced against, so a number in this table can be pasted into
a specification.  
It is sampled five times a second, so a test which allocates and
exits between two samples reports less than it used, and a test
shorter than a fifth of a second reports 0.  
CPU time is not reported: the only ways to obtain it here are
process-wide, and would attribute other tests' work to this one
when tests run concurrently.  
The table is printed once for the run, so this must be set as a
global parameter: on a test line the test pays to be measured and
nothing is printed.

**`max_rss_bytes`** = 1000000000


Maximum resident memory in bytes for the test and everything it starts
(0 for no limit).

Unlike the other limits this one is not enforced by `setrlimit`:
Linux has ignored `RLIMIT_RSS` since 2.4, so for years this parameter
did nothing at all and a single program could exhaust a teaching
machine's memory.  Autotest now adds up the resident memory of the
test's process group a few times a second and stops it when it goes
over.  A program can therefore exceed the limit briefly before it is
stopped, and memory shared between a program and its children is
counted once for each of them.

The default is deliberately generous, because no existing test
specification sets this parameter and every test has until now
inherited a limit that did nothing.  It was chosen by measuring the
COMP1511, COMP1521 and COMP2041 activities: the heaviest legitimate
test found needs a little under 512MB to parse 100000 nested JSON
arrays, while the runaway that prompted the work reached 8GB.  Set
it lower for an exercise where a student's program has no reason to
use much memory.

**`max_file_size_bytes`** = 8192000


Maximum size of any file created in bytes (0 for no limit).

**`max_processes`** = 4096


Maximum number of processes the current process may create (0 for no limit).  
Note: unfortunately this is total per user processes not child processes

**`max_open_files`** = 256


Maximum number of files that can be simultaneously open (0 for no limit).

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

**`show_all_lines`** = False


If true lines are never elided.
Likely to be replaced with improved controls.

**`max_line_length_shown`** = 1024


Maximum line lengths included in components of text explanations.
Any further characters are elided.

**`no_replace_semicolon_reproduce_command`** = False


If true semicolons are not replaced with newlines in the command to reproduce the test if it is included  in a test failure explanation.  
Likely to be replaced with improved controls.

**`marking`** = False


True when autotest was run with **`-m`**, which selects a
specification's automarking tests.  
Set by the command line, not normally by a specification.  It
turns **`dcc_output_checking`** off by default, because that
parameter passes the expected output to the test in
`DCC_EXPECTED_STDOUT`, where the program being tested can read
it.

**`dcc_output_checking`**


Use dcc's builtin output checking to check for tests's expected output.
This is done by setting several environment variables for the test.  
If not set explicitly it is used only for a simple test of a single
`.c` file (no **`expected_stderr`**, **`compiler_args`** or
**`postprocess_output_command`**), and never when **`marking`** is
true, because it passes the expected output to the test in
`DCC_EXPECTED_STDOUT` where the program being tested can read it.  
A specification which wants dcc's diff while marking must set
`dcc_output_checking=1`.

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

### Parameters controlling sandboxing and parallelism

**`sandbox`** = 'auto'


Run each test (and, if **`sandbox_support_commands`** is true, compilers, checkers
and setup commands) inside an unprivileged Linux user-namespace sandbox.  
Inside the sandbox only system directories are visible (read-only, see **`sandbox_read_only_mount_base`**),
the test directory is read-write, `/tmp` is a private directory (see **`sandbox_tmp_bytes`**),
`/dev` is minimal, `/proc` is fresh, and there is no network access unless **`sandbox_network`** is false.  
Landlock and seccomp are used as backstops when available (see **`sandbox_landlock`** and **`sandbox_seccomp`**).  
`auto` uses the sandbox when the host supports it and prints a warning when it does not.  
A true value (`1`, `yes`, `required`, ...) requires the sandbox:
autotest refuses to run if it is not available - use this in marking wrappers.  
A false value (`0`, `no`, `off`, ...) disables the sandbox:
programs then run with all the privileges of the user running autotest.  
Only one value is used for all tests.  This parameter must be set as a global parameter.

**`sandbox_support_commands`** = True


If true, **`compile_commands`**, **`checkers`**, **`pre_compile_command`**, **`setup_command`**
and **`postprocess_output_command`** are also run inside the sandbox.  
If false, only the test **`command`** is sandboxed.

**`sandbox_network`** = True


If true, programs run in the **`sandbox`** have no network access:
they are given a private network namespace with only a loopback interface.  
Set to false to allow tests to use the network.  
Only one value is used for all tests.  This parameter must be set as
a global parameter: a value on a test line is ignored.

An exercise whose tests fetch a URL, or whose **`setup_command`** or
**`pre_compile_command`** installs packages, needs `sandbox_network=False`.
Without it those tests fail, or cannot be run, with no indication that the
network was the reason.  Everything else the sandbox provides is kept:
system directories stay read-only, the invoking user's files stay
unreachable, and tests still cannot see each other.

A test that depends on a service outside the machine is not reproducible:
it fails when the service changes, is unreachable, or is slow, and it fails
for every student at once.  Prefer supplying the data as a file where the
exercise allows it.

**`sandbox_tmp_bytes`** = 268435456


Size in bytes of the private `/tmp` seen by programs run in the **`sandbox`**.
Only one value is used for all tests.  This parameter must be set as a global parameter.

**`sandbox_shm_bytes`** = 67108864


Size in bytes of the private `/dev/shm` seen by programs run in the **`sandbox`**.
Only one value is used for all tests.  This parameter must be set as a global parameter.

**`sandbox_seccomp`** = True


If true, a seccomp filter blocking dangerous system calls is applied to programs run in the **`sandbox`**,
when the kernel and architecture support it.  
The namespace boundary of the sandbox does not depend on this.
Only one value is used for all tests.  This parameter must be set as a global parameter.

**`sandbox_landlock`** = True


If true, Landlock rules restricting filesystem access are applied to programs run in the **`sandbox`**,
when the kernel supports them.  
The namespace boundary of the sandbox does not depend on this.
Only one value is used for all tests.  This parameter must be set as a global parameter.

**`sandbox_read_only_mount_base`** = \['/bin', '/etc', '/lib', '/lib32', '/lib64', '/libx32', '/opt', '/sbin', '/usr'\]


Pathnames of files or directories made visible read-only in the **`sandbox`**.  
Pathnames which do not exist on the host are ignored.  
The parameter **`sandbox_read_only_mount`** should be used to add extra pathnames.  
This parameter need only be set to stop one of these pathnames being visible.
Only one value is used for all tests.  This parameter must be set as a global parameter.

**`sandbox_read_only_mount`** = \[\]


Pathnames of files or directories made visible read-only in the **`sandbox`**
in addition to those specified by **`sandbox_read_only_mount_base`**.  
A `(host_pathname, sandbox_pathname)` tuple can be used to make a pathname visible at a different
location in the sandbox.
Only one value is used for all tests.  This parameter must be set as a global parameter.

**`sandbox_read_write_mount`** = \[\]


Pathnames of files or directories made visible read-write in the **`sandbox`**.  
A `(host_pathname, sandbox_pathname)` tuple can be used to make a pathname visible at a different
location in the sandbox.  
The test directory is always read-write and `/tmp`, `/dev/shm`, `/dev` and `/proc` are always private
to the sandbox, so they do not need to be specified here.
Only one value is used for all tests.  This parameter must be set as a global parameter.

**`stability_runs`** = 1


If greater than 1, each test is run this many times, each in a
fresh copy of the test directory, and any test which does not
reach the same result every time is reported as **`unstable`**
rather than passed or failed.  
The command-line option is **`--check_stability`**.  
This is a staff check for specifications, not something to leave
on: it multiplies the time a run takes.  
Two runs are compared on the verdict and on the explanation of a
failure, with hexadecimal constants removed, so an address printed
by dcc or valgrind does not make every test look unstable.  A
decimal value which varies between runs -- a pid, a timestamp, an
elapsed time -- in the output of a test which *fails* is part of
its explanation and will be reported.  
A test with **`shared_test_directory`** is not repeated: a second
run would see what the first left in that directory.

**`parallel_tests`** = 1


Number of tests executed concurrently.  
`0` means one test per CPU.  
Each test runs in its own copy of the test directory,
unless **`shared_test_directory`** is set.  
Output is printed in test order, regardless of the order in which tests finish.  
Only one value is used for all tests.  This parameter must be set as a global parameter.

**`shared_test_directory`** = False


Run every test in one directory, instead of giving each test its own copy.

Tests then see files left behind by tests that ran before them, which is
how autotest behaved before per-test directories were introduced.
Set this for a test specification where one test prepares files that a
later test uses, for example where one test's **`setup_command`**
creates files that a test without a **`setup_command`** then reads.

Tests sharing a directory can not be run concurrently,
so **`parallel_tests`** is ignored and the tests are run one at a time.  
Set this as a global parameter.  A value on a single test line does
take effect for that test, but the run is not serialized for it, so
that test would run in the directory the other tests are copying.

<!--- end - autogenerated from parameter_descriptions.py --->



## Debugging Autotests

The command line parameter -d/--debug set increasing levels of debug output.

This can also be done using the environmental variable **`AUTOTEST_DEBUG`**
Python stack backtraces are shown when **`AUTOTEST_DEBUG`** is set to any non-empty value
(`0` included); `-d` alone does not show them.



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


## Development

The static checks and the tests are run with `make`; the tools and their
configuration are in `Pipfile` (`pipenv install --dev`) and `pyproject.toml`.
With the tools in a virtualenv rather than under pipenv, name its `bin`
directory: `make lint VENV_BIN=/path/to/venv/bin`.

* `make lint` runs five checkers and must exit 0.
  `ruff` (pyflakes, pycodestyle, bugbear, the flake8-bandit security rules, complexity,
  naming, import order and the pylint rules ruff implements), `black --check` and
  `mypy` (strict for `sandbox*.py` and `subprocess_with_resource_limits.py`, the security
  boundary) cover the whole tree, `pytest/` included.
  `bandit` (security) and `vulture` (dead code) cover the program modules only: the tests'
  security rules come from ruff's `S` family and their dead code from `F`, and vulture can
  not see that pytest calls a fixture or a hook.
  `FA102` is what keeps the Python 3.9 floor: `X | Y` in an annotation is a `TypeError` at
  import time on 3.9 unless the module has `from __future__ import annotations`, and mypy
  can not catch it because it has to be told `python_version = "3.10"`.
* `make format` rewrites the tree with `black` and ruff's safe fixes.
* `make test` runs the `pytest/` suite; a warning is an error.
  Tests which need the sandbox (unprivileged user namespaces) or a tool which is not installed skip.
* `make coverage` runs the suite under branch coverage, the `autotest.py` subprocesses included
  (through `patch = ["subprocess"]` in `[tool.coverage.run]`), and fails below the `fail_under`
  percentage in `pyproject.toml` (raise it when coverage improves, never lower it).
  Run it on a host which can build the sandbox: where it can not, the sandbox tests skip and the
  total falls by about six points.
  Code which runs in a forked child that ends with `os._exit()` or `execve()` can not be measured
  however well it is tested, and is excluded by a comment on its `def` line naming the test which
  exercises it: `# runs only in the sandbox child: <test>` for the stages of `sandbox.py` and
  `sandbox_landlock.py`, `# runs in the forked child before exec: <test>` for the three functions
  of `subprocess_with_resource_limits.py` which apply the resource limits to every command,
  `--no_sandbox` included.
* `make check` is `lint` then `test`.  CI runs `make lint` and `make coverage`,
  so run `make coverage` before pushing: only it applies the `fail_under` gate.
* `make README.md` regenerates this file.  The whole of it is generated: edit
  `README.template.md` or `parameter_descriptions.py`, never `README.md` itself.
* `scripts/replay_activities.sh OLD_TREE NEW_TREE MATERIALS...` replays a course's activities
  against their own model solutions under two trees and reports every activity whose result
  changed - the only check which exercises real specifications rather than fixtures, and the
  one which found every defect the parallel work introduced.  It needs course material, so CI
  can not run it; run it by hand before a change to the sandbox or to parallelism.  It exits
  non-zero on a verdict it has not been told about.  `scripts/replay_approved.txt` holds the
  verdict changes somebody has read and accepted, `scripts/replay_unstable.txt` the activities
  which differ from themselves.  Read the script's header for the `REPLAY_*` variables and the
  memory cap it must be run under.

A checker finding is fixed, not silenced. Where a rule is wrong for one line the suppression is inline,
names the rule and says why: `# noqa: CODE - reason` for ruff, `# nosec BXXX` for bandit
(with the reason before it, as bandit warns about every word after the test id).
The complexity rules are waived on the `def` line of each legacy function that needs it, never for a
whole file, so a newly added complex function is still reported. The few genuinely file-wide
exceptions are listed in `pyproject.toml` with the reason.
