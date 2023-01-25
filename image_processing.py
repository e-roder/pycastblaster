from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from pillow_heif import register_avif_opener
import os.path

#test_image_file_name= "images/image_test/001.heic"
image_processing_directory= "nas_mount/"
aspect_ratio_720p= 1280 / 720 # 720p resolution
supported_image_extensions= (".jpg", ".jpeg", ".png")

# Support for HEIC image format since that is sometimes produced by iOS
register_avif_opener()
register_heif_opener()

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
    image_result= ImageOps.exif_transpose(image)

    if image_result.width >= image_result.height: # landscape
        print("cropping landscape")
        target_aspect_ratio= aspect_ratio_720p
        
    else: #portait
        print("cropping portrait")
        # We will try to fit two portrait images at a time so crop to half-screen
        target_aspect_ratio= aspect_ratio_720p / 2

    image_aspect_ratio= image_result.width / image_result.height

    if image_aspect_ratio > target_aspect_ratio: # too wide
        image_result= crop_image_preserve_height(image_result, target_aspect_ratio)
    else: # too tall
        image_result= crop_image_preserve_width(image_result, target_aspect_ratio)

    ## Convert jpeg's to RGB only (they don't support alpha channels or palette mode)
    #if new_extension.lower() in (".jpeg", ".jpg") and image_result.mode in ("RGBA", "P"):
    image_result= image_result.convert("RGB")

    return image_result

# Processes an image file to be the right dimensions and saves it to output_image_file_name. If
# output_image_file_name isn't a supported image type then the image is saved as a jpeg instead.
# Returns: output_image_file_name, including modified extension if necessary.
def process_image_file(input_image_file_name, output_image_file_name):
    with Image.open(input_image_file_name, "r") as image:
        print("opened image '%s'" % input_image_file_name)
        image= process_image(image)

        # Convert to jpeg if necessary
        output_root, output_extension= os.path.splitext(output_image_file_name)
        new_extension= output_extension if output_extension.lower() in supported_image_extensions else ".jpeg"
        image.save(output_root + new_extension) 

        return output_root + new_extension   

def get_images_from_local_path(local_image_path):
    images= []
    if (os.path.exists(local_image_path)):
        for dirpath, dirnames, filenames in os.walk(local_image_path):
            images= images + [
                os.path.join(dirpath, filename)
                for filename in filenames
                    if filename.lower().endswith(supported_image_extensions)]
    return images

def image_is_portait(image_file_name):
    with Image.open(image_file_name, "r") as image:
        # Images (jpegs only?) may be rotated with EXIF metadata, while the raw image is unrotated
        # Pillow doesn't apply this rotation automatically so we do so manually if it exists. The
        # resulting image has the rotation baked in and the EXIF metadata removed.
        image= ImageOps.exif_transpose(image)
        return image.width < image.height
    return False

# Splice two portait images side-by-side, assuming they are the same width and height
def splice_images(image_file_name_1, image_file_name_2, spliced_image_file_name):
    with Image.open(image_file_name_1) as image_1:
        with Image.open(image_file_name_2) as image_2:
            image_1= process_image(image_1)
            image_2= process_image(image_2)

            # Resize one image so that they're the same size. Always resize down to avoid stretching artifacts?
            if image_1.width > image_2.width:
                image_1= image_1.resize(image_2.width, image_2.height)
            else:
                image_2= image_2.resize((image_1.width, image_1.height))

            # Pasting doesn't automatically resize an image so we have to crop it first
            # (resize() doesn't do what we want because it stretches the original image to fit)
            image_1= image_1.crop((0, 0, image_1.width * 2, image_1.height))
            # Make sure to use image_2.width since image_1 has been resized.
            # paste() operates in-place, unlike most PIL functions so no need to assign to image_1
            image_1.paste(image_2, (image_2.width, 0))
            image_1.save(spliced_image_file_name)

def process_images():
    images= get_images_from_local_path(image_processing_directory)
    image_count= len(images)
    for image_index, image_file_name in enumerate(images):
        process_image(image_file_name, image_file_name)
        image_number= image_index + 1
        if image_number % 5 == 0:
            print("%d / %d images processes: (%.2f%%)" %
                (image_number,
                image_count,
                image_number / image_count * 100))
    print("Finished processing %d images" % image_count)

#process_images()