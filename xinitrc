#!/bin/bash
[[ -r ${HOME}/.Xresources ]] && xrdb -merge "${HOME}/.Xresources"
if [[ -d /etc/X11/xinit/xinitrc.d ]]; then
    for f in /etc/X11/xinit/xinitrc.d/*; do
        [[ -x ${f} ]] && source "${f}"
    done
    unset f
fi
[[ -f ${HOME}/.xprofile ]] && source "${HOME}/.xprofile"

exec startfluxbox &>"${DISPLAY_SERVICE_LOG_DIR:-/tmp}/startx-session.log"
