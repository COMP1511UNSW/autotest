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
#execute cat examples/wrapper.sh
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
#execute cat examples/simple_C/tests.txt
```



## Test Parameters

#execute ./parameter_descriptions.py



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
