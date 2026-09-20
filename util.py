# some shared utility code


import os
import re
import sys


class AutotestException(Exception):  # noqa: N818 - the name every module uses
    pass


class TestSpecificationError(AutotestException):
    pass


class InternalError(AutotestException):
    pass


def die(message):
    raise InternalError(message)


def warn(message):
    my_name = re.sub(r"\.py$", "", os.path.basename(sys.argv[0]))
    print(f"{my_name}: {message}", file=sys.stderr)
