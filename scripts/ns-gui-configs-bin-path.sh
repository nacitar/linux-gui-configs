script_dir=$(dirname "$(readlink -f "${BASH_SOURCE[0]:-${0}}")")
export PATH="${script_dir}/bin:${PATH}"
