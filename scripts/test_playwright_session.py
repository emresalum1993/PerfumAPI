#!/usr/bin/env python3
"""Verify Playwright cookie factory integration (no browser required if cache exists)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from scraper.playwright_cookies import (
    cookies_to_header,
    get_cache_path,
    load_cached_session,
    save_cached_session,
)


def test_cookies_to_header() -> None:
    header = cookies_to_header(
        [
            {"name": "cf_clearance", "value": "abc", "domain": ".fragrantica.com"},
            {"name": "other", "value": "skip", "domain": ".example.com"},
            {"name": "fsuid", "value": "123", "domain": "www.fragrantica.com"},
        ]
    )
    assert "cf_clearance=abc" in header
    assert "fsuid=123" in header
    assert "other=" not in header


def test_cache_roundtrip() -> None:
    path = get_cache_path()
    backup = path.read_text(encoding="utf-8") if path.is_file() else None
    try:
        save_cached_session("cf_clearance=test; fsuid=1", "TestAgent/1.0")
        loaded = load_cached_session()
        assert loaded is not None
        assert "cf_clearance=test" in loaded["cookie_header"]
        assert loaded["user_agent"] == "TestAgent/1.0"
    finally:
        if backup is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(backup, encoding="utf-8")


def test_scraper_loads_playwright_cache() -> None:
    cached = load_cached_session()
    if not cached:
        print("SKIP scraper cache test: no exported_session.json (run bootstrap first)")
        return

    saved = {
        "FRAGRANTICA_COOKIES": os.environ.get("FRAGRANTICA_COOKIES"),
        "USE_PLAYWRIGHT_SESSION": os.environ.get("USE_PLAYWRIGHT_SESSION"),
        "FRAGRANTICA_PW_SCRAPE": os.environ.get("FRAGRANTICA_PW_SCRAPE"),
    }
    os.environ["FRAGRANTICA_COOKIES"] = ""
    os.environ["USE_PLAYWRIGHT_SESSION"] = "true"
    os.environ["FRAGRANTICA_PW_SCRAPE"] = "false"
    try:
        from scraper.scrape import FragranticaScraper

        scraper = FragranticaScraper(delay=1.0)
        assert scraper.using_playwright_session
        assert scraper.session_cookie_header
        assert "Cookie" in scraper.session.headers
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    test_cookies_to_header()
    test_cache_roundtrip()
    test_scraper_loads_playwright_cache()
    print("All Playwright integration checks passed.")
