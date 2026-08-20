"""Outreach scheduler (Phase 2F).

A lightweight background thread that runs OutreachQueue.process_once()
on a configurable cadence. It is dependency-free by design (no APScheduler):
a single daemon thread with a stop event is sufficient for a local-first,
single-node tool.

Safety:
- Every tick funnels through the queue worker, which NEVER sends when
  messaging is disabled (provider "none"), the daily limit is 0, or the
  current time is outside the configured outreach window.
- A failing tick is logged and the loop continues; one bad tick can
  never kill the scheduler.
- Each tick uses its own database session (safe for long-running loops).
- stop() is idempotent and joins the worker thread.
"""

from __future__ import annotations

import threading
import time

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.services.queue import OutreachQueue

logger = get_logger("workers.scheduler")

_default_instance: "OutreachScheduler | None" = None


class OutreachScheduler:
    """Periodic queue-worker loop."""

    def __init__(
        self,
        interval_seconds: float | None = None,
        db_factory=None,
        queue_factory=None,
    ) -> None:
        self.interval = interval_seconds or settings.scheduler_interval_seconds
        self._db_factory = db_factory or SessionLocal
        self._queue_factory = queue_factory or (
            lambda db: OutreachQueue(db)
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.runs = 0
        self.last_result: dict | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background loop (no-op if already running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="outreach-scheduler", daemon=True
        )
        self._thread.start()
        logger.info("scheduler started interval=%ss", self.interval)

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the loop to stop and wait for it to exit."""
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("scheduler stopped runs=%s", self.runs)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_tick_now(self) -> dict:
        """Run one worker tick synchronously (safe under default config)."""
        db = self._db_factory()
        try:
            result = self._queue_factory(db).process_once()
        finally:
            db.close()
        return result

    def _loop(self) -> None:
        logger.info("scheduler loop started")
        while not self._stop.is_set():
            try:
                self.last_result = self.run_tick_now()
                self.runs += 1
                logger.debug("scheduler tick #%s done", self.runs)
            except Exception:  # noqa: BLE001 - the loop must survive any tick error
                logger.exception("scheduler tick failed; continuing")
            # Sleep in small increments so stop() is responsive.
            deadline = time.monotonic() + float(self.interval)
            while not self._stop.is_set() and time.monotonic() < deadline:
                time.sleep(min(0.1, max(deadline - time.monotonic(), 0.001)))
        logger.info("scheduler loop stopped")


def create_scheduler() -> OutreachScheduler | None:
    """Factory honoring settings.scheduler_enabled."""
    if not settings.scheduler_enabled:
        logger.info("scheduler disabled by settings")
        return None
    return OutreachScheduler()


def start_scheduler() -> OutreachScheduler | None:
    """Start the app-wide scheduler (used by the FastAPI lifespan)."""
    global _default_instance
    scheduler = create_scheduler()
    if scheduler is not None:
        scheduler.start()
        _default_instance = scheduler
    return scheduler


def stop_scheduler() -> None:
    """Stop the app-wide scheduler (used by the FastAPI lifespan shutdown)."""
    global _default_instance
    if _default_instance is not None:
        _default_instance.stop()
        _default_instance = None