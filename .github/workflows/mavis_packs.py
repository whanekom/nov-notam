#!/usr/bin/env python3
"""
mavis_packs.py

Logs into MAVIS (Met Office aviation service) via Azure AD B2C, visits each
Aberdeen briefing pack page, renders it to PDF and emails the set.

The site's "Print to PDF" button opens the browser's native print dialog, which
automation can't touch, so we render the page to PDF directly instead. The
output is the same content, laid out for A4.

Credentials come from environment variables. Nothing sensitive lives in here.

Usage:
    python mavis_packs.py
    NO_EMAIL=1 python mavis_packs.py     # render only, don't send
    DEBUG=1 python mavis_packs.py        # visible browser (PDF render disabled)
"""

import logging
import os
import smtplib
import sys
import zipfile
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

BASE = "https://www.mavis.metoffice.gov.uk"
HOME = f"{BASE}/"

PACKS = [
    ("Aberdeen Ekofisk", "aberdeen-ekofisk"),
    ("Aberdeen Northwest", "aberdeen-northwest"),
    ("Aberdeen to East Shetland Basin", "aberdeen-to-east-shetland-basin"),
    ("Aberdeen to Piper", "aberdeen-to-piper"),
    ("East Coast Route", "east-coast-route"),
    ("Stornoway Offshore", "stornoway-offshore"),
]
PACK_URL = BASE + "/bases/aberdeen/briefing-packs/{slug}"

OUT_DIR = Path(os.getenv("OUT_DIR", HERE / "packs"))

MAVIS_USER = os.getenv("MAVIS_USER")
MAVIS_PASS = os.getenv("MAVIS_PASS")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER or "")
MAIL_TO = os.getenv("MAIL_TO", "whanekom@me.com")

# Gmail rejects messages over 25 MB; zip the set if we get near that.
ZIP_THRESHOLD_MB = float(os.getenv("ZIP_THRESHOLD_MB", "18"))

DEBUG = os.getenv("DEBUG") == "1"
NO_EMAIL = os.getenv("NO_EMAIL") == "1"
TIMEOUT = int(os.getenv("TIMEOUT_MS", "45000"))

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(HERE / "mavis.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("mavis")


def log_in(page):
    """Azure AD B2C: username, Next, password, Sign in."""
    log.info("Opening MAVIS")
    page.goto(HOME, wait_until="domcontentloaded", timeout=TIMEOUT)
    page.wait_for_timeout(3000)

    # If we're not already bounced to the login host, look for a sign-in link.
    if "login.auth.metoffice.cloud" not in page.url:
        signin = page.locator(
            "a:has-text('Sign in'), button:has-text('Sign in'), "
            "a:has-text('Log in'), button:has-text('Log in')"
        ).first
        if signin.count():
            log.info("Clicking sign in")
            signin.click()
            page.wait_for_timeout(3000)

    if "login.auth.metoffice.cloud" not in page.url:
        log.info("Already signed in (no login page reached)")
        return

    log.info("On the B2C login page")

    user_field = page.locator(
        "#signInName, #email, input[name='signInName'], "
        "input[type='email'], input[name='email']"
    ).first
    user_field.wait_for(state="visible", timeout=TIMEOUT)
    user_field.fill(MAVIS_USER)

    # B2C sometimes shows username and password together, sometimes in two steps.
    pass_field = page.locator("#password, input[type='password']").first
    if not (pass_field.count() and pass_field.is_visible()):
        log.info("Two-step login - clicking Next")
        nxt = page.locator(
            "#continue, #next, button:has-text('Next'), "
            "button:has-text('Continue'), input[value='Next' i]"
        ).first
        nxt.click()
        page.wait_for_timeout(2500)
        pass_field = page.locator("#password, input[type='password']").first

    pass_field.wait_for(state="visible", timeout=TIMEOUT)
    pass_field.fill(MAVIS_PASS)

    log.info("Submitting password")
    submit = page.locator(
        "#next, #continue, button[type='submit'], "
        "button:has-text('Sign in'), button:has-text('Log in'), "
        "input[type='submit']"
    ).first
    submit.click()

    try:
        page.wait_for_url(f"{BASE}/**", timeout=TIMEOUT)
    except PlaywrightTimeout:
        log.warning("Didn't land back on MAVIS; currently at %s", page.url[:120])

    page.wait_for_timeout(4000)

    if "login.auth.metoffice.cloud" in page.url:
        raise RuntimeError(
            "Still on the login page after submitting. If the account uses "
            "multi-factor authentication, unattended login isn't possible."
        )

    log.info("Logged in")


def wait_for_images(page, timeout_ms=40000):
    """The packs are mostly chart images; don't render until they've loaded."""
    try:
        page.wait_for_function(
            """() => {
                const imgs = Array.from(document.images);
                if (imgs.length === 0) return false;
                return imgs.every(i => i.complete && i.naturalWidth > 0);
            }""",
            timeout=timeout_ms,
        )
        log.info("  all %d image(s) loaded", page.locator("img").count())
    except PlaywrightTimeout:
        total = page.locator("img").count()
        log.warning("  timed out waiting for images (%d on page) - rendering anyway", total)


def render_pack(page, name, slug) -> Path:
    url = PACK_URL.format(slug=slug)
    log.info("Fetching %s", name)
    page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)

    # Confirm we got the pack and not a redirect back to login or a 404.
    if "login.auth" in page.url:
        raise RuntimeError(f"Redirected to login while fetching {name}")

    page.wait_for_timeout(3000)
    wait_for_images(page)
    page.wait_for_timeout(1500)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    out = OUT_DIR / f"{slug}_{stamp}.pdf"

    page.pdf(
        path=str(out),
        format="A4",
        print_background=True,
        margin={"top": "10mm", "bottom": "10mm", "left": "8mm", "right": "8mm"},
    )
    size_kb = out.stat().st_size / 1024
    log.info("  saved %s (%.0f KB)", out.name, size_kb)

    if size_kb < 20:
        log.warning("  %s looks suspiciously small - may be blank", out.name)

    return out


def fetch_all() -> list:
    if DEBUG:
        log.warning("DEBUG mode uses a visible browser; page.pdf() needs headless. "
                    "Run without DEBUG to produce PDFs.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not DEBUG, slow_mo=300 if DEBUG else 0)
        context = browser.new_context(
            viewport={"width": 1400, "height": 1000},
            accept_downloads=True,
        )
        context.set_default_timeout(TIMEOUT)
        page = context.new_page()

        paths = []
        try:
            log_in(page)

            for name, slug in PACKS:
                try:
                    paths.append(render_pack(page, name, slug))
                except Exception as exc:
                    # One bad pack shouldn't lose the other five.
                    log.error("Failed on %s: %s", name, exc)

            if not paths:
                raise RuntimeError("No packs were rendered.")

            return paths
        except Exception:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                page.screenshot(path=HERE / f"error_{stamp}.png", full_page=True)
                (HERE / f"page_{stamp}.html").write_text(page.content(), encoding="utf-8")
                log.error("Saved error_%s.png and page_%s.html", stamp, stamp)
            except Exception:
                pass
            raise
        finally:
            context.close()
            browser.close()


def send_email(paths: list):
    if NO_EMAIL:
        log.info("NO_EMAIL set - skipping the email step")
        return

    total_mb = sum(p.stat().st_size for p in paths) / (1024 * 1024)
    log.info("%d pack(s), %.1f MB total", len(paths), total_mb)

    stamp = datetime.now().strftime("%d %b %Y")
    msg = EmailMessage()
    msg["Subject"] = f"Aberdeen weather briefing packs - {stamp}"
    msg["From"] = MAIL_FROM
    msg["To"] = MAIL_TO

    body = [f"MAVIS briefing packs for {stamp}.", ""]
    body += [f"  - {p.name}" for p in paths]
    if len(paths) < len(PACKS):
        body += ["", f"NOTE: only {len(paths)} of {len(PACKS)} packs were retrieved."]
    msg.set_content("\n".join(body) + "\n")

    if total_mb > ZIP_THRESHOLD_MB:
        log.info("Over %.0f MB - zipping instead of attaching separately", ZIP_THRESHOLD_MB)
        zip_path = OUT_DIR / f"aberdeen_packs_{datetime.now():%Y-%m-%d}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in paths:
                zf.write(p, p.name)
        msg.add_attachment(
            zip_path.read_bytes(),
            maintype="application",
            subtype="zip",
            filename=zip_path.name,
        )
    else:
        for p in paths:
            msg.add_attachment(
                p.read_bytes(),
                maintype="application",
                subtype="pdf",
                filename=p.name,
            )

    log.info("Sending to %s", MAIL_TO)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=120) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)
    log.info("Email sent")


def main():
    missing = [
        name
        for name, value in [
            ("MAVIS_USER", MAVIS_USER),
            ("MAVIS_PASS", MAVIS_PASS),
            ("SMTP_USER", SMTP_USER if not NO_EMAIL else "x"),
            ("SMTP_PASS", SMTP_PASS if not NO_EMAIL else "x"),
        ]
        if not value
    ]
    if missing:
        log.error("Missing environment variables: %s", ", ".join(missing))
        sys.exit(1)

    try:
        send_email(fetch_all())
        log.info("Done")
    except Exception as exc:
        log.exception("Run failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
