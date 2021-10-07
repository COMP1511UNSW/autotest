#!/bin/bash

#
# Build a single executable for autotest including files for 0 or more embedded autotests
#
# The executable and all the autotest functionality and can also run non-embedded autotests.
#
# The executable contains a "#!" line followed by a zip of the autotest Python source.
#
# The zip files also contains xz-compressed tar files of each embedded autotest
#


# This functionality could be embdedded in autotest itself

case "$#" in
0)
	echo "Usage: $0 <created-executable> [autotest-directories]" 1>&2
	exit 1
esac

generated_executable="$1"
shift
embedded_autotests_package_name=embedded_autotests

src_directory=$(dirname "$(readlink -f "$0")")

test -r "$src_directory"/autotest.py || {
	echo "can not find autotest source"  1>&2
	exit 1
}

temp_dir=$(mktemp -d /tmp/bundle_autotests.XXXXXXXXXX) || exit 1
trap 'rm -fr $temp_dir; exit' EXIT INT TERM

cp "$src_directory"/*.py $temp_dir/
mkdir $temp_dir/$embedded_autotests_package_name
touch $temp_dir/$embedded_autotests_package_name/__init__.py

# build tar files for each autotest to be embedded
for pathname in "$@"
do
	for tp in "$pathname" "$pathname"/tests.txt "$pathname"/autotest/tests.txt
	do
		test -f "$tp" || continue
		tests_pathname="$(readlink -f "$tp")"
		break
	done

	case "$tests_pathname" in
	*/tests.txt) ;;
	*)
		echo "$pathname  - no tests.txt found"  1>&2
		exit 1
	esac

	autotest_directory=$(dirname "$tests_pathname")
	exercise=$(basename "$autotest_directory")
	test "$exercise" = autotest &&
		exercise=$(basename "$(dirname "$autotest_directory")")

	test -z "$exercise" && {
		echo "$pathname  - counld not determine exercise name"  1>&2
		exit 1
	}

	add_to_tar=.
	test -f "$pathname" &&
		add_to_tar=tests.txt

	# tar must be xz compressed because code in load_embedded_autotest function expects this

	tar --directory "$autotest_directory" --dereference --xz -cf $temp_dir/$embedded_autotests_package_name/"$exercise.tar" $add_to_tar

done

(
	cd $temp_dir

	test -r __main__.py || cat >__main__.py <<'eof'
from autotest import main
if __name__ == '__main__': main()
eof

	zip .src.zip --quiet -9 -r *.* $embedded_autotests_package_name
)

echo '#!/usr/bin/env python3' >$generated_executable
cat $temp_dir/.src.zip >>$generated_executable

chmod +x $generated_executable
