"""Account management, configuration and task execution behind ./run.sh."""

import argparse
from datetime import date, datetime
from getpass import getpass
import json
import os
from pathlib import Path
import signal

from .accounts import AccountManager, AccountMetaManager, GlobalSettingsManager
from .auth import AuthenticationRequired, interactive_login
from .config import APP_DIR, LOG_MAX_SIZE, account_dir, stats_path
from .deletion import delete_account
from .docker import DockerBrowser
from .emulator import DriverManager
from .locking import BusyError, ProcessLock
from .runner import Runner
from .storage import read_json


def log(message):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {message}", flush=True)


def account_logger(account):
    path = Path(account_dir(account["id"])) / "run.log"

    def emit(message):
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
        print(f"[{account['label']}] {line}", flush=True)
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if path.exists() and path.stat().st_size >= LOG_MAX_SIZE:
            path.unlink()
        with path.open("a") as output:
            output.write(line + "\n")

    return emit


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return number


def parser():
    result = argparse.ArgumentParser(
        prog="./run.sh",
        description="Headless Raspberry Pi Rewards: accounts, login, tasks and scheduling",
    )
    result.add_argument(
        "--setup",
        action="store_true",
        help="Prepare .venv, dependencies and .env (handled by run.sh)",
    )
    result.add_argument("--account", help="Account id or unambiguous label")
    action = result.add_mutually_exclusive_group()
    action.add_argument("--create-account", metavar="LABEL")
    action.add_argument(
        "--delete-account",
        metavar="ID",
        help="Permanently erase account data and its Docker browser profile",
    )
    action.add_argument("--list-accounts", action="store_true")
    action.add_argument(
        "--browser-start",
        action="store_true",
        help="Explicitly start Chromium and leave it running",
    )
    action.add_argument(
        "--browser-stop",
        action="store_true",
        help="Stop Chromium only if Selenium is idle",
    )
    action.add_argument(
        "--schedule",
        choices=("install", "remove", "status"),
        help="Manage the daily user systemd timer",
    )
    auth = result.add_mutually_exclusive_group()
    auth.add_argument("--init", action="store_true")
    auth.add_argument("--reauth", action="store_true")
    result.add_argument(
        "--visible",
        action="store_true",
        help="Open the login browser on optional localhost noVNC",
    )
    result.add_argument(
        "--yes",
        action="store_true",
        help="Intentionally confirm permanent account deletion",
    )
    result.add_argument(
        "--pc", type=nonnegative, help="Desktop search count; saved with --configure"
    )
    result.add_argument(
        "--mobile", type=nonnegative, help="Mobile search count; saved with --configure"
    )
    result.add_argument(
        "--daily-only",
        action="store_true",
        help="Daily Set, More Activities and visual search, without text searches",
    )
    result.add_argument(
        "--force",
        action="store_true",
        help="Ignore the already-run-today schedule guard",
    )
    result.add_argument("--configure", action="store_true")
    result.add_argument(
        "--enable-schedule", action=argparse.BooleanOptionalAction, default=None
    )
    result.add_argument(
        "--advanced", action=argparse.BooleanOptionalAction, default=None
    )
    result.add_argument(
        "--duration-hours", type=int, choices=range(1, 25), metavar="1..24"
    )
    result.add_argument(
        "--queries-per-hour", type=int, choices=range(1, 100), metavar="1..99"
    )
    result.add_argument("--dashboard", choices=("auto", "legacy", "new"))
    result.add_argument(
        "--force-tasks", action=argparse.BooleanOptionalAction, default=None
    )
    result.add_argument(
        "--force-visual", action=argparse.BooleanOptionalAction, default=None
    )
    result.add_argument(
        "--stats",
        action="store_true",
        help="Show the selected account's recorded statistics",
    )
    result.add_argument(
        "--time",
        default="09:00",
        help="Daily local time for --schedule install (default 09:00)",
    )
    return result


def dispatch(args):
    settings = GlobalSettingsManager()
    manager = AccountManager(settings)
    if args.schedule:
        from .scheduling import manage_schedule

        return manage_schedule(args.schedule, args.time)
    if args.browser_stop:
        DockerBrowser(log).stop()
        return 0
    if args.browser_start:
        with DockerBrowser(log).session(keep=True):
            log("Chromium is ready and will remain running")
        return 0
    if args.list_accounts:
        for account in manager.list():
            print(
                f"{account['id']}  {account['label']}  initialized={account['first_setup_done']}"
                f"  deletion_pending={bool(account.get('deletion_pending'))}"
            )
        return 0
    if args.delete_account:
        account = manager.get(args.delete_account)
        if not account:
            raise ValueError("Account does not exist")
        if not args.yes:
            if (
                not os.isatty(0)
                or input(
                    f"Permanently erase '{account['label']}' and all browser credentials? Type yes: "
                ).strip()
                != "yes"
            ):
                log("Deletion cancelled. To confirm noninteractively, supply --yes.")
                return 1
        delete_account(manager, args.delete_account, DockerBrowser(log), log)
        return 0
    if args.create_account:
        account = manager.create(args.create_account)
        args.account = account["id"]
        log(f"Created '{account['label']}', id={account['id']}")
    account = manager.resolve(args.account) if args.account else None
    if account and account.get("deletion_pending"):
        raise ValueError(
            f"Deletion pending. Retry --delete-account {account['id']} --yes"
        )
    if account and args.stats:
        print(json.dumps(read_json(stats_path(account["id"]), {}), indent=2))
        return 0
    if account and args.configure:
        meta = AccountMetaManager(account["id"])
        schedule = meta.get_schedule()
        updates = {
            "queries_pc": args.pc,
            "queries_mobile": args.mobile,
            "enabled": args.enable_schedule,
            "advancedScheduling": args.advanced,
            "runDuration": args.duration_hours,
            "queriesPerHour": args.queries_per_hour,
        }
        schedule.update(
            {key: value for key, value in updates.items() if value is not None}
        )
        meta.set_schedule(schedule)
        if args.dashboard:
            meta.set_dashboard_variant(args.dashboard)
        shared = settings.get_settings()
        for key, value in (
            ("force_daily_tasks", args.force_tasks),
            ("force_visual_search", args.force_visual),
        ):
            if value is not None:
                shared[key] = value
        settings.save_settings(shared)
        log(f"Schedule: {schedule}")
        return 0
    if args.init or args.reauth:
        if not os.isatty(0):
            raise ValueError(
                "Authentication requires an interactive terminal; no browser was started"
            )
        if args.visible and not os.environ.get("SE_VNC_PASSWORD"):
            password = getpass("Temporary noVNC password (exactly 8 characters): ")
            if len(password) != 8:
                raise ValueError("Use exactly 8 characters for the noVNC password")
            os.environ["SE_VNC_PASSWORD"] = password
        with DockerBrowser(log).session(visible=args.visible):
            if args.visible:
                log(
                    "Open noVNC over SSH: ssh -N -L 7900:127.0.0.1:7900 USER@PI; visit http://127.0.0.1:7900"
                )
            interactive_login(
                DriverManager(account["id"]),
                AccountMetaManager(account["id"]),
                visible=args.visible,
            )
        return 0
    if args.create_account:
        return 0
    planned = []
    for item in [account] if account else manager.list():
        if item.get("deletion_pending"):
            continue
        schedule = AccountMetaManager(item["id"]).get_schedule()
        override = args.daily_only or args.pc is not None or args.mobile is not None
        if not override and (
            not schedule["enabled"]
            or (
                not args.force
                and schedule["last_triggered_date"] == date.today().isoformat()
            )
        ):
            continue
        if not item["first_setup_done"]:
            raise AuthenticationRequired(
                f"Initialize {item['label']}: ./run.sh --account {item['id']} --init"
            )
        pc = args.pc if args.pc is not None else int(schedule["queries_pc"])
        mobile = (
            args.mobile if args.mobile is not None else int(schedule["queries_mobile"])
        )
        if pc < 0 or mobile < 0:
            raise ValueError("Saved search counts must be nonnegative")
        planned.append((item, pc, mobile))
    if not planned:
        log(
            "No enabled schedules are due. Use --create-account LABEL --init or --configure --enable-schedule."
        )
        return 0
    failed = 0
    with DockerBrowser(log).session():
        for item, pc, mobile in planned:
            try:
                Runner(item, account_logger(item)).run(
                    pc, mobile, daily_only=args.daily_only
                )
            except AuthenticationRequired as exc:
                log(str(exc))
                failed = 3
            except Exception as exc:
                log(
                    f"{item['label']}: {type(exc).__name__}: {str(exc).split(chr(10), 1)[0]}"
                )
                failed = failed or 1
    return failed


def main(argv=None):
    command = parser()
    args = command.parse_args(argv)
    if args.setup:
        command.error("Use ./run.sh --setup")
    target = args.account or args.create_account
    if (
        args.init
        or args.reauth
        or args.configure
        or args.stats
        or args.daily_only
        or args.pc is not None
        or args.mobile is not None
    ) and not target:
        command.error("This operation requires --account (or --create-account)")
    if args.account and args.create_account:
        command.error("Choose --account or --create-account")
    local_action = (
        args.delete_account
        or args.list_accounts
        or args.browser_start
        or args.browser_stop
        or args.schedule
    )
    if local_action and (
        args.account
        or args.init
        or args.reauth
        or args.configure
        or args.stats
        or args.daily_only
        or args.pc is not None
        or args.mobile is not None
        or args.force
    ):
        command.error(
            "Choose one account-management, browser, scheduling, or run operation"
        )
    if args.configure and (
        args.init or args.reauth or args.stats or args.daily_only or args.force
    ):
        command.error("Use --configure separately from authentication or execution")
    if (args.init or args.reauth or args.stats) and (
        args.daily_only or args.pc is not None or args.mobile is not None
    ):
        command.error("Search counts are for runs or --configure")
    if (
        args.create_account
        and not args.configure
        and (args.daily_only or args.pc is not None or args.mobile is not None)
    ):
        command.error(
            "Create the account with --init, then run it; save counts with --configure"
        )
    if args.visible and not (args.init or args.reauth):
        command.error("--visible requires --init or --reauth")
    if args.yes and not args.delete_account:
        command.error("--yes is only for --delete-account")
    if (
        any(
            value is not None
            for value in (
                args.enable_schedule,
                args.advanced,
                args.duration_hours,
                args.queries_per_hour,
                args.dashboard,
                args.force_tasks,
                args.force_visual,
            )
        )
        and not args.configure
    ):
        command.error("Saved configuration options require --configure")
    if args.daily_only:
        if args.pc or args.mobile:
            command.error("--daily-only conflicts with nonzero search counts")
        args.pc = args.mobile = 0
    os.umask(0o077)
    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        with ProcessLock(os.path.join(APP_DIR, "cli.lock")):
            return dispatch(args)
    except BusyError as exc:
        log(str(exc))
        return 75
    except AuthenticationRequired as exc:
        log(str(exc))
        return 3
    except KeyboardInterrupt:
        log("Interrupted; browser and container cleanup completed where possible")
        return 130
    except Exception as exc:
        log(f"{type(exc).__name__}: {str(exc).split(chr(10), 1)[0]}")
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


def interrupt(signum, frame):
    raise KeyboardInterrupt
