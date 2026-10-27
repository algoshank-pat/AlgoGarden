"""Bounded turn admission for a CPU-only local receptionist."""
import os
import math
import time
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock


class QueueBusy(Exception):
    pass


class TurnScheduler:
    def __init__(self, workers: int = 1, waiting: int = 6, wait_seconds: float = 30):
        if not 1 <= workers <= 4 or not 0 <= waiting <= 16 or not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 120:
            raise ValueError('Invalid turn queue configuration')
        self.workers, self.waiting, self.wait_seconds = workers, waiting, wait_seconds
        self._capacity = BoundedSemaphore(workers + waiting)
        self._workers = BoundedSemaphore(workers)
        self._lock = Lock()
        self._admitted = self._active = 0

    def status(self) -> dict:
        with self._lock:
            return {'active': self._active, 'waiting': self._admitted - self._active,
                    'workers': self.workers, 'queue_limit': self.waiting}

    @contextmanager
    def slot(self):
        if not self._capacity.acquire(blocking=False):
            raise QueueBusy('The receptionist queue is full; please retry shortly.')
        started = time.monotonic()
        acquired = False
        with self._lock:
            self._admitted += 1
        try:
            acquired = self._workers.acquire(timeout=self.wait_seconds)
            if not acquired:
                raise QueueBusy('The receptionist is busy; your turn was not processed. Please retry.')
            with self._lock:
                self._active += 1
            yield round((time.monotonic() - started) * 1000)
        finally:
            with self._lock:
                self._admitted -= 1
                if acquired:
                    self._active -= 1
            if acquired:
                self._workers.release()
            self._capacity.release()


def configured_scheduler() -> TurnScheduler:
    return TurnScheduler(workers=int(os.getenv('RECEPTIONIST_TURN_WORKERS', '1')),
                         waiting=int(os.getenv('RECEPTIONIST_QUEUE_LIMIT', '6')),
                         wait_seconds=float(os.getenv('RECEPTIONIST_QUEUE_WAIT_SECONDS', '30')))
