#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
skill_dir="$(CDPATH= cd -- "$script_dir/.." && pwd)"

install_light=false
install_strong=false

if [[ $# -eq 0 ]]; then
    install_light=true
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --light)
            install_light=true
            ;;
        --strong)
            install_strong=true
            ;;
        --all)
            install_light=true
            install_strong=true
            ;;
        -h|--help)
            printf '%s\n' \
                "Usage: setup.sh [--light] [--strong] [--all]" \
                "No arguments installs only the lightweight OCR runtime."
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            exit 2
            ;;
    esac
    shift
done

create_environment() {
    local destination="$1"
    if [[ -x "$destination/bin/python" || -x "$destination/Scripts/python.exe" ]]; then
        return
    fi
    if python3 -m venv "$destination"; then
        return
    fi
    python3 -m pip install --target "$skill_dir/.bootstrap" virtualenv
    PYTHONPATH="$skill_dir/.bootstrap" python3 -m virtualenv "$destination"
}

if $install_light; then
    create_environment "$skill_dir/.venv"
    "$skill_dir/.venv/bin/pip" install -e "${skill_dir}[light]"
    "$skill_dir/.venv/bin/rapidocr" check
fi

if $install_strong; then
    create_environment "$skill_dir/.venv-docling"
    "$skill_dir/.venv-docling/bin/pip" install \
        --index-url https://download.pytorch.org/whl/cpu torch torchvision
    "$skill_dir/.venv-docling/bin/pip" install -e "${skill_dir}[strong]"
fi

if [[ -x "$skill_dir/.venv/bin/pdf2md" ]]; then
    "$skill_dir/.venv/bin/pdf2md" doctor --json
fi
