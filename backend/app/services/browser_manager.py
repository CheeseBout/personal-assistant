"""Browser Manager — single shared Playwright browser on a background event loop.

Playwright's API is async, but the tool executor calls handlers synchronously
(it never awaits). To bridge this, we run one asyncio event loop on a dedicated
background thread and launch a single persistent Chromium context there. Sync
handlers submit coroutines to that loop via ``run_coroutine_threadsafe`` and
block on the result with a timeout.

Design (per Phase 4 plan):
- One shared browser (persistent context) for the whole app — single-user, local-first.
- A page pool keyed by chat ``session_id`` (one tab per session).
- Cookies/session live in a dedicated profile dir, never the user's real profile.
- Domain allow/block list + scheme guard enforced before navigation.
- The browser stays alive for the app lifetime; pages are created lazily.
"""

import asyncio
import base64
import threading
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import urlparse

from ..core.config import settings
from ..core.logging_config import logger

import logging
logging.getLogger("playwright").setLevel(logging.WARNING)


def _data_dir(rel: str) -> Path:
    """Resolve a ../data/... style setting to an absolute path and ensure it exists."""
    p = Path(rel)
    if not p.is_absolute():
        # settings use paths relative to the backend/ working dir
        p = (Path(__file__).parent.parent.parent / rel).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _trim_a11y(node: Optional[dict], depth: int = 0, max_depth: int = 6) -> Optional[dict]:
    """Reduce a Playwright accessibility snapshot to {role, name, children}.

    Keeps the structure small enough to fit in context while preserving the
    role/name pairs the model needs to target elements (REQUIREMENTS 11.3/11.4).
    """
    if not node or depth > max_depth:
        return None
    out: Dict[str, Any] = {"role": node.get("role")}
    name = (node.get("name") or "").strip()
    if name:
        out["name"] = name[:120]
    children = node.get("children") or []
    trimmed = [c for c in (_trim_a11y(ch, depth + 1, max_depth) for ch in children[:40]) if c]
    if trimmed:
        out["children"] = trimmed
    return out


class BrowserManager:
    """Singleton wrapper around a single persistent Playwright context."""

    _instance: Optional["BrowserManager"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._playwright = None
        self._context = None  # persistent browser context
        self._pages: Dict[str, Any] = {}  # session_id -> Page
        self._started = False

    @classmethod
    def get_instance(cls) -> "BrowserManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = BrowserManager()
        return cls._instance

    # --- background loop lifecycle -------------------------------------------------

    def _ensure_started(self):
        """Start the background loop + browser on first use (thread-safe)."""
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, name="playwright-loop", daemon=True
            )
            self._thread.start()
            # Launch the browser on that loop and wait for it.
            fut = asyncio.run_coroutine_threadsafe(self._launch(), self._loop)
            fut.result(timeout=60)
            self._started = True

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _launch(self):
        from playwright.async_api import async_playwright

        profile_dir = _data_dir(settings.BROWSER_PROFILE_DIR)
        download_dir = _data_dir(settings.BROWSER_DOWNLOAD_DIR)
        _data_dir(settings.BROWSER_SCREENSHOT_DIR)

        self._playwright = await async_playwright().start()
        # Persistent context keeps cookies/sessions in our own profile dir,
        # isolated from the user's real browser profile (REQUIREMENTS 11.5).
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=settings.BROWSER_HEADLESS,
            accept_downloads=True,
            downloads_path=str(download_dir),
        )
        self._context.set_default_navigation_timeout(settings.BROWSER_NAV_TIMEOUT_MS)
        logger.info(
            f"BrowserManager launched (headless={settings.BROWSER_HEADLESS}, "
            f"profile={profile_dir})"
        )

    def _run(self, coro):
        """Submit a coroutine to the background loop and block for the result."""
        self._ensure_started()
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=settings.BROWSER_OP_TIMEOUT_S)

    # --- page pool -----------------------------------------------------------------

    async def _get_page(self, session_id: str):
        page = self._pages.get(session_id)
        if page is None or page.is_closed():
            page = await self._context.new_page()
            self._pages[session_id] = page
        return page

    # --- domain / scheme guard -------------------------------------------------------

    def _check_url(self, url: str) -> Optional[str]:
        """Return an error string if the URL is not allowed, else None."""
        if not url or not isinstance(url, str):
            return "URL is required"
        parsed = urlparse(url if "://" in url else f"https://{url}")
        scheme = (parsed.scheme or "").lower()
        if scheme not in ("http", "https"):
            return f"Scheme '{scheme}' is not allowed (only http/https)"
        host = (parsed.hostname or "").lower()
        if not host:
            return "URL has no host"
        block = [d.strip().lower() for d in (settings.BROWSER_DOMAIN_BLOCKLIST or "").split(",") if d.strip()]
        if any(host == d or host.endswith("." + d) for d in block):
            return f"Domain '{host}' is on the blocklist"
        allow = [d.strip().lower() for d in (settings.BROWSER_DOMAIN_ALLOWLIST or "").split(",") if d.strip()]
        if allow and not any(host == d or host.endswith("." + d) for d in allow):
            return f"Domain '{host}' is not on the allowlist"
        return None

    # --- async operations (run on the background loop) -------------------------------

    async def _a_open(self, session_id: str, url: str):
        if "://" not in url:
            url = f"https://{url}"
        page = await self._get_page(session_id)
        await page.goto(url, wait_until="domcontentloaded")
        return {"status": "success", "url": page.url, "title": await page.title()}

    async def _a_observe(self, session_id: str, max_chars: int, a11y: bool):
        page = await self._get_page(session_id)
        text = (await page.inner_text("body"))[:max_chars]
        forms = await page.eval_on_selector_all(
            "form",
            "els => els.map(f => ({action: f.action, method: f.method, "
            "fields: Array.from(f.elements).map(e => e.name).filter(Boolean)}))",
        )
        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.slice(0, 50).map(a => ({text: (a.innerText||'').trim().slice(0,80), href: a.href}))",
        )
        result = {
            "url": page.url,
            "title": await page.title(),
            "visible_text": text,
            "forms": forms,
            "links": links,
        }
        if a11y:
            # Accessibility tree (REQUIREMENTS 11.3): structured, role-based view of
            # the page. Trimmed to interesting roles to keep the payload small.
            try:
                snapshot = await page.accessibility.snapshot(interesting_only=True)
                result["accessibility_tree"] = _trim_a11y(snapshot)
            except Exception as e:
                result["accessibility_tree"] = None
                logger.warning(f"accessibility snapshot failed: {e}")
        return result

    async def _a_extract(self, session_id: str, selector: str, limit: int):
        page = await self._get_page(session_id)
        matches = await page.eval_on_selector_all(
            selector,
            "els => els.map(e => (e.innerText||e.textContent||'').trim())",
        )
        matches = [m for m in matches if m][:limit]
        return {"matches": matches, "count": len(matches), "selector": selector}

    async def _a_click(self, session_id: str, target: str):
        page = await self._get_page(session_id)
        url_before, title_before = page.url, await page.title()
        # Prefer accessible text; fall back to CSS selector.
        try:
            await page.get_by_text(target, exact=False).first.click(timeout=8000)
        except Exception:
            await page.click(target, timeout=8000)
        await page.wait_for_load_state("domcontentloaded")
        url_after, title_after = page.url, await page.title()
        changed = (url_after != url_before) or (title_after != title_before)
        # Post-action verifier (REQUIREMENTS 11.7): confirm the page reacted.
        verification = {
            "expected": "page URL or title changes after click",
            "observed": {"url_changed": url_after != url_before,
                         "title_changed": title_after != title_before},
            "verified": changed,
        }
        return {
            "status": "success",
            "target": target,
            "url_after": url_after,
            "title_after": title_after,
            "changed": changed,
            "verification": verification,
        }

    async def _a_type(self, session_id: str, target: str, value: str, submit: bool):
        page = await self._get_page(session_id)
        url_before = page.url
        await page.fill(target, value, timeout=8000)
        # Read the field back to confirm the value landed (length only — never the value).
        try:
            filled_len = len(await page.input_value(target, timeout=4000))
        except Exception:
            filled_len = None
        if submit:
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("domcontentloaded")
        url_after = page.url
        # Post-action verifier (REQUIREMENTS 11.7). For a submit we expect navigation;
        # for a plain type we expect the field to now hold the typed length.
        if submit:
            verification = {
                "expected": "navigation / URL change after submit",
                "observed": {"url_changed": url_after != url_before},
                "verified": url_after != url_before,
            }
        else:
            verification = {
                "expected": "input field holds the typed text",
                "observed": {"field_length": filled_len, "expected_length": len(value)},
                "verified": filled_len == len(value) if filled_len is not None else False,
            }
        # Never echo the typed value back.
        return {"status": "success", "target": target, "submitted": submit,
                "url": url_after, "verification": verification}

    async def _a_download(self, session_id: str, target: str, timeout_ms: int):
        page = await self._get_page(session_id)
        download_dir = _data_dir(settings.BROWSER_DOWNLOAD_DIR)
        # Trigger the download by clicking the target, capturing the download event.
        try:
            async with page.expect_download(timeout=timeout_ms) as dl_info:
                try:
                    await page.get_by_text(target, exact=False).first.click(timeout=8000)
                except Exception:
                    await page.click(target, timeout=8000)
            download = await dl_info.value
        except Exception as e:
            return {"error": f"No download started for target '{target}': {e}"}

        suggested = download.suggested_filename or "download.bin"
        # Keep only the basename to avoid path traversal from a hostile page.
        safe_name = Path(suggested).name
        dest = download_dir / safe_name
        await download.save_as(str(dest))
        exists = dest.exists()
        size = dest.stat().st_size if exists else 0
        # Post-action verifier (REQUIREMENTS 11.7): file exists in download folder.
        return {
            "status": "success" if exists else "error",
            "filename": safe_name,
            "saved_path": str(dest),
            "size_bytes": size,
            "verification": {
                "expected": "file saved in browser download folder",
                "observed": {"file_exists": exists, "size_bytes": size},
                "verified": exists and size > 0,
            },
        }

    async def _a_upload(self, session_id: str, selector: str, abs_path: str, filename: str):
        page = await self._get_page(session_id)
        await page.set_input_files(selector, abs_path, timeout=8000)
        # Verify the chosen filename is now reflected in the input's files (DOM state).
        try:
            chosen = await page.eval_on_selector(
                selector,
                "el => el.files ? Array.from(el.files).map(f => f.name) : []",
            )
        except Exception:
            chosen = []
        visible = filename in (chosen or [])
        # Post-action verifier (REQUIREMENTS 11.7): file name visible in DOM state.
        return {
            "status": "success" if visible else "error",
            "selector": selector,
            "filename": filename,
            "verification": {
                "expected": "uploaded file name appears in the input's file list",
                "observed": {"files_in_input": chosen},
                "verified": visible,
            },
        }

    async def _a_screenshot(self, session_id: str):
        page = await self._get_page(session_id)
        png = await page.screenshot(type="png", full_page=False)
        return {
            "status": "success",
            "image_b64": base64.b64encode(png).decode("ascii"),
            "url": page.url,
        }

    async def _a_wait(self, session_id: str, selector: Optional[str], ms: Optional[int]):
        page = await self._get_page(session_id)
        if selector:
            await page.wait_for_selector(selector, timeout=15000)
            return {"status": "success", "waited_for": selector}
        await page.wait_for_timeout(ms or 1000)
        return {"status": "success", "waited_for": f"{ms or 1000}ms"}

    async def _a_close(self, session_id: str):
        page = self._pages.pop(session_id, None)
        if page and not page.is_closed():
            await page.close()
        return {"status": "success", "closed": True}

    async def _a_state(self, session_id: str):
        page = self._pages.get(session_id)
        if page is None or page.is_closed():
            return {"current_url": None, "title": None, "is_active": False}
        return {"current_url": page.url, "title": await page.title(), "is_active": True}

    # --- sync public API (called from tool handlers) ---------------------------------

    def open(self, session_id: str, url: str) -> Dict[str, Any]:
        err = self._check_url(url)
        if err:
            return {"error": err}
        return self._run(self._a_open(session_id, url))

    def observe(self, session_id: str, max_chars: int = 4000, a11y: bool = False) -> Dict[str, Any]:
        return self._run(self._a_observe(session_id, max_chars, a11y))

    def extract(self, session_id: str, selector: str, limit: int = 50) -> Dict[str, Any]:
        return self._run(self._a_extract(session_id, selector, limit))

    def click(self, session_id: str, target: str) -> Dict[str, Any]:
        return self._run(self._a_click(session_id, target))

    def type_text(self, session_id: str, target: str, value: str, submit: bool = False) -> Dict[str, Any]:
        return self._run(self._a_type(session_id, target, value, submit))

    def download(self, session_id: str, target: str, timeout_ms: Optional[int] = None) -> Dict[str, Any]:
        ms = timeout_ms or settings.BROWSER_DOWNLOAD_TIMEOUT_MS
        # The download may take longer than a normal op; give the sync bridge headroom.
        self._ensure_started()
        fut = asyncio.run_coroutine_threadsafe(self._a_download(session_id, target, ms), self._loop)
        return fut.result(timeout=(ms / 1000) + 15)

    def upload(self, session_id: str, selector: str, abs_path: str, filename: str) -> Dict[str, Any]:
        return self._run(self._a_upload(session_id, selector, abs_path, filename))

    def screenshot(self, session_id: str) -> Dict[str, Any]:
        return self._run(self._a_screenshot(session_id))

    def wait(self, session_id: str, selector: Optional[str] = None, ms: Optional[int] = None) -> Dict[str, Any]:
        return self._run(self._a_wait(session_id, selector, ms))

    def close(self, session_id: str) -> Dict[str, Any]:
        return self._run(self._a_close(session_id))

    def state(self, session_id: str) -> Dict[str, Any]:
        if not self._started:
            return {"current_url": None, "title": None, "is_active": False}
        return self._run(self._a_state(session_id))
