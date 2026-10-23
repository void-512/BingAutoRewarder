"""Persistent paths for the Raspberry Pi application (configured through .env)."""

import os
import re
from pathlib import Path, PurePosixPath

BASE_DIR = str(Path(__file__).resolve().parent.parent)
APP_DIR = os.path.abspath(
    os.path.expanduser(
        os.environ.get("AUTOREWARDER_DATA_DIR", os.path.join(BASE_DIR, "var"))
    )
)
ACCOUNTS_DIR = os.path.join(APP_DIR, "accounts")
ACCOUNTS_INDEX_PATH = os.path.join(APP_DIR, "accounts.json")
GLOBAL_SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
JSON_FILE_PATH = os.path.join(ASSETS_DIR, "queries.json")
VISUAL_SEARCH_ASSETS_DIR = os.path.join(ASSETS_DIR, "visual_search_assets")
LOG_FILE_PATH = os.path.join(APP_DIR, "background_log.txt")
LOG_MAX_SIZE = 6 * 1024 * 1024


def validate_account_id(account_id):
    if not isinstance(account_id, str) or not re.fullmatch(r"[a-f0-9]{12}", account_id):
        raise ValueError(
            "Account id must be exactly 12 lowercase hexadecimal characters"
        )
    return account_id


def account_dir(account_id):
    return os.path.join(ACCOUNTS_DIR, validate_account_id(account_id))


def browser_profile_path(account_id):
    """Path understood by Chromium inside the container, never a host path."""
    root = PurePosixPath(
        os.environ.get("AUTOREWARDER_REMOTE_PROFILE_ROOT", "/home/seluser/profiles")
    )
    if (
        not root.is_absolute()
        or ".." in root.parts
        or str(root) in ("/", "/home", "/home/seluser")
    ):
        raise ValueError("Use a dedicated absolute container profile directory")
    return str(root / validate_account_id(account_id))


def history_path(account_id):
    return os.path.join(account_dir(account_id), "history.json")


def status_path(account_id):
    return os.path.join(account_dir(account_id), "status.json")


def stats_path(account_id):
    return os.path.join(account_dir(account_id), "stats.json")


def account_meta_path(account_id):
    return os.path.join(account_dir(account_id), "meta.json")
