#!/bin/bash
#
set -xe

delay() {
    sleep 1
}

~/bin/cssmin -f html/style.css

#delay && /opt/local/bin/ampy -d 1 mkdir lib
#delay && /opt/local/bin/ampy -d 1 mkdir html
#delay && /opt/local/bin/ampy -d 1 put html/pump-on.png html/pump-on.png
#delay && /opt/local/bin/ampy -d 1 put html/pump-off.png html/pump-off.png

#mpy-cross lib/logging.py
#delay && /opt/local/bin/ampy -d 1 put lib/logging.mpy lib/logging.mpy
#mpy-cross -v wificonfig.py
#delay && /opt/local/bin/ampy -d 1 put wificonfig.mpy

delay && /opt/local/bin/ampy -d 1 put main.py
if [[ -f update.py ]]; then
    mpy-cross -v update.py
    delay && /opt/local/bin/ampy -d 1 put update.py
fi

delay && /opt/local/bin/ampy -d 1 put html/index.html html/index.html
delay && /opt/local/bin/ampy -d 1 put html/style.min.css html/style.min.css

mpy-cross -v belkin.py
delay && /opt/local/bin/ampy -d 1 put belkin.mpy

/opt/local/bin/ampy -d 1 put times.dat

delay && /opt/local/bin/ampy -d 1 ls
