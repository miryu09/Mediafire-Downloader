#!/bin/env python3
import atexit, requests, json, os, traceback, time, argparse, threading, re, analyze_mediafire, tempfile
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from bs4 import BeautifulSoup
from log import log

TIMEOUT_T = 30
HTTP_HEADERS = {
	"User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:68.0) Gecko/20100101 Firefox/68.0" #Spoof firefox user agent
}
CUSTOM_FOLDER_RE = re.compile(r'afI= "([a-z0-9]{13}|[a-z0-9]{19})"') #Regex used for locating keys on pages with custom named folders e.g. https://www.mediafire.com/MonHun

ARCHIVE_LOCK = threading.Lock()
PRINT_LOCK = threading.Lock()
PROGRESS_ENABLED = False
MAX_DOWNLOAD_RETRIES = 5
DOWNLOAD_RETRY_DELAY = 10
MAX_FOLDER_RESOLVE_RETRIES = 5

def positive_int(value):
	try:
		number = int(value)
	except ValueError as error:
		raise argparse.ArgumentTypeError("must be a positive integer") from error
	if number <= 0:
		raise argparse.ArgumentTypeError("must be a positive integer")
	return number

def remove_custom_folder_cache(filename):
	try:
		os.remove(filename)
	except FileNotFoundError:
		return
	except OSError as error:
		log("Could not remove custom folder cache {}: {}".format(filename, error))

def emit_progress(event, **details):
	if PROGRESS_ENABLED:
		print("MF_DL_PROGRESS:" + json.dumps({"event": event, **details}), flush=True)

def download_url(url, local_filename):
	response = None
	temporary_path = None
	try:
		response = requests.get(url, headers=HTTP_HEADERS, stream=True, timeout=TIMEOUT_T)
		if response.status_code != 200:
			return response.status_code

		directory = os.path.dirname(os.path.abspath(local_filename))
		file_descriptor, temporary_path = tempfile.mkstemp(
			prefix=".mf-dl-",
			suffix=".part",
			dir=directory,
		)
		with os.fdopen(file_descriptor, "wb") as downloaded_file:
			for chunk in response.iter_content(chunk_size=64 * 1024):
				if chunk:
					emit_progress("network_bytes", bytes=len(chunk))
					downloaded_file.write(chunk)
		os.replace(temporary_path, local_filename)
		temporary_path = None
		return response.status_code
	finally:
		if response is not None:
			response.close()
		if temporary_path is not None and os.path.exists(temporary_path):
			os.unlink(temporary_path)

def get_file_metadata(file_id):
	#Get "response" key from mediafire's file/get_info.php API function
	rq = requests.post("https://www.mediafire.com/api/1.5/file/get_info.php", params={"quick_key": file_id, "response_format": "json"}, headers=HTTP_HEADERS, timeout=TIMEOUT_T)
	return rq.json()["response"]

def find_direct_url(info_url):
	#Find a direct download url on an info page
	rq = requests.get(info_url, headers=HTTP_HEADERS, timeout=TIMEOUT_T)
	soup = BeautifulSoup(rq.text, "lxml")
	download_link = soup.select_one('a[aria-label="Download file"][href]')
	if download_link is None:
		return {"success": 0}

	location = ""
	uploaded_from = soup.find("p", string=re.compile(r"This file was uploaded from "))
	if uploaded_from is not None:
		location_text = uploaded_from.get_text(" ", strip=True)
		location_prefix = "This file was uploaded from "
		if location_text.startswith(location_prefix):
			location = location_text[len(location_prefix):].split(" on ", 1)[0]

	return {"url": download_link["href"], "location": location, "success": 1}

def download_file(mediafire_id, output, only_meta=0, archive_mode=False):
	#Returns 1 on success and 0 otherwise
	metadata = get_file_metadata(mediafire_id)
	if(metadata["result"] != "Success"): #Error from mediafire
		with PRINT_LOCK:
			log("\033[90m{}:\033[0m \033[31m{}: {}\033[0m".format(mediafire_id, metadata["result"], metadata["message"]))
		return 0 #Skip file

	#Display info
	with PRINT_LOCK:
		log("\033[90m{}: {}\033[0m \033[96m{}\033[0m \033[95m{}\033[0m".format(mediafire_id,
		                                                                       metadata["file_info"]["created"],
		                                                                       metadata["file_info"]["owner_name"],
		                                                                       metadata["file_info"]["filename"]))

	if not archive_mode and not only_meta:
		filename = metadata["file_info"]["filename"]
		if "/" in filename or "\\" in filename or filename in (".", ".."):
			with PRINT_LOCK:
				log("Dangerous name {} for file with id {}".format(filename, mediafire_id))
			filename = mediafire_id
		output = output + "/" + filename
		if(os.path.exists(output)): #Duplicate
			log("\033[90m{}:\033[0m \033[33mFile already exists. Skipping...\033[0m".format(mediafire_id))
			return (1, None)

	if only_meta:
		os.makedirs(output + "/keys", exist_ok=True)
		with open(output + "/keys/" + mediafire_id + ".info.json", "w") as fl:
			fl.write(json.dumps(metadata))
		return 1

	#Individually shared files point to an info page, but files shared in a folder point directly to the file
	dwnld_head = requests.head(metadata["file_info"]["links"]["normal_download"], headers=HTTP_HEADERS, timeout=TIMEOUT_T).headers
	if(str(dwnld_head.get("Location")).startswith("https://download")): #Direct
		direct_url = metadata["file_info"]["links"]["normal_download"]
	else: #Info page
		direct_url = find_direct_url(metadata["file_info"]["links"]["normal_download"])
		#If couldn't find a download link; There needs to be an additional check because mediafire's API still returns info about files which were taken down
		if(direct_url["success"] == 0):
			with PRINT_LOCK:
				log("\033[90m{}: \033[0m\033[31mCouldn't find download url\033[0m".format(mediafire_id))
			return 0
		metadata["location"] = direct_url["location"]
		direct_url = direct_url["url"]

	#Download file
	if archive_mode:
		output_dir = output + "/keys/" + mediafire_id
		filename = metadata["file_info"]["filename"]
		if "/" in filename or "\\" in filename or filename in (".", ".."):
			with PRINT_LOCK:
				log("Dangerous name {} for file with id {}".format(filename, mediafire_id))
			filename = mediafire_id
		output_fname = output_dir + "/" + filename
	else:
		output_dir = output[0:output.rfind('/')]
		output_fname = output
	os.makedirs(output_dir, exist_ok=True)
	if download_url(direct_url, output_fname) != 200:
		with PRINT_LOCK:
			log("\033[90m{}: \033[0m\033[31mDownload request failed\033[0m".format(mediafire_id))
		return 0
	#Write metadata
	if archive_mode:
		with open(output + "/keys/" + mediafire_id + ".info.json", "w") as fl:
			fl.write(json.dumps(metadata))
	return 1

def get_folder_content(folder_key, content_type, chunk):
	#Get "response" key from mediafire's folder/get_info.php API function
	rq = requests.get("https://www.mediafire.com/api/1.4/folder/get_content.php",
	                  params={"content_type": content_type, "chunk": chunk, "folder_key": folder_key, "response_format": "json"},
					  headers=HTTP_HEADERS,
					  timeout=TIMEOUT_T)
	return rq.json()["response"]

def get_folder_metadata(folder_key):
	#Get "response" key from mediafire's folder/get_info.php API function
	rq = requests.post("https://www.mediafire.com/api/1.5/folder/get_info.php", params={"folder_key": folder_key, "response_format": "json"}, headers=HTTP_HEADERS, timeout=TIMEOUT_T)
	return rq.json()["response"]

def _file_size_from_metadata(mediafire_id):
	metadata = get_file_metadata(mediafire_id)
	if metadata.get("result") != "Success":
		return None
	size = metadata.get("file_info", {}).get("size")
	try:
		size = int(str(size).replace(",", "").strip())
	except (TypeError, ValueError):
		return None
	return size if size >= 0 else None

def _file_size_from_conv_link(mediafire_id):
	response = None
	try:
		response = requests.get(
			"https://mediafire.com" + mediafire_id,
			headers=HTTP_HEADERS,
			stream=True,
			timeout=TIMEOUT_T,
		)
		if response.status_code != 200:
			return None
		try:
			size = int(response.headers.get("Content-Length", ""))
		except (TypeError, ValueError):
			return None
		return size if size >= 0 else None
	finally:
		if response is not None:
			response.close()

def _folder_contents_for_estimate(folder_key):
	metadata = get_folder_metadata(folder_key)
	if metadata.get("result") != "Success":
		raise RuntimeError(metadata.get("message", "Could not read folder metadata."))

	child_folders = []
	child_files = []
	for content_type, output in (("folders", child_folders), ("files", child_files)):
		chunk = 1
		while True:
			response = get_folder_content(folder_key, content_type, chunk)
			content = response["folder_content"]
			output.extend(content[content_type])
			if content.get("more_chunks") == "no":
				break
			chunk += 1

	child_ids = [folder["folderkey"] for folder in child_folders]
	child_ids.extend(file["quickkey"] for file in child_files)
	avatar = metadata.get("folder_info", {}).get("avatar")
	if isinstance(avatar, str):
		child_ids.extend(analyze_mediafire.get_mediafire_links(avatar)["keys"])
	return child_ids

def estimate_download_size(mediafire_urls, threads=6):
	pending_folders = deque(
		key for key in mediafire_urls["keys"] if len(key) in (13, 19)
	)
	file_ids = list(mediafire_urls["conv"])
	file_ids.extend(
		key for key in mediafire_urls["keys"] if len(key) in (11, 15, 31)
	)
	seen_files = set(file_ids)
	seen_folders = set()
	unknown_folders = 0

	while pending_folders:
		folder_key = pending_folders.popleft()
		if folder_key in seen_folders:
			continue
		seen_folders.add(folder_key)
		try:
			child_ids = _folder_contents_for_estimate(folder_key)
		except Exception as error:
			unknown_folders += 1
			with PRINT_LOCK:
				log("Could not estimate folder {}: {}".format(folder_key, error))
			continue

		for mediafire_id in child_ids:
			if len(mediafire_id) in (13, 19):
				if mediafire_id not in seen_folders:
					pending_folders.append(mediafire_id)
			elif len(mediafire_id) in (11, 15, 31):
				if mediafire_id not in seen_files:
					seen_files.add(mediafire_id)
					file_ids.append(mediafire_id)

	def file_size(mediafire_id):
		try:
			return (
				_file_size_from_conv_link(mediafire_id)
				if mediafire_id.startswith("/conv/")
				else _file_size_from_metadata(mediafire_id)
			)
		except Exception as error:
			with PRINT_LOCK:
				log("Could not estimate file {}: {}".format(mediafire_id, error))
			return None

	with ThreadPoolExecutor(
		max_workers=positive_int(str(threads)),
		thread_name_prefix="mediafire-size",
	) as executor:
		sizes = list(executor.map(file_size, file_ids))

	result = {
		"total_bytes": sum(size for size in sizes if size is not None),
		"file_count": len(file_ids),
		"unknown_files": sum(size is None for size in sizes) + unknown_folders,
	}
	emit_progress(
		"size_estimate",
		total_bytes=result["total_bytes"],
		file_count=result["file_count"],
		unknown_files=result["unknown_files"],
	)
	return result

def download_folder(mediafire_id, output_dir, only_meta=0, archive=None, archive_mode=False):
	#Recursively downloads a folder
	#Returns 1 on success and 0 otherwise
	if archive is None:
		archive = []
	metadata = get_folder_metadata(mediafire_id)
	if(metadata["result"] != "Success"): #Error from mediafire
		with PRINT_LOCK:
			log("\033[90m{}: \033[0m\033[31m{}: {}\033[0m".format(mediafire_id, metadata["result"], metadata["message"]))
		emit_progress("folder_done", child_folders=0, file_count=0, success=False)
		return (0, None) #Skip folder

	with PRINT_LOCK:
		log("\033[90m{}: {}\033[0m \033[96m{}\033[0m \033[95m{}\033[0m".format(mediafire_id,
		                                                                       metadata["folder_info"]["created"],
		                                                                       metadata["folder_info"]["owner_name"],
		                                                                       metadata["folder_info"]["name"]))

	metadata["children"] = {"folders": [], "files": []}
	folder_contents = ((metadata["folder_info"]["name"], mediafire_id), [])

	#Download folders inside
	chunk = 1
	more_chunks = True
	while(more_chunks != "no"):
		children_folders_chunk = get_folder_content(mediafire_id, "folders", chunk)
		metadata["children"]["folders"] += children_folders_chunk["folder_content"]["folders"]
		more_chunks = children_folders_chunk["folder_content"]["more_chunks"]
		chunk+=1
	for folder in metadata["children"]["folders"]:
		folder_contents[1].append(folder["folderkey"])

	#Download files inside
	chunk = 1
	more_chunks = True
	while(more_chunks != "no"):
		children_files_chunk = get_folder_content(mediafire_id, "files", chunk)
		metadata["children"]["files"] += children_files_chunk["folder_content"]["files"]
		more_chunks = children_files_chunk["folder_content"]["more_chunks"]
		chunk+=1
	for fl in metadata["children"]["files"]:
		folder_contents[1].append(fl["quickkey"])

	#Download avatar
	avatar_keys = analyze_mediafire.get_mediafire_links(metadata["folder_info"]["avatar"])["keys"]
	for avatar in avatar_keys: #There should be only 1 key in an avatar link, but loop through it just to be sure
		folder_contents[1].append(avatar)
	child_folder_count = len(metadata["children"]["folders"])
	file_count = len(folder_contents[1]) - child_folder_count

	#Write metadata
	if archive_mode or only_meta:
		if(os.path.exists(output_dir + "/keys/" + mediafire_id + ".info.json")): #Don't overwrite the .info.json file
			log("\033[90m{}:\033[0m \033[33mFile already exists. Skipping...\033[0m".format(mediafire_id))
			emit_progress(
				"folder_done",
				child_folders=child_folder_count,
				file_count=file_count,
			)
			return (1, folder_contents)
		with open(output_dir + "/keys/" + mediafire_id + ".info.json", "w") as fl:
			fl.write(json.dumps(metadata))

	emit_progress(
		"folder_done",
		child_folders=child_folder_count,
		file_count=file_count,
	)
	return (1, folder_contents)

def download(mediafire_id, output, only_meta=0, archive=None, archive_mode=False):
	#Download mediafire key and save it in output
	#In case of a mediafire error return 0
	#In case of an exception - retry after 10 seconds
	#Otherwise return 1
	if archive is None:
		archive = []
	attempts = 0
	folder_started = False
	while attempts < MAX_DOWNLOAD_RETRIES:
			attempts += 1
			with PRINT_LOCK:
				log("\033[90m{}: Downloading...\033[0m".format(mediafire_id))
			try:
				if(mediafire_id.startswith("/conv/")): #Conv link
					with ARCHIVE_LOCK:
						if archive_mode:
							output_file = output + mediafire_id
						else:
							output_file = output
						if(os.path.exists(output_file) or (mediafire_id in archive)): #Duplicate
							log("\033[90m{}:\033[0m \033[33mFile already exists. Skipping...\033[0m".format(mediafire_id))
							emit_progress("file_done", success=True, skipped=True)
							return (1, None)
						archive.append(mediafire_id)
					os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
					if(download_url("https://mediafire.com" + mediafire_id, output_file) == 200): #Success
						log("\033[90m{}: \033[0m\033[96mDownloaded\033[0m".format(mediafire_id))
						emit_progress("file_done", success=True)
						return (1, None)
					else:
						log("\033[90m{}: \033[0m\033[31mNot found!\033[0m".format(mediafire_id))
						emit_progress("file_done", success=False)
						return (0, None)
				elif(len(mediafire_id) in [11, 15, 31]): #Single file
					with ARCHIVE_LOCK:
						known_path_exists = False
						if only_meta or archive_mode:
							known_path_exists = os.path.exists(output + "/keys/" + mediafire_id + ".info.json")
						if(known_path_exists or (mediafire_id in archive)): #Duplicate
							log("\033[90m{}:\033[0m \033[33mFile already exists. Skipping...\033[0m".format(mediafire_id))
							emit_progress("file_done", success=True, skipped=True)
							return (1, None)
						archive.append(mediafire_id)
					download_successful = download_file(mediafire_id, output, only_meta=only_meta, archive_mode=archive_mode)
					emit_progress("file_done", success=bool(download_successful))
					return (download_successful, None)
				elif(len(mediafire_id) in [13, 19]): #Folder
					with ARCHIVE_LOCK:
						#Redownload contents even if .info.json file exists (without overwriting it) but skip if another thread already started downloading it
						if(mediafire_id in archive):
							log("\033[90m{}:\033[0m \033[33mFile already exists. Skipping...\033[0m".format(mediafire_id))
							emit_progress("folder_skipped")
							return (1, None)
						archive.append(mediafire_id)
					emit_progress("folder_start")
					folder_started = True
					return download_folder(mediafire_id, output, only_meta=only_meta, archive=archive, archive_mode=archive_mode)
			except Exception:
				with ARCHIVE_LOCK:
					if mediafire_id in archive:
						archive.remove(mediafire_id)
				with PRINT_LOCK:
					traceback.print_exc()
					if attempts < MAX_DOWNLOAD_RETRIES:
						log("\033[90m{}: \033[0m\033[31mError while downloading! Retrying in 10s ({}/{}).\033[0m".format(mediafire_id, attempts, MAX_DOWNLOAD_RETRIES))
					else:
						log("\033[90m{}: \033[0m\033[31mDownload failed after {} attempts.\033[0m".format(mediafire_id, attempts))
				if attempts < MAX_DOWNLOAD_RETRIES:
					time.sleep(DOWNLOAD_RETRY_DELAY)
					continue
				if folder_started:
					emit_progress("folder_done", child_folders=0, file_count=0, success=False)
				elif mediafire_id.startswith("/conv/") or len(mediafire_id) in [11, 15, 31]:
					emit_progress("file_done", success=False)
				return (0, None)
	return (0, None)

def resolve_custom_folder(name):
	for attempt in range(MAX_FOLDER_RESOLVE_RETRIES):
		log("\033[90mResolving custom folder name:\033[0m \033[96m{}\033[0m".format(name))
		try:
			rq = requests.get("https://mediafire.com/{}".format(name), headers=HTTP_HEADERS, timeout=TIMEOUT_T)
			resolved = CUSTOM_FOLDER_RE.findall(rq.text)
			if(resolved):
				resolved = resolved[0] #First result
				log("\033[96m{}\033[0m \033[90m->\033[0m \033[95m{}\033[0m".format(name, resolved))
				return resolved
			else:
				log("\033[31mUnable to resolve {}\033[0m".format(name))
				return None
		except Exception:
			traceback.print_exc()
			if attempt + 1 < MAX_FOLDER_RESOLVE_RETRIES:
				time.sleep(DOWNLOAD_RETRY_DELAY)
	log("\033[31mUnable to resolve {} after {} attempts\033[0m".format(name, MAX_FOLDER_RESOLVE_RETRIES))
	return None

def worker(args, output, mediafire_id, archive):
	(download_successful, folder_contents) = download(mediafire_id, output, only_meta=args.only_meta, archive=archive, archive_mode=args.archive_mode)
	return (output, folder_contents)

def enqueue_folder_contents(work_queue, output_base, folder_contents, args):
	if folder_contents is None:
		return
	if args.archive_mode or args.only_meta:
		output = args.output
	else:
		folder_name = folder_contents[0][0]
		if "/" in folder_name or "\\" in folder_name or folder_name in (".", ".."):
			with PRINT_LOCK:
				log("Dangerous name {} for folder with id {}".format(folder_name, folder_contents[0][1]))
			folder_name = folder_contents[0][1]
		output = os.path.join(output_base, folder_name)
	for mediafire_id in folder_contents[1]:
		work_queue.append((output, mediafire_id))

def run_download_tasks(args, mediafire_urls, archive):
	work_queue = deque((args.output, mediafire_id) for mediafire_id in mediafire_urls["conv"])
	work_queue.extend((args.output, mediafire_id) for mediafire_id in mediafire_urls["keys"])
	active_tasks = set()

	with ThreadPoolExecutor(
		max_workers=args.threads,
		thread_name_prefix="mediafire-download",
	) as executor:
		while work_queue or active_tasks:
			while work_queue and len(active_tasks) < args.threads:
				output, mediafire_id = work_queue.popleft()
				active_tasks.add(executor.submit(worker, args, output, mediafire_id, archive))

			completed, active_tasks = wait(active_tasks, return_when=FIRST_COMPLETED)
			for task in completed:
				try:
					output_base, folder_contents = task.result()
				except Exception as error:
					with PRINT_LOCK:
						traceback.print_exception(error)
						log("Download task failed: {}".format(error))
					continue
				enqueue_folder_contents(work_queue, output_base, folder_contents, args)

if(__name__ == "__main__"):
	#CLI front end

	parser = argparse.ArgumentParser(description="Mediafire downloader")
	parser.add_argument("--only-meta", action="store_true", help="Only download *.info.json files")
	parser.add_argument("--threads", type=positive_int, default=6, help="How many threads to use; in case mediafire starts showing captchas or smth the amount of threads should be reduced; default is 6")
	parser.add_argument("--archive-mode", "-a", dest="archive_mode", action="store_true", help="Use a flat directory layout and save all file metadata in *.info.json files")
	parser.add_argument("--progress-events", action="store_true", help=argparse.SUPPRESS)
	parser.add_argument("--estimate-size", action="store_true", help=argparse.SUPPRESS)
	parser.add_argument("output", help="Output directory")
	parser.add_argument("input", nargs="+", help="Input file/files which will be searched for links")
	args = parser.parse_args()

	#Archive list is a list of files that started downloading. It's used to
	#stop threads from starting to download the same file multiple times.
	#For example if a file is downloading as a single file in one thread and
	#another thread starts downloading it as part of a directory before the first
	#thread finishes
	archive = []

	#Get ids
	mediafire_urls = analyze_mediafire.read_mediafire_links(args.input)
	PROGRESS_ENABLED = args.progress_events

	if args.estimate_size:
		custom_folders_fname = os.path.join(args.output, "custom_folders.txt")
		custom_folder_lookup = {}
		if os.path.exists(custom_folders_fname):
			with open(custom_folders_fname, "r", encoding="utf-8") as fl:
				for line in fl:
					lookup_entry = line.split(maxsplit=1)
					if len(lookup_entry) == 2:
						custom_folder_lookup[lookup_entry[1]] = lookup_entry[0]
		for custom_folder in mediafire_urls["custom_folders"]:
			resolved = custom_folder_lookup.get(custom_folder)
			if resolved is None:
				resolved = resolve_custom_folder(custom_folder)
			if resolved:
				mediafire_urls["keys"].append(resolved)
		estimate_download_size(mediafire_urls, args.threads)
		raise SystemExit(0)

	#Resolve custom names to keys and save names and their keys to custom_folders.txt
	#custom_folders.txt format:
	#"KEY FOLDER_NAME\n"
	custom_folders_fname = os.path.join(args.output, "custom_folders.txt")
	atexit.register(remove_custom_folder_cache, custom_folders_fname)
	custom_folder_lookup = {}
	if os.path.exists(custom_folders_fname):
		with open(custom_folders_fname, "r", encoding="utf-8") as fl:
			for line in fl:
				lookup_entry = line.split(maxsplit=1)
				if len(lookup_entry) == 2:
					custom_folder_lookup[lookup_entry[1]] = lookup_entry[0]
	#Resolve names
	for custom_folder in mediafire_urls["custom_folders"]:
		if(custom_folder in custom_folder_lookup): #Already in the table
			mediafire_urls["keys"].append(custom_folder_lookup[custom_folder]) #Add resolved key
		else: #Not yet resolved
			resolved = resolve_custom_folder(custom_folder)
			if(resolved):
				os.makedirs(args.output, exist_ok=True)
				with open(custom_folders_fname, "a") as fl:
					fl.write("{} {}\n".format(resolved, custom_folder))
				mediafire_urls["keys"].append(resolved) #Add resolved key
				custom_folder_lookup[custom_folder] = resolved

	if args.progress_events:
		emit_progress(
			"start",
			total=len(mediafire_urls["conv"]) + sum(len(key) in [11, 15, 31] for key in mediafire_urls["keys"]),
			folders_pending=sum(len(key) in [13, 19] for key in mediafire_urls["keys"]),
		)

	#Create download dirs
	if args.archive_mode or args.only_meta:
		os.makedirs(args.output + "/keys", exist_ok=True)
		os.makedirs(args.output + "/conv", exist_ok=True)

	# Download with a bounded number of in-flight tasks and wait for every nested item.
	archive = []
	run_download_tasks(args, mediafire_urls, archive)
