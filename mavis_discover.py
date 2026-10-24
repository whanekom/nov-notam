#!/usr/bin/env python3
"""
mavis_discover.py

One-off helper: signs in to MAVIS and prints the site's page links (address and
link text only), so we can find where aerodrome TAF/METAR are shown.
It deliberately prints no weather content, because this repo is public.
"""

import os
import sys

from playwright.sync_api import sync_playwright

import mavis_packs as mp

START_PAGES = [
    "/",
    "/bases/aberdeen",
    "/bases/aberdeen/briefing-packs",
]
KEYWORDS = ("taf", "metar", "aerodrome", "airfield", "airport", "observ",
            "forecast", "report", "site", "station", "bases", "egp", "ehkd", "egnh")


def links_on(page):
    return page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => [e.getAttribute('href'), (e.innerText||'').trim().slice(0,60)])",
    )


PAGES = [
    "/bases/aberdeen/tafs-metars-warnings/" + slug for slug in (
        "aberdeen-ekofisk", "aberdeen-northwest", "aberdeen-to-east-shetland-basin",
        "aberdeen-to-piper", "east-coast-route", "stornoway-offshore")
] + [
    "/bases/blackpool/tafs-metars-warnings/irish-sea",
    "/bases/blackpool/tafs-metars-warnings/west-coast-route-and-ireland",
    "/bases/sumburgh-offshore/tafs-metars-warnings/shetland-to-east-shetland-basin",
    "/bases/humberside-offshore/tafs-metars-warnings/southern-north-sea-north",
    "/bases/norwich/tafs-metars-warnings/east-coast-route",
]

PROBE = """() => {
  const out = [];
  const all = Array.from(document.querySelectorAll('body *'));
  for (const el of all) {
    const own = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim();
    const m = /^(TAF|METAR|SPECI)\\b(\\s+(AMD|COR))?\\s+([A-Z]{4})/.exec(own);
    if (!m) continue;
    const chain = [];
    let e = el;
    for (let i = 0; i < 4 && e; i++, e = e.parentElement)
      chain.push(e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\\s+/).slice(0,2).join('.') : ''));
    out.push(m[1] + ' ' + m[4] + '  <' + chain.join(' < ') + '>');
  }
  return out;
}"""


def main():
    if not (mp.MAVIS_USER and mp.MAVIS_PASS):
        print("MAVIS_USER / MAVIS_PASS missing")
        sys.exit(1)
    lines = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
        page.set_default_timeout(mp.TIMEOUT)
        mp.log_in(page)
        for path in PAGES:
            try:
                page.goto(mp.BASE + path, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                found = page.evaluate(PROBE)
                heads = page.eval_on_selector_all("h1,h2,h3", "els=>els.map(e=>e.innerText.trim().slice(0,40))")
                lines.append(f"== {path} [{page.title()[:40]}] {len(found)} reports; headings: {heads[:12]}")
                lines += ["   " + f for f in found[:60]]
            except Exception as exc:
                lines.append(f"!! {path}: {exc.__class__.__name__}")
        browser.close()
    body = "\n".join(lines).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::notice title=MAVIS stations::{body[:60000]}")


if __name__ == "__main__":
    main()
