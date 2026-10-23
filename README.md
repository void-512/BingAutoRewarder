# AutoRewarder Pi

A headless Debian 13 ARM64 / Raspberry Pi 5 application. Python runs on the host
and controls Chromium through Selenium in Docker. Daily Set, More Activities,
desktop/mobile searches, visual search, account history and statistics are retained.
There is no desktop GUI, Edge, Windows packaging or host browser requirement.
Task automation is adapted from [safarsin/AutoRewarder](https://github.com/safarsin/AutoRewarder).

## Install

System prerequisites (run only if missing):

```bash
sudo apt-get update
sudo apt-get install python3 python3-venv ca-certificates docker.io docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
# Log out and back in for Docker group membership to take effect.
```

Docker access grants control of the host; use a trusted application account.
Install `openssh-server` only if SSH is not already available. No host Chromium,
VNC package, display server or apt installation is performed by the application.

```bash
git clone <repository-url> autorewarder-pi
cd autorewarder-pi
./run.sh --setup
./run.sh --create-account Alice --init
./run.sh --account Alice --daily-only
```

`--setup` checks prerequisites and Docker access, creates `.env` and `.venv`,
installs binary Python wheels, and validates Compose. Repeat after application
updates; unchanged requirements are not reinstalled. Existing settings, accounts
and profiles are retained. No container starts until a browser operation needs it.
The three direct runtime dependencies are Selenium, requests and Pillow.

The entry point works from any directory, including paths containing spaces.
`./run.sh --help` works before setup and shows all options after setup.

## Configuration and persistent data

`.env` is the single deployment configuration file; see `.env.example`. Paths are
relative to the checkout unless absolute. Defaults store host data under `var/`
and Chromium data under `var/chromium/profiles/`. Keep these directories and `.env`
across updates. They are excluded from Git. Account schedule/task settings are
saved by `--configure` in the persistent account store.

On the first `--setup` after upgrading the earlier implementation, an existing
`deploy/autorewarder.env` is copied to `.env` so custom data/profile locations stay
intact. Thereafter only `.env` is read. The old local file is not deleted or changed.
No account-data migration or reset is performed.

Chromium sees `/home/seluser/profiles/<account-id>`. This directory is a persistent
Docker bind mount of `<AUTOREWARDER_CHROMIUM_DATA_DIR>/profiles`; it is never a
throwaway container layer. Startup prepares only the mount root's `seluser`
ownership. Profile directories are private to the browser user; the host account
store uses umask 077. Browser cookies use the container's basic password store
without a desktop keyring, so protect profile backups and Docker access as credentials.

The default image is pinned to the ARM64 manifest of
`selenium/standalone-chromium:4.51.0-20261010` in `deploy/compose.yaml`. Override
`SELENIUM_IMAGE` deliberately when updating. First use may pull the image, with
visible progress. `--setup` does not pull it. The defaults use one session, two
CPU cores, a 3 GiB memory limit, 1 GiB shared memory and 512 MiB temporary RAM storage.
The host kernel must support memory cgroups to enforce the memory limit; Docker
prints a warning if it cannot. Resource settings still need adjustment for your
actual workload. Logs rotate; temporary Python/test/pip data uses
`~/.cache/codex-tmp`.

## Browser lifecycle

All operations use `./run.sh`; manual Docker startup is unnecessary.

- If Chromium is running with the profile mount configured in `.env`, reuse it
  and leave it running afterward. A different mount is rejected before account use.
- If stopped or absent, start it, wait for Selenium readiness, perform the operation,
  then stop it on success, failure or interruption. Storage stays intact.
- Local listing, creation without login, configuration, statistics and scheduling
  do not start Docker.
- Operations serialize through host-data and container leases, including across
  checkouts using the same local WebDriver port. An active foreign Selenium
  session prevents deletion/stopping. If one remains active during cleanup,
  Chromium stays running and the CLI reports it.

Optional explicit controls:

```bash
./run.sh --browser-start   # Deliberately keep Chromium running
./run.sh --browser-stop    # Refuses if a browser session is active
```

WebDriver binds only to `127.0.0.1:4444`. noVNC is disabled normally. There is no
background container-management daemon and no volume-deleting shutdown command.
After a hard kill/power loss, a started container may remain running; the next run
reuses it. Finish any active session before stopping it explicitly.

## Authentication

Login presents page information followed by numbered **Input**, **Click** and
navigation options. Enter only a number; input values are requested automatically
and hidden. Numbered options also refresh the page, check authentication, wait for
remote MFA approval, navigate back/open Rewards, or cancel. Invalid numbers simply
show the menu again.

Use Microsoft's offered Authenticator, passwordless or verification-code methods.
Complete challenges yourself and choose **Stay signed in** when offered. OAuth
access tokens are not substituted for browser cookies. Session verification requires
an authenticated Rewards dashboard with a readable balance, including zero; changed
site markup or an unavailable dashboard can also make verification fail.

```bash
./run.sh --account Alice --reauth
# Optional graphical fallback for native dialogs, QR codes or CAPTCHA:
./run.sh --account Alice --reauth --visible
```

If Chromium is already running without noVNC, stop it while idle with
`./run.sh --browser-stop` before `--visible`. The fallback prompts for an 8-character
VNC password unless `SE_VNC_PASSWORD` is configured. In another terminal on your
workstation:

```bash
ssh -N -L 7900:127.0.0.1:7900 USER@YOUR_PI
```

Open [noVNC through the tunnel](http://127.0.0.1:7900), complete login, then choose
**Check authentication status** in the terminal menu. Port 7900 is loopback-only;
raw VNC port 5900 is not published. A container started for this login is stopped
afterward; its next normal start uses the base configuration without noVNC.
Passkeys may require Bluetooth or physical-device access that this container does
not expose; use another offered sign-in method when necessary. No challenge is bypassed.

## Commands

Account selectors accept an ID or an unambiguous case-insensitive label. Deletion
requires the exact ID printed by `--list-accounts`.

```bash
./run.sh --create-account Alice            # Create without starting Docker
./run.sh --list-accounts
./run.sh --account Alice --init
./run.sh --account Alice --daily-only
./run.sh --account Alice --pc 0 --mobile 0 # Equivalent daily-only run
./run.sh --account Alice --pc 30 --mobile 20
./run.sh --account Alice --stats

# Configure counts and whether this account participates in unattended runs:
./run.sh --account Alice --configure --enable-schedule --pc 30 --mobile 20
./run.sh --account Alice --configure --enable-schedule --pc 0 --mobile 0
./run.sh --account Alice --configure --no-enable-schedule
./run.sh --account Alice --configure --advanced --duration-hours 3 --queries-per-hour 10
./run.sh --account Alice --configure --no-advanced --dashboard auto
./run.sh --account Alice --configure --force-tasks --force-visual
./run.sh --account Alice --configure --no-force-tasks --no-force-visual

./run.sh                                 # All enabled accounts, once per local date
./run.sh --force                          # Ignore the already-run-today guard
./run.sh --delete-account ACCOUNT_ID      # Interactive, explicit confirmation
./run.sh --delete-account ACCOUNT_ID --yes
```

Explicit counts and `--daily-only` bypass schedule enable/date guards. A missing
count uses that account's saved count. Force-task/visual settings are shared
operational settings; completed cards still use the existing website checks.
Advanced pacing applies when enabled. A failed partial run retains its statistics
and is retryable, so a retry may repeat some completed searches.

Deletion acquires the run/profile locks, verifies Selenium is idle, and removes
only the selected mounted Chromium profile using a checked container-side path.
It then erases that account's host files, schedule and index/current-account
references. Other accounts and the shared mount remain intact. IDs, symlinks and
mount boundaries are checked. Partial failures retain a visible `deletion_pending`
index entry, disable scheduling, and report locations to inspect; retry the same
delete command. Do not delete profile files manually while Chromium is running.

## Daily scheduling

```bash
./run.sh --account Alice --configure --enable-schedule --pc 30 --mobile 20
./run.sh --schedule install --time 09:00
sudo loginctl enable-linger "$USER"        # Once, to run after logout/reboot
./run.sh --schedule status
./run.sh --schedule remove
```

The installer renders user systemd units with the actual checkout path. The timer
runs all enabled accounts at the chosen **host-local** time, with up to 15 minutes
of jitter, and catches missed runs. Ensure the host timezone and `.env`'s `TZ`
match. Disable any old upstream per-account timers before switching to this timer.
The service uses the same automatic container lifecycle, logs to the journal,
retries ordinary failures after 10 minutes (at most three starts/hour), and sends
SIGINT on shutdown so browser cleanup runs. Removing the timer lets an already
active run finish.

```bash
journalctl --user -u autorewarder-pi.service -f
systemctl --user start autorewarder-pi.service
```

Per-account logs are under `var/accounts/<id>/run.log`, bounded at 6 MiB each.
Exit codes: 0 success/no schedules due; 1 failure; 2 invalid arguments; 3 human
reauthentication needed; 75 another operation/session is busy; 130 interrupted.
No external notification service is configured.

## Development and validation

```bash
./run.sh --setup
.venv/bin/python -m pip install -r requirements-dev.txt
TMPDIR="$HOME/.cache/codex-tmp" TMP="$HOME/.cache/codex-tmp" TEMP="$HOME/.cache/codex-tmp" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
```

The offline suite isolates all data in RAM and covers lifecycle decisions,
confirmation/deletion boundaries and retries, numbered authentication, Remote
WebDriver/CDP/file upload serialization, scheduling, and CLI-only imports. CI also
runs formatting, lint, type checks and Compose/shell validation. Live browser checks
use disposable profiles and never access Microsoft accounts. Successful transport
or cookie-persistence checks do not establish that Microsoft Rewards will credit
any particular task; real sign-in and Rewards workflows need account-level validation.

Validation on 2026-10-23: 34 tests passed in both development and fresh runtime-only
environments. Fresh/repeated setup, outside-directory invocation, Black, Flake8,
mypy, both Compose configurations and rendered systemd units passed. Live tests
with the cached ARM64 Chromium image verified DOM actions, uploads, mobile CDP,
cookie persistence across browser/container restarts, selected-profile deletion,
and cleanup after failure/interruption. The original running container was left
running. Real Microsoft login, Rewards crediting, noVNC interaction and a timer run
after reboot remain unverified.
