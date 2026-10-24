#!/usr/bin/env python3
"""
mavis_weather.py

Signs in to MAVIS, reads the raw TAF and METAR text for every station on the
TAFs, METARs & Warnings pages, and keeps a rolling 24-hour history.

Output (OUT_FILE, default weather/mavis_weather.json):
  {
    "updated": "2026-10-24T09:40:00Z",
    "latest":  {"EGPD": {"taf": "...", "metar": "...", "seen": "..."}, ...},
    "history": [{"time": "...", "reports": {"EGPD": {"taf": "...", "metar": "..."}}}, ...]
  }
History keeps one snapshot per run for the last HISTORY_HOURS (default 24).

This repo is public, so the weather itself must never be written to the
log or committed here. The workflow uploads OUT_FILE to private storage.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

import mavis_packs as mp

PAGES = [
    "/bases/aberdeen/tafs-metars-warnings/aberdeen-ekofisk",
    "/bases/aberdeen/tafs-metars-warnings/aberdeen-northwest",
    "/bases/aberdeen/tafs-metars-warnings/aberdeen-to-east-shetland-basin",
    "/bases/aberdeen/tafs-metars-warnings/aberdeen-to-piper",
    "/bases/aberdeen/tafs-metars-warnings/east-coast-route",
    "/bases/aberdeen/tafs-metars-warnings/stornoway-offshore",
    "/bases/blackpool/tafs-metars-warnings/irish-sea",
    "/bases/humberside-offshore/tafs-metars-warnings/southern-north-sea-north",
]

OUT_FILE = Path(os.getenv("OUT_FILE", mp.HERE / "weather" / "mavis_weather.json"))
HISTORY_HOURS = float(os.getenv("HISTORY_HOURS", "24"))

HEAD = re.compile(r"^(TAF|METAR|SPECI)\b(?:\s+(?:AMD|COR))*\s+([A-Z]{4})\s+(\d{6})Z")


def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def collect(page):
    """Return {ICAO: {"taf": str, "metar": str}} from every page."""
    found = {}
    for path in PAGES:
        try:
            page.goto(mp.BASE + path, wait_until="domcontentloaded")
            page.wait_for_selector(".site-data__raw", timeout=20000)
            page.wait_for_timeout(1500)
        except Exception as exc:
            mp.log.warning("Could not read %s (%s)", path, exc.__class__.__name__)
            continue
        texts = page.eval_on_selector_all(".site-data__raw", "els => els.map(e => e.innerText)")
        n = 0
        for raw in texts:
            txt = clean(raw)
            m = HEAD.match(txt)
            if not m:
                continue
            kind = "taf" if m.group(1) == "TAF" else "metar"
            icao, stamp = m.group(2), m.group(3)
            slot = found.setdefault(icao, {})
            # Keep the most recently issued report of each kind.
            old = slot.get(kind)
            if old is None or HEAD.match(old).group(3) < stamp:
                slot[kind] = txt
            n += 1
        mp.log.info("%s: %d reports", path.rsplit("/", 1)[-1], n)
    return found


def main():
    if not (mp.MAVIS_USER and mp.MAVIS_PASS):
        mp.log.error("MAVIS_USER / MAVIS_PASS missing")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
        page.set_default_timeout(mp.TIMEOUT)
        mp.log_in(page)
        reports = collect(page)
        browser.close()

    if not reports:
        mp.log.error("No reports found")
        sys.exit(1)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = now.isoformat().replace("+00:00", "Z")

    data = {"latest": {}, "history": []}
    if OUT_FILE.exists():
        try:
            data = json.loads(OUT_FILE.read_text())
        except Exception:
            mp.log.warning("Previous file unreadable; starting fresh")

    cutoff = now - timedelta(hours=HISTORY_HOURS)
    data["history"] = [
        h for h in data.get("history", [])
        if datetime.fromisoformat(h["time"].replace("Z", "+00:00")) >= cutoff
    ]
    data["history"].append({"time": stamp, "reports": reports})

    latest = data.get("latest", {})
    for icao, r in reports.items():
        latest[icao] = {**latest.get(icao, {}), **r, "seen": stamp}
    data["latest"] = latest
    data["updated"] = stamp

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(data, separators=(",", ":")))

    tafs = sum(1 for r in reports.values() if "taf" in r)
    metars = sum(1 for r in reports.values() if "metar" in r)
    summary = (f"{len(reports)} stations, {tafs} TAFs, {metars} METARs; "
               f"{len(data['history'])} snapshots kept ({HISTORY_HOURS:g} h); "
               f"stations: {' '.join(sorted(reports))}")
    issued = " ".join(
        f"{k}:{HEAD.match(reports[k]['taf']).group(3)}Z"
        for k in ("EGPD", "EGPB", "EGPC", "EGPA", "EHKD", "EGNH") if "taf" in reports.get(k, {}))
    summary += f"; TAF issue times (DDHHMM): {issued}"
    mp.log.info(summary)
    print(f"::notice title=MAVIS weather::{summary}")


if __name__ == "__main__":
    main()
