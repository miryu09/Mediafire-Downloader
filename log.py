import sys
import threading

from colorama import init

for stream in (sys.stdout, sys.stderr):
	if hasattr(stream, "reconfigure"):
		stream.reconfigure(errors="backslashreplace")

init()

def log(msg):
	thread_name = threading.current_thread().name
	if(thread_name != "MainThread"):
		print("\033[90mThread #{}\033[0m ".format(thread_name), end="")
	print(msg)
