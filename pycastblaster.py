import enum
import http.server
import json
import os
import random
import shutil
import socket
import sys
import threading
import time
import uuid

import pychromecast
import pychromecast.discovery
import ruamel.yaml
import zeroconf

import image_processing

class Config:
	def __init__(self) -> None:
		##### Configurable constants (via config.yaml)
		self.local_images_path= "images/"
		self.local_temp_path= "temp/"
		self.http_server_port= 8000
		# Resize generated images down to this scale, so that they can be loaded faster by chromecast.
		# Adjust to max support resolution of your chromecast.
		self.max_image_height_pixels= 720
		self.chromecast_friendly_name= "Family Room TV"
		self.slideshow_duration_seconds= 5
		self.interruption_idle_seconds= 20
		self.image_scanning_frequency_seconds= 10 * 60 # 10 minutes

		# Not configurable (no need to expose additional complexity)
		self.local_temp_image_list_file_name= "pycastblaster_temp_files.txt"
		self.local_temp_image_list_file_path= os.path.join(self.local_temp_path, self.local_temp_image_list_file_name)
		self.server_url= "http://" + get_ip() + ":" + str(self.http_server_port)
		image_processing.set_max_image_height(self.max_image_height_pixels)

class Globals:
	def __init__(self) -> None:
		self.exit_event= threading.Event() # Quit gracefully (stops casting session)
		self.reload_event= threading.Event() # Restart gracefully after quitting. Set *before* setting exit_event.
		self.paused= False
		# State of the ImageServerThread, stored in globals so that it can be accessed by the HTTP Request Handlers
		self.image_references= ()
		self.current_image_reference_index= -1
		self.image_reference_lock= threading.Lock()
		
		self.recent_logs= []
		self.recent_logs_lock= threading.Lock()

g_config= None # Config
g_globals= None # Globals()

def get_config_file_path():
	return "config.yaml" if (len(sys.argv) == 1) else sys.argv[1]

def load_config():
	config_file_path= get_config_file_path()

	if not os.path.exists(config_file_path):
		log("No config file '%s', using default values" % config_file_path)
	else:
		with open(config_file_path) as config_file:
			yaml_reader= ruamel.yaml.YAML() # round-trip loader preserves comments
			config_yaml= yaml_reader.load(config_file)

			global g_config

			if "images_path" in config_yaml: g_config.local_images_path= config_yaml["images_path"]
			# local_temp_image_path must be child of local_images_path in order to serve to Chromecast
			if "temp_path" in config_yaml:
				g_config.local_temp_path= config_yaml["temp_path"]
				# have the default local_temp_image_list_file_path be relative to local_temp_image_path
				g_config.local_temp_image_list_file_path= os.path.join(g_config.local_temp_path, g_config.local_temp_image_list_file_name)
			if "http_server_port" in config_yaml:
				g_config.http_server_port= int(config_yaml["http_server_port"])
				g_config.server_url= "http://" + get_ip() + ":" + str(g_config.http_server_port)
			if "chromecast_name" in config_yaml: g_config.chromecast_friendly_name= config_yaml["chromecast_name"]
			if "slideshow_duration_seconds" in config_yaml: g_config.slideshow_duration_seconds= float(config_yaml["slideshow_duration_seconds"])
			if "max_image_height_pixels" in config_yaml:
				g_config.max_image_height_pixels= int(config_yaml["max_image_height_pixels"])
				image_processing.set_max_image_height(g_config.max_image_height_pixels)
			if "interruption_idle_seconds" in config_yaml: g_config.interruption_idle_seconds= int(config_yaml["interruption_idle_seconds"])
			# User-facing config option is in minutes for convenience, but using seconds internally since that's what time.sleep() uses.
			if "image_scanning_frequency_minutes" in config_yaml: g_config.image_scanning_frequency_seconds= \
				60 * int(config_yaml["image_scanning_frequency_minutes"])

# In Ubuntu, socket.gethostbyname(socket.gethostname()) returns '127.0.0.1', instead of 192.168.0.X
# Per, https://stackoverflow.com/questions/166506/finding-local-ip-addresses-using-pythons-stdlib, this will return
# the LAN IP address from behind a NAT, not the public IP address of your modem.
def get_ip():
		s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		s.settimeout(0)
		try:
			# doesn't even have to be reachable
			s.connect(('8.8.8.8', 1))
			IP = s.getsockname()[0]
		except Exception:
			IP = '127.0.0.1'
		finally:
			s.close()
		return IP

# file extension to MIME type
content_type_dictionary= {
	".jpg" :  "image/jpeg",
	".jpeg" : "image/jpeg",
	".png" : "image.png" }

class ImageLayout(enum.IntEnum):
	Unknown= 0
	Landscape= 1
	Portrait= 2

class ImageReference:
	def __init__(self, local_image_path, url_path, image_layout=ImageLayout.Unknown):
		self.local_image_path= local_image_path
		self.url_path= url_path
		self.image_layout= image_layout

# Build the URL path:
# 1. include the root server URL
# 2. Remove the root of the local_temp_path because HTTPHandler uses that as the root directory, so it's
# not included.
def local_image_file_path_to_url(local_image_file_path):
	return g_config.server_url + "/" + os.path.relpath(local_image_file_path, g_config.local_temp_path)

def log(string_arg):
	current_time = time.localtime()
	string= "%s: %s" % (time.strftime("%m/%d/%Y %H:%M:%S", current_time), string_arg)
	print(string)

	global g_globals

	if (g_globals):
		g_globals.recent_logs_lock.acquire()
		g_globals.recent_logs.append(string)

		max_log_lines= 200

		while (len(g_globals.recent_logs) > max_log_lines):
			g_globals.recent_logs.pop(0)

		g_globals.recent_logs_lock.release()

# Custom class in order to serve up a specific subdirectory
class HTTPHandler(http.server.SimpleHTTPRequestHandler):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, directory=g_config.local_temp_path, **kwargs)

	def _set_response(self, status):
		self.send_response(status)
		self.send_header('Content-type', 'text/html')
		self.end_headers()

	def do_GET(self):
		global g_globals

		if (self.path == "/state"):
			g_globals.image_reference_lock.acquire()
			g_globals.recent_logs_lock.acquire()

			status= http.HTTPStatus.OK
			message= "GET request for {}".format(self.path)
			image_index_min= max(g_globals.current_image_reference_index - 4, 0)
			image_index_max= min(g_globals.current_image_reference_index + 10, len(g_globals.image_references) - 1)
			image_subset= g_globals.image_references[image_index_min:image_index_max]

			state_data= {
				"chromecast_name" : g_config.chromecast_friendly_name,
				"is_paused" : g_globals.paused,
				"slideshow_duration_seconds" : g_config.slideshow_duration_seconds,
				"image_path" : g_config.local_images_path,
				"images" : [os.path.relpath(image_reference.local_image_path, g_config.local_images_path) for image_reference in image_subset],
				"current_image_index" : g_globals.current_image_reference_index,
				"images_min_index" : image_index_min,
				"image_count" : len(g_globals.image_references),
				"log_lines" : g_globals.recent_logs
			}
			message= json.dumps(state_data)
			g_globals.recent_logs_lock.release()
			g_globals.image_reference_lock.release()

			self._set_response(status)
			self.wfile.write(message.encode('utf-8'))
		elif (self.path.startswith("/image/")):
			g_globals.image_reference_lock.acquire()
			image_path_rel= self.path.removeprefix("/image/").replace("%20", " ")

			try:
				# Make sure the requested image path is within the local image path - no extracurricular explorations!
				local_image_path_abs= os.path.abspath(g_config.local_images_path)
				image_path_abs= os.path.abspath(os.path.join(local_image_path_abs, image_path_rel))

				if os.path.commonpath([local_image_path_abs]) == os.path.commonpath([local_image_path_abs, image_path_abs]):
					with open(os.path.join(g_config.local_images_path, image_path_rel), "rb") as image_file:
						#note that this potentially makes every file on your computer readable by the internet
						self.send_response(http.HTTPStatus.OK)
						extension= os.path.splitext(image_path_rel)[1].lower()
						content_type= content_type_dictionary[extension]
						self.send_header('Content-type', content_type)
						self.end_headers()
						self.wfile.write(image_file.read())
				else:
					self.send_error(http.HTTPStatus.NOT_FOUND,"File Not Found: '%s'" % (image_path_rel))
			except IOError as e:
				self.send_error(http.HTTPStatus.NOT_FOUND,"File Not Found: '%s': '%s'" % (image_path_rel, e))
			except Exception as e:
				self.send_error(http.HTTPStatus.BAD_REQUEST,"Error: '%s'" % e)
			finally:
				g_globals.image_reference_lock.release()
		else:
			super().do_GET()

	def do_POST(self):
		global g_globals
		global g_config

		content_length = int(self.headers['Content-Length']) # <--- Gets the size of data
		post_data = self.rfile.read(content_length) # <--- Gets the data itself
		post_data_string= post_data.decode('utf-8')
		# log("POST request,\nPath: %s\nHeaders:\n%s\n\nBody:\n%s\n" % (str(self.path), str(self.headers), post_data_string))
		status= http.HTTPStatus.OK
		message= "POST request for {}".format(self.path)

		if (self.path == "/command"):
			post_data_json= json.loads(post_data_string)
			command_name= post_data_json["name"]
			command_parameters= post_data_json["parameters"]

			if (command_name == "exit"):
				log("Received 'exit' command, quitting.")
				g_globals.exit_event.set()
			elif (command_name == "pause"):
				g_globals.paused= not g_globals.paused
				log("Received 'pause' command, toggling pause '%s'." % ("On" if g_globals.paused else "Off"))
			elif (command_name == "reload"):
				log("Received 'reload' command, restarting.")
				g_globals.reload_event.set()
				g_globals.exit_event.set()
			elif (command_name == "duration_update"):
				duration_seconds= float(command_parameters)
				if duration_seconds <= 0:
					message= command_name + ": Invalid duration '%s'" % (command_parameters)
					status= http.HTTPStatus.BAD_REQUEST
				else:
					log("Received '%s' command, updating duration (%f) -> (%f)" % (command_name, g_config.slideshow_duration_seconds, duration_seconds))
					g_config.slideshow_duration_seconds= duration_seconds

					config_file_path= get_config_file_path()

					if not os.path.exists(config_file_path):
						log("No config file '%s', unable to save change to slideshow duration" % config_file_path)
					else:
						with open(config_file_path) as config_file_read:
							yaml_read_writer= ruamel.yaml.YAML() # round-trip loader preserves comments
							yaml_read_writer.preserve_quotes= True
							config_yaml= yaml_read_writer.load(config_file_read)
							config_yaml["slideshow_duration_seconds"]= g_config.slideshow_duration_seconds

							config_file_read.close()

							# Safety dance - make sure we don't do a partial write of the config file
							config_file_path_new= config_file_path + ".new"
							config_file_path_old= config_file_path + ".old"
							with open(config_file_path_new, "w+") as config_file_write:
								yaml_read_writer.dump(config_yaml, config_file_write)
								config_file_write.close()
								
								# If os.replace is atomic and safe then we could do: os.replace(config_file_path, config_file_path_new)
								if os.path.exists(config_file_path_old):
									os.remove(config_file_path_old)
								os.rename(config_file_path, config_file_path_old)
								os.rename(config_file_path_new, config_file_path)
								os.remove(config_file_path_old)
								
			else:
				message= "Received unknown command '%s'" % (str(command_name))
				log(message)
				status= http.HTTPStatus.BAD_REQUEST

			self._set_response(status)
			self.wfile.write(message.encode('utf-8'))


class WebServerThread(threading.Thread):
	def __init__(self):
		threading.Thread.__init__(self, daemon=True)
		self.http_server= None

	def run(self):
		try:
			with http.server.ThreadingHTTPServer(("", g_config.http_server_port), HTTPHandler) as self.http_server:
				log("serving at port [%d]" % g_config.http_server_port)
				self.http_server.serve_forever()
		except Exception as e:
			log("ERROR: Failed to start web server: '%s'" % e)
			global g_globals
			g_globals.exit_event.set()
	
	def shutdown(self):
		if self.http_server:
			self.http_server.shutdown()


class ImageServerThread(threading.Thread):
	def __init__(self, caster, temp_image_list_file, temp_image_file_names):
		threading.Thread.__init__(self, daemon=True)
		
		# Synchronization: internal events, use start_serving and stop_serving_and_wait
		self.should_serve= threading.Event()
		self.not_serving= threading.Event()
		self.not_serving.set()

		self.caster= caster
		self.image_references= []
		self.pending_new_image_references= None # 
		random.shuffle(self.image_references)
		self.temp_image_list_file= temp_image_list_file
		self.temp_image_file_names= temp_image_file_names

		self.previous_image_index= 0
		# When we splice one portrait image with the next one in the list, we don't want to display that image
		# when we encounter it so we remember its name to skip when we encounter it.
		# It's possible if we merge in new portait images ahead of the skipped portait that we might need to track
		# more than one portrait image to skip, so make this a set.
		self.skip_portait_image_names= set()

	def run(self):
		while not g_globals.exit_event.is_set():
			self.should_serve.wait()
			self.not_serving.clear()
			while self.should_serve.is_set() and not g_globals.exit_event.is_set():
				self.merge_pending_image_references()
				self.serve_images()              
			self.not_serving.set()

	def start_serving(self):
		self.should_serve.set()

	def stop_serving_and_wait(self):
		self.should_serve.clear()
		return self.not_serving.wait()
	
	def add_image_references(self, new_image_references):
		# Image Server thread is may be consuming pending_new_image_references, wait until it's done
		while(self.pending_new_image_references is not None):
			time.sleep(0.5)
		self.pending_new_image_references= new_image_references

	def merge_pending_image_references(self):
		if not self.pending_new_image_references is None:
			# Merge in the new images so that we don't replay images we've already served.
			log("Merging in [%d] new images." % (len(self.pending_new_image_references)))

			# First, shuffle the new images with the images that haven't been served yet
			shuffled_image_references= self.image_references[self.previous_image_index:]
			shuffled_image_references= shuffled_image_references + self.pending_new_image_references
			random.shuffle(shuffled_image_references)

			# Then, prepend the images that have already been served
			merged_image_references= self.image_references[:max(self.previous_image_index - 1, 0)] + shuffled_image_references

			# Finally, swap the merged image list into place
			self.image_references= merged_image_references

			# Keep the global list of image references up to date
			global g_globals
			g_globals.image_reference_lock.acquire()
			g_globals.image_references= self.image_references
			g_globals.image_reference_lock.release()

			# Invalidate self.pending_new_image_references once we're done to signal that we're ready to accept
			# more new images from the Image Scanner.
			self.pending_new_image_references= None

	def serve_images(self):
		image_count= len(self.image_references)
		start_index= max(0, min(image_count - 1, self.previous_image_index - 1))
		interrupted= False

		for image_index, image_reference in enumerate(self.image_references[start_index:], start_index):
			global g_globals
			g_globals.image_reference_lock.acquire()
			g_globals.current_image_reference_index= image_index
			g_globals.image_reference_lock.release()

			# Lazily evaluate IsPortrait rather than on startup because it's slow (need to open image file and
			# potentially transpose it)
			if image_reference.image_layout == ImageLayout.Unknown:
				image_reference.image_layout= ImageLayout.Portrait if image_processing.image_is_portait(image_reference.local_image_path) else ImageLayout.Landscape
				self.image_references[image_index]= image_reference # Update list of images so we don't need to evaluate this image again

			processed_image= False

			if image_reference.local_image_path in self.skip_portait_image_names:
				# If this image is a portait we've already displayed then, skip it
				self.skip_portait_image_names.remove(image_reference.local_image_path)
				continue
			elif image_reference.image_layout == ImageLayout.Portrait:
				# Find the next portait image in images to splice with
				# If there is one then set skip_next_portait, splice it with this one, and replace image
				for search_image_index in range(image_index + 1, image_count):
					search_image= self.image_references[search_image_index]

					# Lazily evaluate IsPortrait rather than on startup because it's slow (need to open image file and
					# potentially transpose it)
					if search_image.image_layout == ImageLayout.Unknown:
						search_image.image_layout= ImageLayout.Portrait if image_processing.image_is_portait(search_image.local_image_path) else ImageLayout.Landscape
						# Update list of images so we don't need to evaluate this image again
						self.image_references[search_image_index]= search_image

					if (search_image.image_layout == ImageLayout.Portrait and
						not search_image.local_image_path in self.skip_portait_image_names):
						self.skip_portait_image_names.add(search_image.local_image_path)
						# Select a temporary file name for the spliced image (generate a unique ID since chromecast caches images
						# if we reuse file names)
						spliced_image_file_name= os.path.join(g_config.local_temp_path, str(uuid.uuid4())) + ".jpg"
						self.temp_image_file_names.append(spliced_image_file_name)
						log("Splicing '%s' + '%s' into '%s'" % (image_reference.local_image_path, search_image.local_image_path, spliced_image_file_name))
						# create temporary spliced image
						image_processing.splice_images(image_reference.local_image_path, search_image.local_image_path, spliced_image_file_name)
						# generate a temporary image reference to the spliced image that now has a landscape layout
						image_reference= ImageReference(spliced_image_file_name, local_image_file_path_to_url(spliced_image_file_name), ImageLayout.Landscape)
						processed_image= True
						break

			if not processed_image:
				# Process image on the fly, generate a temporary file to store the processed image
				temp_image_file_name= os.path.join(g_config.local_temp_path, str(uuid.uuid4())) + ".jpg"
				# Update temp_image_file_name in case process_image renamed it
				temp_image_file_name= image_processing.process_image_file(image_reference.local_image_path, temp_image_file_name)
				self.temp_image_file_names.append(temp_image_file_name)
				# generate a temporary image reference to the processed image
				image_reference= ImageReference(temp_image_file_name, local_image_file_path_to_url(temp_image_file_name), ImageLayout.Landscape)
				processed_image= True

			# clean up temporary spliced images, leave a few around in-case they're still being served
			if len(self.temp_image_file_names) > 2:
				to_delete= self.temp_image_file_names.pop(0)
				if os.path.exists(to_delete):
					log("Purging temporary image '%s'" % to_delete)
					os.remove(to_delete)

			# update list of temporary image files
			self.temp_image_list_file.seek(0)
			self.temp_image_list_file.truncate()
			for temp_image_file_name in self.temp_image_file_names:
				self.temp_image_list_file.write(temp_image_file_name + "\n")
			self.temp_image_list_file.flush()

			if not self.caster.try_to_play_media(image_reference.url_path):
				# If we failed to play media, the Chromecast probably disconnected, so stop trying to serve images
				# before we trigger some exception in the pychromecast library
				self.should_serve.clear()
				interrupted= True
				log("Stopping Image Server thread because we failed to play media (timed out?).")
				break

			initial_duration_seconds= g_config.slideshow_duration_seconds
			sleep_time_remaining= initial_duration_seconds
			while (sleep_time_remaining > 0.0):
				### Handle Exit Conditions
				# The casting thread signaled that we should stop, e.g. the Chromecast was removed (turned off?)
				if not self.should_serve.is_set():
					interrupted= True # This will cause us to break out of the image loop
					break

				# This should be redundant with the above, but avoid any race conditions where the ChromecastPoller resets
				# should_serve by explicitly checking g_globals.exit_event as well.
				if g_globals.exit_event.is_set():
					interrupted= True # This will cause us to break out of the image loop
					break

				# There are new pending images to merge with our list, stop serving for a moment.
				# NOTE: Do this *after* we sleep because this should be pretty quick, so if we didn't sleep then
				# we'd skip the image(s) we just prepared
				if self.pending_new_image_references is not None:
					interrupted= True # This will cause us to break out of the image loop
					break

				### Manage Timer
				# Somebody updated the duration from the website, adjust the current timer
				if (g_config.slideshow_duration_seconds != initial_duration_seconds):
					delta_time= g_config.slideshow_duration_seconds - initial_duration_seconds
					initial_duration_seconds= g_config.slideshow_duration_seconds
					sleep_time_remaining= max(sleep_time_remaining + delta_time, 0.0)

				sleep_duration= min(sleep_time_remaining, 1.0) # Sleep in one second increments

				if not g_globals.paused:
					sleep_time_remaining= sleep_time_remaining - sleep_duration

				time.sleep(sleep_duration)

			if interrupted:
				break

			self.previous_image_index= image_index

		# If we finished looping over our images without interruption then shuffle them and start at the beginning.
		# It's not quite trivial to compare previous_image_index against the number of images because we might skip
		# the last image without incrementing previous_image_index.
		if not interrupted:
			log("Image list complete, shuffling and restarting")
			random.shuffle(self.image_references)
			self.previous_image_index= 0
			self.skip_portait_image_names.clear()

class CanCastResult(enum.IntEnum):
	Success= 0
	FailNotConnected= 1
	FailNoStatus= 2
	FailExpectedActived= 3
	FailInUse= 4

class ChromeCastPoller:
	def __init__(self, chromecast_friendly_name):
		def add_callback(uuid, _service):
			log("Chromecast added %s (%s)" % (self.browser.devices[uuid].friendly_name, uuid))
			
			if (self.browser.devices[uuid].friendly_name == self.friendly_name):
				if self.cast_lock.acquire():
					# Clean up any lingering image serving thread first
					if self.image_serving_thread and self.image_serving_thread.is_alive():
						self.stop_image_server()

					self.chromecast= pychromecast.get_chromecast_from_cast_info(self.browser.devices[uuid], zconf=self.browser.zc, tries= 2, retry_wait= 2.0, timeout= 5.0)
					self.chromecast.wait() # Wait to connect before releasing self.cast_lock and allowing wait_for_idle to start the image server
					self.cast_lock.release()

		def remove_callback(uuid, _service, cast_info):
			log("Chromecast removed %s (%s)" % (self.browser.devices[uuid].friendly_name, uuid))

			def get_chromecast_from_uuid(uuid):
				return pychromecast.get_chromecast_from_cast_info(self.browser.devices[uuid], zconf=self.browser.zc)

			if (self.browser.devices[uuid].friendly_name == self.friendly_name):
				if self.cast_lock.acquire():
					self.stop_image_server()
					self.chromecast= None

					self.cast_lock.release()


		self.browser= pychromecast.discovery.CastBrowser(pychromecast.discovery.SimpleCastListener(add_callback, remove_callback), zeroconf.Zeroconf())
		self.friendly_name= chromecast_friendly_name
		self.cast_lock= threading.RLock() # making this a reentrant lock so that can_cast() can take the lock, to make sure it's always thread-safe to use
		self.chromecast= None

		self.image_serving_thread= None

	def __del__(self):
		self.stop()

	def start(self):
		# Start a separate thread to wait for the Chromecast to be idle rather than blocking this one
		self.wait_for_idle_thread= threading.Thread(target= self.wait_for_idle, daemon= True)
		self.wait_for_idle_thread.start()
		
		self.browser.start_discovery()

		log("Chrome Cast poller started, looking for '%s'" % self.friendly_name)

	def stop(self):
		if self.cast_lock.acquire():
			if self.chromecast:
				self.chromecast.quit_app()
			self.cast_lock.release()
		
		# Discovery and the browser are distinct from the Chromecast. Additionally, we can create a deadlock here
		# where we have self.cast_lock and are blocked on stop_discovery() waiting for the zeroconf thread to terminate,
		# while the zeroconf thread has triggered our remove_callback() and is waiting on self.cast_lock
		self.browser.stop_discovery()

	def stop_image_server(self):
		self.image_serving_thread.stop_serving_and_wait()

	def can_cast(self, must_be_active):
		result= (False, "")

		if self.cast_lock.acquire():    
			if (not self.chromecast or
				not self.chromecast.socket_client or
				not self.chromecast.socket_client.is_connected):
					result= (CanCastResult.FailNotConnected, "Not connected")
			elif self.chromecast.status is None:
				result= (CanCastResult.FailNoStatus, "No Status")
				# We can't really disambiguate between "Somebody else is using the default media receiver app" and
				# "We are using the Default Media Receiver app, or were and then restarted our program" so just treat
				# it as if nobody else is trying to cast right now... :/ Maybe we could look at what media is currently playing and see
				# if it's coming from our server?
			elif must_be_active and not self.chromecast.app_id == pychromecast.APP_MEDIA_RECEIVER:
				result= (CanCastResult.FailExpectedActived, "Expected Chromecast to be casting, maybe somebody is starting to cast")
			elif not self.chromecast.app_id in (None, pychromecast.IDLE_APP_ID, pychromecast.APP_MEDIA_RECEIVER):
				result= (CanCastResult.FailInUse, "Chromecast in use by '%s' (%s)" % (self.chromecast.app_display_name, self.chromecast.app_id))
			else:
				result= (CanCastResult.Success, "")

			self.cast_lock.release()
		
		return result

	def wait_for_idle(self):
		was_active= False
		while not g_globals.exit_event.is_set():
			self.image_serving_thread.not_serving.wait()
			
			if was_active and not g_globals.exit_event.is_set():
				log("Bonus interruption idle (%f s): We got interrupted, so maybe something else is trying to start" % g_config.interruption_idle_seconds)
				time.sleep(g_config.interruption_idle_seconds)

			can_cast, reason= self.can_cast(must_be_active= False)
			if can_cast == CanCastResult.Success:
				self.chromecast.media_controller.launch()
				self.chromecast.media_controller.block_until_active(10)
				self.image_serving_thread.start_serving()
				was_active= True
			else:
				was_active= False
				log("Blocking for idle, reason: %s" % reason)

			time.sleep(5)

	def try_to_play_media(self, url):
		success= False
		# Don't block, in order to avoid deadlocks when remove_callback has been called and is trying to signal image_serving_thread to stop.
		# Do wait for a little bit though, so that the image serving thread doesn't fail the first few iterations while waiting for the lock to be released
		if self.cast_lock.acquire(timeout= 1):
			can_cast, reason= self.can_cast(must_be_active=True)
			if can_cast == CanCastResult.Success:
				extension= os.path.splitext(url)[1].lower()
				content_type= content_type_dictionary[extension]
				log("Serving '%s'" % url)
				try:
					self.chromecast.media_controller.play_media(url, content_type)
					self.chromecast.media_controller.block_until_active(timeout=1.0)
					success= self.chromecast.media_controller.session_active_event.is_set()
				except pychromecast.error.NotConnected:
					log("Couldn't play media, Chromecast not connected")
				except pychromecast.error.ControllerNotRegistered:
					log("Couldn't play media, Controller not registered")
			else:
				log("Couldn't play media, reason: %s" % reason)

			self.cast_lock.release()
		
		return success

class ImageScanningThread(threading.Thread):
	def __init__(self, image_server):
		threading.Thread.__init__(self, daemon= True)
		self.local_image_paths= set() # Set: local_file_path
		self.image_server= image_server
		self.daemon= True

	def run(self):
		scan_interrupt_seconds= 10

		while(not g_globals.exit_event.is_set()):
			scan_interrupt_timestamp_seconds= time.monotonic() + scan_interrupt_seconds

			# Walk local_images_path scanning for supported image files. If we aren't already tracking them in
			# self.local_image_paths then add it to the list of new images to update the image server with.
			new_local_image_paths= []
			if (os.path.exists(g_config.local_images_path)):
				for dirpath, dirnames, filenames in os.walk(g_config.local_images_path, followlinks=True):
					for filename in filenames:
						if filename.lower().endswith(image_processing.supported_image_extensions) and not filename.startswith("._"):
							image_path= os.path.join(dirpath, filename)

							# skip temp images and images we've already processed
							if (not image_path.startswith(g_config.local_temp_path) and
							not image_path in self.local_image_paths):
								new_local_image_paths.append(image_path)
								self.local_image_paths.add(image_path)

						if scan_interrupt_seconds >= 0:
							# Update the image server periodically so that churning through a massive list of images doesn't block the image server
							# when starting up.
							new_time= time.monotonic()
							if new_time >= scan_interrupt_timestamp_seconds:
								scan_interrupt_timestamp_seconds= new_time + scan_interrupt_seconds
								self.update_image_server_blocking(new_local_image_paths)
								# Make sure to clear out the list of new images so they don't get added again.
								new_local_image_paths.clear()
			else:
				log("ERROR: Image Path '%s' does not exist" % (g_config.local_images_path))
			
			# Once we have an initial set of images, no need to update the image server in the middle of scanning images anymore, since it
			# probably slows down the scanning process.
			if len(self.local_image_paths) > 0:
				scan_interrupt_seconds= -1

			if (len(new_local_image_paths) > 0):
				self.update_image_server_blocking(new_local_image_paths)

			sleep_time_remaining_seconds= g_config.image_scanning_frequency_seconds
			while sleep_time_remaining_seconds > 0 and not g_globals.exit_event.is_set():
				sleep_step_seconds= min(sleep_time_remaining_seconds, 5.0)
				sleep_time_remaining_seconds= sleep_time_remaining_seconds - sleep_step_seconds
				time.sleep(sleep_step_seconds)

	def update_image_server_blocking(self, new_local_image_paths):
		image_references= [ImageReference(image_path, "", ImageLayout.Unknown)
			for image_path in new_local_image_paths]

		self.image_server.add_image_references(image_references)

	def get_images_from_local_path(local_image_path):
		images= []
		if (os.path.exists(local_image_path)):
			for dirpath, dirnames, filenames in os.walk(local_image_path, followlinks=True):
				images= images + [
					os.path.join(dirpath, filename)
					for filename in filenames
						if filename.lower().endswith(image_processing.supported_image_extensions) and not filename.startswith("._")]
		return images

def main():
	random.seed()

	log("Serving local directory '%s' and spinning up HTTP server '%s'" % (
		g_config.local_images_path,
		g_config.server_url))

	# Make sure the spliced image path exists since it's for files generated by the application, don't expect
	# users to create it
	if not os.path.exists(g_config.local_temp_path):
		os.makedirs(g_config.local_temp_path)

	# Copy HTML index to temp path
	shutil.copy("index.html", g_config.local_temp_path)

   # Spin up a separate thread to run a web server. The server exposes images in local_images_path to the Chromecast
	web_server= WebServerThread()
	web_server.start()

	# delete any temp files we created from a previous run (by tracking a list of files)
	# if the list file doesn't exist yet then create it now to track temp files created this run
	if os.path.exists(g_config.local_temp_image_list_file_path):
		temp_image_list_file= open(g_config.local_temp_image_list_file_path, "r+")
		for line in temp_image_list_file:
			# make sure to strip out any file path from file names so that any file we delete must be contained
			# in the directory we expect
			file_name_to_delete= os.path.join(g_config.local_temp_path, os.path.basename(line.strip()))
			if os.path.exists(file_name_to_delete):
				log("Purging temporary image '%s' from '%s'" % (file_name_to_delete, g_config.local_temp_image_list_file_path))
				os.remove(file_name_to_delete)
		
		temp_image_list_file.seek(0)
		temp_image_list_file.truncate()
	else:
		temp_image_list_file= open(g_config.local_temp_image_list_file_path, "w+")

	temp_image_file_names= []
	
	# Three pieces:
	# 1. Chromecast Poller: Waits for the Chromecast to be available
	# 2. Image Server: Serves images to Chromecast when told by the Chromecast Poller.
	# 3. Image Scanner: Periodically scans for new images and merges them into the list of the Image Server
	chromecast_poller= ChromeCastPoller(g_config.chromecast_friendly_name)
	image_serving_thread= ImageServerThread(chromecast_poller, temp_image_list_file, temp_image_file_names)
	image_scanning_thread= ImageScanningThread(image_serving_thread)

	chromecast_poller.image_serving_thread= image_serving_thread

	# Start the image server first which will block until the Chromecast poller tells it to serve
	image_serving_thread.start() # Will block on image_serving_thread.should_serve
	# Then start the image scanner to begin populating the image server
	image_scanning_thread.start()
	# Finally start the chromecast poller to look for chromecasts, now that the server is ready to serve (and will have some images soon)
	chromecast_poller.start()

	# Just blocking to keep program alive
	g_globals.exit_event.wait()

	# Notify and wait for the image serving thread specifically, since it is using temp_image_list_file, before closing the file.
	image_serving_thread.should_serve.clear()
	image_serving_thread.not_serving.wait()

	temp_image_list_file.close()

	# Stop the Chromecast Poller (disconnect from the Chromecast) after the image serving thread is done serving
	chromecast_poller.stop()

	web_server.shutdown()
	log("Waiting for web server to shut down...")
	web_server.join()
	log("Waiting for image server to shut down...")
	image_serving_thread.join()
	log("Waiting for image scanner to shut down...")
	image_scanning_thread.join()
	log("Waiting for Chromecast Poller to shut down...")
	chromecast_poller.wait_for_idle_thread.join()

def initialize():
	global g_config
	global g_globals
	
	g_config= Config()
	g_globals= Globals()
	load_config()

while True:
	initialize()
	main()

	if (not g_globals.reload_event.is_set()):
		break