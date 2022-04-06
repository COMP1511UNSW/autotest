#!/bin/sh

# overly simple test script which needs a lot of work

export SAMPLE_ENVIRONMENT_VARIABLE=sample_value

base=$(dirname $(readlink -f "$0"))/../tests

for dir in $base/*/
do
	cd "$dir"
	
	
	
	autotest=../../autotest.py
	autotest_source="../../*.py"
	autotest_args="-a autotest"
	
	# if there is an test.txt file in a test directory
	# then it is a meta autotest specifies a test of autotest (beware confusing output)
	# and we pass the autotest source so it can test itself
	test -r "tests.txt" && autotest_args="-a . $autotest_source"

	autotest_command="$autotest $autotest_args"	
	
	if $autotest_command 2>&1| grep ' tests passed 0 tests failed *$' >/dev/null
	then
		echo passed "$dir"
	else
		indent='   '
		echo failed "$dir"
		echo "$indent cd $dir"
		echo "$indent $autotest_command"
		$autotest_command 2>&1 |
		sed "s/^/$indent /"
	fi
done
