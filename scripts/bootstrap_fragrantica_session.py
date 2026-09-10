#!/usr/bin/env python3
"""First-run bootstrap: pass Cloudflare in headed Chrome and cache cookies for the scraper.

Use the project venv:
  ./venv/bin/python scripts/bootstrap_fragrantica_session.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    print(
        "Missing venv packages. Run:\n"
        "  ./venv/bin/python scripts/bootstrap_fragrantica_session.py",
        file=sys.stderr,
    )
    raise SystemExit(1)

load_dotenv()

from scraper.playwright_cookies import export_session_cookies, get_bootstrap_url, get_profile_dir


def main() -> int:
    print("Fragrantica Playwright bootstrap")
    print(f"Profile directory: {get_profile_dir()}")
    print(f"Target URL: {get_bootstrap_url()}")
    print()

    try:
        session = export_session_cookies(wait_for_human=True)
    except Exception as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        return 1

    has_clearance = "cf_clearance" in session["cookie_header"]
    cookie_count = len([p for p in session["cookie_header"].split(";") if p.strip()])

    print()
    print(f"cf_clearance present: {has_clearance}")
    print(f"cookie pairs exported: {cookie_count}")
    print(f"user_agent: {session['user_agent']}")
    print()
    print("Cached to exported_session.json in your profile folder.")
    print("Set USE_PLAYWRIGHT_SESSION=true in .env and run the scraper/API.")
    print()
    print("Optional manual override (copy into .env if needed):")
    print(f"FRAGRANTICA_USER_AGENT={session['user_agent']}")
    print(f"FRAGRANTICA_COOKIES={session['cookie_header'][:120]}...")

    return 0 if has_clearance else 1


if __name__ == "__main__":
    raise SystemExit(main())
