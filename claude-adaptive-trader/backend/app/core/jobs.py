"""Background jobs: downloads, backtests and other work too slow for a request.

Jobs run on a small thread pool. Each has an id, a progress fraction, a status message and,
when done, a small JSON result or an error. Long functions receive a ``JobContext`` to
report progress and to notice a cancellation request.
"""
from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.clock import now_ms
from app.core.errors import NotFound
from app.monitoring.logging import get_logger

log = get_logger("jobs")


class JobCancelled(Exception):
    pass


@dataclass
class Job:
    id: str
    kind: str
    label: str
    status: str = "queued"          # queued | running | completed | failed | cancelled
    progress: float = 0.0
    message: str = ""
    created_at: int = field(default_factory=now_ms)
    started_at: int | None = None
    finished_at: int | None = None
    result: Any = None
    error: str | None = None
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "label": self.label, "status": self.status,
            "progress": round(self.progress, 4), "message": self.message, "created_at": self.created_at,
            "started_at": self.started_at, "finished_at": self.finished_at, "result": self.result,
            "error": self.error, "cancel_requested": self.cancel_requested,
        }


class JobContext:
    def __init__(self, job: Job) -> None:
        self._job = job

    def progress(self, fraction: float, message: str = "") -> None:
        self._job.progress = max(0.0, min(1.0, float(fraction)))
        if message:
            self._job.message = message
        if self._job.cancel_requested:
            raise JobCancelled()

    def check(self) -> None:
        if self._job.cancel_requested:
            raise JobCancelled()

    @property
    def job_id(self) -> str:
        return self._job.id


class JobManager:
    def __init__(self, workers: int = 2, keep: int = 200) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._keep = keep
        self._lock = threading.Lock()

    def submit(self, kind: str, label: str, fn: Callable[[JobContext], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > self._keep:
                old = self._order.pop(0)
                if self._jobs.get(old) and self._jobs[old].status in {"queued", "running"}:
                    self._order.insert(0, old)
                    break
                self._jobs.pop(old, None)
        self._pool.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn: Callable[[JobContext], Any]) -> None:
        if job.cancel_requested:
            job.status, job.finished_at = "cancelled", now_ms()
            return
        job.status, job.started_at = "running", now_ms()
        try:
            job.result = fn(JobContext(job))
            job.status, job.progress = "completed", 1.0
        except JobCancelled:
            job.status = "cancelled"
            job.message = "cancelled"
        except Exception as exc:
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            log.error("job %s (%s) failed: %s", job.id, job.kind, job.error,
                      extra={"context": {"traceback": traceback.format_exc()[-4000:]}})
        finally:
            job.finished_at = now_ms()

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise NotFound(f"job {job_id} not found")
        return job

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock:
            ids = list(reversed(self._order))[:limit]
            return [self._jobs[i] for i in ids if i in self._jobs]

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status in {"queued", "running"}:
            job.cancel_requested = True
        return job

    def running(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status in {"queued", "running"})

    def shutdown(self) -> None:
        with self._lock:
            for job in self._jobs.values():
                if job.status in {"queued", "running"}:
                    job.cancel_requested = True
        self._pool.shutdown(wait=True, cancel_futures=True)
