#!/usr/bin/env bash
set -euo pipefail
umask 077

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_dir"

mode="${1:-}"
export AUTOREWARDER_ENV_FILE="$repo_dir/.env"

# Initialize configuration
if [[ "$mode" == "--setup" ]]; then
    [[ $# -eq 1 ]] || { echo 'Use --setup by itself.' >&2; exit 2; }

    if [[ ! -f .env ]]; then
        if [[ -f deploy/autorewarder.env ]]; then
            cp deploy/autorewarder.env .env
        else
            cp .env.example .env
        fi
        chmod 600 .env
    fi
fi

# Load configuration
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi

export AUTOREWARDER_DATA_DIR="$(realpath -m "${AUTOREWARDER_DATA_DIR:-$repo_dir/var}")"
export AUTOREWARDER_CHROMIUM_DATA_DIR="$(realpath -m "${AUTOREWARDER_CHROMIUM_DATA_DIR:-$AUTOREWARDER_DATA_DIR/chromium}")"
export AUTOREWARDER_REMOTE_URL="${AUTOREWARDER_REMOTE_URL:-http://127.0.0.1:4444}"
export AUTOREWARDER_REMOTE_PROFILE_ROOT="${AUTOREWARDER_REMOTE_PROFILE_ROOT:-/home/seluser/profiles}"

endpoint="${AUTOREWARDER_REMOTE_URL#*://}"
endpoint="${endpoint%%/*}"
if [[ "$endpoint" =~ :([0-9]+)$ ]]; then
    export AUTOREWARDER_WEBDRIVER_PORT="${BASH_REMATCH[1]}"
fi

export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Help
if [[ "$mode" == "--help" || "$mode" == "-h" ]]; then
    if [[ -x .venv/bin/python ]]; then
        exec .venv/bin/python -m src --help
    fi

    cat <<'HELP'
Headless Raspberry Pi Rewards
  ./run.sh --setup
  ./run.sh --create-account Alice --init
  ./run.sh --list-accounts
  ./run.sh --account Alice --daily-only
  ./run.sh --account Alice --pc 30 --mobile 20
  ./run.sh --account Alice --reauth [--visible]
  ./run.sh --account Alice --configure --enable-schedule --pc 30 --mobile 20
  ./run.sh --delete-account ID [--yes]
  ./run.sh --schedule install [--time 09:00]
  ./run.sh --schedule status|remove
  ./run.sh --browser-start|--browser-stop
HELP
    exit 0
fi

# Cache directories
scratch_dir="$HOME/.cache/codex-tmp/autorewarder"
mkdir -p "$scratch_dir"
export TMPDIR="$scratch_dir" TMP="$scratch_dir" TEMP="$scratch_dir"
export PIP_CACHE_DIR="$scratch_dir/pip" XDG_CACHE_HOME="$scratch_dir"

# Setup
if [[ "$mode" == "--setup" ]]; then
    command -v python3 >/dev/null || { echo 'Python 3 is missing.' >&2; exit 1; }
    command -v docker >/dev/null || { echo 'Docker is missing.' >&2; exit 1; }
    docker compose version >/dev/null 2>&1 || { echo 'Docker Compose v2 is missing.' >&2; exit 1; }
    docker info >/dev/null 2>&1 || { echo 'Cannot access Docker daemon.' >&2; exit 1; }

    [[ -x .venv/bin/python ]] || python3 -m venv .venv
    .venv/bin/python -m pip install --only-binary=:all: -r requirements.txt
    .venv/bin/python -m pip check

    mkdir -p "$AUTOREWARDER_DATA_DIR"
    docker compose --env-file "$AUTOREWARDER_ENV_FILE" \
        -f deploy/compose.yaml config --quiet

    echo 'Setup complete. Next: ./run.sh --create-account Alice --init'
    exit 0
fi

# Run application
[[ -f .env && -x .venv/bin/python ]] || {
    echo 'Run ./run.sh --setup first.' >&2
    exit 1
}

exec .venv/bin/python -m src "$@"
