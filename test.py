import time
import pychromecast
import web_crawler
import random
import os.path
import http.server
import socketserver
import threading
import socket
import image_processing

##### config constants - TODO: don't hardcode?
#If images are hosted on an external server. Else, images are hosted locally
#else, we will spin up our own HTTP server
external_server_url= None #"http://192.168.0.69:8000"
local_image_path= "nas_mount/" #"images/"
http_server_port= 8000
chromecast_friendly_names= ["Family Room TV", "Basement Lights TV"]
image_cache_file_path= "image_cache.txt"
slideshow_duration_seconds= 5

# file extension to MIME type
content_type_dictionary= {
    ".jpg" :  "image/jpeg",
    ".jpeg" : "image/jpeg",
    ".png" : "image.png" }

def get_image_urls_from_external_server():
    # Crawling the image server via HTTP requests is slow so cache the results in a file
    if (os.path.exists(image_cache_file_path)): # Use cache
        images= []
        with open(image_cache_file_path) as image_cache_file:
            images= [line.strip() for line in image_cache_file.readlines()]
        return images
    else: # Slow HTTP crawling
        images= web_crawler.parse_url(external_server_url)
        with open(image_cache_file_path, "w") as image_cache_file:
            image_cache_file.writelines('\n'.join(images))
        return images

# Custom class in order to serve up a specific subdirectory
class HTTPHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=local_image_path, **kwargs)

def web_server_thread():
    with http.server.ThreadingHTTPServer(("", http_server_port), HTTPHandler) as http_server:
        print("serving at port", http_server_port)
        http_server.serve_forever()

def main():
    # compile list of images from image server/file share
    images= []

    if (external_server_url):
        print("Using external server '%s'" % external_server_url)
        images= get_image_urls_from_external_server()
    else:
        server_url= "http://" + socket.gethostbyname(socket.gethostname()) + ":" + str(http_server_port)
        print("Serving local directory '%s' and spinning up HTTP server '%s'" % (
            local_image_path,
            server_url))
        # Build the URL path:
        # 1. include the root server URL
        # 2. Remove the root of the local_image_path because HTTPHandler uses that as the root directory, so it's
        # not included.
        images= [
            server_url + "/" + os.path.relpath(image_path, local_image_path)
            for image_path in image_processing.get_images_from_local_path(local_image_path)]
        # Spin up a separate thread to run a web server. The server exposes images in local_image_path to the Chromecast
        threading.Thread(target= web_server_thread).start()
        
    random.shuffle(images)

    # List chromecasts on the network, but don't connect
    services, browser = pychromecast.discovery.discover_chromecasts()
    # Shut down discovery
    pychromecast.discovery.stop_discovery(browser)

    # Discover and connect to chromecasts named Living Room
    chromecasts, browser = pychromecast.get_listed_chromecasts(friendly_names=chromecast_friendly_names)

    cast = chromecasts[0]
    # Start worker thread and wait for cast device to be ready
    cast.wait()
    print(cast.cast_info)

    print(cast.status)
    mc = cast.media_controller
    
    # Quit gracefully (stops casting session)
    exit= False # nothing sets this now but we can poke it in the debugger

    while(not exit):
        for image in images:
            extension= os.path.splitext(image)[1].lower()
            content_type= content_type_dictionary[extension]
            mc.play_media(image, content_type)
            mc.block_until_active()
            time.sleep(slideshow_duration_seconds)
            
            if (exit):
               break
        
        random.shuffle(images)

    cast.quit_app()

    # Shut down discovery
    pychromecast.discovery.stop_discovery(browser)

main()