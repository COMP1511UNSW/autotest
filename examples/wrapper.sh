#!/bin/sh

# try adding new parameter and just seeing how it wokrs
parameters="
	default_compilers = {'c' : [['clang', '-Werror', '-std=gnu11', '-g', '-lm']]}
	upload_url = https://example.com/autotest.cgi
	unicode_stdin = True
"

exec ./autotest.py --exercise_directory ./examples --parameters "$parameters" "$@"
