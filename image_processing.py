import PIL.Image, PIL.ImageOps, PIL.ImageFilter
import pillow_heif
import os.path
import enum

#test_image_file_name= "images/image_test/001.heic"
image_processing_directory= "nas_mount/"
aspect_ratio_720p= 1280 / 720 # 720p resolution
# Resize generated images down to this scale, so that they can be loaded faster by chromecast.
# Adjust to max support resolution of your chromecast.
max_image_height_pixels= 720
supported_image_extensions= (".jpg", ".jpeg", ".png")

# How to handle images that aren't 720 aspect ratio
class ImageProcessing(enum.IntEnum):
	Crop= 0 # Removes edges of image to fit
	Blur= 1 # Use a blurred copy of the image as a background

landscape_processing_mode= ImageProcessing.Blur
portrait_processing_mode= ImageProcessing.Crop

# Support for HEIC image format since that is sometimes produced by iOS
pillow_heif.register_avif_opener()
pillow_heif.register_heif_opener()

def crop_image_preserve_width(image, target_aspect_ratio):
	target_height= image.width / target_aspect_ratio
	vertical_crop_half= (image.height - target_height) / 2 # may be negative
	return image.crop((
		0,
		vertical_crop_half,
		image.width,
		vertical_crop_half + target_height))

def crop_image_preserve_height(image, target_aspect_ratio):
	target_width= (image.height * target_aspect_ratio)
	horizontal_crop_half= (image.width - target_width) / 2 # may be negative
	return image.crop((
		horizontal_crop_half,
		0,
		horizontal_crop_half + target_width,
		image.height))

def process_image(image):
	# Images (jpegs only?) may be rotated with EXIF metadata, while the raw image is unrotated
	# Pillow doesn't apply this rotation automatically so we do so manually if it exists. The
	# resulting image has the rotation baked in and the EXIF metadata removed.
	image_result= PIL.ImageOps.exif_transpose(image)

	if image_result.width >= image_result.height: # landscape
		print("cropping landscape")
		target_aspect_ratio= aspect_ratio_720p
		max_image_width_pixels= int(max_image_height_pixels * aspect_ratio_720p)
		processing_mode= landscape_processing_mode		
	else: #portait
		print("cropping portrait")
		# We will try to fit two portrait images at a time so crop to half-screen
		target_aspect_ratio= aspect_ratio_720p / 2
		max_image_width_pixels= int(max_image_height_pixels * aspect_ratio_720p / 2)
		processing_mode= portrait_processing_mode

	image_aspect_ratio= image_result.width / image_result.height

	if processing_mode==ImageProcessing.Crop:
		if image_aspect_ratio > target_aspect_ratio: # too wide
			image_result= crop_image_preserve_height(image_result, target_aspect_ratio)
		else: # too tall
			image_result= crop_image_preserve_width(image_result, target_aspect_ratio)
	elif processing_mode==ImageProcessing.Blur:
		# make copy
		blurred_copy= image_result
		# Crop copy to correct aspect ratio and resize copy to be large enough to contain image_result
		if image_aspect_ratio > target_aspect_ratio: # too wide
			blurred_copy= crop_image_preserve_height(blurred_copy, target_aspect_ratio)
			blurred_copy= blurred_copy.resize((image_result.width, int(image_result.width / target_aspect_ratio)))
		else: # too tall
			blurred_copy= crop_image_preserve_width(blurred_copy, target_aspect_ratio)
			blurred_copy= blurred_copy.resize((int(image_result.height * target_aspect_ratio), image_result.height))

		# blur copy
		blurred_copy= blurred_copy.filter(filter= PIL.ImageFilter.BoxBlur(16))
		# paste original centered in copy
		delta_width= blurred_copy.width - image_result.width
		delta_height= blurred_copy.height - image_result.height
		blurred_copy.paste(image_result, (int(delta_width / 2), int(delta_height / 2)))
		image_result= blurred_copy

	## Convert jpeg's to RGB only (they don't support alpha channels or palette mode)
	#if new_extension.lower() in (".jpeg", ".jpg") and image_result.mode in ("RGBA", "P"):
	image_result= image_result.convert("RGB")

	if max_image_height_pixels > 0:
		image_result= image_result.resize((max_image_width_pixels, max_image_height_pixels))

	return image_result

# Processes an image file to be the right dimensions and saves it to output_image_file_name. If
# output_image_file_name isn't a supported image type then the image is saved as a jpeg instead.
# Returns: output_image_file_name, including modified extension if necessary.
def process_image_file(input_image_file_name, output_image_file_name):
	with PIL.Image.open(input_image_file_name, "r") as image:
		print("opened image '%s'" % input_image_file_name)
		image= process_image(image)

		# Convert to jpeg if necessary
		output_root, output_extension= os.path.splitext(output_image_file_name)
		new_extension= output_extension if output_extension.lower() in supported_image_extensions else ".jpeg"
		image.save(output_root + new_extension) 

		return output_root + new_extension

def image_is_portait(image_file_name):
	with PIL.Image.open(image_file_name, "r") as image:
		# Images (jpegs only?) may be rotated with EXIF metadata, while the raw image is unrotated
		# Pillow doesn't apply this rotation automatically so we do so manually if it exists. The
		# resulting image has the rotation baked in and the EXIF metadata removed.
		image= PIL.ImageOps.exif_transpose(image)
		return image.width < image.height
	return False

# Splice two portait images side-by-side, assuming they are the same width and height
def splice_images(image_file_name_1, image_file_name_2, spliced_image_file_name):
	with PIL.Image.open(image_file_name_1) as image_1:
		with PIL.Image.open(image_file_name_2) as image_2:
			image_1= process_image(image_1)
			image_2= process_image(image_2)

			# Resize one image so that they're the same size. Always resize down to avoid stretching artifacts?
			if image_1.width > image_2.width:
				image_1= image_1.resize((image_2.width, image_2.height))
			else:
				image_2= image_2.resize((image_1.width, image_1.height))

			# Pasting doesn't automatically resize an image so we have to crop it first
			# (resize() doesn't do what we want because it stretches the original image to fit)
			image_1= image_1.crop((0, 0, image_1.width * 2, image_1.height))
			# Make sure to use image_2.width since image_1 has been resized.
			# paste() operates in-place, unlike most PIL functions so no need to assign to image_1
			image_1.paste(image_2, (image_2.width, 0))
			image_1.save(spliced_image_file_name)

def set_max_image_height(new_max_image_height_pixels):
	global max_image_height_pixels
	max_image_height_pixels= new_max_image_height_pixels