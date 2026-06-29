# -*- coding: utf-8 -*-
"""Desktop control (Phase 10) — drive mouse/keyboard on the real desktop.

This is the most dangerous capability in the system: it moves the mouse,
clicks, types, and presses keys on the user's actual machine. It is gated
three ways:

1. OFF by default. ``DESKTOP_ENABLE_CONTROL`` must be turned on in .env.
2. Every state-changing action routes through the HITL approval pipeline
   (seeded with risk_level>=2, requires_approval=1 -> ask_strong). The engine
   itself never decides permission; it only executes once dispatched.
3. Optional window allowlist (``DESKTOP_CONTROL_WINDOW_ALLOWLIST``): if set,
   actions are refused unless the active window title matches.

Targeting prefers the accessibility tree (element name / auto_id via pywinauto,
reusing Phase 9's UIA walk) so the agent acts on named controls rather than
guessing pixels. Raw (x, y) coordinates via pyautogui are the fallback.

Every optional dependency (pyautogui/pywinauto) degrades gracefully: a missing
library returns an ``error`` dict, never raises.
"""

from typing import Any, Dict, List, Optional

from ..core.config import settings
from ..core.logging_config import logger

# Modifier/whitelist keys accepted by desktop.key, mapped to pyautogui names.
_VALID_KEYS = {
    "enter", "return", "tab", "esc", "escape", "space", "backspace", "delete",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    "ctrl", "control", "alt", "shift", "win", "winleft", "cmd",
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
    "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
}

_DISABLED_RESULT = {
    "status": "disabled",
    "error": "Desktop control bị tắt. Bật DESKTOP_ENABLE_CONTROL=true trong .env để cho phép.",
}


def _allowlist() -> List[str]:
    raw = settings.DESKTOP_CONTROL_WINDOW_ALLOWLIST or ""
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


class DesktopControl:
    """Singleton service that performs mouse/keyboard actions on the desktop."""

    _instance: Optional["DesktopControl"] = None

    @classmethod
    def get_instance(cls) -> "DesktopControl":
        if cls._instance is None:
            cls._instance = DesktopControl()
        return cls._instance

    # --- gating helpers -------------------------------------------------

    def _gate(self) -> Optional[Dict[str, Any]]:
        """Return a refusal dict if control is disabled or window not allowed."""
        if not settings.DESKTOP_ENABLE_CONTROL:
            return dict(_DISABLED_RESULT)
        allow = _allowlist()
        if allow:
            from .desktop_perception import _active_window_title
            title = (_active_window_title() or "").lower()
            if not any(a in title for a in allow):
                return {
                    "status": "denied",
                    "error": f"Cửa sổ đang hoạt động ('{title}') không nằm trong allowlist điều khiển.",
                }
        return None

    def _pyautogui(self):
        """Lazy-import pyautogui with failsafe on. Returns module or None."""
        try:
            import pyautogui
        except ModuleNotFoundError:
            return None
        pyautogui.FAILSAFE = True  # mouse to a corner aborts — physical kill switch
        pyautogui.PAUSE = 0.05
        return pyautogui

    def _resolve_element(self, name: Optional[str], auto_id: Optional[str]):
        """Find a UI element in the active window by auto_id then name (UIA).

        Returns the pywinauto wrapper, or None if not found / unavailable.
        Mirrors the active-window selection used by Phase 9's _accessibility_tree.
        """
        if not name and not auto_id:
            return None
        try:
            from pywinauto import Desktop
        except ModuleNotFoundError:
            return None
        try:
            wins = Desktop(backend="uia").windows(active_only=True)
            if not wins:
                return None
            win = wins[0]
            win.set_focus()
        except Exception as e:
            logger.error(f"desktop control: cannot focus active window: {e}")
            return None

        try:
            if auto_id:
                matches = win.descendants(auto_id=auto_id)
                if matches:
                    return matches[0]
            if name:
                matches = win.descendants(title=name)
                if matches:
                    return matches[0]
                # Fallback: case-insensitive substring match on element name.
                for el in win.descendants():
                    try:
                        if name.lower() in (el.element_info.name or "").lower():
                            return el
                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"desktop control: element resolution failed: {e}")
        return None

    def _post_observe(self, session_id: str, verify: bool) -> Optional[Dict[str, Any]]:
        """Lightweight re-observe after an action so the agent can confirm the result.

        Active window + heuristic summary only (no full OCR / no DB write).
        """
        if not verify:
            return None
        try:
            from .desktop_perception import _active_window_title
            return {"active_window": _active_window_title()}
        except Exception as e:
            logger.error(f"post-observe failed: {e}")
            return None

    # --- actions --------------------------------------------------------

    def click(self, session_id: str = "", name: Optional[str] = None,
              auto_id: Optional[str] = None, x: Optional[int] = None,
              y: Optional[int] = None, button: str = "left",
              double: bool = False, verify: bool = True) -> Dict[str, Any]:
        gate = self._gate()
        if gate:
            return gate
        button = button if button in ("left", "right", "middle") else "left"

        # Prefer the named element; fall back to raw coordinates.
        if name or auto_id:
            el = self._resolve_element(name, auto_id)
            if el is None:
                return {"status": "error", "error": f"Không tìm thấy phần tử UI (name={name!r}, auto_id={auto_id!r})."}
            try:
                if double:
                    el.double_click_input(button=button)
                else:
                    el.click_input(button=button)
            except Exception as e:
                return {"status": "error", "error": f"Click phần tử thất bại: {e}"}
            target_desc = f"element name={name!r} auto_id={auto_id!r}"
        elif x is not None and y is not None:
            pg = self._pyautogui()
            if pg is None:
                return {"status": "error", "error": "pyautogui chưa được cài đặt."}
            try:
                pg.moveTo(x, y, duration=settings.DESKTOP_CONTROL_MOVE_DURATION_S)
                pg.click(x=x, y=y, button=button, clicks=2 if double else 1)
            except Exception as e:
                return {"status": "error", "error": f"Click toạ độ thất bại: {e}"}
            target_desc = f"({x}, {y})"
        else:
            return {"status": "error", "error": "Cần cung cấp name/auto_id hoặc toạ độ x,y."}

        return {
            "status": "success",
            "action": "double_click" if double else "click",
            "button": button,
            "target": target_desc,
            "post_observation": self._post_observe(session_id, verify),
        }

    def type_text(self, session_id: str = "", text: str = "",
                  name: Optional[str] = None, auto_id: Optional[str] = None,
                  enter: bool = False, verify: bool = True) -> Dict[str, Any]:
        gate = self._gate()
        if gate:
            return gate
        if not text:
            return {"status": "error", "error": "Thiếu nội dung cần gõ (text)."}

        if name or auto_id:
            el = self._resolve_element(name, auto_id)
            if el is None:
                return {"status": "error", "error": f"Không tìm thấy ô nhập (name={name!r}, auto_id={auto_id!r})."}
            # Focus the element via UIA (puts the correct window+control in focus),
            # then type with pyautogui.write — more reliable than type_keys for
            # arbitrary text (type_keys drops/garbles fast special-char input).
            try:
                el.click_input()
            except Exception as e:
                return {"status": "error", "error": f"Không focus được ô nhập: {e}"}
            pg = self._pyautogui()
            if pg is None:
                return {"status": "error", "error": "pyautogui chưa được cài đặt."}
            try:
                pg.write(text, interval=0.02)
            except Exception as e:
                return {"status": "error", "error": f"Gõ vào phần tử thất bại: {e}"}
        else:
            pg = self._pyautogui()
            if pg is None:
                return {"status": "error", "error": "pyautogui chưa được cài đặt."}
            try:
                pg.write(text, interval=0.02)
            except Exception as e:
                return {"status": "error", "error": f"Gõ văn bản thất bại: {e}"}

        if enter:
            pg = self._pyautogui()
            if pg is not None:
                try:
                    pg.press("enter")
                except Exception as e:
                    logger.error(f"press enter after type failed: {e}")

        # Never echo the typed text back (it may be sensitive).
        return {
            "status": "success",
            "action": "type",
            "chars": len(text),
            "enter": bool(enter),
            "post_observation": self._post_observe(session_id, verify),
        }

    def press_key(self, session_id: str = "", keys: str = "",
                  verify: bool = True) -> Dict[str, Any]:
        gate = self._gate()
        if gate:
            return gate
        keys = (keys or "").strip().lower()
        if not keys:
            return {"status": "error", "error": "Thiếu phím cần nhấn (keys)."}

        # "ctrl+c" -> hotkey; "enter" -> single press.
        parts = [p.strip() for p in keys.split("+") if p.strip()]
        invalid = [p for p in parts if p not in _VALID_KEYS]
        if invalid:
            return {"status": "error", "error": f"Phím không hợp lệ: {', '.join(invalid)}"}

        pg = self._pyautogui()
        if pg is None:
            return {"status": "error", "error": "pyautogui chưa được cài đặt."}
        try:
            if len(parts) > 1:
                pg.hotkey(*parts)
            else:
                pg.press(parts[0])
        except Exception as e:
            return {"status": "error", "error": f"Nhấn phím thất bại: {e}"}

        return {
            "status": "success",
            "action": "key",
            "keys": keys,
            "post_observation": self._post_observe(session_id, verify),
        }

    def mouse_move(self, session_id: str = "", x: Optional[int] = None,
                   y: Optional[int] = None, name: Optional[str] = None,
                   auto_id: Optional[str] = None) -> Dict[str, Any]:
        gate = self._gate()
        if gate:
            return gate
        if name or auto_id:
            el = self._resolve_element(name, auto_id)
            if el is None:
                return {"status": "error", "error": "Không tìm thấy phần tử UI để di chuột tới."}
            try:
                rect = el.element_info.rectangle
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
            except Exception as e:
                return {"status": "error", "error": f"Không lấy được vị trí phần tử: {e}"}
            x, y = cx, cy
        if x is None or y is None:
            return {"status": "error", "error": "Cần toạ độ x,y hoặc name/auto_id."}
        pg = self._pyautogui()
        if pg is None:
            return {"status": "error", "error": "pyautogui chưa được cài đặt."}
        try:
            pg.moveTo(x, y, duration=settings.DESKTOP_CONTROL_MOVE_DURATION_S)
        except Exception as e:
            return {"status": "error", "error": f"Di chuột thất bại: {e}"}
        return {"status": "success", "action": "mouse_move", "position": [x, y]}

    def scroll(self, session_id: str = "", amount: int = 0,
               direction: str = "down") -> Dict[str, Any]:
        gate = self._gate()
        if gate:
            return gate
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            return {"status": "error", "error": "amount phải là số nguyên."}
        if amount <= 0:
            return {"status": "error", "error": "amount phải > 0."}
        pg = self._pyautogui()
        if pg is None:
            return {"status": "error", "error": "pyautogui chưa được cài đặt."}
        clicks = amount if direction == "up" else -amount
        try:
            pg.scroll(clicks)
        except Exception as e:
            return {"status": "error", "error": f"Cuộn thất bại: {e}"}
        return {"status": "success", "action": "scroll", "direction": direction, "amount": amount}

    def drag(self, session_id: str = "", from_x: Optional[int] = None,
             from_y: Optional[int] = None, to_x: Optional[int] = None,
             to_y: Optional[int] = None, button: str = "left",
             verify: bool = True) -> Dict[str, Any]:
        gate = self._gate()
        if gate:
            return gate
        if None in (from_x, from_y, to_x, to_y):
            return {"status": "error", "error": "Cần đủ toạ độ from_x, from_y, to_x, to_y."}
        button = button if button in ("left", "right", "middle") else "left"
        pg = self._pyautogui()
        if pg is None:
            return {"status": "error", "error": "pyautogui chưa được cài đặt."}
        try:
            pg.moveTo(from_x, from_y, duration=settings.DESKTOP_CONTROL_MOVE_DURATION_S)
            pg.dragTo(to_x, to_y, duration=max(0.3, settings.DESKTOP_CONTROL_MOVE_DURATION_S), button=button)
        except Exception as e:
            return {"status": "error", "error": f"Kéo-thả thất bại: {e}"}
        return {
            "status": "success",
            "action": "drag",
            "from": [from_x, from_y],
            "to": [to_x, to_y],
            "post_observation": self._post_observe(session_id, verify),
        }

    def wait(self, session_id: str = "", seconds: float = 0,
             name: Optional[str] = None, auto_id: Optional[str] = None,
             timeout: float = 10.0) -> Dict[str, Any]:
        """Wait a fixed duration, or until a UI element appears (up to timeout).

        Read-only / non-mutating, so it is not gated by control-enabled and can
        run without approval — useful between higher-risk steps.
        """
        if name or auto_id:
            import time
            deadline = time.time() + max(0.0, min(float(timeout), 60.0))
            while time.time() < deadline:
                if self._resolve_element(name, auto_id) is not None:
                    return {"status": "success", "action": "wait", "found": True}
                time.sleep(0.5)
            return {"status": "success", "action": "wait", "found": False,
                    "error": "Hết thời gian chờ, không thấy phần tử."}
        try:
            import time
            time.sleep(max(0.0, min(float(seconds), 60.0)))
        except (TypeError, ValueError):
            return {"status": "error", "error": "seconds phải là số."}
        return {"status": "success", "action": "wait", "seconds": seconds}
