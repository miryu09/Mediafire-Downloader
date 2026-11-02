#!/bin/env python3
import urllib.parse, re

URL_RE = re.compile(r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+")

def truncate_schema(url):
	return url[url.find("://")+3:]

def get_urls(string):
	return URL_RE.findall(string)

def get_urls_from_files(files):
	urls = []
	for filename in files:
		with open(filename, "r", encoding="utf-8", errors="replace") as input_file:
			urls.extend(get_urls(input_file.read().replace("\n", " ")))
	return urls

if(__name__ == "__main__"):
	import sys

	if(len(sys.argv) < 2):
		print("Usage: ")
		print(" ./{} FILE [FILE]".format(sys.argv[0]))
		exit()

	urls = get_urls_from_files(sys.argv[1:])
	for url in urls:
		print(url)
