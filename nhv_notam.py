#!/usr/bin/env python3
"""
nhv_notam.py  (v3)

Changes from v2:
  - Searches every frame on the page for the Print PDF control, since the
    rendered briefing may sit inside an iframe.
  - Logs every button and link it can see after Generate, so if the control
    still isn't found we can see what's actually there.
  - Separates "couldn't click the button" from "clicked but nothing downloaded",
    which v2 conflated.
  - Watches for downloads, popups and same-tab PDF navigation.
  - Auto-accepts any JS dialog.
"""

import logging
import os
import re
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv

    load_dotenv(HERE / ".env")
except ImportError:
    pass

BASE_URL = "https://nats-uk.ead-it.com/cms-nats/opencms/en/home/"
BRIEFING_MATCH = os.getenv("BRIEFING_MATCH", "NHVNOTM")
USE_BUTTON_ID = "mainForm:handbook:resultList:useButton"

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", HERE / "downloads"))

NATS_USER = os.getenv("NATS_USER")
NATS_PASS = os.getenv("NATS_PASS")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER or "")
MAIL_TO = os.getenv("MAIL_TO", "whanekom@me.com")

DEBUG = os.getenv("DEBUG") == "1"
NO_EMAIL = os.getenv("NO_EMAIL") == "1"
TIMEOUT = int(os.getenv("TIMEOUT_MS", "20000"))

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(HERE / "nhv_notam.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("nhv")


def log_in(page):
    log.info("Opening %s", BASE_URL)
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=TIMEOUT)

    user_field = page.locator(
        "input[name*='user' i], input[id*='user' i], "
        "input[placeholder*='user' i], input[type='text']"
    ).first
    pass_field = page.locator("input[type='password']").first

    user_field.wait_for(state="visible", timeout=TIMEOUT)
    user_field.fill(NATS_USER)
    pass_field.fill(NATS_PASS)

    log.info("Submitting credentials")
    submit = page.locator(
        "button[type='submit'], input[type='submit'], "
        "button:has-text('Login'), button:has-text('Log in'), a:has-text('Login')"
    ).first
    if submit.count():
        submit.click()
    else:
        pass_field.press("Enter")

    page.wait_for_load_state("networkidle", timeout=TIMEOUT)
    log.info("Logged in")


def open_briefing_handbook(page):
    log.info("Opening Pre-flight Briefing > Briefing Handbook")

    menu = page.get_by_text("Pre-flight Briefing", exact=False).first
    menu.wait_for(state="visible", timeout=TIMEOUT)
    menu.hover()

    handbook = page.get_by_text("Briefing Handbook", exact=False).first
    handbook.wait_for(state="visible", timeout=15000)
    handbook.click()

    page.wait_for_load_state("networkidle", timeout=TIMEOUT)
    page.wait_for_timeout(2000)


def use_button(page):
    return page.locator(f'[id="{USE_BUTTON_ID}"]')


def wait_for_use_enabled(page, timeout_ms=8000) -> bool:
    try:
        page.wait_for_function(
            """(id) => {
                const el = document.getElementById(id);
                return el && !el.disabled &&
                       el.getAttribute('aria-disabled') !== 'true';
            }""",
            arg=USE_BUTTON_ID,
            timeout=timeout_ms,
        )
        return True
    except PlaywrightTimeout:
        return False


# The briefing list only shows the last week unless its "Valid from" filter
# is widened. After a break of more than a week (the job was paused, or the
# site was down) the saved briefing drops out of that view, so look further
# back, one step at a time, until it turns up.
VALID_FROM_STEPS = ["LAST_MONTH", "LAST_THREE_MONTHS", "LAST_YEAR", "ALL"]


def widen_valid_from(page, value: str) -> bool:
    """Set the list's "Valid from" filter. Returns False if it is not there."""
    changed = page.evaluate(
        """(value) => {
            const header = [...document.querySelectorAll('th')].find((th) =>
                /valid from/i.test(th.getAttribute('aria-label') || th.textContent || ''));
            const select = header && header.querySelector('select');
            if (!select || ![...select.options].some((o) => o.value === value)) return false;
            select.value = value;
            select.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }""",
        value,
    )
    if changed:
        try:
            page.wait_for_load_state("networkidle", timeout=TIMEOUT)
        except PlaywrightTimeout:
            pass
        page.wait_for_timeout(2000)
    return bool(changed)


def find_matching_row(page, pattern):
    """(index of the newest matching row or None, number of rows shown)."""
    rows = page.locator("tr[data-ri]")
    count = rows.count()
    log.info("Found %d row(s) in the briefing list", count)
    for i in range(min(count, 10)):
        log.info("  row %d: %s", i, rows.nth(i).inner_text().replace("\n", " | ")[:160])
    for i in range(count):
        if pattern.search(rows.nth(i).inner_text()):
            return i, count
    return None, count


def select_saved_briefing(page):
    log.info("Looking for a briefing matching %r", BRIEFING_MATCH)
    pattern = re.compile(re.escape(BRIEFING_MATCH), re.IGNORECASE)

    target_index, count = find_matching_row(page, pattern)
    widened = False
    if target_index is None:
        for value in VALID_FROM_STEPS:
            log.info("No match in this view - widening 'Valid from' to %s", value)
            if not widen_valid_from(page, value):
                log.warning("Could not find the 'Valid from' filter on the page")
                break
            widened = True
            target_index, count = find_matching_row(page, pattern)
            if target_index is not None:
                break

    if count == 0:
        raise RuntimeError("No rows found in the briefing list.")
    if target_index is None:
        if widened:
            raise RuntimeError(
                f"No saved briefing matching {BRIEFING_MATCH!r} in the Briefing Handbook, "
                "even looking back as far as it goes. It may need saving again on the NATS site."
            )
        target_index = 0

    rows = page.locator("tr[data-ri]")
    log.info("Selecting row %d (newest match)", target_index)
    rows.nth(target_index).locator("td").first.click()

    page.wait_for_timeout(1000)
    if not wait_for_use_enabled(page):
        raise RuntimeError("Row selected but the Use button never enabled.")

    log.info("Clicking Use")
    use_button(page).click()
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)


def generate_briefing(page):
    log.info("Generating briefing")

    generate = page.get_by_role("button", name="Generate", exact=False).first
    if not generate.count():
        generate = page.locator(
            "input[value='Generate' i], button:has-text('Generate'), "
            "a:has-text('Generate')"
        ).first
    generate.click()

    page.wait_for_load_state("networkidle", timeout=60000)
    page.wait_for_timeout(5000)


def describe_controls(page):
    """Log every clickable thing across every frame, to find Print PDF."""
    log.info("--- controls visible after Generate ---")
    for fi, frame in enumerate(page.frames):
        log.info("frame %d: %s", fi, (frame.url or "")[:120])
        try:
            controls = frame.locator("button, a, input[type='button'], input[type='submit']")
            n = min(controls.count(), 40)
            for i in range(n):
                el = controls.nth(i)
                try:
                    label = (el.inner_text() or "").strip()
                    if not label:
                        label = el.get_attribute("value") or el.get_attribute("title") or ""
                    label = label.replace("\n", " ").strip()
                    if label:
                        log.info("    [%d] %s", i, label[:80])
                except Exception:
                    continue
        except Exception as exc:
            log.info("    could not enumerate (%s)", exc)
    log.info("--- end controls ---")


def find_print_control(page):
    """Return a locator for the Print PDF control, searching all frames."""
    selectors = [
        "button:has-text('Print PDF')",
        "a:has-text('Print PDF')",
        "input[value*='Print PDF' i]",
        "button:has-text('Print')",
        "a:has-text('Print')",
        "input[value*='Print' i]",
        "[title*='Print' i]",
        "[id*='pdf' i]",
    ]
    for frame in page.frames:
        for sel in selectors:
            try:
                loc = frame.locator(sel).first
                if loc.count() and loc.is_visible():
                    log.info("Found print control %r in frame %s", sel, (frame.url or "top")[:80])
                    return loc
            except Exception:
                continue
    return None


def save_pdf(page, context) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    target = DOWNLOAD_DIR / f"NHV_NOTAM_{stamp}.pdf"

    describe_controls(page)

    control = find_print_control(page)
    if control is None:
        raise RuntimeError(
            "Could not find a Print PDF control in any frame. "
            "See the control list above and the saved HTML."
        )

    downloads = []
    page.on("download", lambda d: downloads.append(d))
    page.on("dialog", lambda d: d.accept())

    pages_before = set(context.pages)

    log.info("Clicking the print control")
    control.click(timeout=15000)

    # Give it time to do whatever it does.
    page.wait_for_timeout(12000)

    if downloads:
        downloads[0].save_as(target)
        log.info("Saved via download event: %s", target)
        return target

    new_pages = [p for p in context.pages if p not in pages_before]
    candidates = new_pages + [page]
    for candidate in candidates:
        try:
            url = candidate.url
        except Exception:
            continue
        if "pdf" in url.lower() or "print" in url.lower():
            log.info("Fetching PDF from %s", url[:120])
            response = context.request.get(url)
            if response.ok:
                target.write_bytes(response.body())
                log.info("Saved via direct fetch: %s", target)
                return target
            log.warning("Fetch returned HTTP %s", response.status)

    log.info("Pages now open: %s", [p.url[:80] for p in context.pages])
    raise RuntimeError(
        "Clicked the print control but no PDF appeared. See the page list above."
    )


def fetch_briefing() -> Path:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not DEBUG, slow_mo=400 if DEBUG else 0)
        context = browser.new_context(accept_downloads=True)
        context.set_default_timeout(TIMEOUT)
        page = context.new_page()

        try:
            log_in(page)
            open_briefing_handbook(page)
            select_saved_briefing(page)
            generate_briefing(page)
            return save_pdf(page, context)
        except Exception:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                page.screenshot(path=HERE / f"error_{stamp}.png", full_page=True)
                (HERE / f"page_{stamp}.html").write_text(page.content(), encoding="utf-8")
                log.error("Saved error_%s.png and page_%s.html", stamp, stamp)
            except Exception:
                pass
            if DEBUG:
                page.pause()
            raise
        finally:
            context.close()
            browser.close()


def send_email(pdf_path: Path):
    if NO_EMAIL:
        log.info("NO_EMAIL set - skipping the email step")
        return

    stamp = datetime.now().strftime("%d %b %Y")
    msg = EmailMessage()
    msg["Subject"] = f"NHV NOTAM Briefing - {stamp}"
    msg["From"] = MAIL_FROM
    msg["To"] = MAIL_TO
    msg.set_content(f"Automated NATS AIS briefing for {stamp}.\n\nFile: {pdf_path.name}\n")
    msg.add_attachment(
        pdf_path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=pdf_path.name,
    )

    log.info("Sending to %s", MAIL_TO)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)
    log.info("Email sent")


def main():
    missing = [
        name
        for name, value in [
            ("NATS_USER", NATS_USER),
            ("NATS_PASS", NATS_PASS),
            ("SMTP_USER", SMTP_USER if not NO_EMAIL else "x"),
            ("SMTP_PASS", SMTP_PASS if not NO_EMAIL else "x"),
        ]
        if not value
    ]
    if missing:
        log.error("Missing environment variables: %s", ", ".join(missing))
        sys.exit(1)

    try:
        send_email(fetch_briefing())
        log.info("Done")
    except Exception as exc:
        log.exception("Run failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
