"""An asyncio event loop on its own thread, for bleak and the Controller.

Qt owns the main thread, and on Windows that thread is a COM single-threaded
apartment, which stops bleak's WinRT callbacks from firing. So everything
Bluetooth runs on this thread, which joins the multithreaded apartment, and
Qt talks to it only through call() / submit() and signals.
"""
import asyncio
import concurrent.futures
import logging
import sys
import threading
from typing import Any, Callable, Coroutine

log = logging.getLogger(__name__)

COINIT_MULTITHREADED = 0


class AsyncRunner:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="pitpat-asyncio", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        asyncio.set_event_loop(self.loop)
        self.loop.call_soon(self._ready.set)
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()

    def call(self, fn: Callable[..., Any], *args: Any) -> None:
        """Runs fn(*args) on the loop thread, without waiting."""
        self.loop.call_soon_threadsafe(self._guarded, fn, *args)

    def _guarded(self, fn: Callable[..., Any], *args: Any) -> None:
        try:
            fn(*args)
        except Exception:
            log.exception("Error in %s", getattr(fn, "__name__", fn))

    def submit(self, coro: Coroutine) -> "concurrent.futures.Future":
        """Runs a coroutine on the loop thread; the Future is for any thread."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self) -> None:
        if self._thread.is_alive():
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._thread.join(timeout=3)
