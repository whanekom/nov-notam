#!/usr/bin/env python3
"""
nhv_notam.py  (v2)

Logs into the NATS AIS UK site, regenerates the saved briefing, downloads the
PDF and emails it.

Changes from v1:
  - Logs every row it can see in the briefing list, so we can find out what the
    saved briefing is actually called.
  - Selects the row the way PrimeFaces expects, then waits for the "Use" button
    to become enabled rather than clicking a disabled button 120 times.
  - Targets the Use button by its real element id.
  - Saves the page HTML on failure alongside the screenshot.
  - Shorter default timeout so failures surface quickly.
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

# Matched case-insensitively against each row's text. "NHV" alone is a safer
# default than the full name, since we aren't sure of the exact spelling yet.
BRIEFING_MATCH = os.getenv("BRIEFING_MATCH", "NHV")

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


def describe_rows(page):
    """Log what's actually in the saved-briefing list."""
    rows = page.locator("tr[data-ri]")
    count = rows.count()
    log.info("Found %d row(s) in the briefing list", count)

    for i in range(min(count, 25)):
        try:
            text = rows.nth(i).inner_text().replace("\n", " | ").strip()
            log.info("  row %d: %s", i, text[:200])
        except Exception as exc:
            log.info("  row %d: could not read (%s)", i, exc)

    if count == 0:
        # Fall back to dumping any table rows at all, to see what we've got.
        plain = page.locator("table tr")
        log.info("No PrimeFaces rows; %d plain <tr> present", plain.count())
        for i in range(min(plain.count(), 15)):
            try:
                text = plain.nth(i).inner_text().replace("\n", " | ").strip()
                if text:
                    log.info("  tr %d: %s", i, text[:200])
            except Exception:
                pass

    return rows, count


def use_button(page):
    return page.locator(f'[id="{USE_BUTTON_ID}"]')


def wait_for_use_enabled(page, timeout_ms=10000) -> bool:
    """PrimeFaces enables the button by removing the disabled attribute."""
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


def select_saved_briefing(page):
    log.info("Looking for a briefing matching %r", BRIEFING_MATCH)

    rows, count = describe_rows(page)
    if count == 0:
        raise RuntimeError(
            "No rows found in the briefing list. Check the saved HTML artefact."
        )

    pattern = re.compile(re.escape(BRIEFING_MATCH), re.IGNORECASE)

    target_index = None
    for i in range(count):
        try:
            if pattern.search(rows.nth(i).inner_text()):
                target_index = i
                break
        except Exception:
            continue

    if target_index is None:
        log.warning(
            "Nothing matched %r - falling back to the first row (newest)",
            BRIEFING_MATCH,
        )
        target_index = 0

    row = rows.nth(target_index)
    log.info("Selecting row %d", target_index)

    # Try progressively more specific ways of selecting the row, checking after
    # each whether the Use button woke up.
    attempts = [
        ("radio/checkbox in row", lambda: row.locator(
            "div.ui-radiobutton-box, div.ui-chkbox-box, "
            "input[type='radio'], input[type='checkbox']").first.click()),
        ("first cell", lambda: row.locator("td").first.click()),
        ("row itself", lambda: row.click()),
    ]

    for label, action in attempts:
        try:
            action()
            log.info("Clicked %s", label)
        except Exception as exc:
            log.info("Could not click %s (%s)", label, exc)
            continue

        page.wait_for_timeout(1000)
        if wait_for_use_enabled(page, 8000):
            log.info("Use button is now enabled")
            use_button(page).click()
            page.wait_for_load_state("networkidle", timeout=TIMEOUT)
            return

    raise RuntimeError(
        "Selected a row but the Use button never enabled. "
        "Check the saved HTML artefact to see how selection is wired up."
    )


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
    page.wait_for_timeout(4000)


def save_pdf(page, context) -> Path:
    log.info("Requesting the PDF")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y-%m-%d")
    target = DOWNLOAD_DIR / f"NHV_NOTAM_{stamp}.pdf"

    print_button = page.get_by_role("button", name="Print PDF", exact=False).first
    if not print_button.count():
        print_button = page.locator(
            "input[value*='Print PDF' i], button:has-text('Print PDF'), "
            "a:has-text('Print PDF')"
        ).first

    try:
        with page.expect_download(timeout=45000) as dl_info:
            print_button.click()
        dl_info.value.save_as(target)
        log.info("Saved via download event: %s", target)
        return target
    except PlaywrightTimeout:
        log.warning("No download event - checking for a PDF tab")

    pdf_page = next(
        (p for p in context.pages if "pdf" in p.url.lower()), None
    )
    if pdf_page is None:
        raise RuntimeError("Print PDF produced neither a download nor a PDF tab.")

    response = context.request.get(pdf_page.url)
    if not response.ok:
        raise RuntimeError(f"Failed to fetch PDF: HTTP {response.status}")
    target.write_bytes(response.body())
    log.info("Saved via direct fetch: %s", target)
    return target


def fetch_briefing() -> Path:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not DEBUG,
            slow_mo=400 if DEBUG else 0,
        )
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
                (HERE / f"page_{stamp}.html").write_text(
                    page.content(), encoding="utf-8"
                )
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
