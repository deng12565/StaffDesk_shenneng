"""StaffDeck 后端模块：进程内异步任务队列，为知识、技能、反馈和记忆等后台工作提供统一调度。

主要入口：AsyncJob, AsyncJobQueue, enqueue_async_job, get_async_job_queue, shutdown_async_jobs；主要协作模块：app.db.models。阅读时先从这些入口跟踪调用关系。
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any

from app.db.models import new_id, utc_now


AsyncJobStatus = str


@dataclass
class AsyncJob:
    id: str
    name: str
    status: AsyncJobStatus = "queued"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class AsyncJobQueue:
    def __init__(self, max_workers: int = 4, max_history: int = 500):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ultrarag-job")
        self._lock = Lock()
        self._jobs: dict[str, AsyncJob] = {}
        self._max_history = max_history

    # 阅读提示：提交任务时只登记和排队；真正执行发生在线程池 worker 中。
    def enqueue(
        self,
        name: str,
        func: Callable[..., Any],
        *args: Any,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncJob:
        job = AsyncJob(id=new_id("job"), name=name, metadata=metadata or {})
        with self._lock:
            self._jobs[job.id] = job
            self._trim_history_locked()
        self._executor.submit(self._run_job, job.id, func, args, kwargs)
        return job

    def get(self, job_id: str) -> AsyncJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, limit: int = 100) -> list[AsyncJob]:
        with self._lock:
            rows = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
        return rows[:limit]

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _run_job(
        self,
        job_id: str,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        self._update(job_id, status="running", started_at=utc_now())
        try:
            func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - background jobs must never crash the request path.
            self._update(job_id, status="failed", finished_at=utc_now(), error=str(exc))
            return
        self._update(job_id, status="succeeded", finished_at=utc_now(), error=None)

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for key, value in changes.items():
                setattr(job, key, value)

    def _trim_history_locked(self) -> None:
        overflow = len(self._jobs) - self._max_history
        if overflow <= 0:
            return
        removable = sorted(
            (
                job
                for job in self._jobs.values()
                if job.status in {"succeeded", "failed"}
            ),
            key=lambda item: item.created_at,
        )
        for job in removable[:overflow]:
            self._jobs.pop(job.id, None)


_default_queue = AsyncJobQueue()


# 阅读提示：业务模块统一从这个门面提交后台任务，不直接持有全局队列实例。
def enqueue_async_job(
    name: str,
    func: Callable[..., Any],
    *args: Any,
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> AsyncJob:
    return _default_queue.enqueue(name, func, *args, metadata=metadata, **kwargs)


def get_async_job_queue() -> AsyncJobQueue:
    return _default_queue


def shutdown_async_jobs() -> None:
    _default_queue.shutdown()
