import time
import pychromecast
import web_crawler
import random
import os
import http.server
import socketserver
import threading
import socket
import image_processing
import uuid
from enum import IntEnum

##### config constants - TODO: don't hardcode?
#If images are hosted on an external server. Else, images are hosted locally
#else, we will spin up our own HTTP server
external_server_url= None #"http://192.168.0.69:8000"
local_images_path= "images/"
local_spliced_image_path= local_images_path + "temp/" # must be child of local_images_path in order to serve to Chromecast
http_server_port= 8000
server_url= "http://" + socket.gethostbyname(socket.gethostname()) + ":" + str(http_server_port)
chromecast_friendly_names= ["Family Room TV", "Basement Lights TV"]
image_cache_file_path= "image_cache.txt"
slideshow_duration_seconds= 30

# file extension to MIME type
content_type_dictionary= {
    ".jpg" :  "image/jpeg",
    ".jpeg" : "image/jpeg",
    ".png" : "image.png" }

class ImageLayout(IntEnum):
    Unknown= 0
    Landscape= 1
    Portrait= 2

# Build the URL path:
# 1. include the root server URL
# 2. Remove the root of the local_images_path because HTTPHandler uses that as the root directory, so it's
# not included.
def local_image_file_path_to_url(local_image_file_path):
    return server_url + "/" + os.path.relpath(local_image_file_path, local_images_path)

# Convert a URL path (for a URL served by *our* HTTP server) back to a local path
def url_to_local_image_file_path(url):
    return local_images_path + url[len(server_url + "/"):]

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
        super().__init__(*args, directory=local_images_path, **kwargs)

def web_server_thread():
    with http.server.ThreadingHTTPServer(("", http_server_port), HTTPHandler) as http_server:
        print("serving at port", http_server_port)
        http_server.serve_forever()

def main():
    # compile list of images from image server/file share
    images= [] # if splice_images is true then this is a tuple of (file_path, is_portait)
    # If we are hosting images ourself then we can splice portait images together into a new image to serve, so
    # that they fill the entire screen.
    splice_images= False

    random.seed()

    if (external_server_url):
        print("Using external server '%s'" % external_server_url)
        images= get_image_urls_from_external_server()
    else:
        print("Serving local directory '%s' and spinning up HTTP server '%s'" % (
            local_images_path,
            server_url))
        
        # Build images as a tuple of (URL path, is_portait). is_portait lets us quickly find and splice portrait
        # images together into a temporary image to serve
        # -Skip images that are in local_spliced_image_path (maybe left-over from a previous run)        
        images= [
            (local_image_file_path_to_url(image_path), ImageLayout.Unknown)
            for image_path in image_processing.get_images_from_local_path(local_images_path)
                if not image_path.startswith(local_spliced_image_path)]
        # Spin up a separate thread to run a web server. The server exposes images in local_images_path to the Chromecast
        threading.Thread(target= web_server_thread).start()
        splice_images= True

        # Make sure the spliced image path exists since it's for files generated by the application, don't expect
        # users to create it
        if not os.path.exists(local_spliced_image_path):
            os.makedirs(local_spliced_image_path)

        # TODO: delete any files currently in the local_spliced_image_path from a previous run?
        
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
    # When we splice one portrait image with the next one in the list, we don't want to display that image
    # when we encounter it so we set this bool to ignore it.
    skip_next_portrait= False
    image_count= len(images)
    temp_image_file_names= []

    while(not exit):
        for image_index, image in enumerate(images):
            if splice_images:
                # Lazily evaluate IsPortrait rather than on startup because it's slow (need to open image file and
                # potentially transpose it)
                if image[1] == ImageLayout.Unknown:
                    local_image_path= url_to_local_image_file_path(image[0])
                    image= (
                        image[0],
                        ImageLayout.Portrait if image_processing.image_is_portait(local_image_path) else ImageLayout.Landscape)
                    images[image_index]= image # Update list of images so we don't need to evaluate this image again

                if image[1] == ImageLayout.Landscape:
                    # Process image on the fly, generate a temporary file to store the processed image
                    temp_image_file_name= local_spliced_image_path + str(uuid.uuid4()) + ".jpg"
                    local_image= url_to_local_image_file_path(image[0])
                    # Update temp_image_file_name in case process_image renamed it
                    temp_image_file_name= image_processing.process_image_file(local_image, temp_image_file_name)
                    temp_image_file_names.append(temp_image_file_name)
                    image= (local_image_file_path_to_url(temp_image_file_name), False)
                elif skip_next_portrait:
                    # If this image is a portait, clear skip_next_portait and skip it
                    skip_next_portrait= False
                    continue
                else: # ImageLayout.Portrait
                    # Find the next portait image in images to splice with
                    # If there is one then set skip_next_portait, splice it with this one, and replace image
                    for search_image_index in range(image_index + 1, image_count):
                        search_image= images[search_image_index]

                        # Lazily evaluate IsPortrait rather than on startup because it's slow (need to open image file and
                        # potentially transpose it)
                        if search_image[1] == ImageLayout.Unknown:
                            local_search_image_path= url_to_local_image_file_path(search_image[0])
                            search_image= (
                                search_image[0],
                                ImageLayout.Portrait if image_processing.image_is_portait(local_search_image_path) else ImageLayout.Landscape)
                            images[search_image_index]= search_image # Update list of images so we don't need to evaluate this image again

                        if search_image[1] == ImageLayout.Portrait:
                            skip_next_portrait= True
                            # Select a temporary file name for the spliced image (generate a unique ID since chromecast caches images
                            # if we reuse file names)
                            spliced_image_file_name= local_spliced_image_path + str(uuid.uuid4()) + ".jpg"
                            temp_image_file_names.append(spliced_image_file_name)
                            local_image_1= url_to_local_image_file_path(image[0])
                            local_image_2= url_to_local_image_file_path(search_image[0])
                            print("Splicing '%s' + '%s' into '%s'" % (local_image_1, local_image_2, spliced_image_file_name))
                            # create temporary spliced image
                            image_processing.splice_images(local_image_1, local_image_2, spliced_image_file_name)
                            # replace image URL
                            image= (local_image_file_path_to_url(spliced_image_file_name), image[1])
                            break

                # replace (image_path,is_portait) tuple with just image_path so that it's similar to
                # the case where splice_images is false
                image= image[0]

            # clean up temporary spliced images, leave a few around in-case they're still being served
            if len(temp_image_file_names) > 2:
                to_delete= temp_image_file_names.pop(0)
                if os.path.exists(to_delete):
                    print("Purging temporary image '%s'" % to_delete)
                    os.remove(to_delete)

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