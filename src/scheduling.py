"""Render and install the daily user timer for this checkout, without root."""

from pathlib import Path
import re
import subprocess

from .config import BASE_DIR


def render_units(run_path, run_time):
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", run_time):
        raise ValueError("Use a 24-hour time such as 09:00")
    # systemd has its own quoting and specifiers, independent of shell escaping.
    executable = (
        str(run_path)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("%", "%%")
        .replace("$", "$$")
    )
    if "\n" in executable or "\r" in executable:
        raise ValueError("Checkout path must not contain line breaks")
    directory = Path(BASE_DIR) / "deploy/systemd"
    service = (
        (directory / "autorewarder-pi.service")
        .read_text()
        .replace("@RUN_SH@", executable)
    )
    timer = (
        (directory / "autorewarder-pi.timer")
        .read_text()
        .replace("@RUN_TIME@", run_time)
    )
    return service, timer


def manage_schedule(action, run_time):
    folder = Path.home() / ".config/systemd/user"
    unit = "autorewarder-pi.timer"
    if action == "install":
        service, timer = render_units(Path(BASE_DIR) / "run.sh", run_time)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "autorewarder-pi.service").write_text(service)
        (folder / unit).write_text(timer)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", unit], check=True)
        print(
            f"Daily timer installed for {run_time} host-local time. For runs after logout/reboot, enable linger:"
        )
        print('sudo loginctl enable-linger "$USER"')
    elif action == "remove":
        subprocess.run(["systemctl", "--user", "disable", "--now", unit], check=True)
        # An already-active service is allowed to finish and clean up its container.
        for name in ("autorewarder-pi.service", unit):
            (folder / name).unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    else:
        return subprocess.run(
            ["systemctl", "--user", "list-timers", "--all", unit]
        ).returncode
    return 0
