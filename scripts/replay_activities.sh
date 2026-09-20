#!/bin/bash
#
# Replay a course's activities against their own model solutions, under two
# versions of autotest, and report every activity whose result changed.
#
# The oracle is that a course's model solution passes that course's own tests.
# It needs no student data and no privacy negotiation, and it is the only check
# that exercises real specifications rather than the fixtures in tests/.
#
# It was written to validate the sandbox and parallel-execution work against
# the COMP1511, COMP1521 and COMP2041 26T2 activity sets, and it found three
# things the fixture suite could not: a dangling symlink in an autotest
# directory deleted a file the submission had supplied, a per-test copy could
# not carry a file whose mode forbids reading, and a specification whose tests
# share files through setup_command needs shared_test_directory.
#
# usage:
#   scripts/replay_activities.sh OLD_AUTOTEST_DIR NEW_AUTOTEST_DIR MATERIALS...
#
#   OLD_AUTOTEST_DIR  a checkout to compare against, e.g. a worktree of main
#   NEW_AUTOTEST_DIR  the tree under test, usually this one
#   MATERIALS         one or more course directories holding activities/
#
# environment:
#   REPLAY_PYTHON   interpreter to run autotest with (default: python3).
#                   autotest's "#!" line is /usr/bin/python3 -I, which ignores
#                   PYTHONPATH, so a virtual environment must be named here.
#   REPLAY_JOBS     activities to run at once (default: 8)
#   REPLAY_TMPDIR   where the runs happen. Give it a filesystem with room:
#                   each test gets its own copy of its directory, and a
#                   small tmpfs will fail with ENOSPC and look like a defect.
#   REPLAY_ARGS     extra arguments for the new tree only, e.g. --no_sandbox
#
set -u

case "$#" in
0 | 1 | 2)
	echo "usage: $0 old_autotest_dir new_autotest_dir materials..." 1>&2
	exit 2
	;;
esac

old_tree=$(cd "$1" && pwd) || exit 1
new_tree=$(cd "$2" && pwd) || exit 1
shift 2

python=${REPLAY_PYTHON:-python3}

# Checked before anything runs.  An interpreter that cannot start makes every
# activity fail the same way under both trees, which this script would then
# report as "identical output" -- a comparison that passes because nothing
# happened is worse than one that fails.
for tree in "$old_tree" "$new_tree"
do
	test -f "$tree/autotest.py" || {
		echo "$0: $tree does not hold autotest.py" 1>&2
		exit 1
	}
done
"$python" -c 'import termcolor' 2>/dev/null || {
	echo "$0: $python cannot import termcolor, which autotest needs." 1>&2
	echo "$0: set REPLAY_PYTHON to an interpreter that has it." 1>&2
	exit 1
}
jobs=${REPLAY_JOBS:-8}
extra_args=${REPLAY_ARGS:-}
work=${REPLAY_TMPDIR:-${TMPDIR:-/tmp}}/autotest-replay.$$
mkdir -p "$work/old" "$work/new" || exit 1
trap 'rm -rf "$work"' EXIT INT TERM

activities="$work/activities"
: >"$activities"
for materials in "$@"
do
	for activity in "$materials"/activities/*/
	do
		test -f "$activity/autotest/tests.txt" || continue
		test -d "$activity/solutions" || continue
		echo "${activity%/}" >>"$activities"
	done
done

n_activities=$(wc -l <"$activities")
test "$n_activities" -gt 0 || {
	echo "$0: no activity has both autotest/tests.txt and solutions/" 1>&2
	exit 1
}
echo "replaying $n_activities activities, $jobs at a time"

# Run one activity under one tree.  Everything that legitimately varies between
# two runs -- temporary paths, the compiler's scratch object files, addresses,
# pids -- is rewritten, so that a difference in the output means a difference in
# behaviour.  Nothing else is normalised: these exercises are mostly numeric
# output, and a broader rule would hide the differences worth seeing.
run_one() {
	tree=$1 activity=$2 outdir=$3 args=$4
	name=$(basename "$activity")
	run="$work/$(basename "$outdir")-$name"
	mkdir -p "$run/submission" || return 0
	cp -a "$activity"/solutions/. "$run/submission"/ 2>/dev/null

	output=$(cd "$run/submission" && TMPDIR="$run" timeout -k 5 300 \
		"$python" "$tree/autotest.py" \
		-D "$run/submission" -a "$activity/autotest" $args 2>&1)

	printf '%s' "$output" |
		sed -e 's/\x1b\[[0-9;]*m//g' \
			-e "s#$run#RUN#g" \
			-e 's#/tmp/tmp[A-Za-z0-9_]\{6,\}#TMPDIR#g' \
			-e 's#RUN/tmp[A-Za-z0-9_]\{6,\}#TMPDIR#g' \
			-e 's#RUN#TMPDIR#g' \
			-e 's#[A-Za-z0-9_.-]*-[0-9a-f]\{6\}\.o#OBJECT.o#g' \
			-e 's/0x[0-9a-f][0-9a-f]*/0xADDRESS/g' \
			-e 's/\(process \)[0-9][0-9]*/\1PID/g' \
			>"$outdir/$name"

	rm -rf "$run"
}
export -f run_one
export work python

xargs -a "$activities" -P "$jobs" -I{} \
	bash -c 'run_one "$0" "{}" "$1" "$2"' "$old_tree" "$work/old" "" \
	>/dev/null 2>&1
xargs -a "$activities" -P "$jobs" -I{} \
	bash -c 'run_one "$0" "{}" "$1" "$2"' "$new_tree" "$work/new" "$extra_args" \
	>/dev/null 2>&1

identical=0
changed="$work/changed"
: >"$changed"
for old in "$work"/old/*
do
	name=$(basename "$old")
	new="$work/new/$name"
	test -f "$new" || continue
	if cmp -s "$old" "$new"
	then
		identical=$((identical + 1))
	else
		echo "$name" >>"$changed"
	fi
done

echo "$identical activities produced identical output"
echo "$(wc -l <"$changed") activities differed"

# A changed verdict is what matters; everything else is a difference in
# wording that a person should still read, but which does not change a mark.
verdict='[0-9][0-9]* tests passed'
while read -r name
do
	old_verdict=$(grep -o "$verdict.*" "$work/old/$name" | tail -1)
	new_verdict=$(grep -o "$verdict.*" "$work/new/$name" | tail -1)
	test "$old_verdict" = "$new_verdict" && continue
	echo "  $name"
	echo "      old: ${old_verdict:-(no verdict: autotest did not finish)}"
	echo "      new: ${new_verdict:-(no verdict: autotest did not finish)}"
done <"$changed"
