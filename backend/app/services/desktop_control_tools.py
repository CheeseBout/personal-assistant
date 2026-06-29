# -*- coding: utf-8 -*-
"""Desktop control tools (Phase 10) — mouse/keyboard executors.

Executors follow the standard signature ``execute(arguments, session_id) -> dict``
and delegate to the DesktopControl singleton. State-changing tools (click/type/
key/drag) are seeded at risk_level>=2 + requires_approval, so they only reach
here after the user approves via the HITL pipeline. mouse_move/scroll/wait are
low risk and may run directly.
"""

from typing import Any, Dict

from .desktop_control import DesktopControl
from ..core.config import settings


def _verify(arguments: Dict[str, Any]) -> bool:
    return bool(arguments.get("verify", settings.DESKTOP_CONTROL_VERIFY_DEFAULT))


def desktop_click(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Click a UI element (by name/auto_id) or raw coordinates (x, y)."""
    return DesktopControl.get_instance().click(
        session_id=session_id,
        name=arguments.get("name"),
        auto_id=arguments.get("auto_id"),
        x=arguments.get("x"),
        y=arguments.get("y"),
        button=arguments.get("button", "left"),
        double=bool(arguments.get("double", False)),
        verify=_verify(arguments),
    )


def desktop_type(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Type text into a focused element (by name/auto_id) or the active field."""
    return DesktopControl.get_instance().type_text(
        session_id=session_id,
        text=arguments.get("text", ""),
        name=arguments.get("name"),
        auto_id=arguments.get("auto_id"),
        enter=bool(arguments.get("enter", False)),
        verify=_verify(arguments),
    )


def desktop_key(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Press a key or key combination (e.g. 'enter', 'ctrl+c')."""
    return DesktopControl.get_instance().press_key(
        session_id=session_id,
        keys=arguments.get("keys", ""),
        verify=_verify(arguments),
    )


def desktop_mouse_move(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Move the mouse cursor to coordinates or a UI element (no click)."""
    return DesktopControl.get_instance().mouse_move(
        session_id=session_id,
        x=arguments.get("x"),
        y=arguments.get("y"),
        name=arguments.get("name"),
        auto_id=arguments.get("auto_id"),
    )


def desktop_scroll(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Scroll the active window up or down."""
    return DesktopControl.get_instance().scroll(
        session_id=session_id,
        amount=arguments.get("amount", 0),
        direction=arguments.get("direction", "down"),
    )


def desktop_drag(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Drag the mouse from one coordinate to another (e.g. select, move)."""
    return DesktopControl.get_instance().drag(
        session_id=session_id,
        from_x=arguments.get("from_x"),
        from_y=arguments.get("from_y"),
        to_x=arguments.get("to_x"),
        to_y=arguments.get("to_y"),
        button=arguments.get("button", "left"),
        verify=_verify(arguments),
    )


def desktop_wait(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Wait a fixed duration or until a UI element appears."""
    return DesktopControl.get_instance().wait(
        session_id=session_id,
        seconds=arguments.get("seconds", 0),
        name=arguments.get("name"),
        auto_id=arguments.get("auto_id"),
        timeout=arguments.get("timeout", 10.0),
    )
