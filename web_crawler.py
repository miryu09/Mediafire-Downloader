#!/bin/env python3
import argparse, threading, queue, ipaddress, os, analyze, requests, time, traceback, re
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from log import log

TIMEOUT_T = 30
HTTP_HEADERS = {
	"User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:68.0) Gecko/20100101 Firefox/68.0" #Spoof firefox user agent
}
SCRAPE_CONTENT_TYPE = ["text/plain", "text/html"]
#Don't verify certificates so sites with expired certificates will also work
#This will allow MITM attacks on https sites, but if that's the case then the
#scraper getting incorrect links is the least of your problems
VERIFY_CERTIFICATES=False
requests.packages.urllib3.disable_warnings()

STATUS_NOT_WORKING = 0
STATUS_WORKING = 1
STATUS_ERROR = 2

MAX_RETRIES = 5

def positive_int(value):
	try:
		number = int(value)
	except ValueError as error:
		raise argparse.ArgumentTypeError("must be a positive integer") from error
	if number <= 0:
		raise argparse.ArgumentTypeError("must be a positive integer")
	return number

def worker(download_queue, output, output_list, archive, print_lock, output_lock, archive_lock, url_filter, url_regex, thread_id, threads_working, threads_working_lock):
	while(1):
		#Get element from the queue
		with threads_working_lock:
			threads_working[thread_id] = STATUS_NOT_WORKING
		url = download_queue.get(block=1)
		with threads_working_lock:
			threads_working[thread_id] = STATUS_WORKING

		#Download page
		retries = MAX_RETRIES
		skip_get = 1 #It's determined later
		while((retries > 0)):
			try:
				#Determine if content type is suitable
				web_rq = requests.head(url, headers=HTTP_HEADERS, timeout=TIMEOUT_T, allow_redirects=True, verify=VERIFY_CERTIFICATES)
				web_headers = web_rq.headers
				for content_type in SCRAPE_CONTENT_TYPE:
					if(content_type in str(web_headers.get("Content-Type"))):
						skip_get = 0
						break
				if(skip_get): #Skip based on content type
					with print_lock:
						log("\033[93mSkipping\033[0m {}".format(url))
				else:
					with print_lock:
						log("\033[95mScraping\033[0m \033[96mstatus={}\033[0m {}".format(web_rq.status_code, url))
					web_html = requests.get(url, headers=HTTP_HEADERS, timeout=TIMEOUT_T, verify=VERIFY_CERTIFICATES).text
					web_html = web_html.replace("<wbr>", "") #Some sites using <wbr> tag break url search
				break
			except Exception:
				with print_lock:
					traceback.print_exc()
					print("\033[31mError while downloading \033[0m{}\033[31m! Retrying in 2 minutes ({} retries remaining)\033[0m".format(url, retries))
				with threads_working_lock:
					threads_working[thread_id] = STATUS_ERROR
				time.sleep(60*2)
				retries-=1
		if(retries == 0):
			with print_lock:
				log("\033[31mMax number of retries exceeded on\033[0m {}\033[31m!\033[0m".format(url))
			continue
		elif(retries<MAX_RETRIES):
			with threads_working_lock:
				threads_working[thread_id] = STATUS_WORKING #Working again
		if(skip_get):
			continue

		#Get urls
		html_urls = analyze.get_urls(web_html) #This will get every absolute URL; this has the advantage of getting all of the urls written in plain text
		#This will also get the absolute URL for every relative path in <a> tags
		soup = BeautifulSoup(web_html, "lxml")
		for link in soup.find_all("a"):
			href = link.get("href")
			if not isinstance(href, str) or not href.strip():
				continue
			try:
				html_urls.append(urljoin(url, href))
			except ValueError:
				continue

		for html_url in html_urls:
			html_url_no_schema = analyze.truncate_schema(html_url)
			try: #Skip private ip addresses
				if(ipaddress.ip_address(html_url_no_schema).is_private):
					with print_lock:
						log("\033[31mSkipping local address\033[0m {}".format(html_url_no_schema))
					continue
			except ValueError:
				pass

			if(("mediafire.com" in html_url) or ("mfi.re" in html_url)): #If mediafire
				with output_lock:
						already_downloaded = 0
						if(html_url_no_schema in output_list):
							already_downloaded = 1
						if(not already_downloaded):
							with open(output, "a") as fl:
								fl.write(html_url)
								fl.write("\n")
							output_list.append(html_url_no_schema)
							with print_lock:
								log("\033[92mFound\033[0m {}".format(html_url))
			else: #Recurse into every site which isn't mediafire
				if(url_filter):
					if(not url_filter in html_url): #Filter url
						continue
				elif(url_regex):
					if(not url_regex.match(html_url)):
						continue
				with archive_lock:
						already_downloaded = 0
						if(html_url_no_schema in archive):
							already_downloaded = 1
						if(not already_downloaded):
							download_queue.put(html_url)
							archive.append(html_url_no_schema)

if(__name__ == "__main__"):
	#CLI front end
	parser = argparse.ArgumentParser(description="Mediafire link web scraper")
	parser.add_argument("--threads", type=positive_int, default=6, help="How many threads to use; if the site you're trying to crawl will start displaying captchas or smth the amount of threads should be reduced; default is 6")
	parser_filters = parser.add_mutually_exclusive_group()
	parser_filters.add_argument("--filter", default="", help="Only scrape websites where filter is found in the url")
	parser_filters.add_argument("--regex", default="", help="Same as filter but uses regex")
	parser.add_argument("start", help="Start URL from which the scraper will begin to recursively scrape pages")
	parser.add_argument("output", help="File where a list of links will be saved")

	args = parser.parse_args()

	#If archive or output file doesn't exist create it
	if(not os.path.exists(args.output)):
		with open(args.output, 'w'): pass

	#Start workers
	output_list = []
	archive = []
	worker_list = []
	print_lock = threading.Lock()
	output_lock = threading.Lock()
	archive_lock = threading.Lock() #Should also be locked while reading to avoid race conditions
	threads_working_lock = threading.Lock()
	download_queue = queue.Queue()
	download_queue.put(args.start)
	threads_working = [0]*args.threads #Keep track of which threads are working
	for i in range(args.threads):
		current_worker = threading.Thread(target=worker,
		                                  daemon=True,
		                                  args=(download_queue,
		                                        args.output,
		                                        output_list,
		                                        archive,
		                                        print_lock,
		                                        output_lock,
		                                        archive_lock,
		                                        args.filter,
		                                        re.compile(args.regex),
		                                        i,
		                                        threads_working,
		                                        threads_working_lock,))
		current_worker.name = i
		current_worker.start()
		worker_list.append(current_worker)
	#Monitor threads
	#In this case waiting for threads to exit wouldn't be possible because if
	#a thread has nothing to do it must wait for pages to scrape. If a thread
	#would exit immediately it wouldn't wait for the first thread to fetch URLs
	#from the starting page and as a result only a single thread would remain.
	any_thread_alive = 1
	while(any_thread_alive):
		time.sleep(15)

		with threads_working_lock:
			threads_working_copy = threads_working.copy() #Copy thread status
		#Check if any thread is alive
		any_thread_alive = 0
		for i in range(args.threads):
			if(threads_working_copy[i]):
				any_thread_alive = 1
				break

		#Show thread status
		for i in range(args.threads):
			thread_color = ""
			if(threads_working_copy[i] == 0): thread_color = "\033[31m"
			elif(threads_working_copy[i] == 1): thread_color = "\033[32m"
			elif(threads_working_copy[i] == 2): thread_color = "\033[33m"
			threads_working_copy[i] = thread_color + str(i) + "\033[0m"
		with print_lock:
			log("\033[90mThread status:\033[0m " + "".join(threads_working_copy))
