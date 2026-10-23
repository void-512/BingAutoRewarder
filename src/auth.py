"""Interactive browser authentication without exchanging OAuth tokens for cookies."""

from getpass import getpass
import sys
import time
from urllib.parse import urlsplit

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from .stats import scrape_points_balance

REWARDS_URL = "https://rewards.bing.com/dashboard"
REWARDS_HOSTS = {"rewards.bing.com", "rewards.microsoft.com"}
LOGIN_HOSTS = REWARDS_HOSTS | {
    "login.live.com",
    "account.live.com",
    "login.microsoftonline.com",
    "www.bing.com",
    "account.microsoft.com",
}


class AuthenticationRequired(RuntimeError):
    """The stored browser profile no longer opens an authenticated dashboard."""


def session_ready(driver):
    """Require a Rewards origin and an account-specific balance, including zero."""
    page = urlsplit(driver.current_url)
    return (
        page.scheme == "https"
        and page.hostname in REWARDS_HOSTS
        and scrape_points_balance(driver) is not None
    )


def require_session(driver):
    """Fail closed before doing unattended tasks; do not log cookies or page text."""
    for url in (REWARDS_URL, "https://rewards.bing.com/"):
        driver.get(url)
        try:
            WebDriverWait(driver, 15).until(session_ready)
            return
        except WebDriverException:
            pass
    raise AuthenticationRequired(
        "Rewards session expired, challenged, or dashboard could not be verified. "
        "Run ./run.sh --reauth --account <id-or-label>; use --visible if graphical interaction is needed."
    )


def interactive_login(
    manager, meta, visible=False, read=input, secret=getpass, emit=print
):
    """A numbered view of Microsoft's real browser form; never exchanges tokens for cookies."""
    if not sys.stdin.isatty():
        raise ValueError("Authentication requires an interactive terminal")
    driver = manager.setup_driver(headless=not visible)
    verified = False
    try:
        driver.get(REWARDS_URL)
        emit(
            "Choose a numbered action. Input values are hidden and never logged.\n"
            "Use the site's Authenticator/passwordless options when offered.\n"
            "For native dialogs, QR codes or CAPTCHA, use the optional --visible noVNC session."
        )
        while True:
            if session_ready(driver):
                verified = True
                break
            page = urlsplit(driver.current_url)
            if page.scheme != "https" or page.hostname not in LOGIN_HOSTS:
                raise AuthenticationRequired(
                    "Unexpected sign-in origin; inspect it with --visible"
                )
            emit(
                f"\nPage information: {page.scheme}://{page.netloc}{page.path}\n"
                + driver.find_element(By.TAG_NAME, "body").text[:6000]
            )
            actions = []
            for field in driver.find_elements(
                By.CSS_SELECTOR,
                "input:not([type=hidden]):not([type=submit]):not([type=button]):"
                "not([type=checkbox]):not([type=radio])",
            ):
                if field.is_displayed() and field.is_enabled():
                    label = (
                        field.get_attribute("aria-label")
                        or field.get_attribute("name")
                        or "Value"
                    )
                    actions.append(("input", f"Input: {label}", field))
            for button in driver.find_elements(
                By.CSS_SELECTOR,
                "button, input[type=submit], input[type=button], a, [role=button]",
            ):
                if button.is_displayed() and button.is_enabled():
                    label = (
                        button.text
                        or button.get_attribute("value")
                        or button.get_attribute("aria-label")
                    )
                    if label:
                        actions.append(("click", f"Click: {label}", button))
            actions.extend(
                (kind, label, None)
                for kind, label in (
                    ("back", "Navigation: back"),
                    ("rewards", "Navigation: open Rewards"),
                    ("refresh", "Refresh this page"),
                    ("check", "Check authentication status"),
                    ("wait", "Wait for approval on another device (up to 30 seconds)"),
                    ("cancel", "Cancel and retain the existing profile"),
                )
            )
            for index, (_, label, _) in enumerate(actions, 1):
                emit(f"{index}. {label}")
            choice = read("Select a number: ").strip()
            if not choice.isdigit() or not 1 <= int(choice) <= len(actions):
                emit("Enter one of the listed numbers.")
                continue
            kind, _, element = actions[int(choice) - 1]
            if kind == "cancel":
                raise AuthenticationRequired(
                    "Authentication cancelled; profile retained"
                )
            try:
                if kind == "input":
                    value = secret("Value (hidden): ")
                    element.clear()
                    element.send_keys(value)
                    del value
                elif kind == "click":
                    element.click()
                elif kind == "back":
                    driver.back()
                elif kind == "rewards":
                    driver.get(REWARDS_URL)
                elif kind == "refresh":
                    driver.refresh()
                elif kind == "check":
                    try:
                        require_session(driver)
                        verified = True
                        break
                    except AuthenticationRequired:
                        emit(
                            "The Rewards session is not verified yet. Complete the displayed sign-in steps."
                        )
                elif kind == "wait":
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline and not session_ready(driver):
                        time.sleep(2)
                time.sleep(0.5)
            except WebDriverException:
                emit(
                    "The page changed or that action is unavailable. Choose from the updated menu."
                )
    finally:
        driver.quit()
    if verified:
        meta.mark_up_as_done()
        emit("Authenticated Rewards dashboard verified; persistent profile saved.")
