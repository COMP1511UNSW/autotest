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
