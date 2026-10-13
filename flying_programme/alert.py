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
  python alert.py --test          send a short test message
  add --dry-run to print the WhatsApp text instead of sending it

Settings come from environment variables (GitHub Actions secrets):
  GMAIL_USER, GMAIL_APP_PASSWORD   mailbox the programme is forwarded to
  PUSHOVER_APP_TOKEN, PUSHOVER_USER_KEY  send as a Pushover notification, or
  WHATSAPP_PHONE, CALLMEBOT_APIKEY       send as a WhatsApp through CallMeBot
  CREW_CODE                        whose lines to send (defaults to WIHAN)
  EXTRA_PILOTS                     other pilots who get their own alert, written
                                   CODE=their Pushover user key, one per line
  KEEP_EMAILS                      true = do not move handled emails to Trash
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
# Once an email has been dealt with it is moved to Gmail's Trash (Gmail empties
# that after 30 days). Set KEEP_EMAILS=true to leave handled emails in place.
KEEP_EMAILS = os.environ.get("KEEP_EMAILS", "").strip().lower() in ("1", "true", "yes")
FOOTER = "Details subject to change - the Ops email is the authority."
# Gmail label that records who has already had the alert for an email when a
# send to somebody else failed, so the next run only sends what is missing.
PART_LABEL = "programme-alert-part-"
# Mistakes found in the settings. The run carries on for everyone it can and
# ends as failed, so the mistake shows on the run page.
SETUP_PROBLEMS: list[str] = []


class PermanentSendError(RuntimeError):
    """The service refused the message for a reason that trying again cannot
    fix (a wrong key, for instance)."""


def _problem(text: str) -> None:
    if text not in SETUP_PROBLEMS:
        SETUP_PROBLEMS.append(text)
        print(f"::error::{text}" if os.environ.get("GITHUB_ACTIONS") else text, file=sys.stderr)


def extra_pilots() -> list[tuple[str, str]]:
    """Other pilots who get their own alert, as (crew code, Pushover user key).

    They come from the EXTRA_PILOTS setting, written CODE=key with one pilot
    per line (commas or semicolons between pilots also work). Their codes and
    keys exist only in that setting; neither is ever written to the log.
    """
    found: list[tuple[str, str]] = []
    entries = [e.strip() for e in re.split(r"[\n,;]+", os.environ.get("EXTRA_PILOTS", ""))]
    for number, entry in enumerate([e for e in entries if e], start=1):
        code, _, key = re.sub(r"\s*[=:]\s*", "=", entry, count=1).partition("=")
        code, key = code.strip().upper(), key.strip()
        if not re.fullmatch(r"[A-Z0-9]{3,8}", code) or not key:
            _problem(
                f"EXTRA_PILOTS entry {number} is not written as CODE=key "
                "(a crew code, an equals sign, then that pilot's Pushover user key)."
            )
        elif code == CREW_CODE or any(code == c for c, _ in found):
            _problem(f"EXTRA_PILOTS entry {number} repeats a crew code that is already set up.")
        else:
            found.append((code, key))
    return found


def recipients() -> list[dict]:
    """Everyone who gets an alert: the main pilot first, then any extra pilots.

    "who" is how a recipient is named in the log - never an extra pilot's code.
    """
    people = [{"code": CREW_CODE, "user_key": None, "who": "the main pilot", "main": True}]
    for number, (code, key) in enumerate(extra_pilots(), start=1):
        people.append(
            {"code": code, "user_key": key, "who": f"extra pilot {number}", "main": False}
        )
    return people



# --------------------------------------------------------------------------
# WhatsApp
# --------------------------------------------------------------------------

def _verdict_only(body: str, message: str) -> str:
    """CallMeBot's result with the echoed message and phone number removed.

    Its reply repeats the whole message before saying what happened. Run logs
    may be readable by other people, so only the result is ever logged.
    """
    plain = re.sub(r"<[^>]+>", " ", body)
    marker = plain.rfind("Text to send:")
    if marker != -1:
        tail = plain[marker:]
        last_line = message.strip().splitlines()[-1].strip()
        end = tail.rfind(last_line)
        plain = tail[end + len(last_line):] if end != -1 else ""
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain or len(plain) > 200:
        return "no readable result in the reply"
    return plain


def send_whatsapp(text: str) -> None:
    """Send a WhatsApp message to yourself through CallMeBot."""
    query = urllib.parse.urlencode({
        "phone": _setting("WHATSAPP_PHONE"),
        "text": text,
        "apikey": _setting("CALLMEBOT_APIKEY"),
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
        verdict = _verdict_only(body, text)
        if status is not None and "queued" in body.lower():
            print(f"WhatsApp message accepted by CallMeBot (HTTP {status}).")
            return
        last_error = f"HTTP {status}: {verdict}"
        print(f"CallMeBot attempt {attempt + 1} not accepted: {last_error}")
        if re.search(r"apikey|api key|not allowed|not activated|paused", body, re.I):
            break  # a set-up problem; trying again will not help
        time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"WhatsApp send failed: {last_error}")


PUSHOVER_LIMIT = 1000  # Pushover allows 1024 characters per message


def pushover_parts(text: str) -> tuple[str, list[str]]:
    """Turn the alert text into a Pushover title and one or more message bodies.

    The first line becomes the notification title. *bold* becomes <b>bold</b>.
    Anything longer than Pushover's limit is split between paragraphs.
    """
    paragraphs = text.strip().split("\n\n")
    first_lines = paragraphs[0].split("\n")
    title = first_lines[0].strip().strip("*").strip() or "Flying programme"
    rest = ["\n".join(first_lines[1:])] if len(first_lines) > 1 else []
    body_paragraphs = [p for p in rest + paragraphs[1:] if p.strip()] or [title]

    def to_html(paragraph: str) -> str:
        safe = paragraph.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", safe)

    bodies, current = [], ""
    for paragraph in map(to_html, body_paragraphs):
        while len(paragraph) > PUSHOVER_LIMIT:  # one huge paragraph: cut it
            bodies.append(paragraph[:PUSHOVER_LIMIT])
            paragraph = paragraph[PUSHOVER_LIMIT:]
        joined = f"{current}\n\n{paragraph}" if current else paragraph
        if len(joined) > PUSHOVER_LIMIT:
            bodies.append(current)
            current = paragraph
        else:
            current = joined
    if current:
        bodies.append(current)
    return title[:250], bodies


def _setting(name: str) -> str:
    """A secret's value with stray spaces and line breaks removed (pasting on a
    phone often adds one, and the services then reject the value)."""
    return os.environ.get(name, "").strip()


def _check_pushover_value(name: str, value: str) -> None:
    """Pushover keys and tokens are exactly 30 letters and digits. Say what is
    wrong with the shape of a value without ever showing the value itself."""
    if re.fullmatch(r"[A-Za-z0-9]{30}", value):
        return
    odd = len(re.findall(r"[^A-Za-z0-9]", value))
    raise PermanentSendError(
        f"{name} does not look like a Pushover value: it has {len(value)} characters "
        f"({odd} of them not a letter or digit); it should be exactly 30 letters and digits."
    )


def send_pushover(text: str, user_key: str | None = None, who: str = "") -> None:
    """Send the alert as a Pushover notification (official Pushover API).

    Goes to the main pilot (PUSHOVER_USER_KEY) unless another pilot's user key
    is given; "who" then names that pilot in any error.
    """
    token = _setting("PUSHOVER_APP_TOKEN")
    user = (user_key or "").strip() or _setting("PUSHOVER_USER_KEY")
    key_name = f"The key for {who} in EXTRA_PILOTS" if user_key else "PUSHOVER_USER_KEY"
    _check_pushover_value("PUSHOVER_APP_TOKEN", token)
    _check_pushover_value(key_name, user)
    # Pushover user keys start with "u" and application tokens with "a", so
    # the two commonest mix-ups can be named without showing either value.
    if token == user:
        raise PermanentSendError(
            f"PUSHOVER_APP_TOKEN and {key_name} hold the same value. One should be the "
            "application's API token, the other a user key."
        )
    if not user_key and user.startswith("a") and token.startswith("u"):
        raise PermanentSendError(
            "The two Pushover secrets look swapped: PUSHOVER_USER_KEY holds an application "
            "token and PUSHOVER_APP_TOKEN holds a user key."
        )
    if not user.startswith(("u", "g")):
        raise PermanentSendError(
            f"{key_name} is not a user key (those start with 'u'). "
            "It may be the application token instead."
        )
    title, bodies = pushover_parts(text)
    for index, body in enumerate(bodies, start=1):
        part_title = title if len(bodies) == 1 else f"{title} ({index}/{len(bodies)})"
        data = urllib.parse.urlencode({
            "token": token,
            "user": user,
            "title": part_title,
            "message": body,
            "html": "1",
        }).encode()
        last_error = "unknown error"
        for attempt in range(3):
            try:
                request = urllib.request.Request(
                    "https://api.pushover.net/1/messages.json", data=data, method="POST",
                    headers={"User-Agent": "flying-programme-alert/1.0"},
                )
                with urllib.request.urlopen(request, timeout=60) as resp:
                    status, reply = resp.status, resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                status, reply = exc.code, exc.read().decode("utf-8", "replace")
            except OSError as exc:
                status, reply = None, str(exc)
            if status == 200 and '"status":1' in reply.replace(" ", ""):
                print(f"Pushover accepted the notification ({index} of {len(bodies)}).")
                break
            # Pushover's error text names the problem (bad key, quota) and
            # never repeats the message, so it is safe to log.
            errors = re.search(r'"errors"\s*:\s*\[(.*?)\]', reply or "", re.S)
            last_error = f"HTTP {status}: {errors.group(1) if errors else 'no readable reason'}"[:300]
            print(f"Pushover attempt {attempt + 1} not accepted: {last_error}")
            if status is not None and 400 <= status < 500:
                # Retrying cannot help.
                raise PermanentSendError(f"Pushover send failed: {last_error}")
            time.sleep(10 * (attempt + 1))
        else:
            raise RuntimeError(f"Pushover send failed: {last_error}")


def send_message(
    text: str, dry_run: bool = False, user_key: str | None = None, who: str = ""
) -> None:
    """Send the alert by whichever service is set up: Pushover if its two
    settings exist, otherwise WhatsApp through CallMeBot.

    With another pilot's Pushover user key the message goes to that pilot
    instead of the main one (Pushover only).
    """
    if dry_run:
        print(f"----- Message (not sent){' for ' + who if who else ''} -----")
        print(text)
        print("------------------------------")
        return
    try:
        if user_key:
            if not _setting("PUSHOVER_APP_TOKEN"):
                raise PermanentSendError(
                    "Extra pilots are sent to by Pushover, and PUSHOVER_APP_TOKEN is not set."
                )
            send_pushover(text, user_key, who)
        elif _setting("PUSHOVER_APP_TOKEN") and _setting("PUSHOVER_USER_KEY"):
            send_pushover(text)
        elif _setting("WHATSAPP_PHONE") and _setting("CALLMEBOT_APIKEY"):
            send_whatsapp(text)
        else:
            raise RuntimeError("No way to send: neither Pushover nor WhatsApp is set up.")
    except RuntimeError as exc:
        # On GitHub, put the service's own reason on the run's summary page.
        # It never contains the message text, a key or a token.
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error::{exc}" + (f" ({who})" if who else ""))
        raise


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


def format_line(row: dict, own_line: bool = True, code: str | None = None) -> str:
    """One programme line as WhatsApp text: the duty itself, nobody else's code."""
    import programme

    code = code or CREW_CODE
    out = [f"*{line_title(row)}*"]
    if row["std"] and row["report"]:
        own = programme.own_report(row, code) if own_line else row["report"]
        if own != row["report"]:  # the cell lists one report time per pilot
            out.append(f"Report: {own} (cell shows {row['report']})")
        else:
            out.append(f"Report: {row['report']}")
    if row["aircraft"]:
        out.append(f"Aircraft: {row['aircraft']}")
    if row["callsign"]:
        out.append(f"Callsign: {row['callsign']}")
    if not own_line:  # a line shown only because its crew cell needs checking
        out.append(f"Crew as read: {row['crew']}")
    elif SHOW_CREW:
        mates = programme.crewmates(row, code)
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


def build_message(
    table: dict, subject_date: dt.date | None, updated: bool = False, code: str | None = None
) -> str:
    """Turn a parsed programme into the alert text for one crew code.

    The message answers one question - is this pilot on the programme - and
    gives only that person's own lines. No other crew are listed.
    """
    import programme  # imported here so --peek needs no OCR libraries

    code = code or CREW_CODE

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

    mine, maybe, mentions = programme.find_duties(table["rows"], code)
    if mine:
        count = "1 line" if len(mine) == 1 else f"{len(mine)} lines"
        parts.append(f"{code} is on the programme ({count}):")
        parts.extend(format_line(r, code=code) for r in mine)
    else:
        parts.append(f"{code} is not on the programme.")
    for r in mentions:  # named in the remarks of a line flown by others
        note = r["remarks"] or r["report"]
        parts.append(f"{code} is mentioned on the {line_title(r)} line: {note}")
    if maybe:
        parts.append(
            f"WARNING: a code very close to {code} is on the line(s) below - "
            "a typo or a misread. Check the Ops email."
        )
        parts.extend(format_line(r, own_line=False, code=code) for r in maybe)
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


def read_email(raw: bytes, any_date: bool = False) -> dict:
    """Read the programme out of one email, once, whoever it is for.

    Ops send the programme either as a real table in the email text or as a
    picture of the spreadsheet; the table is tried first because it needs no
    OCR. Returns one of
      {"kind": "skip"}        nothing to send (a reply with no programme in
                              it, or a programme for a day that has passed)
      {"kind": "table", "table": ..., "subject_date": ...}
      {"kind": "unreadable", "text": ...}   only a warning to go and look
    """
    import programme

    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = str(msg.get("subject", ""))
    subject_date = parse_programme_date(subject)
    today = dt.datetime.now(UK).date()
    if subject_date and subject_date < today and not any_date:
        print(f"Skipping '{subject}': programme date has passed.")
        return {"kind": "skip"}

    for html in html_bodies(msg):
        try:
            table = programme.read_programme_html(html)
        except programme.ProgrammeReadError:
            continue
        print("Programme read from the table in the email text.")
        return {"kind": "table", "table": table, "subject_date": subject_date}

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
        return {"kind": "table", "table": table, "subject_date": subject_date}

    if re.match(r"\s*re\s*:", subject, re.I):
        print(f"Skipping '{subject}': a reply with no programme in it.")
        return {"kind": "skip"}
    return {"kind": "unreadable", "text": unreadable_message(subject, reason)}


def text_for(
    programme_read: dict, updated: bool = False, code: str | None = None
) -> tuple[str | None, bool]:
    """The alert text for one crew code from what read_email found.

    Returns (text, readable). text is None when nothing should be sent.
    readable is False when the programme could not be read and the text is
    only a warning to go and look at the email.
    """
    if programme_read["kind"] == "skip":
        return None, True
    if programme_read["kind"] == "unreadable":
        return programme_read["text"], False
    return (
        build_message(programme_read["table"], programme_read["subject_date"], updated, code),
        True,
    )


def message_for_email(
    raw: bytes, updated: bool = False, any_date: bool = False, code: str | None = None
) -> tuple[str | None, bool]:
    """Read one programme email and build the alert text for one crew code."""
    return text_for(read_email(raw, any_date), updated, code)


# --------------------------------------------------------------------------
# Gmail
# --------------------------------------------------------------------------

def open_mailbox() -> imaplib.IMAP4_SSL:
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])
    # Gmail's "All Mail" and "Trash"/"Bin" folders, whatever language they are named in.
    imap.all_folder, imap.trash_folder = "INBOX", None
    _, listing = imap.list()
    for line in listing or []:
        text = line.decode("utf-8", "replace")
        m = re.search(r'"([^"]+)"\s*$', text)
        if not m:
            continue
        if "\\All" in text:
            imap.all_folder = m.group(1)
        elif "\\Trash" in text:
            imap.trash_folder = m.group(1)
    imap.select(f'"{imap.all_folder}"')
    return imap


def _list_selected_folder(imap: imaplib.IMAP4_SSL, uids: list[bytes]) -> list[dict]:
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
            "in_trash": False,
            # Crew codes that already had this email's alert (see PART_LABEL).
            "parts": {
                m.decode().upper()
                for m in re.findall(PART_LABEL.encode() + rb"([A-Za-z0-9]+)", meta)
            },
        })
    return emails


def list_programme_emails(imap: imaplib.IMAP4_SSL) -> list[dict]:
    """Recent programme emails as {uid, subject, received, done, in_trash, parts}, oldest first.

    Emails already moved to Trash are included (always as done) so that the
    "did one arrive today" and "is this a second one for the same date"
    checks still see them. Only emails outside Trash are ever processed.
    """
    query = f'"subject:({SUBJECT_WORDS}) newer_than:{LOOKBACK_DAYS}d"'
    _, data = imap.uid("SEARCH", "X-GM-RAW", query)
    emails = _list_selected_folder(imap, data[0].split() if data and data[0] else [])

    if imap.trash_folder:
        try:
            imap.select(f'"{imap.trash_folder}"', readonly=True)
            since = (dt.datetime.now(UK) - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
            _, data = imap.uid("SEARCH", "SUBJECT", f'"{SUBJECT_WORDS}"', "SINCE", since)
            trashed = _list_selected_folder(imap, data[0].split() if data and data[0] else [])
            for item in trashed:
                item["done"], item["in_trash"] = True, True
            emails += trashed
        except Exception as exc:  # the Trash is only a cross-check; carry on without it
            print(f"Could not look in Trash: {exc}")
        finally:
            imap.select(f'"{imap.all_folder}"')
    return sorted(emails, key=lambda e: e["received"])


def move_to_trash(imap: imaplib.IMAP4_SSL, uid: bytes) -> None:
    """Move a handled email to Gmail's Trash. Never raises: the email is
    already marked as done, so a failure here must not cause a re-send."""
    if not imap.trash_folder:
        print("No Trash folder found - leaving the email where it is.")
        return
    try:
        status, _ = imap.uid("COPY", uid, f'"{imap.trash_folder}"')
        print("Email moved to Trash." if status == "OK" else f"Could not move to Trash: {status}")
    except Exception as exc:
        print(f"Could not move to Trash: {exc}")


def is_programme_subject(subject: str) -> bool:
    """True for "FLYING PROGRAMME ..." (with or without Fwd:/RE: in front), and for
    variations such as "UPDATED FLYING PROGRAMME 10/10/26" that carry a date."""
    bare = re.sub(r"^\s*((fwd?|re)\s*:\s*)+", "", subject, flags=re.I).lower()
    if bare.startswith(SUBJECT_WORDS):
        return True
    return SUBJECT_WORDS in bare and parse_programme_date(subject) is not None


def mark_done(imap: imaplib.IMAP4_SSL, uid: bytes) -> None:
    imap.uid("STORE", uid, "+X-GM-LABELS", f"({DONE_LABEL})")


def mark_parts(imap: imaplib.IMAP4_SSL, uid: bytes, codes: list[str]) -> None:
    """Record who has had the alert for an email that is not finished yet."""
    labels = " ".join(PART_LABEL + code for code in codes)
    imap.uid("STORE", uid, "+X-GM-LABELS", f"({labels})")


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


def _tell_main_pilot(person: dict, reason: str, dry_run: bool) -> None:
    """Let the main pilot know an extra pilot's alert could not be delivered.
    Goes to the main pilot's own phone, so it can name the other pilot."""
    try:
        send_message(
            f"*Flying programme alert*\n\nWARNING: tonight's alert could not be delivered to "
            f"{person['code']} and will not be tried again ({reason}). They have not been told.",
            dry_run,
        )
    except Exception as exc:
        print(f"Could not tell the main pilot either: {exc}", file=sys.stderr)


def cmd_process(dry_run: bool) -> int:
    imap = open_mailbox()
    emails = list_programme_emails(imap)
    people = recipients()
    sent_dates = {parse_programme_date(e["subject"]) for e in emails if e["done"]}
    failures = 0
    for item in (e for e in emails if not e["done"]):
        print(f"Processing '{item['subject']}' received {item['received']:%d/%m %H:%M}")
        _, fetched = imap.uid("FETCH", item["uid"], "(BODY.PEEK[])")
        raw = next(part[1] for part in fetched if isinstance(part, tuple))
        date = parse_programme_date(item["subject"])
        updated = date is not None and date in sent_dates
        try:
            programme_read = read_email(raw)
        except Exception as exc:  # leave it unmarked so the next run tries again
            failures += 1
            print(f"FAILED on '{item['subject']}': {exc}", file=sys.stderr)
            continue

        readable, delivered, retry_later, gave_up = True, [], False, False
        for person in people:
            if person["code"] in item["parts"]:
                continue  # had it on an earlier run
            try:
                text, readable = text_for(programme_read, updated, person["code"])
                if text:
                    send_message(text, dry_run, person["user_key"], person["who"])
                delivered.append(person["code"])
            except PermanentSendError as exc:
                print(f"FAILED for {person['who']} on '{item['subject']}': {exc}", file=sys.stderr)
                if person["main"]:
                    retry_later = True
                    break
                # A wrong key will be just as wrong on the next run: say so once
                # and move on, rather than failing every half hour.
                gave_up = True
                _tell_main_pilot(person, str(exc), dry_run)
            except Exception as exc:
                print(f"FAILED for {person['who']} on '{item['subject']}': {exc}", file=sys.stderr)
                retry_later = True
                if person["main"]:
                    break  # the service is probably down for everyone; try again next run

        if retry_later:
            # Leave the email unfinished so the next run tries again, but note
            # who already has it so nobody gets it twice.
            failures += 1
            if delivered and not dry_run:
                mark_parts(imap, item["uid"], delivered)
            continue
        if gave_up:
            failures += 1
        if not dry_run:
            mark_done(imap, item["uid"])
            # An email that could not be read stays put, so it can be looked at.
            if readable and not KEEP_EMAILS:
                move_to_trash(imap, item["uid"])
        sent_dates.add(date)
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
    failures = 0
    for person in recipients():
        if person["main"]:
            text = (
                f"*Flying programme*\n\nWARNING: no programme email has reached the alert "
                f"mailbox today (checked {now:%H:%M} UK). Check Outlook - the forwarding may "
                "have stopped."
            )
        else:
            text = (
                f"*Flying programme*\n\nWARNING: tonight's programme has not reached the alert "
                f"service (checked {now:%H:%M} UK), so no alert will come. Check the Ops email "
                "for your flights."
            )
        try:
            send_message(text, dry_run, person["user_key"], person["who"])
        except Exception as exc:
            if person["main"]:
                raise
            failures += 1
            print(f"FAILED for {person['who']}: {exc}", file=sys.stderr)
    return 1 if failures else 0


def cmd_test(dry_run: bool) -> int:
    """A short test message to everyone, each naming the crew code it is set up
    for, so a key filed against the wrong pilot shows up straight away."""
    failures = 0
    for person in recipients():
        try:
            send_message(
                f"*Flying programme alert*\n\nTest message - the alert can reach this phone. "
                f"It is set up for crew code {person['code']}.",
                dry_run, person["user_key"], person["who"],
            )
        except Exception as exc:
            failures += 1
            print(f"FAILED for {person['who']}: {exc}", file=sys.stderr)
    return 1 if failures else 0


def cmd_eml(path: str) -> int:
    with open(path, "rb") as fh:
        programme_read = read_email(fh.read(), any_date=True)
    for person in recipients():
        text, _ = text_for(programme_read, code=person["code"])
        if text:
            send_message(text, dry_run=True, who=person["who"])
        else:
            print("(no message would be sent for this email)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--peek", action="store_true")
    parser.add_argument("--check-missing", action="store_true")
    parser.add_argument("--eml", metavar="FILE")
    parser.add_argument("--test", action="store_true", help="send a short test message")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    dry_run = args.dry_run or os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
    if args.eml:
        return cmd_eml(args.eml)
    if args.test:
        result = cmd_test(dry_run)
    elif args.peek:
        return cmd_peek()
    elif args.check_missing:
        result = cmd_check_missing(dry_run)
    else:
        result = cmd_process(dry_run)
    return 1 if SETUP_PROBLEMS else result


if __name__ == "__main__":
    sys.exit(main())
