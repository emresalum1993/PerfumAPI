"""
Playwright persistent Chrome profile → export cookies for requests scraper.
"""

from __future__ import annotations

import atexit
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv

load_dotenv()

PROFILE_LOCK = threading.Lock()

CF_BLOCK_MARKERS = (
    "just a moment",
    "cf-challenge",
    "checking your browser",
    "turnstile",
    "enable javascript and cookies",
)

DEFAULT_BOOTSTRAP_URL = "https://www.fragrantica.com"
DEFAULT_PROFILE_DIR = ".pw-fragrantica"
CACHE_FILENAME = "exported_session.json"


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_profile_dir() -> Path:
    raw = (os.getenv("FRAGRANTICA_PW_USER_DATA") or DEFAULT_PROFILE_DIR).strip()
    path = Path(raw)
    if not path.is_absolute():
        path = get_project_root() / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_cache_path() -> Path:
    return get_profile_dir() / CACHE_FILENAME


def get_bootstrap_url() -> str:
    return (os.getenv("FRAGRANTICA_PW_BOOTSTRAP_URL") or DEFAULT_BOOTSTRAP_URL).strip()


def get_browser_channel() -> str:
    return (os.getenv("FRAGRANTICA_PW_CHANNEL") or "chrome").strip()


def cookies_to_header(cookies: List[Dict[str, Any]]) -> str:
    pairs = []
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        domain = (cookie.get("domain") or "").lower()
        if not name or value is None:
            continue
        if domain and "fragrantica.com" not in domain:
            continue
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _looks_blocked(html: str) -> bool:
    lower = (html or "").lower()
    return any(marker in lower for marker in CF_BLOCK_MARKERS)


def _has_cf_clearance(cookies: List[Dict[str, Any]]) -> bool:
    return any(c.get("name") == "cf_clearance" and c.get("value") for c in cookies)


def save_cached_session(cookie_header: str, user_agent: str) -> None:
    payload = {
        "cookie_header": cookie_header,
        "user_agent": user_agent,
        "exported_at": int(time.time()),
    }
    get_cache_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_cached_session() -> Optional[Dict[str, str]]:
    cache_path = get_cache_path()
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    cookie_header = (data.get("cookie_header") or "").strip()
    user_agent = (data.get("user_agent") or "").strip()
    if not cookie_header:
        return None
    return {"cookie_header": cookie_header, "user_agent": user_agent}


def export_session_cookies(
    url: Optional[str] = None,
    *,
    wait_for_human: bool = False,
    timeout_seconds: int = 60,
) -> Dict[str, str]:
    """
    Open headed persistent Chrome, navigate to Fragrantica, export cookies + UA.

    Args:
        url: Page to load (defaults to FRAGRANTICA_PW_BOOTSTRAP_URL)
        wait_for_human: If True, block until user presses Enter (bootstrap flow)
        timeout_seconds: Poll timeout when wait_for_human is False
    """
    from playwright.sync_api import sync_playwright

    target_url = (url or get_bootstrap_url()).strip()
    profile_dir = str(get_profile_dir())
    channel = get_browser_channel()

    with PROFILE_LOCK:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                channel=channel,
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                print(f"🌐 Playwright: opening {target_url} (profile={profile_dir})", flush=True)
                response = page.goto(target_url, wait_until="domcontentloaded", timeout=60000)

                if wait_for_human:
                    print(
                        "Complete any Cloudflare / Turnstile check in the browser window, "
                        "then press Enter here...",
                        flush=True,
                    )
                    input()
                else:
                    deadline = time.time() + timeout_seconds
                    while time.time() < deadline:
                        cookies = context.cookies()
                        html = page.content()
                        status = response.status if response else None
                        if (
                            status == 200
                            and _has_cf_clearance(cookies)
                            and not _looks_blocked(html)
                        ):
                            break
                        remaining = max(0, int(deadline - time.time()))
                        print(
                            f"⏳ Waiting for Cloudflare clearance ({remaining}s left)...",
                            flush=True,
                        )
                        time.sleep(2)
                        response = page.reload(wait_until="domcontentloaded", timeout=30000)

                cookies = context.cookies()
                html = page.content()
                user_agent = page.evaluate("() => navigator.userAgent")

                if not _has_cf_clearance(cookies):
                    raise RuntimeError(
                        "cf_clearance cookie not found. Complete Cloudflare in the browser "
                        "and run bootstrap again."
                    )
                if _looks_blocked(html):
                    raise RuntimeError(
                        "Page still looks like a Cloudflare challenge. "
                        "Complete verification in the browser and retry."
                    )

                cookie_header = cookies_to_header(cookies)
                if not cookie_header:
                    raise RuntimeError("No Fragrantica cookies exported from browser profile.")

                result = {"cookie_header": cookie_header, "user_agent": user_agent}
                save_cached_session(cookie_header, user_agent)
                print(
                    f"✅ Exported {len(cookies)} cookies "
                    f"(cf_clearance={'yes' if _has_cf_clearance(cookies) else 'no'})",
                    flush=True,
                )
                return result
            finally:
                context.close()


class HeadedScrapeBrowser:
    """
    One headed persistent Chrome, always driven from a single worker thread.

    Playwright sync is thread-affine; FastAPI uses to_thread so all browser
    calls must hop onto this executor.
    """

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pw-scrape")
        self._pw = None
        self._context = None
        self._page = None

    def _ensure(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright

        profile_dir = str(get_profile_dir())
        channel = get_browser_channel()
        with PROFILE_LOCK:
            self._pw = sync_playwright().start()
            self._context = self._pw.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                channel=channel,
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        print(f"🎭 Headed Chrome scrape ready (profile={profile_dir})", flush=True)

    def _wait_clearance(self) -> bool:
        timeout_seconds = int(os.getenv("FRAGRANTICA_PW_TIMEOUT", "60"))
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            cookies = self._context.cookies()
            html = self._page.content()
            if _has_cf_clearance(cookies) and not _looks_blocked(html):
                return True
            remaining = max(0, int(deadline - time.time()))
            print(
                f"⏳ Cloudflare in Chrome — complete the check if needed ({remaining}s left)...",
                flush=True,
            )
            time.sleep(2)
        return False

    def _get(self, url: str) -> Tuple[int, str]:
        self._ensure()
        print(f"🎭 Chrome GET: {url}", flush=True)
        response = self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
        status = response.status if response else 0
        html = self._page.content()
        if status in (403, 429) or _looks_blocked(html):
            self._wait_clearance()
            html = self._page.content()
            status = 200 if html and not _looks_blocked(html) else status
        return status, html

    def _page_fetch_post(
        self,
        url: str,
        data: Union[Dict[str, str], str],
    ) -> Tuple[int, str]:
        """POST from the tab (real cookies, UA, CF). Not context.request — that 403s."""
        if "fragrantica.com" not in (self._page.url or ""):
            self._page.goto(get_bootstrap_url(), wait_until="domcontentloaded", timeout=60000)
        payload = {
            "url": url,
            "form": data if isinstance(data, dict) else None,
            "bodyStr": None if isinstance(data, dict) else data,
        }
        result = self._page.evaluate(
            """async ({ url, form, bodyStr }) => {
                const body = form ? new URLSearchParams(form) : bodyStr;
                const res = await fetch(url, {
                    method: "POST",
                    credentials: "include",
                    headers: {
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "X-Requested-With": "XMLHttpRequest",
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                    },
                    body,
                });
                return { status: res.status, text: await res.text() };
            }""",
            payload,
        )
        return int(result.get("status") or 0), str(result.get("text") or "")

    def _post(
        self,
        url: str,
        data: Union[Dict[str, str], str],
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, str]:
        self._ensure()
        print(f"🎭 Chrome page fetch POST: {url}", flush=True)
        status, body = self._page_fetch_post(url, data)
        if status in (403, 429) or _looks_blocked(body):
            print("🎭 POST blocked — reloading tab, then retrying from the page...", flush=True)
            self._page.reload(wait_until="domcontentloaded", timeout=30000)
            self._wait_clearance()
            status, body = self._page_fetch_post(url, data)
        return status, body

    def _close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if self._pw is not None:
                self._pw.stop()
            self._pw = None
            self._context = None
            self._page = None

    def _run(self, fn, *args, **kwargs):
        return self._executor.submit(fn, *args, **kwargs).result()

    def get(self, url: str) -> Tuple[int, str]:
        return self._run(self._get, url)

    def post(
        self,
        url: str,
        data: Union[Dict[str, str], str],
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, str]:
        return self._run(self._post, url, data, headers)

    def _ensure_and_wait(self) -> bool:
        self._ensure()
        return self._wait_clearance()

    def wait_clearance(self) -> bool:
        return self._run(self._ensure_and_wait)

    def close(self) -> None:
        try:
            self._run(self._close)
        except Exception:
            pass
        self._executor.shutdown(wait=True)


_headed_scrape: Optional[HeadedScrapeBrowser] = None
_headed_scrape_lock = threading.Lock()


def get_headed_scrape_browser() -> HeadedScrapeBrowser:
    global _headed_scrape
    with _headed_scrape_lock:
        if _headed_scrape is None:
            _headed_scrape = HeadedScrapeBrowser()
            atexit.register(_headed_scrape.close)
        return _headed_scrape


def headed_scrape_get(url: str) -> Tuple[int, str]:
    return get_headed_scrape_browser().get(url)


def headed_scrape_post(
    url: str,
    data: Union[Dict[str, str], str],
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, str]:
    return get_headed_scrape_browser().post(url, data, headers)


def headed_scrape_wait_clearance() -> bool:
    return get_headed_scrape_browser().wait_clearance()


def resolve_playwright_session(*, refresh: bool = False) -> Optional[Dict[str, str]]:
    """Load cached session or export from profile when USE_PLAYWRIGHT_SESSION is enabled."""
    if not _env_truthy("USE_PLAYWRIGHT_SESSION"):
        return None
    if not refresh:
        cached = load_cached_session()
        if cached and "cf_clearance" in cached["cookie_header"]:
            return cached
    return export_session_cookies(
        wait_for_human=False,
        timeout_seconds=int(os.getenv("FRAGRANTICA_PW_TIMEOUT", "60")),
    )


if __name__ == "__main__":
    session = export_session_cookies(wait_for_human=True)
    assert "cf_clearance" in session["cookie_header"], "bootstrap self-check failed"
    print(f"user_agent={session['user_agent'][:80]}...")
