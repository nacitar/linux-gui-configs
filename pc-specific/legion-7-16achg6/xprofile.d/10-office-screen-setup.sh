#!/bin/bash

screens=$(xrandr --query \
    | sed -n 's/^\([^ ]\+\) connected .*$/\1/p' \
    | sort \
    | paste -sd ' ')
if [[ ${screens} == "DP-2 DP-4 HDMI-0" ]]; then
    xrandr \
        --output HDMI-0 --mode 1920x1080 --pos 0x0 --primary \
        --output DP-2 --mode 1920x1080 --pos 1920x0 \
        --output DP-4 --off
else
    echo "Not applying configuration; unexpected screen setup: ${0}"
fi
