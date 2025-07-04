#!/bin/bash
set -eu
script_dir=$(dirname "$(readlink -f "${BASH_SOURCE[0]:-${0}}")")
show_usage() {
    >&2 cat << EOF
Usage: $(basename "${0}") OPTIONS

OPTIONS
  -h, --help
      Display this help text and exit.

  -l, --list-pcs
      List the names of PCs with special configurations.

  --pc=<name>
      The name of a PC whose special configurations you also want installed.
EOF
}
error() {
    [[ ${1} == "-u" ]] && shift && show_usage
    >&2 printf "\nERROR: %s: %s\n" "$(basename "${0}")" "${*}"
    exit 1
}

list_pcs=
pc_name=
while ((${#})); do
    case "${1}" in
        -h|--help) show_usage; exit 0 ;;
        -l|--list-pcs) list_pcs=1 ;;
        --pc=*) pc_name=${1#*=} ;;
        *) error -u "unsupported argument ${1}" ;;
    esac
    shift
done

pc_specific_root=${script_dir}/pc-specific

if [[ -n ${list_pcs} ]]; then
    ls "${pc_specific_root}"
    exit 0
fi

pc_dir=
if [[ -n ${pc_name} ]]; then
    pc_dir=${pc_specific_root}/${pc_name}
    if [[ ! -d ${pc_dir} ]]; then
        error -u "no pc config exists for ${pc_name}: ${pc_dir}"
    fi
fi

if [[ ! -d ${HOME} ]]; then
    error "ERROR: HOME is not defined; exiting for safety..."
fi
config_home=${XDG_CONFIG_HOME:-${HOME}/.config}
gui_config_dir=${config_home}/ns-gui-utility
mkdir -p "${gui_config_dir}"

symlink_overwrite() {
    if (( ${#} != 2 )); then
        error "usage: ${FUNCNAME[0]} <source-dir-or-file> <dest-symlink>"
    fi
    local source=${1}
    local destination=${2}
    if [[ ! -e ${source} ]]; then
        >&2 echo "WARNING: skipping missing path: ${source}"
        return
    fi
    if [[ -d ${destination} ]]; then
        rm -rf "${destination}"
    fi
    ln -sf "${source}" "${destination}"
}

for filename in Xresources xinitrc xprofile xsettingsd; do
    symlink_overwrite "${script_dir}/${filename}" "${HOME}/.${filename}"
done

symlink_overwrite "${script_dir}/fluxbox" "${HOME}/.fluxbox"
symlink_overwrite "${script_dir}/picom.conf" "${config_home}/picom.conf"
symlink_overwrite "${script_dir}/kitty" "${config_home}/kitty"
if [[ -n ${pc_dir} ]]; then
    symlink_overwrite \
        "${pc_dir}/output-profiles.json" \
        "${gui_config_dir}/output-profiles.json"
    symlink_overwrite \
        "${pc_dir}/on-output-profile-change" \
        "${gui_config_dir}/on-output-profile-change"
    symlink_overwrite \
        "${pc_dir}/on-primary-output-change" \
        "${gui_config_dir}/on-primary-output-change"
fi

path_d_dir="${HOME}/.integration/path.d"
if [[ -d ${path_d_dir} ]]; then
    symlink_overwrite \
        "${script_dir}/scripts/bin" \
        "${path_d_dir}/ns-gui-configs-bin"
else
    error \
        "No ${path_d_dir} present; tools not added to PATH!  Expected to be" \
        "used with a .bashrc that adds path.d subdirectories to PATH."
fi
