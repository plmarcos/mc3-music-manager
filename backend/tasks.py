"""Background-task helper — the web-view equivalent of _start_background_task.

Same contract as the Tkinter app: run a worker on a daemon thread, then report
success or failure. The difference is that progress is streamed to the UI via
the Bridge (evaluate_js) instead of a queue.Queue.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional


def run_in_background(
    name: str,
    worker: Callable[[], object],
    on_done: Optional[Callable[[object], None]] = None,
    on_error: Optional[Callable[[BaseException], None]] = None,
) -> threading.Thread:
    def runner() -> None:
        try:
            result = worker()
            if on_done is not None:
                on_done(result)
        except BaseException as exc:  # noqa: BLE001 - report everything to the UI
            if on_error is not None:
                on_error(exc)

    thread = threading.Thread(target=runner, name=f"mc3web_{name}", daemon=True)
    thread.start()
    return thread
