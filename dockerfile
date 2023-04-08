FROM ubuntu:latest
RUN apt-get update && apt-get install python3 -y
RUN apt-get update && apt-get install python3-pip -y
RUN apt-get update && apt-get install git -y

WORKDIR /home/
RUN git clone https://github.com/e-roder/pycastblaster.git
WORKDIR /home/pycastblaster
RUN pip3 install -r requirements.txt
RUN mkdir -p images/Curated
COPY config_example.yaml config.yaml


ENTRYPOINT [ "python3", "pycastblaster.py" ]