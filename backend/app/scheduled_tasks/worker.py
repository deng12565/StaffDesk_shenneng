"""StaffDeck 后端模块：定时任务后台 worker，周期扫描到期任务并管理线程生命周期。

主要入口：run_worker, start_background_worker, stop_background_worker, main；主要协作模块：app.db、app.db.seed、app.scheduled_tasks.service。阅读时先从这些入口跟踪调用关系。
"""

from __future__ import annotations

import argparse
import signal
import threading
from time import sleep

from sqlmodel import Session

from app.db import engine, init_db
from app.db.seed import seed_demo_data
from app.scheduled_tasks.service import WORKER_SLEEP_SECONDS, due_scheduled_tasks, execute_scheduled_task


_stopped = False
_background_thread: threading.Thread | None = None


def _handle_stop(_signum: int, _frame: object) -> None:
    global _stopped
    _stopped = True


def run_worker(*, once: bool = False, poll_seconds: float = WORKER_SLEEP_SECONDS) -> None:
    init_db()
    with Session(engine) as db:
        seed_demo_data(db)
    while not _stopped:
        with Session(engine) as db:
            due = due_scheduled_tasks(db)
            for task in due:
                execute_scheduled_task(db, task)
        if once:
            return
        sleep(max(1.0, poll_seconds))


def start_background_worker(*, poll_seconds: float = WORKER_SLEEP_SECONDS) -> None:
    global _background_thread, _stopped
    if _background_thread and _background_thread.is_alive():
        return
    _stopped = False
    _background_thread = threading.Thread(
        target=run_worker,
        kwargs={"once": False, "poll_seconds": poll_seconds},
        name="ultrarag-scheduled-task-worker",
        daemon=True,
    )
    _background_thread.start()


def stop_background_worker() -> None:
    global _stopped
    _stopped = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run StaffDeck scheduled task worker")
    parser.add_argument("--once", action="store_true", help="scan and execute due tasks once, then exit")
    parser.add_argument("--poll-seconds", type=float, default=WORKER_SLEEP_SECONDS)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    run_worker(once=args.once, poll_seconds=args.poll_seconds)


if __name__ == "__main__":
    main()
