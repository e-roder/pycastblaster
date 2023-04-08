# Pycastblaster
A python program to manage and cast images from a local or network drive to Chromecast devices. Leave this program running and it will detect when a specific Chromecast device on the network turns on and start casting to it (work in progress).

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
| images_path | Local or network mapped directory to select images from. Image order is randomized. | *./images* |
| temp_directory | Name of directory for storing temporary image files. This directory will be created inside 'images_path' (no need to create it manually). | *temp* |
| http_server_port | Port to serve images from (this is how they are accessed by the Chromecast).  | 8000 |
| chromecast_name | Name of the Chromecast, configured in the Google Home app. https://support.google.com/googlenest/answer/7550874?hl=en | "Family Room TV" |
| slideshow_duration_seconds | How many seconds before advancing to the next image. | 15 |
| max_image_height_pixels | Display resolution of your Chromecast, usually 720 or 1080. | 720 |

## Casting to multiple Chromecasts
This program only supports casting to a single device at a time, for simplicity. To cast images to multiple devices (though not synchronized), you can run multiple instances of this program with different config files and options. E.g.:
`python3 pycastblaster config1.yaml` and `python3 pycastblaster config2.yaml`. 

You'll want to make sure the following options are unique for each device:
1. temp_directory (There's a text file that manages temporary images, and multiple instances would fight over the contents of that file.)
2. http_server_port
3. chromecast_name

## Refresh Behavior
Currently there's no support for refreshing the list of images or adjusting the slideshow time while running, but it's on the TODO list.

## Using with Docker
Included are two example files for use with Docker: dockerfile and docker-compose.yaml.

dockerfile assumes...
- There's a config_example.yaml file adjacent to it (config_example.yaml is conveniently included).

docker-compose.yaml assumes...
- You have named your docker image 'pycastblaster'
- The images you want to cast are in /media/nas/images