#!/usr/bin/env python3
"""Spike test: plain requests vs cloudscraper against a Fragrantica perfume page."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

TEST_URL = os.getenv(
    "FRAGRANTICA_TEST_URL",
    "https://www.fragrantica.com/perfume/Creed/Aventus-9828.html",
)


def _looks_blocked(status_code: int, html: str) -> bool:
    if status_code in (403, 429):
        return True
    lower = (html or "").lower()
    return any(
        token in lower
        for token in ("just a moment", "cf-challenge", "checking your browser", "turnstile")
    )


def test_plain_requests() -> dict:
    import requests

    response = requests.get(TEST_URL, timeout=25)
    html = response.text or ""
    return {
        "mode": "requests",
        "status": response.status_code,
        "bytes": len(html),
        "blocked": _looks_blocked(response.status_code, html),
        "has_perfume_title": "aventus" in html.lower(),
    }


def test_cloudscraper() -> dict:
    import ssl

    import certifi
    import cloudscraper

    ssl_context = ssl.create_default_context(cafile=certifi.where())
    scraper = cloudscraper.create_scraper(
        browser="chrome",
        interpreter=os.getenv("FRAGRANTICA_CLOUDSCRAPER_INTERPRETER", "js2py"),
        debug=os.getenv("FRAGRANTICA_CLOUDSCRAPER_DEBUG", "").lower() in ("1", "true", "yes"),
        ssl_context=ssl_context,
    )
    response = scraper.get(TEST_URL, timeout=45)
    html = response.text or ""
    return {
        "mode": "cloudscraper",
        "status": response.status_code,
        "bytes": len(html),
        "blocked": _looks_blocked(response.status_code, html),
        "has_perfume_title": "aventus" in html.lower(),
    }


def test_scraper_class() -> dict:
    saved_cookies = os.environ.get("FRAGRANTICA_COOKIES")
    os.environ["FRAGRANTICA_COOKIES"] = ""
    os.environ["USE_CLOUDSCRAPER"] = "true"
    try:
        from scraper.scrape import FragranticaScraper

        scraper = FragranticaScraper(delay=1.0)
        soup = scraper._get_page(TEST_URL)
        html = scraper.last_html or ""
        return {
            "mode": "FragranticaScraper+cloudscraper",
            "status": 200 if soup is not None else None,
            "bytes": len(html),
            "blocked": soup is None or _looks_blocked(200 if soup else 403, html),
            "has_perfume_title": bool(soup and "aventus" in html.lower()),
        }
    finally:
        if saved_cookies is None:
            os.environ.pop("FRAGRANTICA_COOKIES", None)
        else:
            os.environ["FRAGRANTICA_COOKIES"] = saved_cookies
        os.environ.pop("USE_CLOUDSCRAPER", None)


if __name__ == "__main__":
    print(f"Testing URL: {TEST_URL}\n")
    for fn in (test_plain_requests, test_cloudscraper, test_scraper_class):
        try:
            result = fn()
            ok = result["status"] == 200 and not result["blocked"] and result["has_perfume_title"]
            mark = "PASS" if ok else "FAIL"
            print(f"[{mark}] {result}")
        except Exception as exc:
            print(f"[ERROR] {fn.__name__}: {exc}")
