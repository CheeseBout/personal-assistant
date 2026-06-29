"""Desktop perception (Phase 9) — read-only: see/read/summarize the screen.

This layer NEVER controls the desktop (no mouse/keyboard/clipboard) — that is
Phase 10 (Desktop Control). It only captures the screen, reads text via OCR,
detects the active window, reads the accessibility tree, and produces a summary,
so the agent can advise the user about what's on screen.

Privacy (REQUIREMENTS §12.5): OCR text is masked for secrets before it is stored
or sent to a model. Screenshots are kept locally and only sent to a vision model
if DESKTOP_ENABLE_VISION is explicitly turned on.

Every optional dependency (mss/Pillow/pytesseract/pygetwindow/pywinauto) degrades
gracefully: if a piece is missing the corresponding field is None and an
``error`` note explains why, rather than raising.
"""

import base64
import io
import json
import os
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime

from sqlalchemy.orm import Session

from ..models.database import get_sync_db, DesktopObservation
from ..core.config import settings
from ..core.logging_config import logger
from ..core.redaction import redact_text


def _capture_screen_png() -> bytes:
    """Capture the primary monitor as PNG bytes. Requires mss + Pillow."""
    try:
        import mss
        from PIL import Image
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "mss and Pillow are required for screen capture. Install backend/requirements.txt."
        ) from exc

    with mss.mss() as sct:
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        shot = sct.grab(monitor)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _active_window_title() -> Optional[str]:
    """Return the foreground window title, or None if unavailable."""
    try:
        import pygetwindow as gw
    except ModuleNotFoundError:
        return None
    try:
        win = gw.getActiveWindow()
        if win is None:
            return None
        # pygetwindow returns an object on Windows, a string on some platforms.
        return getattr(win, "title", None) or (win if isinstance(win, str) else None)
    except Exception as e:
        logger.error(f"active window detection failed: {e}")
        return None


def _ocr(png_bytes: bytes) -> Optional[str]:
    """Extract text from a PNG via pytesseract. Returns None if OCR unavailable."""
    if not settings.DESKTOP_ENABLE_OCR:
        return None
    try:
        import pytesseract
        from PIL import Image
    except ModuleNotFoundError:
        return None
    try:
        img = Image.open(io.BytesIO(png_bytes))
        return pytesseract.image_to_string(img)
    except Exception as e:
        # Tesseract binary missing or unreadable image.
        logger.error(f"OCR failed: {e}")
        return None


def _mask(text: Optional[str]) -> Optional[str]:
    """Mask secret-shaped substrings from OCR text when masking is enabled."""
    if text is None:
        return None
    if settings.DESKTOP_MASK_SENSITIVE:
        text = redact_text(text)
    if len(text) > settings.DESKTOP_OCR_MAX_CHARS:
        text = text[: settings.DESKTOP_OCR_MAX_CHARS] + "\n…[truncated]"
    return text


def _accessibility_tree(max_depth: int = 3) -> Optional[List[Dict[str, Any]]]:
    """Read the accessibility tree of the foreground window via pywinauto (UIA).

    Returns a flat-ish list of UI element dicts, or None if unavailable.
    Text fields are masked for secrets. Depth and element count are capped
    to avoid flooding the agent context.
    """
    try:
        from pywinauto import Desktop
    except ModuleNotFoundError:
        return None

    try:
        desktop = Desktop(backend="uia")
        wins = desktop.windows(active_only=True)
        if not wins:
            return None
        win = wins[0]
    except Exception as e:
        logger.error(f"accessibility tree: cannot get active window: {e}")
        return None

    elements: List[Dict[str, Any]] = []
    limit = settings.DESKTOP_A11Y_MAX_ELEMENTS

    def _walk(wrapper, depth: int):
        if len(elements) >= limit or depth > max_depth:
            return
        try:
            elem = wrapper.element_info
            name = elem.name or ""
            if settings.DESKTOP_MASK_SENSITIVE and name:
                name = redact_text(name)
            rect = elem.rectangle
            entry = {
                "type": elem.control_type or "Unknown",
                "name": name,
                "auto_id": getattr(elem, "automation_id", "") or "",
                "rect": {"left": rect.left, "top": rect.top,
                         "right": rect.right, "bottom": rect.bottom} if rect else None,
                "enabled": getattr(elem, "enabled", True),
            }
            elements.append(entry)
        except Exception:
            return

        if depth < max_depth:
            try:
                for child in wrapper.children():
                    if len(elements) >= limit:
                        break
                    _walk(child, depth + 1)
            except Exception:
                pass

    _walk(win, 0)
    return elements if elements else None


def _list_windows() -> List[Dict[str, Any]]:
    """List all visible desktop windows with title and state."""
    try:
        import pygetwindow as gw
    except ModuleNotFoundError:
        return []
    try:
        result = []
        for w in gw.getAllWindows():
            title = getattr(w, "title", None) or ""
            if not title.strip():
                continue
            result.append({
                "title": title,
                "visible": getattr(w, "visible", True),
                "minimized": getattr(w, "isMinimized", False),
                "maximized": getattr(w, "isMaximized", False),
                "active": getattr(w, "isActive", False),
            })
        return result
    except Exception as e:
        logger.error(f"list_windows failed: {e}")
        return []


class DesktopPerception:
    """Singleton service for on-demand, read-only screen perception."""

    _instance: Optional["DesktopPerception"] = None

    @classmethod
    def get_instance(cls) -> "DesktopPerception":
        if cls._instance is None:
            cls._instance = DesktopPerception()
        return cls._instance

    def _save_capture(self, png_bytes: bytes) -> str:
        capture_dir = settings.DESKTOP_CAPTURE_DIR
        os.makedirs(capture_dir, exist_ok=True)
        # Filename without Date.now style randomness issues — uuid is fine here.
        path = os.path.join(capture_dir, f"cap_{uuid.uuid4().hex}.png")
        with open(path, "wb") as f:
            f.write(png_bytes)
        return path

    def observe(self, session_id: str = "", include_summary: bool = True,
                include_image_b64: bool = False, db: Optional[Session] = None) -> Dict[str, Any]:
        """Capture + OCR + active window + accessibility tree (+ optional summary). Read-only.

        Returns a dict with active_window, ocr_text (masked), ui_elements,
        summary, image_path, and an ``error`` field if capture was unavailable.
        Never raises.
        """
        active_window = _active_window_title()
        ui_elements = _accessibility_tree()

        try:
            png = _capture_screen_png()
        except RuntimeError as e:
            return {
                "status": "unavailable",
                "active_window": active_window,
                "ocr_text": None,
                "ui_elements": ui_elements,
                "summary": None,
                "image_path": None,
                "masked": settings.DESKTOP_MASK_SENSITIVE,
                "error": str(e),
            }
        except Exception as e:
            logger.error(f"screen capture failed: {e}")
            return {
                "status": "error",
                "active_window": active_window,
                "ocr_text": None,
                "ui_elements": ui_elements,
                "summary": None,
                "image_path": None,
                "masked": settings.DESKTOP_MASK_SENSITIVE,
                "error": str(e),
            }

        image_path = self._save_capture(png)
        ocr_text = _mask(_ocr(png))

        summary = None
        if include_summary:
            summary = self._summarize(png, ocr_text, active_window)

        result = {
            "status": "success",
            "active_window": active_window,
            "ocr_text": ocr_text,
            "ui_elements": ui_elements,
            "summary": summary,
            "image_path": image_path,
            "masked": settings.DESKTOP_MASK_SENSITIVE,
            "error": None,
        }

        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True
        try:
            obs = DesktopObservation(
                id=str(uuid.uuid4()),
                session_id=session_id,
                active_window=active_window,
                ocr_text=ocr_text,
                summary=summary,
                image_path=image_path,
                masked=settings.DESKTOP_MASK_SENSITIVE,
                ui_elements=json.dumps(ui_elements, ensure_ascii=False) if ui_elements else None,
                created_at=datetime.utcnow(),
            )
            db.add(obs)
            db.commit()
            result["id"] = obs.id
        finally:
            if close_db:
                db.close()

        if include_image_b64:
            result["image_b64"] = base64.b64encode(png).decode("ascii")
        return result

    def _summarize(self, png_bytes: bytes, ocr_text: Optional[str],
                   active_window: Optional[str]) -> Optional[str]:
        """Produce a short summary of the screen.

        Uses the vision model only when explicitly enabled (privacy). Otherwise
        falls back to a heuristic summary built from active window + OCR text.
        """
        if settings.DESKTOP_ENABLE_VISION:
            try:
                from .llm import LLMProvider
                llm = LLMProvider(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=settings.OPENAI_BASE_URL,
                    model=settings.MODEL,
                )
                b64 = base64.b64encode(png_bytes).decode("ascii")
                return llm.vision(
                    b64,
                    "Mô tả ngắn gọn nội dung màn hình này bằng tiếng Việt. "
                    "Không suy đoán; chỉ nêu những gì nhìn thấy.",
                )
            except Exception as e:
                logger.error(f"vision summary failed, falling back to OCR: {e}")

        # Heuristic fallback — no model call.
        parts = []
        if active_window:
            parts.append(f"Cửa sổ: {active_window}")
        if ocr_text:
            snippet = ocr_text.strip().splitlines()
            preview = " ".join(snippet[:6]).strip()
            if preview:
                parts.append(f"Văn bản: {preview[:300]}")
        return " | ".join(parts) if parts else None
