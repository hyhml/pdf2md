#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
skill_dir="$(CDPATH= cd -- "$script_dir/.." && pwd)"

install_light=false
install_strong=false
model_tier=""

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
        --models)
            shift
            if [[ $# -eq 0 || ("$1" != "basic" && "$1" != "standard") ]]; then
                printf '%s\n' "--models requires basic or standard" >&2
                exit 2
            fi
            model_tier="$1"
            install_strong=true
            ;;
        --all)
            install_light=true
            install_strong=true
            model_tier="standard"
            ;;
        -h|--help)
            printf '%s\n' \
                "Usage: setup.sh [--light] [--strong] [--models basic|standard] [--all]" \
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
    create_environment "$skill_dir/.venv-mineru"
    "$skill_dir/.venv-mineru/bin/pip" install "mineru>=4.0,<5"
fi

if [[ -n "$model_tier" ]]; then
    MINERU_HOME="$skill_dir/.mineru" \
    MODELSCOPE_CACHE="$skill_dir/.cache/modelscope" \
    HF_HOME="$skill_dir/.cache/huggingface" \
        "$skill_dir/.venv-mineru/bin/mineru-kit" models download \
        --tier "$model_tier" --source modelscope
fi

if [[ -x "$skill_dir/.venv/bin/pdf2md" ]]; then
    "$skill_dir/.venv/bin/pdf2md" doctor --json
fi
