"""Push side of the Python <-> JS bridge.

In the Tkinter app, background threads couldn't touch widgets directly, so
progress/log/status were marshaled through a queue.Queue drained by
root.after(...). Here the equivalent is: a worker thread calls bridge.emit(),
which pushes a JS event into the WebView via window.evaluate_js(). pywebview
serialises the call onto the UI thread for us, so this is thread-safe and
replaces the whole queue-drain machinery with a couple of lines.

The JS side registers handlers via window.__mc3.on(event, fn); see
frontend/js/app.js.
"""

from __future__ import annotations

import json
from typing import Any


class Bridge:
    def __init__(self) -> None:
        self.window = None  # set once the pywebview window exists

    def bind(self, window) -> None:
        self.window = window

    def emit(self, event: str, payload: Any = None) -> None:
        """Fire a named event to the web UI. Safe to call from any thread."""
        if self.window is None:
            return
        data = json.dumps(payload if payload is not None else {})
        # json.dumps produces valid JS literals for both the event name and the
        # payload, so there is nothing to escape by hand.
        script = f"window.__mc3 && window.__mc3._dispatch({json.dumps(event)}, {data});"
        try:
            self.window.evaluate_js(script)
        except Exception:
            # The window may be closing/gone mid-task; dropping the event is fine.
            pass
