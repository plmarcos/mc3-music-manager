"""MC3 Music Manager — web-view edition (entry point).

Walking skeleton: a pywebview window that loads an HTML/CSS/JS frontend and
talks to the reused Python backend. Run with:

    python main.py

This does NOT touch the original project — it lives entirely in its own folder.
"""

from __future__ import annotations

import os
import sys

import webview

from backend import core
from backend.api import Api
from backend.bridge import Bridge

# Resolve the UI from the RESOURCE root so it works both from source and when
# frozen by PyInstaller (where __file__ points inside the bundle).
INDEX = core.RESOURCE_ROOT / "frontend" / "index.html"

WINDOW_TITLE = "MC3 Music Manager"

# DevTools (right-click > Inspect) only when developing: set MC3_DEBUG=1.
DEBUG = os.environ.get("MC3_DEBUG", "") == "1" or not getattr(sys, "frozen", False)


def _restore_frameless_resize() -> None:
    """Give a frameless window its resize border back.

    pywebview's winforms backend sets FormBorderStyle.None for frameless windows
    (winforms.py:269-271), which drops WS_THICKFRAME — the user can no longer drag
    the edges to resize. Re-adding WS_THICKFRAME (WITHOUT WS_CAPTION) is exactly
    the "custom chrome, still resizable" pattern modern apps use. WS_MINIMIZEBOX
    goes back too so taskbar/Win+D minimise behaves.

    Best-effort: any failure just leaves the window fixed-size, never crashes.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        GWL_STYLE = -16
        WS_THICKFRAME = 0x00040000
        WS_MINIMIZEBOX = 0x00020000
        SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x0002, 0x0001, 0x0004, 0x0020

        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if not hwnd:
            return
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_THICKFRAME | WS_MINIMIZEBOX)
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
    except Exception:  # noqa: BLE001 - cosmetic; must never break startup
        pass


def main() -> None:
    bridge = Bridge()
    api = Api(bridge)

    window = webview.create_window(
        WINDOW_TITLE,
        url=str(INDEX),
        js_api=api,
        width=1120,
        height=780,
        min_size=(920, 620),
        background_color="#070510",  # MIDNIGHT NEON base — avoids a white flash on open
        # Custom neon title bar lives in the HTML (see .titlebar / #btn-win-*).
        # easy_drag=False on purpose: otherwise the WHOLE window is a drag handle
        # and normal UI interaction/text selection breaks. The title bar opts in
        # via pywebview's `pywebview-drag-region` class instead.
        frameless=True,
        easy_drag=False,
    )
    bridge.bind(window)
    # Once the window exists, put the resize border back (see the function docstring).
    window.events.shown += _restore_frameless_resize

    webview.start(debug=DEBUG)


if __name__ == "__main__":
    main()
