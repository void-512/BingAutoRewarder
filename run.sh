#!/usr/bin/env bash
set -euo pipefail
umask 077
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_dir"
export AUTOREWARDER_ENV_FILE="$repo_dir/.env"

if [[ "${1:-}" == --setup ]]; then
    [[ $# -eq 1 ]] || { echo 'Use --setup by itself.' >&2; exit 2; }
    if [[ ! -f .env ]]; then
        if [[ -f deploy/autorewarder.env ]]; then
            cp -- deploy/autorewarder.env .env
            echo 'Preserved your existing deployment settings in .env.'
        else
            cp -- .env.example .env
        fi
        chmod 600 .env
    fi
fi
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi
export AUTOREWARDER_DATA_DIR="$(realpath -m -- "${AUTOREWARDER_DATA_DIR:-$repo_dir/var}")"
export AUTOREWARDER_CHROMIUM_DATA_DIR="$(realpath -m -- "${AUTOREWARDER_CHROMIUM_DATA_DIR:-$AUTOREWARDER_DATA_DIR/chromium}")"
export AUTOREWARDER_REMOTE_URL="${AUTOREWARDER_REMOTE_URL:-http://127.0.0.1:4444}"
export AUTOREWARDER_REMOTE_PROFILE_ROOT="${AUTOREWARDER_REMOTE_PROFILE_ROOT:-/home/seluser/profiles}"
endpoint_authority="${AUTOREWARDER_REMOTE_URL#*://}"
endpoint_authority="${endpoint_authority%%/*}"
if [[ "$endpoint_authority" =~ :([0-9]+)$ ]]; then
    export AUTOREWARDER_WEBDRIVER_PORT="${BASH_REMATCH[1]}"
fi
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    if [[ -x .venv/bin/python ]]; then
        exec .venv/bin/python -m src --help
    fi
    cat <<'HELP'
Headless Raspberry Pi Rewards
  ./run.sh --setup                         Prepare .venv, dependencies and .env
  ./run.sh --create-account Alice --init   Create an account and sign in
  ./run.sh --list-accounts                 List account IDs and initialization
  ./run.sh --account Alice --daily-only    Daily tasks and visual search
  ./run.sh --account Alice --pc 30 --mobile 20
  ./run.sh --account Alice --reauth [--visible]
  ./run.sh --account Alice --configure --enable-schedule --pc 30 --mobile 20
  ./run.sh --delete-account ID [--yes]      Permanently erase one account
  ./run.sh --schedule install [--time 09:00]
  ./run.sh --schedule status|remove
  ./run.sh --browser-start|--browser-stop
Run --help again after setup for all configuration options.
HELP
    exit 0
fi
scratch_dir="$HOME/.cache/codex-tmp/autorewarder"
mkdir -p "$scratch_dir"
export TMPDIR="$scratch_dir" TMP="$scratch_dir" TEMP="$scratch_dir"
export PIP_CACHE_DIR="$scratch_dir/pip" XDG_CACHE_HOME="$scratch_dir"

if [[ "${1:-}" == --setup ]]; then
    prerequisites='sudo apt-get install python3 python3-venv ca-certificates docker.io docker-compose'
    command -v python3 >/dev/null || { echo "$prerequisites" >&2; exit 1; }
    python3 -c 'import ensurepip, venv' 2>/dev/null || { echo "$prerequisites" >&2; exit 1; }
    command -v docker >/dev/null || { echo "Docker is missing. $prerequisites" >&2; exit 1; }
    docker compose version >/dev/null 2>&1 || { echo "Compose v2 is missing. $prerequisites" >&2; exit 1; }
    docker info >/dev/null 2>&1 || {
        echo 'Cannot access Docker. Run sudo systemctl enable --now docker.' >&2
        echo 'Grant this user Docker access: sudo usermod -aG docker "$USER", then log in again.' >&2
        exit 1
    }
    [[ -x .venv/bin/python ]] || python3 -m venv .venv
    requirements_hash="$(sha256sum requirements.txt | cut -d ' ' -f 1)"
    if [[ ! -f .venv/.requirements.sha256 ]] || [[ "$(cat .venv/.requirements.sha256)" != "$requirements_hash" ]]; then
        .venv/bin/python -m pip install --only-binary=:all: -r requirements.txt
        printf '%s\n' "$requirements_hash" > .venv/.requirements.sha256
    fi
    .venv/bin/python -m pip check
    mkdir -p "$AUTOREWARDER_DATA_DIR"
    docker compose --env-file "$AUTOREWARDER_ENV_FILE" -f deploy/compose.yaml config --quiet
    echo 'Setup complete. No browser was started. Next: ./run.sh --create-account Alice --init'
    exit 0
fi
[[ -f .env && -x .venv/bin/python ]] || { echo 'Run ./run.sh --setup first.' >&2; exit 1; }
exec .venv/bin/python -m src "$@"
