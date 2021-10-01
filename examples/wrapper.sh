#!/bin/sh

parameters="$parameters
	default_compilers = {'c' : [['clang', '-Werror', '-std=gnu11', '-g', '-lm']]}
	upload_url = $autotest_upload_url
	upload_fields = {'zid' : '$LOGNAME'} 
"

exec /usr/local/autotest/autotest.py --exercise_directory /home/class/activities --parameters "$parameters" "$@"
