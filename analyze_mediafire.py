#!/bin/env python3
import re

#Searches for keys and lists of keys; also considers url encoded symbols
MF_KEY_RE = re.compile(r"(?i)(?:media.{0,5}?fire.{0,5}?com|mfi.{0,5}?re).{0,40}?(?:(?:\/|%2F)[^\r\n\/]{0,40}?(?:\/|%2F)|(?:\?|%3F))[^\r\n\/]{0,40}?([0-9a-z,.]{22,}|[0-9a-z,]{19}|[0-9a-z,]{15}|[0-9a-z,]{13}|[0-9a-z,]{11})")

#Searches for conv links to files (e.g. https://img19.mediafire.com/22d9d9406ca5a970a04cf542eb7e210241fcd8ca47494a3483729a9b7320afbb5g.jpg which can also be
#https://img19.mediafire.com/conv/22d9d9406ca5a970a04cf542eb7e210241fcd8ca47494a3483729a9b7320afbb5g.jpg)
#and for links to custom folders (e.g. https://www.mediafire.com/MonHun); this regex also considers url encoded symbols
MF_LINK_RE = re.compile(r"(?i)(?:media.{0,5}?fire.{0,5}?com|mfi.{0,5}?re)[^\/?%\r\n]{0,5}?(?:\/|%2F)[^\/?%\r\n]{0,5}?([a-zA-Z0-9_.]+)\s")

def get_mediafire_links(text):
	#Get all of the mediafire keys, conv links and custom folders from text
	output = {"keys": [], "conv": [], "custom_folders": []}

	mf_key_matches = MF_KEY_RE.findall(text)
	for match in mf_key_matches:
		if("." in match): #/conv/ link
			output["conv"].append("/conv/" + match)
			continue
		for mf_key in match.split(","): #mf key; mediafire allows specifying multiple keys by separating them with ","
			if(len(mf_key) in [11,13,15,19,31]):
				if(mf_key not in output["keys"]): output["keys"].append(mf_key) #It's a valid key
			else:
				#There are also sharekey links: http://www.mediafire.com/?sharekey=2c58ee7e4f7e7dc2312dbd5f2bdc506260259ca196fcd1eb416b94653a3044fd
				#Don't know how long they are so just to be sure treat every key that isn't 11,13,15,19 or 31 chars long as a sharekey.
				#Difference between custom named folders and these links is that sharekey links redirect to regular links with keys, but
				#the method of resolving custom names to keys should work with these links too.
				sharekey_link = "?sharekey="+mf_key
				if(sharekey_link not in output["custom_folders"]): output["custom_folders"].append(sharekey_link)

	mf_link_matches = MF_LINK_RE.findall(text)
	for match in mf_link_matches:
		if("." in match): #file
			if(match not in output["conv"]): output["conv"].append("/conv/" + match)
		else: #custom folder
			if(match not in output["custom_folders"]): output["custom_folders"].append(match)

	return output

def read_mediafire_links(files):
	#Get all of the mediafire keys, conv links and custom folders from list of files
	output = {"keys": [], "conv": [], "custom_folders": []}
	for fname in files:
		with open(fname, "r", encoding="utf-8", errors="replace") as fl:
			data = fl.read()
		mf_links = get_mediafire_links(data)
		for link in mf_links["keys"]: #Remove duplicates across files
			if(link not in output["keys"]): output["keys"].append(link.lower()) #Everything except links is case insensitive and links are lowercase anyway
		for link in mf_links["conv"]:
			if(link not in output["conv"]): output["conv"].append(link.lower())
		for link in mf_links["custom_folders"]:
			if(link not in output["custom_folders"]): output["custom_folders"].append(link.lower())

	return output

if(__name__ == "__main__"):
	import sys

	if(len(sys.argv) < 2):
		print("Usage: ")
		print(" ./{} FILE [FILE]".format(sys.argv[0]))
		exit()

	urls = read_mediafire_links(sys.argv[1:])

	if(urls["keys"]):
			print("--- KEYS ---")
			for fl in urls["keys"]:
				print("https://mediafire.com/?{}".format(fl))
			print()
	
	if(urls["conv"]):
			print("--- CONV LINKS ---")
			for fl in urls["conv"]:
				print("https://mediafire.com{}".format(fl))
			print()
	
	if(urls["custom_folders"]):
			print("--- CUSTOM FOLDERS ---")
			for fl in urls["custom_folders"]:
				print("https://mediafire.com/{}".format(fl))
			print()
