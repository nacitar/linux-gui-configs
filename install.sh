#!/bin/bash
set -eu
if [[ ! -d ${HOME} ]]; then
    error "ERROR: HOME is not defined; exiting for safety..."
fi
script_dir=$(readlink -f "$(dirname "${BASH_SOURCE[0]:-${0}}")")
config_home=${XDG_CONFIG_HOME:-}
if [[ -n ${config_home} ]]; then
    config_home=$(readlink -f "${config_home}")
else
    config_home=${HOME}/.config
fi

error() {
    >&2 echo "ERROR: ${@}"
    exit 1
}
symlink_overwrite() {
    if (( ${#} != 2 )); then
        error "usage: ${FUNCNAME[0]} <source-dir-or-file> <dest-symlink>"
    fi
    local source=${1}
    local destination=${2}
    if [[ -d ${destination} ]]; then
        rm -rf "${destination}"
    fi
    ln -sf "${source}" "${destination}"
}

for filename in Xresources xinitrc xprofile xsettingsd; do
    symlink_overwrite "${script_dir}/home/${filename}" "${HOME}/.${filename}"
done

symlink_overwrite "${script_dir}/fluxbox" "${HOME}/.fluxbox"
symlink_overwrite "${script_dir}/picom.conf" "${config_home}/picom.conf"
symlink_overwrite "${script_dir}/kitty" "${config_home}/kitty"
