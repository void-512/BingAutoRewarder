"""Small Compose lifecycle wrapper, used only while holding the CLI process lock."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import subprocess
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

from .config import APP_DIR, BASE_DIR, browser_profile_path
from .locking import BusyError, ProcessLock


class DockerBrowser:
    def __init__(self, emit=print):
        self.emit = emit
        self.endpoint = os.environ.get(
            "AUTOREWARDER_REMOTE_URL", "http://127.0.0.1:4444"
        ).rstrip("/")
        if urlsplit(self.endpoint).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError(
                "WebDriver must use a loopback URL for this local Docker deployment"
            )
        self.compose = [
            "compose",
            "--env-file",
            os.environ.get("AUTOREWARDER_ENV_FILE", os.path.join(BASE_DIR, ".env")),
            "-f",
            os.path.join(BASE_DIR, "deploy/compose.yaml"),
        ]
        self.http = build_opener(ProxyHandler({}))

    def container_lock(self):
        # Different checkouts/data stores can share the same localhost Grid.
        # Hold one user-wide lease for its port, including between browser phases.
        port = urlsplit(self.endpoint).port or 4444
        key = hashlib.sha256(str(port).encode()).hexdigest()[:16]
        return ProcessLock(
            Path.home() / ".cache/codex-tmp" / f"autorewarder-container-{key}.lock"
        )

    def command(self, arguments, capture=True):
        """Forward signals to Docker subprocesses before cleaning up the container."""
        process = subprocess.Popen(
            ["docker", *arguments],
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            output, error = process.communicate()
        except BaseException:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        if process.returncode:
            detail = (error or "Docker command failed; see output above").strip()
            raise RuntimeError(detail)
        return (output or "").strip()

    def check(self):
        if not shutil.which("docker"):
            raise RuntimeError(
                "Docker is missing. Install: sudo apt-get install docker.io docker-compose"
            )
        try:
            self.command(["compose", "version"])
        except RuntimeError as exc:
            raise RuntimeError(
                "Docker Compose v2 is required: sudo apt-get install docker-compose"
            ) from exc
        try:
            self.command(["info", "--format", "{{.ServerVersion}}"])
        except RuntimeError as exc:
            raise RuntimeError(
                "Cannot access Docker. Start it with sudo systemctl enable --now docker; "
                'grant this user Docker access (sudo usermod -aG docker "$USER", then log in again). '
                "Do not run the application as root."
            ) from exc

    def container_id(self):
        ids = self.command(
            [*self.compose, "ps", "--all", "--quiet", "chromium"]
        ).splitlines()
        return ids[0] if ids else None

    def running(self, container):
        return (
            self.command(["inspect", "--format", "{{.State.Running}}", container])
            == "true"
        )

    def status(self):
        try:
            with self.http.open(self.endpoint + "/status", timeout=5) as response:
                return json.load(response)["value"]
        except (OSError, URLError, ValueError, KeyError):
            return None

    def sessions(self):
        status = self.status()
        if status is None:
            return None
        return [
            slot["session"]
            for node in status.get("nodes", [])
            for slot in node.get("slots", [])
            if slot.get("session")
        ]

    def wait_ready(self):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = self.status()
            if status and status.get("ready"):
                return
            time.sleep(2)
        raise RuntimeError(
            "Selenium did not become ready; inspect docker compose logs chromium"
        )

    def profile_root(self):
        return str(PurePosixPath(browser_profile_path("000000000000")).parent)

    def verify_profile_mount(self, container):
        root = PurePosixPath(self.profile_root())
        mounts = json.loads(
            self.command(["inspect", "--format", "{{json .Mounts}}", container])
        )
        for mount in mounts:
            destination = PurePosixPath(mount["Destination"])
            if mount.get("RW") and (destination == root or destination in root.parents):
                expected = (
                    Path(
                        os.environ.get(
                            "AUTOREWARDER_CHROMIUM_DATA_DIR",
                            os.path.join(APP_DIR, "chromium"),
                        )
                    )
                    / "profiles"
                )
                actual = Path(mount["Source"]).joinpath(
                    *root.relative_to(destination).parts
                )
                if mount["Type"] == "bind" and actual.resolve() == expected.resolve():
                    return
                raise RuntimeError(
                    "Chromium's mounted profile storage differs from .env. "
                    "Correct the configuration before using or deleting accounts; existing storage was left intact."
                )
        raise RuntimeError(
            f"Profile directory {root} is not backed by a writable Docker mount"
        )

    @contextmanager
    def session(self, visible=False, keep=False):
        with self.container_lock():
            with self._session(visible=visible, keep=keep) as container:
                yield container

    @contextmanager
    def _session(self, visible=False, keep=False):
        self.check()
        container = self.container_id()
        started = not (container and self.running(container))
        complete = False
        try:
            if started:
                compose = self.compose.copy()
                if visible:
                    compose.extend(
                        ["-f", os.path.join(BASE_DIR, "deploy/compose.auth.yaml")]
                    )
                self.emit(
                    "Starting Chromium; Docker may pull the configured ARM64 image on first use."
                )
                self.command(
                    [
                        *compose,
                        "up",
                        "-d",
                        "--wait",
                        "--wait-timeout",
                        "180",
                        "chromium",
                    ],
                    capture=False,
                )
                container = self.container_id()
                if not container:
                    raise RuntimeError("Compose did not create a Chromium container")
                self.verify_profile_mount(container)
                # The image runs browsers as seluser. Fix only the mount root,
                # never recursively change an existing account's files.
                prepare = (
                    "import os,pwd,sys; p=sys.argv[1]; u=pwd.getpwnam('seluser'); "
                    "os.makedirs(p,exist_ok=True); os.chown(p,u.pw_uid,u.pw_gid); os.chmod(p,0o700)"
                )
                self.command(
                    [
                        "exec",
                        "--user",
                        "root",
                        container,
                        "python3",
                        "-c",
                        prepare,
                        self.profile_root(),
                    ]
                )
            else:
                self.verify_profile_mount(container)
            if not started and visible:
                ports = json.loads(
                    self.command(
                        [
                            "inspect",
                            "--format",
                            "{{json .NetworkSettings.Ports}}",
                            container,
                        ]
                    )
                )
                if not ports.get("7900/tcp"):
                    raise RuntimeError(
                        "Running Chromium has no noVNC port. Use --browser-stop when idle, then retry --visible"
                    )
            self.wait_ready()
            yield container
            complete = True
        finally:
            if started and not (keep and complete):
                container = container or self.container_id()
                if container:
                    active = self.sessions()
                    if active:
                        self.emit(
                            "Leaving Chromium running because a browser session is still active."
                        )
                    else:
                        self.emit(
                            "Stopping Chromium started by this invocation; profile storage is retained."
                        )
                        self.command([*self.compose, "stop", "chromium"], capture=False)

    def stop(self):
        with self.container_lock():
            self._stop()

    def _stop(self):
        self.check()
        container = self.container_id()
        if container and self.running(container):
            active = self.sessions()
            if active is None:
                raise RuntimeError(
                    "Cannot verify Selenium is idle; refusing to stop it"
                )
            if active:
                raise BusyError(
                    "A browser session is active; finish it before stopping Chromium"
                )
            self.command([*self.compose, "stop", "chromium"], capture=False)
