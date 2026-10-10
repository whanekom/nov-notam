#!/usr/bin/env python3
"""NHV flying programme -> WhatsApp.

Looks in a Gmail mailbox for the evening "FLYING PROGRAMME dd/mm/yy" email
(forwarded there from Outlook), reads the programme - whether Ops pasted it
as a table or as a picture of the spreadsheet - picks out the lines for one
crew code and sends them to WhatsApp.

Usage
  python alert.py                 process any new programme emails
  python alert.py --peek          only report whether there is a new email
  python alert.py --check-missing warn if no programme email arrived today
  python alert.py --eml FILE      run against a saved .eml file (prints only)
  add --dry-run to print the WhatsApp text instead of sending it

Settings come from environment variables (GitHub Actions secrets):
  GMAIL_USER, GMAIL_APP_PASSWORD   mailbox the programme is forwarded to
  WHATSAPP_PHONE, CALLMEBOT_APIKEY where to send the message
  CREW_CODE                        defaults to WIHAN
"""

from __future__ import annotations

import argparse
import datetime as dt
import email
import email.policy
import imaplib
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")
CREW_CODE = os.environ.get("CREW_CODE", "WIHAN").strip().upper()
SUBJECT_WORDS = "flying programme"
DONE_LABEL = "programme-alert-sent"
LOOKBACK_DAYS = 3
# Your own lines show who you are crewed with (their code). Nobody else on the
# programme is listed. Set SHOW_CREW=false to leave the crewmate out as well.
SHOW_CREW = os.environ.get("SHOW_CREW", "").strip().lower() not in ("0", "false", "no")
FOOTER = "Details subject to change - the Ops email is the authority."


# --------------------------------------------------------------------------
# WhatsApp
# --------------------------------------------------------------------------

def send_whatsapp(text: str, dry_run: bool = False) -> None:
    """Send a WhatsApp message to yourself through CallMeBot."""
    if dry_run:
        print("----- WhatsApp message (not sent) -----")
        print(text)
        print("---------------------------------------")
        return
    query = urllib.parse.urlencode({
        "phone": os.environ["WHATSAPP_PHONE"],
        "text": text,
        "apikey": os.environ["CALLMEBOT_APIKEY"],
    })
    url = "https://api.callmebot.com/whatsapp.php?" + query
    last_error = "unknown error"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                status, body = resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read().decode("utf-8", "replace")
        except OSError as exc:
            status, body = None, str(exc)
        # CallMeBot echoes the message first and puts its verdict at the end,
        # so the end of the reply is the part worth reading and logging.
        verdict = re.sub(r"<[^>]+>", " ", body)
        verdict = re.sub(r"\s+", " ", verdict).strip()[-300:]
        if status is not None and "queued" in body.lower():
            print(f"WhatsApp message accepted by CallMeBot (HTTP {status}).")
            return
        last_error = f"HTTP {status}: ...{verdict}"
        print(f"CallMeBot attempt {attempt + 1} not accepted: {last_error}")
        if re.search(r"apikey|api key|not allowed|not activated|paused", body, re.I):
            break  # a set-up problem; trying again will not help
        time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"WhatsApp send failed: {last_error}")


# --------------------------------------------------------------------------
# Message text
# --------------------------------------------------------------------------

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def parse_programme_date(text: str) -> dt.date | None:
    """Find the programme date in a subject line or cell.

    Handles both styles Ops use: "FLYING PROGRAMME 10/10/26" and
    "Flying Programme - Saturday 26th September" (no year: nearest one is used).
    """
    text = text or ""
    m = re.search(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})\b", text)
    if m:
        year = int(m.group(3)) if len(m.group(3)) == 4 else 2000 + int(m.group(3))
        try:
            return dt.date(year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = re.search(
        r"(\d{1,2})\s*(?:st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r")[a-z]*\.?(?:\s+(\d{4}))?",
        text, re.I,
    )
    if not m:
        return None
    day, month = int(m.group(1)), _MONTHS.index(m.group(2).lower()) + 1
    today = dt.datetime.now(UK).date()
    years = [int(m.group(3))] if m.group(3) else [today.year - 1, today.year, today.year + 1]
    candidates = []
    for year in years:
        try:
            candidates.append(dt.date(year, month, day))
        except ValueError:
            pass
    return min(candidates, key=lambda d: abs((d - today).days)) if candidates else None


def nice_date(d: dt.date | None) -> str:
    return d.strftime("%a %d %b").replace(" 0", " ") if d else "(date not found)"


def line_title(row: dict) -> str:
    if row["std"]:
        return f"STD {row['std']} {row['route']}".strip()
    return row["report"] or row["route"] or "Duty"


def format_line(row: dict, mine: bool = True) -> str:
    """One programme line as WhatsApp text: the duty itself, nobody else's code."""
    import programme

    out = [f"*{line_title(row)}*"]
    if row["std"] and row["report"]:
        mine = programme.own_report(row, CREW_CODE)
        if mine != row["report"]:  # the cell lists one report time per pilot
            out.append(f"Report: {mine} (cell shows {row['report']})")
        else:
            out.append(f"Report: {row['report']}")
    if row["aircraft"]:
        out.append(f"Aircraft: {row['aircraft']}")
    if row["callsign"]:
        out.append(f"Callsign: {row['callsign']}")
    if not mine:  # a line shown only because its crew cell needs checking
        out.append(f"Crew as read: {row['crew']}")
    elif SHOW_CREW:
        mates = programme.crewmates(row, CREW_CODE)
        if mates:
            out.append(f"Crewmate: {', '.join(mates)}")
    if row["remarks"]:
        out.append(f"Remarks: {row['remarks']}")
    if row.get("crew_inherited"):
        out.append("(Crew cell blank on this line - assumed same crew as the line above.)")
    if row.get("crew_doubt"):
        out.append("(The crew cell on this line was hard to read.)")
    if row.get("doubts"):
        out.append("(Hard to read: " + "; ".join(row["doubts"]) + ")")
    return "\n".join(out)


def build_message(table: dict, subject_date: dt.date | None, updated: bool = False) -> str:
    """Turn a parsed programme into the WhatsApp text for CREW_CODE.

    The message answers one question - is CREW_CODE on the programme - and
    gives only that person's own lines. No other crew are listed.
    """
    import programme  # imported here so --peek needs no OCR libraries

    image_date = parse_programme_date(table.get("date") or "")
    prog_date = subject_date or image_date
    head = f"*Flying programme {nice_date(prog_date)}*"
    if updated:
        head += "\n(another programme email for this date - may be a revision)"
    parts = [head]

    if subject_date and image_date and subject_date != image_date:
        parts.append(
            f"WARNING: the email says {subject_date:%d/%m/%y} but the table is dated "
            f"{image_date:%d/%m/%y}. Check the Ops email."
        )

    mine, maybe, mentions = programme.find_duties(table["rows"], CREW_CODE)
    if mine:
        count = "1 line" if len(mine) == 1 else f"{len(mine)} lines"
        parts.append(f"{CREW_CODE} is on the programme ({count}):")
        parts.extend(format_line(r) for r in mine)
    else:
        parts.append(f"{CREW_CODE} is not on the programme.")
    for r in mentions:  # named in the remarks of a line flown by others
        note = r["remarks"] or r["report"]
        parts.append(f"{CREW_CODE} is mentioned on the {line_title(r)} line: {note}")
    if maybe:
        parts.append(
            f"WARNING: a code very close to {CREW_CODE} is on the line(s) below - "
            "a typo or a misread. Check the Ops email."
        )
        parts.extend(format_line(r, mine=False) for r in maybe)
    elif not mine and table.get("doubts"):
        # Picture days only: say so when the answer rests on an unclear read.
        parts.append(
            "WARNING: some crew codes in the picture were hard to read. Check the Ops email."
        )
    if mine or maybe or mentions:
        parts.append(FOOTER)
    return "\n\n".join(parts)


def unreadable_message(subject: str, reason: str) -> str:
    return (
        f"*Flying programme {nice_date(parse_programme_date(subject))}*\n\n"
        f"WARNING: the programme email arrived but the table could not be read ({reason}). "
        "Check the Ops email."
    )


# --------------------------------------------------------------------------
# Reading one email
# --------------------------------------------------------------------------

def images_in(msg: email.message.Message) -> list[bytes]:
    """All pictures in the email (including inside a forwarded copy), largest first."""
    found = []
    for part in msg.walk():
        if part.get_content_maintype() == "image":
            payload = part.get_payload(decode=True)
            if payload:
                found.append(payload)
    return sorted(found, key=len, reverse=True)


def html_bodies(msg: email.message.Message) -> list[str]:
    """The HTML text of the email (and of a forwarded copy inside it)."""
    found = []
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            try:
                found.append(part.get_content())
            except (LookupError, UnicodeError):
                payload = part.get_payload(decode=True) or b""
                found.append(payload.decode("utf-8", "replace"))
    return found


def message_for_email(raw: bytes, updated: bool = False, any_date: bool = False) -> str | None:
    """Build the WhatsApp text for one programme email.

    Ops send the programme either as a real table in the email text or as a
    picture of the spreadsheet; the table is tried first because it needs no
    OCR. Returns None when the email should be ignored (a reply with no
    programme in it, or a programme for a day that has already passed).
    """
    import programme

    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = str(msg.get("subject", ""))
    subject_date = parse_programme_date(subject)
    today = dt.datetime.now(UK).date()
    if subject_date and subject_date < today and not any_date:
        print(f"Skipping '{subject}': programme date has passed.")
        return None

    for html in html_bodies(msg):
        try:
            table = programme.read_programme_html(html)
        except programme.ProgrammeReadError:
            continue
        print("Programme read from the table in the email text.")
        return build_message(table, subject_date, updated)

    reason = "no table or picture in the email"
    for picture in images_in(msg)[:5]:
        try:
            table = programme.read_programme(picture)
        except programme.ProgrammeReadError as exc:
            reason = str(exc)
            continue
        if not table["rows"]:
            reason = "the table has no crew lines"
            continue
        print("Programme read from the picture in the email.")
        return build_message(table, subject_date, updated)

    if re.match(r"\s*re\s*:", subject, re.I):
        print(f"Skipping '{subject}': a reply with no programme in it.")
        return None
    return unreadable_message(subject, reason)


# --------------------------------------------------------------------------
# Gmail
# --------------------------------------------------------------------------

def open_mailbox() -> imaplib.IMAP4_SSL:
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])
    folder = "INBOX"
    _, listing = imap.list()
    for line in listing or []:
        text = line.decode("utf-8", "replace")
        if "\\All" in text:  # Gmail's "All Mail", whatever language it is named in
            m = re.search(r'"([^"]+)"\s*$', text)
            if m:
                folder = m.group(1)
    imap.select(f'"{folder}"')
    return imap


def list_programme_emails(imap: imaplib.IMAP4_SSL) -> list[dict]:
    """Recent programme emails as {uid, subject, received, done}, oldest first."""
    query = f'"subject:({SUBJECT_WORDS}) newer_than:{LOOKBACK_DAYS}d"'
    _, data = imap.uid("SEARCH", "X-GM-RAW", query)
    uids = data[0].split() if data and data[0] else []
    emails = []
    for uid in uids:
        _, fetched = imap.uid(
            "FETCH", uid, "(X-GM-LABELS INTERNALDATE BODY.PEEK[HEADER.FIELDS (SUBJECT)])"
        )
        meta, header = b"", b""
        for item in fetched:
            if isinstance(item, tuple):
                meta += item[0]
                header += item[1]
            elif isinstance(item, bytes):
                meta += item
        subject = str(
            email.message_from_bytes(header, policy=email.policy.default).get("subject", "")
        )
        if not is_programme_subject(subject):
            continue
        stamp = imaplib.Internaldate2tuple(meta)
        received = (
            dt.datetime.fromtimestamp(time.mktime(stamp), UK) if stamp else dt.datetime.now(UK)
        )
        emails.append({
            "uid": uid,
            "subject": subject,
            "received": received,
            "done": DONE_LABEL.encode() in meta,
        })
    return sorted(emails, key=lambda e: e["received"])


def is_programme_subject(subject: str) -> bool:
    """True for "FLYING PROGRAMME ..." (with or without Fwd:/RE: in front), and for
    variations such as "UPDATED FLYING PROGRAMME 10/10/26" that carry a date."""
    bare = re.sub(r"^\s*((fwd?|re)\s*:\s*)+", "", subject, flags=re.I).lower()
    if bare.startswith(SUBJECT_WORDS):
        return True
    return SUBJECT_WORDS in bare and parse_programme_date(subject) is not None


def mark_done(imap: imaplib.IMAP4_SSL, uid: bytes) -> None:
    imap.uid("STORE", uid, "+X-GM-LABELS", f"({DONE_LABEL})")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_peek() -> int:
    imap = open_mailbox()
    new = [e for e in list_programme_emails(imap) if not e["done"]]
    imap.logout()
    print(f"{len(new)} new programme email(s).")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as fh:
            fh.write(f"new={'true' if new else 'false'}\n")
    return 0


def cmd_process(dry_run: bool) -> int:
    imap = open_mailbox()
    emails = list_programme_emails(imap)
    sent_dates = {parse_programme_date(e["subject"]) for e in emails if e["done"]}
    failures = 0
    for item in (e for e in emails if not e["done"]):
        print(f"Processing '{item['subject']}' received {item['received']:%d/%m %H:%M}")
        _, fetched = imap.uid("FETCH", item["uid"], "(BODY.PEEK[])")
        raw = next(part[1] for part in fetched if isinstance(part, tuple))
        date = parse_programme_date(item["subject"])
        try:
            text = message_for_email(raw, updated=date is not None and date in sent_dates)
            if text:
                send_whatsapp(text, dry_run)
            if not dry_run:
                mark_done(imap, item["uid"])
            sent_dates.add(date)
        except Exception as exc:  # leave it unmarked so the next run tries again
            failures += 1
            print(f"FAILED on '{item['subject']}': {exc}", file=sys.stderr)
    imap.logout()
    return 1 if failures else 0


def cmd_check_missing(dry_run: bool) -> int:
    imap = open_mailbox()
    emails = list_programme_emails(imap)
    imap.logout()
    now = dt.datetime.now(UK)
    if any(e["received"].date() == now.date() for e in emails):
        print("A programme email arrived today.")
        return 0
    send_whatsapp(
        f"*Flying programme*\n\nWARNING: no programme email has reached the alert mailbox "
        f"today (checked {now:%H:%M} UK). Check Outlook - the forwarding may have stopped.",
        dry_run,
    )
    return 0


def cmd_eml(path: str) -> int:
    with open(path, "rb") as fh:
        text = message_for_email(fh.read(), any_date=True)
    if text:
        send_whatsapp(text, dry_run=True)
    else:
        print("(no message would be sent for this email)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--peek", action="store_true")
    parser.add_argument("--check-missing", action="store_true")
    parser.add_argument("--eml", metavar="FILE")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    dry_run = args.dry_run or os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
    if args.eml:
        return cmd_eml(args.eml)
    if args.peek:
        return cmd_peek()
    if args.check_missing:
        return cmd_check_missing(dry_run)
    return cmd_process(dry_run)


if __name__ == "__main__":
    sys.exit(main())
