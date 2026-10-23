"""Confirmed, retryable erasure of one account's host and container data."""

from pathlib import Path
import shutil

from .accounts import AccountMetaManager
from .config import APP_DIR, LOG_FILE_PATH, validate_account_id
from .emulator.driver import profile_lock_path
from .locking import BusyError, ProcessLock

# Arguments are passed directly to Python in the container, never through a shell.
# rmtree does not follow directory symlinks on Linux; refuse symlink roots too.
DELETE_PROFILE = r"""
import os, pathlib, re, shutil, sys
root, account = pathlib.Path(sys.argv[1]), sys.argv[2]
if not re.fullmatch(r"[a-f0-9]{12}", account):
    raise SystemExit("Invalid account id")
if not root.is_absolute() or root.resolve() != root or root == pathlib.Path("/"):
    raise SystemExit("Unsafe profile root")
target = root / account
if target.is_symlink() or target.resolve().parent != root:
    raise SystemExit("Unsafe profile target")
profile = os.fsencode(str(target))
for command in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
    try:
        arguments = command.read_bytes().split(b"\0")
    except (FileNotFoundError, ProcessLookupError):
        continue
    if b"--user-data-dir=" + profile in arguments or any(
        value == b"--user-data-dir" and index + 1 < len(arguments) and arguments[index + 1] == profile
        for index, value in enumerate(arguments)
    ):
        raise SystemExit("Selected browser profile is still in use")
if target.exists():
    shutil.rmtree(target)
if target.exists() or target.is_symlink():
    raise SystemExit("Profile remains after deletion")
print("Selected Chromium profile removed (or already absent)")
"""


def safe_directory(parent, account_id):
    parent = Path(parent)
    target = parent / validate_account_id(account_id)
    if (
        parent.is_symlink()
        or target.is_symlink()
        or target.resolve().parent != parent.resolve()
    ):
        raise ValueError(f"Refusing unsafe account directory: {target}")
    return target


def delete_account(manager, account_id, docker, emit=print):
    account = manager.get(account_id)
    if account is None:
        raise ValueError("Account does not exist; nothing was deleted")
    log = Path(LOG_FILE_PATH)
    if log.is_symlink():
        raise ValueError(f"Refusing a symlink for the shared legacy log: {log}")
    duplicate_label = (
        sum(entry["label"] == account["label"] for entry in manager.read_index()) > 1
    )
    if (
        log.exists()
        and duplicate_label
        and f"] [{account['label']}] " in log.read_text()
    ):
        raise RuntimeError(
            f"Legacy shared log {log} contains an ambiguous duplicate account label. "
            "Resolve those log records before deletion; no account data was erased."
        )
    targets = [
        safe_directory(Path(APP_DIR) / "accounts", account_id),
        safe_directory(Path(APP_DIR) / "chromium-profiles", account_id),
    ]
    lock_path = profile_lock_path(account_id)
    with ProcessLock(lock_path):
        with docker.session() as container:
            active = docker.sessions()
            if active is None:
                raise RuntimeError(
                    "Cannot verify Selenium is idle; no account data was deleted"
                )
            if active:
                raise BusyError(
                    "A browser session is active; no account data was deleted"
                )
            docker.verify_profile_mount(container)
            # Keep a disabled tombstone in the index until every step succeeds.
            # A failed deletion is visible, cannot be scheduled, and can be retried.
            accounts = manager.read_index()
            for entry in accounts:
                if entry["id"] == account_id:
                    entry["deletion_pending"] = True
            meta = AccountMetaManager(account_id)
            schedule = meta.get_schedule()
            schedule["enabled"] = False
            manager.write_index(accounts)
            try:
                meta.set_schedule(schedule)
                docker.command(
                    [
                        "exec",
                        "--user",
                        "root",
                        container,
                        "python3",
                        "-c",
                        DELETE_PROFILE,
                        docker.profile_root(),
                        account_id,
                    ]
                )
                for target in targets:
                    if target.exists():
                        shutil.rmtree(target)
                settings = manager.settings.get_settings()
                remaining = [entry for entry in accounts if entry["id"] != account_id]
                if settings.get("current_account_id") == account_id:
                    settings["current_account_id"] = next(
                        (
                            entry["id"]
                            for entry in remaining
                            if not entry.get("deletion_pending")
                        ),
                        None,
                    )
                old_schedule = settings.get("schedule", {})
                if isinstance(old_schedule, dict) and account_id in (
                    old_schedule.get("account_id"),
                    old_schedule.get("accountId"),
                ):
                    settings.pop("schedule")
                manager.settings.save_settings(settings)
                # Earlier versions used one bounded log. Remove identifiable
                # selected-account records while retaining other accounts' logs.
                log = Path(LOG_FILE_PATH)
                if log.exists():
                    labels = [entry["label"] for entry in remaining]
                    prefixes = [f"] [{account_id}] "]
                    if account["label"] not in labels:
                        prefixes.append(f"] [{account['label']}] ")
                    lines = log.read_text().splitlines(keepends=True)
                    log.write_text(
                        "".join(
                            line
                            for line in lines
                            if not any(prefix in line for prefix in prefixes)
                        )
                    )
                manager.write_index(remaining)
            except Exception as exc:
                raise RuntimeError(
                    f"Deletion incomplete for {account_id}: {exc}. Scheduling is disabled. "
                    f"Remaining host data/index may be under {APP_DIR}; container profile is "
                    f"{docker.profile_root()}/{account_id}. Retry --delete-account {account_id} --yes."
                ) from exc
    # All application operations hold cli.lock, so no new profile lease can
    # acquire this account's now-unused lock between release and unlink.
    Path(lock_path).unlink(missing_ok=True)
    emit(
        f"Permanently deleted account {account_id}, including its Chromium profile and schedule."
    )
