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


def main():
    if not (mp.MAVIS_USER and mp.MAVIS_PASS):
        print("MAVIS_USER / MAVIS_PASS missing")
        sys.exit(1)
    seen = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
        page.set_default_timeout(mp.TIMEOUT)
        mp.log_in(page)
        queue = list(START_PAGES)
        visited = set()
        while queue and len(visited) < 25:
            path = queue.pop(0)
            if path in visited:
                continue
            visited.add(path)
            try:
                page.goto(mp.BASE + path, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
            except Exception as exc:
                print(f"!! {path}: {exc.__class__.__name__}")
                continue
            print(f"\n== {path}  (title: {page.title()[:60]})")
            for href, text in links_on(page):
                if not href or href.startswith(("mailto:", "tel:", "#", "javascript")):
                    continue
                href = href.replace(mp.BASE, "")
                if href.startswith("http"):
                    continue
                if href not in seen:
                    seen[href] = text
                    print(f"   {href}   [{text}]")
                low = (href + " " + text).lower()
                if any(k in low for k in KEYWORDS) and href not in visited:
                    queue.append(href.split("?")[0])
        browser.close()
    print(f"\nVisited {len(visited)} pages, {len(seen)} distinct links.")


if __name__ == "__main__":
    main()
