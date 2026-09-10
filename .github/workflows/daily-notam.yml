#!/usr/bin/env python3
"""
nhv_notam.py

Logs into the NATS AIS UK site, regenerates the saved "NHV NOTAM" briefing,
downloads the resulting PDF and emails it.

Credentials are read from environment variables (or a .env file sitting next to
this script). Nothing sensitive is stored in this file.

Usage:
    python nhv_notam.py              # normal headless run
    DEBUG=1 python nhv_notam.py      # visible browser, slowed down, pauses on error
    NO_EMAIL=1 python nhv_notam.py   # download only, skip the email step
"""

import logging
import os
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent

# Optional: load a .env file sitting beside the script.
try:
    from dotenv import load_dotenv

    load_dotenv(HERE / ".env")
except ImportError:
    pass

BASE_URL = "https://nats-uk.ead-it.com/cms-nats/opencms/en/home/"
BRIEFING_NAME = "NHV NOTAM"          # the saved briefing to pick from the list
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", HERE / "downloads"))

NATS_USER = os.getenv("NATS_USER")
NATS_PASS = os.getenv("NATS_PASS")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.mail.me.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")   # your full iCloud address
SMTP_PASS = os.getenv("SMTP_PASS")   # app-specific password, NOT your Apple ID password
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER or "")
MAIL_TO = os.getenv("MAIL_TO", "whanekom@me.com")

DEBUG = os.getenv("DEBUG") == "1"
NO_EMAIL = os.getenv("NO_EMAIL") == "1"
TIMEOUT = int(os.getenv("TIMEOUT_MS", "60000"))

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(HERE / "nhv_notam.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("nhv")


# --------------------------------------------------------------------------
# Browser steps
# --------------------------------------------------------------------------


def log_in(page):
    """Fill the login box in the top right of the home page."""
    log.info("Opening %s", BASE_URL)
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=TIMEOUT)

    # The login fields live in a small form top-right. Try a few likely
    # selectors before giving up, since the markup may not use obvious labels.
    user_field = page.locator(
        "input[name*='user' i], input[id*='user' i], "
        "input[placeholder*='user' i], input[type='text']"
    ).first
    pass_field = page.locator("input[type='password']").first

    user_field.wait_for(state="visible", timeout=TIMEOUT)
    user_field.fill(NATS_USER)
    pass_field.fill(NATS_PASS)

    log.info("Submitting credentials")
    # Either a submit button or just pressing Enter in the password box.
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
    """Hover 'Pre-flight Briefing' and click 'Briefing Handbook' at the bottom."""
    log.info("Opening Pre-flight Briefing > Briefing Handbook")

    menu = page.get_by_text("Pre-flight Briefing", exact=False).first
    menu.wait_for(state="visible", timeout=TIMEOUT)
    menu.hover()

    handbook = page.get_by_text("Briefing Handbook", exact=False).first
    handbook.wait_for(state="visible", timeout=15000)
    handbook.click()

    page.wait_for_load_state("networkidle", timeout=TIMEOUT)


def select_saved_briefing(page):
    """Pick the newest saved briefing (top of the list) and click Use."""
    log.info("Selecting saved briefing: %s", BRIEFING_NAME)

    row = page.get_by_text(BRIEFING_NAME, exact=False).first
    try:
        row.wait_for(state="visible", timeout=TIMEOUT)
        row.click()
    except PlaywrightTimeout:
        # Fall back to whatever sits at the top of the list.
        log.warning("Could not find '%s' by name - taking the top row instead",
                    BRIEFING_NAME)
        page.locator("table tr, ul li, .list-item").nth(1).click()

    use_button = page.get_by_role("button", name="Use", exact=False).first
    if not use_button.count():
        use_button = page.locator(
            "input[value='Use' i], a:has-text('Use'), button:has-text('Use')"
        ).first
    use_button.click()
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)


def generate_briefing(page):
    """Click Generate and wait for the briefing to render."""
    log.info("Generating briefing")

    generate = page.get_by_role("button", name="Generate", exact=False).first
    if not generate.count():
        generate = page.locator(
            "input[value='Generate' i], button:has-text('Generate'), "
            "a:has-text('Generate')"
        ).first
    generate.click()

    # The server pulls the data and renders the document - give it room.
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)
    page.wait_for_timeout(3000)


def save_pdf(page, context) -> Path:
    """Click 'Print PDF' and capture whatever comes back."""
    log.info("Requesting the PDF")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y-%m-%d")
    target = DOWNLOAD_DIR / f"NHV_NOTAM_{stamp}.pdf"

    print_button = page.get_by_role("button", name="Print PDF", exact=False).first
    if not print_button.count():
        print_button = page.locator(
            "input[value*='Print PDF' i], button:has-text('Print PDF'), "
            "a:has-text('Print PDF'), *:has-text('Print PDF')"
        ).first

    # Case 1: it fires a normal download event.
    try:
        with page.expect_download(timeout=45000) as dl_info:
            print_button.click()
        download = dl_info.value
        download.save_as(target)
        log.info("Saved via download event: %s", target)
        return target
    except PlaywrightTimeout:
        log.warning("No download event - checking for a PDF opened in a new tab")

    # Case 2: the PDF opens in a new tab / same tab instead of downloading.
    pdf_page = None
    for candidate in context.pages:
        if ".pdf" in candidate.url.lower() or "pdf" in candidate.url.lower():
            pdf_page = candidate
            break

    if pdf_page is None:
        raise RuntimeError(
            "Print PDF produced neither a download nor a PDF tab. "
            "Re-run with DEBUG=1 to watch what happens."
        )

    # Fetch the PDF bytes reusing the logged-in session cookies.
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
            pdf_path = save_pdf(page, context)
            return pdf_path
        except Exception:
            shot = HERE / f"error_{datetime.now():%Y%m%d_%H%M%S}.png"
            try:
                page.screenshot(path=shot, full_page=True)
                log.error("Screenshot written to %s", shot)
            except Exception:
                pass
            if DEBUG:
                log.error("Pausing so you can inspect the page. Close the "
                          "inspector window to continue.")
                page.pause()
            raise
        finally:
            context.close()
            browser.close()


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------


def send_email(pdf_path: Path):
    if NO_EMAIL:
        log.info("NO_EMAIL set - skipping the email step")
        return

    stamp = datetime.now().strftime("%d %b %Y")
    msg = EmailMessage()
    msg["Subject"] = f"NHV NOTAM Briefing - {stamp}"
    msg["From"] = MAIL_FROM
    msg["To"] = MAIL_TO
    msg.set_content(
        f"Automated NATS AIS briefing for {stamp}.\n\n"
        f"File: {pdf_path.name}\n"
    )
    msg.add_attachment(
        pdf_path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=pdf_path.name,
    )

    log.info("Sending to %s via %s:%s", MAIL_TO, SMTP_HOST, SMTP_PORT)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)
    log.info("Email sent")


# --------------------------------------------------------------------------


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
        pdf_path = fetch_briefing()
        send_email(pdf_path)
        log.info("Done")
    except Exception as exc:
        log.exception("Run failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
