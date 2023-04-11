import time
import pychromecast
import pychromecast.discovery
import random
import os
import http.server
import socketserver
import threading
import socket
import image_processing
import uuid
import enum
import yaml
import sys
import zeroconf

##### Configurable constants (via config.yaml)
local_images_path= "images/"
local_temp_image_path= local_images_path + "temp/" # must be child of local_images_path in order to serve to Chromecast
http_server_port= 8000
# Resize generated images down to this scale, so that they can be loaded faster by chromecast.
# Adjust to max support resolution of your chromecast.
max_image_height_pixels= 720
chromecast_friendly_name= "Family Room TV"
slideshow_duration_seconds= 5

# Not configurable (no need to expose additional complexity)
local_temp_image_list_file_name= "pycastblaster_temp_files.txt"
local_temp_image_list_file_path= os.path.join(local_temp_image_path, local_temp_image_list_file_name)

def load_config():
    config_file_path= "config.yaml" if (len(sys.argv) == 1) else sys.argv[1]

    if not os.path.exists(config_file_path):
        print("No config file '%s', using default values" % config_file_path)
    else:
        with open(config_file_path) as config_file:
            config_yaml= yaml.safe_load(config_file)

            global local_images_path
            global local_temp_image_path
            global local_temp_image_list_file_path
            global http_server_port
            global chromecast_friendly_name
            global slideshow_duration_seconds

            if "images_path" in config_yaml: local_images_path= config_yaml["images_path"]
            # local_temp_image_path must be child of local_images_path in order to serve to Chromecast
            if "temp_directory" in config_yaml:
                local_temp_image_path= os.path.join(local_images_path, config_yaml["temp_directory"])
                # have the default local_temp_image_list_file_path be relative to local_temp_image_path
                local_temp_image_list_file_path= os.path.join(local_temp_image_path, local_temp_image_list_file_name)
            if "http_server_port" in config_yaml: http_server_port= int(config_yaml["http_server_port"])
            if "chromecast_name" in config_yaml: chromecast_friendly_name= config_yaml["chromecast_name"]
            if "slideshow_duration_seconds" in config_yaml: slideshow_duration_seconds= float(config_yaml["slideshow_duration_seconds"])
            if "max_image_height_pixels" in config_yaml: max_image_height_pixels= int(config_yaml["max_image_height_pixels"])

# We must load the config before setting...
#   -server_url, since it references http_server_port
#   -image_processing.max_image_height_pixels, since we can't modify it from inside load_config(), we have to redirect through a global

load_config()

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

server_url= "http://" + get_ip() + ":" + str(http_server_port)
image_processing.max_image_height_pixels= max_image_height_pixels

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
# 2. Remove the root of the local_images_path because HTTPHandler uses that as the root directory, so it's
# not included.
def local_image_file_path_to_url(local_image_file_path):
    return server_url + "/" + os.path.relpath(local_image_file_path, local_images_path)

# Convert a URL path (for a URL served by *our* HTTP server) back to a local path
def url_to_local_image_file_path(url):
    return local_images_path + url[len(server_url + "/"):]

# Custom class in order to serve up a specific subdirectory
class HTTPHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=local_images_path, **kwargs)

def web_server_thread():
    with http.server.ThreadingHTTPServer(("", http_server_port), HTTPHandler) as http_server:
        print("serving at port", http_server_port)
        http_server.serve_forever()

class ImageServerThread(threading.Thread):
    def __init__(self, caster, image_references, temp_image_list_file, temp_image_file_names):
        threading.Thread.__init__(self)
        self.stop_thread_event= threading.Event()
        self.caster= caster
        self.image_references= image_references
        self.temp_image_list_file= temp_image_list_file
        self.temp_image_file_names= temp_image_file_names

    def run(self):
        # When we splice one portrait image with the next one in the list, we don't want to display that image
        # when we encounter it so we set this bool to ignore it.
        skip_next_portrait= False
        image_count= len(self.image_references)

        while (not self.stop_thread_event.is_set()):
            for image_index, image_reference in enumerate(self.image_references):
                # Lazily evaluate IsPortrait rather than on startup because it's slow (need to open image file and
                # potentially transpose it)
                if image_reference.image_layout == ImageLayout.Unknown:
                    image_reference.image_layout= ImageLayout.Portrait if image_processing.image_is_portait(image_reference.local_image_path) else ImageLayout.Landscape
                    self.image_references[image_index]= image_reference # Update list of images so we don't need to evaluate this image again

                if image_reference.image_layout == ImageLayout.Landscape:
                    # Process image on the fly, generate a temporary file to store the processed image
                    temp_image_file_name= os.path.join(local_temp_image_path, str(uuid.uuid4())) + ".jpg"
                    # Update temp_image_file_name in case process_image renamed it
                    temp_image_file_name= image_processing.process_image_file(image_reference.local_image_path, temp_image_file_name)
                    self.temp_image_file_names.append(temp_image_file_name)
                    # generate a temporary image reference to the processed image
                    image_reference= ImageReference(temp_image_file_name, local_image_file_path_to_url(temp_image_file_name), ImageLayout.Landscape)
                elif skip_next_portrait:
                    # If this image is a portait, clear skip_next_portait and skip it
                    skip_next_portrait= False
                    continue
                else: # ImageLayout.Portrait
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

                        if search_image.image_layout == ImageLayout.Portrait:
                            skip_next_portrait= True
                            # Select a temporary file name for the spliced image (generate a unique ID since chromecast caches images
                            # if we reuse file names)
                            spliced_image_file_name= os.path.join(local_temp_image_path, str(uuid.uuid4())) + ".jpg"
                            self.temp_image_file_names.append(spliced_image_file_name)
                            print("Splicing '%s' + '%s' into '%s'" % (image_reference.local_image_path, search_image.local_image_path, spliced_image_file_name))
                            # create temporary spliced image
                            image_processing.splice_images(image_reference.local_image_path, search_image.local_image_path, spliced_image_file_name)
                            # generate a temporary image reference to the spliced image that now has a landscape layout
                            image_reference= ImageReference(spliced_image_file_name, local_image_file_path_to_url(spliced_image_file_name), ImageLayout.Landscape)
                            break

                # clean up temporary spliced images, leave a few around in-case they're still being served
                if len(self.temp_image_file_names) > 2:
                    to_delete= self.temp_image_file_names.pop(0)
                    if os.path.exists(to_delete):
                        print("Purging temporary image '%s'" % to_delete)
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
                    self.stop_thread_event.set()
                    print("Stopping Image Server thread because we failed to play media (timed out?).")
                    break
                time.sleep(slideshow_duration_seconds)
            
            random.shuffle(self.image_references)

class ChromeCastPoller:
    def __init__(self, chromecast_friendly_name, image_references, temp_image_list_file, temp_image_file_names):
        def add_callback(uuid, _service):
            print("Chromecast added %s (%s)", self.browser.devices[uuid].friendly_name, uuid)
            
            if (self.browser.devices[uuid].friendly_name == self.friendly_name):
                if self.cast_lock.acquire():
                    # Clean up any lingering image serving thread first
                    if self.image_serving_thread and self.image_serving_thread.is_alive():
                        self.stop_image_server()

                    self.chromecast= pychromecast.get_chromecast_from_cast_info(self.browser.devices[uuid], zconf=self.browser.zc, tries= 2, retry_wait= 2.0, timeout= 5.0)
                    self.chromecast.wait() # Wait to connect before starting the image server                 
                    self.image_serving_thread= ImageServerThread(self, self.image_references, self.temp_image_list_file, self.temp_image_file_names)
                    
                    # Release the lock *before* starting the image_serving_thread so that it doesn't fail to acquire the cast_lock on the first (few?) iterations
                    self.cast_lock.release()

                    self.image_serving_thread.start()

        def remove_callback(uuid, _service, cast_info):
            print("Chromecast removed %s (%s)", self.browser.devices[uuid].friendly_name, uuid)

            def get_chromecast_from_uuid(uuid):
                return pychromecast.get_chromecast_from_cast_info(self.browser.devices[uuid], zconf=self.browser.zc)

            if (self.browser.devices[uuid].friendly_name == self.friendly_name):
                if self.cast_lock.acquire():
                    self.stop_image_server()
                    self.chromecast= None

                    self.cast_lock.release()


        self.browser= pychromecast.discovery.CastBrowser(pychromecast.discovery.SimpleCastListener(add_callback, remove_callback), zeroconf.Zeroconf())
        self.friendly_name= chromecast_friendly_name
        self.cast_lock= threading.Lock()
        self.chromecast= None
        self.image_serving_thread= None

        # plumbing to image_server_thread
        self.image_references= image_references
        self.temp_image_list_file= temp_image_list_file
        self.temp_image_file_names= temp_image_file_names
        
        self.browser.start_discovery()

        print("Chrome Cast poller started, looking for '%s'" % self.friendly_name)

    def __del__(self):
        if self.cast_lock.acquire():
            if self.chromecast:
                self.chromecast.quit_app()
                self.browser.stop_discovery()

    def stop_image_server(self):
        self.image_serving_thread.stop_thread_event.set()
        self.image_serving_thread.join()

    def try_to_play_media(self, url):
        success= False
        # Don't block, in order to avoid deadlocks when remove_callback has been called and is trying to signal image_serving_thread to stop.
        if self.cast_lock.acquire(blocking= False):
            extension= os.path.splitext(url)[1].lower()
            content_type= content_type_dictionary[extension]
            print("Serving '%s'" % url)
            try:
                self.chromecast.media_controller.play_media(url, content_type)
                self.chromecast.media_controller.block_until_active(timeout=1.0)
                success= self.chromecast.media_controller.session_active_event.is_set()
            except pychromecast.error.NotConnected:
                print("Couldn't play media, Chromecast not connected")
            except pychromecast.error.ControllerNotRegistered:
                print("Couldn't play media, Controller not registered")

            self.cast_lock.release()
        
        return success

# class ImageScanningThread(threading.Thread):
#     def __init__(self):
#         threading.Thread.__init(self, daemon= True)
#         self.image_references= [] # ImageReferences
#         self.daemon= True

#     def run(self):
#         # TODO scan for files
#         pass

def main():
    # compile list of images from image file share
    image_references= [] # ImageReferences

    random.seed()

    print("Serving local directory '%s' and spinning up HTTP server '%s'" % (
        local_images_path,
        server_url))
    
    # Build images as a tuple of (URL path, is_portait). is_portait lets us quickly find and splice portrait
    # images together into a temporary image to serve
    # -Skip images that are in local_spliced_image_path (maybe left-over from a previous run)        
    image_references= [
        ImageReference(image_path, local_image_file_path_to_url(image_path), ImageLayout.Unknown)
        for image_path in image_processing.get_images_from_local_path(local_images_path)
            if not image_path.startswith(local_temp_image_path)]
    # Spin up a separate thread to run a web server. The server exposes images in local_images_path to the Chromecast
    threading.Thread(target= web_server_thread).start()

    # Make sure the spliced image path exists since it's for files generated by the application, don't expect
    # users to create it
    if not os.path.exists(local_temp_image_path):
        os.makedirs(local_temp_image_path)

    # delete any temp files we created from a previous run (by tracking a list of files)
    # if the list file doesn't exist yet then create it now to track temp files created this run
    if os.path.exists(local_temp_image_list_file_path):
        temp_image_list_file= open(local_temp_image_list_file_path, "r+")
        for line in temp_image_list_file:
            # make sure to strip out any file path from file names so that any file we delete must be contained
            # in the directory we expect
            file_name_to_delete= os.path.join(local_temp_image_path, os.path.basename(line.strip()))
            if os.path.exists(file_name_to_delete):
                print("Purging temporary image '%s' from '%s'" % (file_name_to_delete, local_temp_image_list_file_path))
                os.remove(file_name_to_delete)
        
        temp_image_list_file.seek(0)
        temp_image_list_file.truncate()
    else:
        temp_image_list_file= open(local_temp_image_list_file_path, "w+")

    random.shuffle(image_references)

    temp_image_file_names= []
    caster= ChromeCastPoller(chromecast_friendly_name, image_references, temp_image_list_file, temp_image_file_names)

    # Quit gracefully (stops casting session)
    exit= False # nothing sets this now but we can poke it in the debugger

    # Just blocking to keep the caster alive
    while(not exit):
        time.sleep(1.0)

    temp_image_list_file.close()

main()
