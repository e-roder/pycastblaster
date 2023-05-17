# Pycastblaster
A python program to manage and cast images from a local or network drive to Chromecast devices. Leave this program running and it will detect when a specific Chromecast device on the network turns on and start casting to it.

**This program only runs on Linux, because the pychromecast library uses the network function `poll()` which is not supported on Windows.**

Example usage:
`python3 pycastblaster`

## What other options exist and why choose Pycastblaster instead?
1. Google Photos: Cloud hosted, limited free storage capacity, limited control over image selection.
2. Plex Media Server: No control over slideshow duration, grey bars on the sides of images, seemed to stop casting after some time.

## Configuration
The program can be configured with a YAML file, "config.yaml" by default. You can specify a config file as a command line option, e.g.
`python3 pycastblaster config2.yaml`.

**Options:**

| Name | Description | Default Value |
| ---- | ----------- | ------------- |
| images_path | Local or network mapped directory to select images from. May be relative or absolute. Image order is randomized. | *images* |
| temp_path | Path for storing temporary image files (created automatically). May be relative or absolute. | *temp* |
| http_server_port | Port to serve images from (this is how they are accessed by the Chromecast).  | 8000 |
| chromecast_name | Name of the Chromecast, configured in the Google Home app. https://support.google.com/googlenest/answer/7550874?hl=en | "Family Room TV" |
| slideshow_duration_seconds | How many seconds before advancing to the next image. | 15 |
| max_image_height_pixels | Display resolution of your Chromecast, usually 720 or 1080. | 720 |
| interruption_idle_seconds | Grace period to wait for another Chromecast app to start up when we detect that we're interrupted (otherwise we may just interrupt them again). | 20 |
| image_scanning_frequency_minutes | Time (in MINUTES) to wait before rescanning for new images. | 10 |

## Controlling via webbrowser
You can navigate to <your IP address>:<http_server_port> to access a website and control Pycastblaster. Current features available via the website:
* Pause: Pause the slideshow on the current image (pauses slideshow timer). Click again to resume.
* Exit: Stop Pycastblaster.

## Casting to multiple Chromecasts
This program only supports casting to a single device at a time, for simplicity. To cast images to multiple devices (though not synchronized), you can run multiple instances of this program with different config files and options. E.g.:
`python3 pycastblaster config1.yaml` and `python3 pycastblaster config2.yaml`. 

You'll want to make sure the following options are unique for each device:
1. temp_path (There's a text file that manages temporary images, and multiple instances would fight over the contents of that file.)
2. http_server_port
3. chromecast_name

## Refresh Image List
New images are automatically detected and shuffled into the remainder of the playlist. Use the config option `image_scanning_frequency_minutes` to control how often this happens.

## Using with Docker
Included are two example files for use with Docker: dockerfile and docker-compose.yaml.

dockerfile assumes...
- There's a config_example.yaml file adjacent to it (config_example.yaml is conveniently included).

docker-compose.yaml assumes...
- You have named your docker image 'pycastblaster'
- The images you want to cast are in /media/nas/images