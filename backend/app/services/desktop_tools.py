"""Desktop perception tools (Phase 9) — read-only screen perception.

Executors follow the standard signature: ``execute(arguments, session_id)``.
ALL tools here are read-only (capture/read/summarize). Desktop control
(click/type) is Phase 10 and intentionally absent.
"""

from typing import Any, Dict

from .desktop_perception import DesktopPerception


def desktop_observe(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Capture the screen, OCR it, detect the active window, read a11y tree, and summarize.

    Read-only. Screenshot stays local; only masked text leaves storage. The
    base64 image is NOT returned to the agent context by default (privacy).
    """
    include_summary = arguments.get("include_summary", True)
    return DesktopPerception.get_instance().observe(
        session_id=session_id,
        include_summary=bool(include_summary),
        include_image_b64=False,
    )


def desktop_active_window(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Return just the active (foreground) window title — cheap, no capture."""
    from .desktop_perception import _active_window_title
    return {"active_window": _active_window_title()}


def desktop_ui_elements(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Read the accessibility tree of the active window — structured UI elements."""
    from .desktop_perception import _accessibility_tree
    max_depth = arguments.get("max_depth", 3)
    max_depth = max(1, min(max_depth, 5))
    elements = _accessibility_tree(max_depth=max_depth)
    if elements is None:
        return {"ui_elements": [], "error": "Accessibility API unavailable (pywinauto not installed or no active window)"}
    return {"ui_elements": elements, "count": len(elements)}


def desktop_list_windows(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """List all visible desktop windows with title and state."""
    from .desktop_perception import _list_windows
    windows = _list_windows()
    return {"windows": windows, "count": len(windows)}
