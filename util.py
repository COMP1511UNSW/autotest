# some shared utility code


import os, re, sys


class AutotestException(Exception):
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
