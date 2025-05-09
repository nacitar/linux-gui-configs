#!/bin/bash
[ -r "${HOME}/.Xresources" ] && xrdb -merge "${HOME}/.Xresources"
if [ -d /etc/X11/xinit/xinitrc.d ]; then
    for f in /etc/X11/xinit/xinitrc.d/*; do
        [ -x "${f}" ] && . "${f}"
    done
    unset f
fi
[ -f "${HOME}/.xprofile" ] && . "${HOME}/.xprofile"

env > "${DISPLAY_SERVICE_LOG_DIR:-/tmp}/xenv"

exec startfluxbox
#exec kitty
