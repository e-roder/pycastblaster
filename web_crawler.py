import urllib.request
import re

link_pattern= re.compile("<a href=\"([0-9a-zA-Z-%.]+)")
supported_image_extensions= (".jpg", ".jpeg", ".png")

def parse_url(url):
    fp = urllib.request.urlopen(url)
    mybytes = fp.read()

    html_string = mybytes.decode("utf8")
    fp.close()
    links= link_pattern.findall(html_string)
    images= []
    
    for link in links:
        if (link.lower().endswith(supported_image_extensions)):
            images.append(url + "/" + link)
        elif ("." in link): # unsupported file formate
            pass
        else: # recurse subdirectory
            images= images + parse_url(url + "/" + link)

    return images
    
#root_url= "http://192.168.0.69:8000"
#print("\n".join(parse_url(root_url)))