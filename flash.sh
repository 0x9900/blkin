#!/bin/bash
#
set -xe

delay() {
    sleep 2
}

~/bin/cssmin -f html/style.css

/opt/local/bin/ampy -d 1 put times.dat

#delay && /opt/local/bin/ampy -d 1 mkdir lib
#delay && /opt/local/bin/ampy -d 1 mkdir html
#delay && /opt/local/bin/ampy -d 1 put html/pump-on.png html/pump-on.png
#delay && /opt/local/bin/ampy -d 1 put html/pump-off.png html/pump-off.png
delay && /opt/local/bin/ampy -d 1 put main.py

delay && /opt/local/bin/ampy -d 1 put html/index.html html/index.html
delay && /opt/local/bin/ampy -d 1 put html/style.min.css html/style.min.css

#mpy-cross lib/logging.py
#delay && /opt/local/bin/ampy -d 1 put lib/logging.mpy lib/logging.mpy

mpy-cross -v wificonfig.py
delay && /opt/local/bin/ampy -d 1 put wificonfig.mpy

mpy-cross -v belkin.py
delay && /opt/local/bin/ampy -d 1 put belkin.mpy

delay && /opt/local/bin/ampy -d 1 ls
