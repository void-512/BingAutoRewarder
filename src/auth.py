"""Interactive browser authentication without exchanging OAuth tokens for cookies."""

import re
import select
import sys
import termios
import time
from urllib.parse import urlsplit

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
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


# Read text and controls together so a React transition cannot mix two steps.
# Deliberately exclude input values from the snapshot and its fingerprint.
LOGIN_PAGE_SCRIPT = """
const visible = e => e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility !== 'hidden';
const enabled = e => !e.disabled && e.getAttribute('aria-disabled') !== 'true';
const scope = document.querySelector('#view, [role=main], main, form') || document.body;
const label = e => e.getAttribute('aria-label') ||
    (e.getAttribute('aria-labelledby') || '').split(/\\s+/)
      .map(id => document.getElementById(id)?.textContent || '').join(' ').trim() ||
    Array.from(e.labels || []).map(l => l.innerText).join(' ').trim() ||
    e.placeholder || e.name || e.id || 'Value';
const actions = [];
for (const e of document.querySelectorAll(
    'input:not([type=hidden]):not([type=submit]):not([type=button]):' +
    'not([type=checkbox]):not([type=radio])')) {
    if (visible(e) && enabled(e) && !e.readOnly)
        actions.push({kind:'input', label:label(e), element:e, primary:scope.contains(e)});
}
for (const e of document.querySelectorAll(
    'button, input[type=submit], input[type=button], a, [role=button]')) {
    if (!visible(e) || !enabled(e)) continue;
    const text = e.innerText || (e.tagName === 'INPUT' ? e.value : '') ||
        e.getAttribute('aria-label');
    if (text) actions.push({kind:'click', label:text.trim(), element:e,
        primary:scope.contains(e) && !e.closest('footer')});
}
const busy = Array.from(scope.querySelectorAll(
    '[aria-busy=true], [role=progressbar], progress')).some(visible);
return {url:location.href, text:(document.body?.innerText || '').slice(0,6000),
    actions:actions, ready:document.readyState !== 'loading' && !busy &&
        actions.some(a => a.primary && a.element.id !== 'back-button')};
"""


def read_login_page(driver):
    return driver.execute_script(LOGIN_PAGE_SCRIPT)


def login_page_key(page):
    return (
        page["url"],
        page["text"],
        page["ready"],
        tuple((a["kind"], a["label"], a["element"].id) for a in page["actions"]),
    )


def wait_for_login_page(driver, previous=None, timeout=10):
    """Wait for a stable interactive step, with a fixed upper bound."""
    deadline = time.monotonic() + timeout
    key = None
    stable_since = time.monotonic()
    page = None
    while True:
        now = time.monotonic()
        try:
            page = read_login_page(driver)
            current = login_page_key(page)
            if current != key:
                key, stable_since = current, now
            if page["ready"] and key != previous and now - stable_since >= 0.6:
                return page
        except WebDriverException:
            page = None
            key, stable_since = None, now
        if now >= deadline:
            # Take a fresh snapshot if every earlier read failed.
            return page if page is not None else read_login_page(driver)
        time.sleep(0.2)


def read_login_choice(driver, shown_key, emit=print):
    """Watch the page while waiting for a terminal choice, on the main thread."""
    sys.stdout.write("Select a number: ")
    sys.stdout.flush()
    while True:
        readable, _, _ = select.select([sys.stdin], [], [], 1)
        if readable:
            return input("").strip()
        try:
            changed = session_ready(driver) or (
                login_page_key(read_login_page(driver)) != shown_key
            )
        except WebDriverException:
            changed = True  # Let the bounded page reader handle navigation races.
        if changed:
            sys.stdout.write("\n")
            sys.stdout.flush()
            # A half-entered number belongs to the old menu. Never reuse it as
            # a choice on a different screen (especially an approval screen).
            try:
                termios.tcflush(sys.stdin, termios.TCIFLUSH)
            except (OSError, ValueError):
                pass
            emit("Sign-in page updated automatically; showing the current step.")
            return None


class AuthenticationRequired(RuntimeError):
    """The stored browser profile no longer opens an authenticated dashboard."""


def authenticator_number(driver):
    """Read only Microsoft's number-matching challenge, never other hidden fields."""
    for selector, attribute, require_visible in (
        ("#psRNGCEntropy", "value", False),
        ("#idRichContext_DisplaySign", "textContent", True),
    ):
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            if require_visible and not element.is_displayed():
                continue
            value = (element.get_attribute(attribute) or "").strip()
            if re.fullmatch(r"[0-9]{1,3}", value):
                return value
    return None


def show_authenticator_number(driver, emit, previous=None):
    number = authenticator_number(driver)
    if number is not None and number != previous:
        emit(
            f"Microsoft Authenticator number: {number}\n"
            "Enter this number in Microsoft Authenticator on your device."
        )
    return number


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
    manager, meta, visible=False, read=input, value_read=input, emit=print
):
    """A numbered view of Microsoft's real browser form; never exchanges tokens for cookies."""
    if not sys.stdin.isatty():
        raise ValueError("Authentication requires an interactive terminal")
    driver = manager.setup_driver(headless=not visible)
    verified = False
    next_page = None
    try:
        driver.get(REWARDS_URL)
        emit(
            "Choose a numbered action. Typed values are visible in the terminal.\n"
            "Use the site's Authenticator/passwordless options when offered.\n"
            "For native dialogs, QR codes or CAPTCHA, use the optional --visible noVNC session."
        )
        while True:
            if session_ready(driver):
                verified = True
                break
            snapshot = next_page or wait_for_login_page(driver)
            next_page = None
            page = urlsplit(snapshot["url"])
            if page.scheme != "https" or page.hostname not in LOGIN_HOSTS:
                raise AuthenticationRequired(
                    "Unexpected sign-in origin; inspect it with --visible"
                )
            emit(
                f"\nPage information: {page.scheme}://{page.netloc}{page.path}\n"
                + snapshot["text"]
            )
            number = show_authenticator_number(driver, emit)
            shown_key = login_page_key(snapshot)
            actions = [
                (
                    a["kind"],
                    f"{'Input' if a['kind'] == 'input' else 'Click'}: {a['label']}",
                    a["element"],
                )
                for a in snapshot["actions"]
            ]
            actions.extend(
                (kind, label, None)
                for kind, label in (
                    ("back", "Navigation: back"),
                    ("rewards", "Navigation: open Rewards"),
                    ("refresh", "Refresh this page"),
                    ("check", "Check authentication status"),
                    (
                        "wait",
                        "Wait for page update or device approval (up to 30 seconds)",
                    ),
                    ("cancel", "Cancel and retain the existing profile"),
                )
            )
            for index, (_, label, _) in enumerate(actions, 1):
                emit(f"{index}. {label}")
            choice = (
                read_login_choice(driver, shown_key, emit)
                if read is input
                else read("Select a number: ").strip()
            )
            if choice is None:
                continue
            if not choice.isdigit() or not 1 <= int(choice) <= len(actions):
                emit("Enter one of the listed numbers.")
                continue
            kind, _, element = actions[int(choice) - 1]
            if kind == "cancel":
                raise AuthenticationRequired(
                    "Authentication cancelled; profile retained"
                )
            try:
                latest = read_login_page(driver)
                if login_page_key(latest) != shown_key:
                    emit(
                        "The sign-in page changed while waiting. Choose from the updated menu."
                    )
                    next_page = wait_for_login_page(driver)
                    continue
                if kind == "input":
                    value = value_read("Value: ")
                    try:
                        if not value:
                            emit(
                                "No value entered; the existing field was left unchanged."
                            )
                            continue
                        element.clear()
                        element.send_keys(value)
                        if element.get_property("value") != value:
                            emit(
                                "The field did not retain the entered value. Enter it again."
                            )
                        else:
                            element.send_keys(Keys.TAB)
                            emit("Value entered.")
                    finally:
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
                        number = show_authenticator_number(driver, emit, number)
                        current = read_login_page(driver)
                        if current["ready"] and login_page_key(current) != shown_key:
                            break
                        time.sleep(0.5)
                previous = (
                    shown_key
                    if kind in ("click", "back", "rewards", "refresh")
                    else None
                )
                next_page = wait_for_login_page(driver, previous=previous)
                if not next_page["ready"]:
                    emit(
                        "The sign-in page is still loading. Wait for a page update; refreshing resets the form."
                    )
                elif previous is not None and login_page_key(next_page) == previous:
                    emit(
                        "The page has not advanced. Review any validation message before retrying."
                    )
            except WebDriverException:
                emit(
                    "The page changed or that action is unavailable. Choose from the updated menu."
                )
    finally:
        driver.quit()
    if verified:
        meta.mark_up_as_done()
        emit("Authenticated Rewards dashboard verified; persistent profile saved.")
