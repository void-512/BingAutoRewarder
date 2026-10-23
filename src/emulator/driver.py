"""Selenium Remote WebDriver using one persistent container profile per account."""

import hashlib
import os

from selenium import webdriver
from selenium.common.exceptions import InvalidSessionIdException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.remote.client_config import ClientConfig
from selenium.webdriver.remote.file_detector import LocalFileDetector

from ..config import APP_DIR, browser_profile_path
from ..locking import ProcessLock


def profile_lock_path(account_id):
    digest = hashlib.sha256(os.fsencode(browser_profile_path(account_id))).hexdigest()
    return os.path.join(APP_DIR, "locks", digest + ".lock")


class RemoteDriver(webdriver.Remote):
    profile_lock = None

    def quit(self):
        try:
            super().quit()
        except InvalidSessionIdException:
            # The remote browser is already gone; release the profile lease so
            # recovery can create a fresh session using that same profile.
            pass
        if self.profile_lock is not None:
            self.profile_lock.release()
            self.profile_lock = None


class DriverManager:
    MOBILE_USER_AGENT = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2_1 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 "
        "Mobile/15E148 Safari/604.1"
    )

    def __init__(self, account_id):
        self.account_id = account_id

    def setup_driver(self, headless=True, mobile=False):
        options = Options()
        for argument in (
            f"--user-data-dir={browser_profile_path(self.account_id)}",
            "--profile-directory=Default",
            "--no-default-browser-check",
            "--no-first-run",
            "--password-store=basic",
            "--window-size=412,915" if mobile else "--window-size=1920,1080",
        ):
            options.add_argument(argument)
        if headless:
            options.add_argument("--headless=new")
        if mobile:
            options.add_argument(f"--user-agent={self.MOBILE_USER_AGENT}")
        lease = ProcessLock(profile_lock_path(self.account_id)).__enter__()
        _driver = None
        try:
            endpoint = os.environ.get(
                "AUTOREWARDER_REMOTE_URL", "http://127.0.0.1:4444"
            )
            _driver = RemoteDriver(
                command_executor=endpoint,
                options=options,
                client_config=ClientConfig(remote_server_addr=endpoint, timeout=120),
                file_detector=LocalFileDetector(),
            )
            _driver.profile_lock = lease
            _driver.set_page_load_timeout(60)
            _driver.set_script_timeout(30)
        except BaseException:
            if _driver is not None:
                _driver.quit()
            else:
                lease.release()
            raise

        if mobile:
            # Turn the session into a genuine mobile one at the engine level.
            # Beyond the UA string, this makes `navigator.maxTouchPoints > 0`,
            # `window.matchMedia("(pointer: coarse)")` true, the viewport match
            # iPhone metrics, and touch events fire for real — so sites that
            # fingerprint using the DOM/CSS touch surface see a real mobile.
            try:
                _driver.execute_cdp_cmd(
                    "Emulation.setTouchEmulationEnabled",
                    {"enabled": True, "maxTouchPoints": 5},
                )
                _driver.execute_cdp_cmd(
                    "Emulation.setEmitTouchEventsForMouse",
                    {"enabled": True, "configuration": "mobile"},
                )
                _driver.execute_cdp_cmd(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": 412,
                        "height": 915,
                        "deviceScaleFactor": 3,
                        "mobile": True,
                    },
                )
                _driver.execute_cdp_cmd(
                    "Emulation.setUserAgentOverride",
                    {
                        "userAgent": self.MOBILE_USER_AGENT,
                        "platform": "iPhone",
                        "userAgentMetadata": {
                            "platform": "iOS",
                            "platformVersion": "17.2.1",
                            "architecture": "",
                            "model": "iPhone",
                            "mobile": True,
                        },
                    },
                )
            except Exception:
                # CDP is best-effort; fall back to the UA+window-size flags.
                pass

        return _driver
