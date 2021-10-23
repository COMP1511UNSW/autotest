#!/usr/bin/python3

#
# This code provides the equivalent of subprocess.run 
# with limits on CPU time and other resources.
#
# It was originally written for Python 2 
# Recent versions of Python 3 may allow this code to be rewrritten and much simplified
#

import asyncio,  locale, re, os, resource, signal, subprocess, sys, tempfile, threading

def run(command, **parameters):
	if sys.platform == "win32":
		loop = asyncio.ProactorEventLoop()
		asyncio.set_event_loop(loop)
	else:
		loop = asyncio.get_event_loop()
	try:
		cooroutine = run_coroutine(loop, command, **parameters)
		output = loop.run_until_complete(cooroutine)
	except KeyboardInterrupt:
		sys.exit(1)
	except OSError as e:
		return (b'', re.sub(r'^\[.*?\] *', '', str(e)).encode('UTF-8'), 2)
	return output


@asyncio.coroutine
def run_coroutine(loop, command,
				stdin=None,
				# these default should be unused - see parameter_descriptions.py for actual defaults
				shell=False,
				max_real_seconds=None,
				max_core_size=0,
				max_cpu_seconds=60,
				max_stack_bytes=32000000,
				max_rss_bytes=100000000,
				max_file_size_bytes=8192000,
				max_processes=4096,  # unfortunately this is total per user processes not child processes
				max_open_files=256,
				max_stdout_bytes=1000000,
				max_stderr_bytes=10000,
				debug=0,
				nice=0,
				**parameters):
	exit_future = asyncio.Future(loop=loop)

	def set_rlimit(which, limit):
		try:
			# having soft limit < hard limit necessary to produce nice message from SIGXCPU
			resource.setrlimit(which, (int(limit), int(limit)+1))
		except ValueError:
			# ignore value errors because they can result from a
			# lower resource limit already being set
			pass

	def set_limits():
		# don't set RLIMIT_DATA it breaks address-sanitizer
		# don't set RLIMIT_VMEM it breaks memory-sanitizer

		# The maximum size (in bytes) of a core file that the current process can create
		set_rlimit(resource.RLIMIT_CORE, max_core_size)

		# The maximum amount of processor time (in seconds) that a process can use
		set_rlimit(resource.RLIMIT_CPU, max_cpu_seconds)

		# The maximum size of a file which the process may create.
		set_rlimit(resource.RLIMIT_FSIZE, max_file_size_bytes)

		# The maximum size (in bytes) of the call stack for the current process.
		set_rlimit(resource.RLIMIT_STACK, max_stack_bytes)

		# TThe maximum resident set size that should be made available to the process.
		set_rlimit(resource.RLIMIT_RSS, max_rss_bytes)

		# The maximum number of processes the current process may create.
		set_rlimit(resource.RLIMIT_NPROC, max_processes)

		# The maximum number of open files
		set_rlimit(resource.RLIMIT_NOFILE, max_open_files+1)

		if nice != 0:
			os.nice(nice)
	# Create the subprocess
	command = ["/bin/sh", '-c', command] if isinstance(command, str) else command

	# subtle issue with providing string as input so just write it to a temporary file
	if stdin:
		stdin_stream = tempfile.TemporaryFile()
		# TODO: Verify that the else case is correct.
		if not parameters["unicode_stdin"]:
			stdin_stream.write(stdin.encode(locale.getpreferredencoding(False)))
		else:
			stdin_stream.write(stdin)

		stdin_stream.seek(0)
	else:
		stdin_stream = subprocess.DEVNULL
	process = loop.subprocess_exec(
		lambda: SubprocessProtocol(exit_future, max_stdout_bytes, max_stderr_bytes),
		*command, preexec_fn=set_limits, stdin=stdin_stream)
	transport, protocol = yield from process
	errors = []
	if max_real_seconds:
		def wall_clock_alarm(errors):
			errors.append(b'Error: real time limit of %d seconds exceeded\n' % max_real_seconds)
			transport.kill()
			transport.close()
			if debug > 1:
				print('wall clock alarm', file=sys.stderr);
		timer = threading.Timer(max_real_seconds, lambda errors=errors: wall_clock_alarm(errors))
		if debug > 1:
			print('wall clock timer set for', max_real_seconds, 'seconds', file=sys.stderr)
		timer.start()
	# Wait for the subprocess exit using the process_exited() method
	# of the protocol
	yield from exit_future
	if max_real_seconds:
		timer.cancel()
	transport.close()
	if stdin:
		stdin_stream.close()
	(stdout, stderr) = protocol.process_streams[1:3]
	exit_status = transport.get_returncode()
	if errors:
		stderr += b''.join(errors)
	elif exit_status == -signal.SIGXCPU:
		stderr += b'Error: CPU limit of %d seconds exceeded\n' % max_cpu_seconds
	elif exit_status == -signal.SIGXFSZ:
		stderr += b'Error: maximum file creation size of %d bytes exceeded\n' % max_file_size_bytes
	if debug > 2:
		print('run_corotine', stdout, stderr, transport.get_returncode(), file=sys.stderr)
	return (stdout, stderr, transport.get_returncode())

class SubprocessProtocol(asyncio.SubprocessProtocol):
	def __init__(self, exit_future, max_stdout_bytes, max_stderr_bytes, debug = 0):
		self.exit_future = exit_future
		self.output = bytearray()
		self.process_streams = (None, bytearray(), bytearray())
		self.max_stream_bytes = (None, max_stdout_bytes, max_stderr_bytes)
		self.finished = [False, False, False]
		self.debug = debug
	def pipe_data_received(self, fd, data):
		if self.debug > 1:
			print('pipe_data_received(%d)'%fd, file=sys.stderr)
		n_bytes = len(data)
		max_bytes = max(0, self.max_stream_bytes[fd] - len(self.process_streams[fd]))
		self.process_streams[fd].extend(data[0:max_bytes])
		if n_bytes > max_bytes:
			if fd == 1 and not self.finished[1]:
				self.process_streams[2].extend(b'\nError too much output - maximum stdout bytes of %d exceeded.' % self.max_stream_bytes[fd])
			self.finished = [True, True, True]
			if self.debug > 1:
				print('stream limit exceeded(fd=%d)'%fd , file=sys.stderr)
			self.terminate()

	def pipe_connection_lost(self, fd, exc):
		if self.debug > 1:
			print('pipe_connection_lost(%d)'%fd, file=sys.stderr);
		self.finished[fd] = True
		self.check_everything_finished()

	def process_exited(self):
		if self.debug > 1:
			print('process_exited', file=sys.stderr);
		self.finished[0] = True
		self.check_everything_finished()

	def check_everything_finished(self):
		if all(self.finished):
			if self.debug > 1:
				print('finished', file=sys.stderr)
			self.terminate()

	def terminate(self):
		try:
			self.exit_future.set_result(True)
		except Exception:
			pass

if __name__ == '__main__':
#    print(run(sys.argv[1], inpuy=" ".join(sys.argv[2:]), max_cpu=1, debug=2))
	print(run(sys.argv[1:], max_cpu_seconds=10, max_real_seconds=30, debug=0))
