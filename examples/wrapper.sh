#!/bin/sh

parameters="
	default_compilers = {'c' : [['clang', '-Werror', '-std=gnu11', '-g', '-lm']]}
	upload_url = https://example.com/autotest.cgi
"

exec /usr/local/autotest/autotest.py --exercise_directory /home/class/activities --parameters "$parameters" "$@"
