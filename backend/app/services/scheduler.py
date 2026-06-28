"""Scheduler (Phase 8) — runs safe recurring tasks (e.g. periodic news summary).

Backed by APScheduler's BackgroundScheduler to match the codebase's sync
services. Jobs are persisted in the ``scheduled_tasks`` table (our own store,
not APScheduler's job store) so they survive restarts and are visible/editable
from the UI.

Safety (REQUIREMENTS §20.2): a scheduled task must NEVER auto-perform a
dangerous action. Only ``SAFE_KINDS`` may be scheduled; anything else is
rejected at creation. The only kind today is ``news_summary``, which produces a
read-only report and never sends/deletes/modifies anything.

If APScheduler is not installed the manager degrades gracefully: tasks can
still be stored and run on demand (run_now), but nothing fires automatically.
"""

import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime

from sqlalchemy import select

from ..models.database import get_sync_db, ScheduledTask
from ..core.config import settings
from ..core.logging_config import logger
from .news_service import generate_report

# Only these task kinds may be scheduled to run automatically.
SAFE_KINDS = {"news_summary"}


def _parse_schedule(schedule: str) -> Dict[str, Any]:
    """Parse "interval:<seconds>" or "cron:<hour> <minute>" into trigger kwargs.

    Returns {"type": "interval"|"cron", ...kwargs} or raises ValueError.
    """
    schedule = (schedule or "").strip()
    if schedule.startswith("interval:"):
        seconds = int(schedule.split(":", 1)[1])
        seconds = max(seconds, settings.SCHEDULER_MIN_INTERVAL_S)
        return {"type": "interval", "seconds": seconds}
    if schedule.startswith("cron:"):
        rest = schedule.split(":", 1)[1].strip().split()
        if len(rest) != 2:
            raise ValueError("cron schedule must be 'cron:<hour> <minute>'")
        hour, minute = int(rest[0]), int(rest[1])
        return {"type": "cron", "hour": hour, "minute": minute}
    raise ValueError("schedule must start with 'interval:' or 'cron:'")


class SchedulerManager:
    """Singleton scheduler for safe recurring tasks."""

    _instance: Optional["SchedulerManager"] = None

    def __init__(self):
        self._scheduler = None
        self._available = False

    @classmethod
    def get_instance(cls) -> "SchedulerManager":
        if cls._instance is None:
            cls._instance = SchedulerManager()
        return cls._instance

    # ---- lifecycle ----

    def start(self):
        """Start the background scheduler and load persisted enabled tasks."""
        if not settings.SCHEDULER_ENABLED:
            logger.info("Scheduler disabled by config")
            return
        if self._scheduler is not None:
            return
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ModuleNotFoundError:
            logger.warning("APScheduler not installed; scheduled tasks will not fire automatically")
            self._available = False
            return

        self._scheduler = BackgroundScheduler()
        self._scheduler.start()
        self._available = True
        self._load_tasks()
        logger.info("Scheduler started")

    def shutdown(self):
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None

    def _load_tasks(self):
        """Register all enabled tasks with the running scheduler."""
        db = next(get_sync_db())
        try:
            rows = db.execute(
                select(ScheduledTask).where(ScheduledTask.enabled == True)  # noqa: E712
            ).scalars().all()
            for t in rows:
                self._register_job(t.id, t.schedule)
        finally:
            db.close()

    def _register_job(self, task_id: str, schedule: str):
        """Add/replace the APScheduler job for a task."""
        if not self._available or self._scheduler is None:
            return
        try:
            trig = _parse_schedule(schedule)
        except ValueError as e:
            logger.error(f"Cannot register job {task_id}: {e}")
            return
        trigger_type = trig.pop("type")
        self._scheduler.add_job(
            self._run_task,
            trigger=trigger_type,
            args=[task_id],
            id=task_id,
            replace_existing=True,
            **trig,
        )

    def _unregister_job(self, task_id: str):
        if self._available and self._scheduler is not None:
            try:
                self._scheduler.remove_job(task_id)
            except Exception:
                pass

    # ---- execution ----

    def _run_task(self, task_id: str):
        """Execute a task by id. Only safe kinds are dispatched."""
        db = next(get_sync_db())
        try:
            task = db.get(ScheduledTask, task_id)
            if not task or not task.enabled:
                return
            if task.kind not in SAFE_KINDS:
                logger.error(f"Refusing to run unsafe scheduled kind: {task.kind}")
                task.last_status = "error"
                task.last_run_at = datetime.utcnow()
                db.add(task)
                db.commit()
                return

            task.last_status = "running"
            task.last_run_at = datetime.utcnow()
            db.add(task)
            db.commit()

            status = "error"
            try:
                if task.kind == "news_summary":
                    params = task.params_json or {}
                    result = generate_report(
                        query=params.get("query", ""),
                        max_sources=params.get("max_sources"),
                        task_id=task.id,
                        db=db,
                    )
                    status = "success" if result.get("status") == "success" else result.get("status", "error")
            except Exception as e:
                logger.error(f"Scheduled task {task_id} failed: {e}")
                status = "error"

            task.last_status = status
            db.add(task)
            db.commit()
        finally:
            db.close()

    def run_now(self, task_id: str) -> Dict[str, Any]:
        """Run a task immediately (used by the API run-now and for testing)."""
        db = next(get_sync_db())
        try:
            task = db.get(ScheduledTask, task_id)
            if not task:
                return {"status": "error", "error": "Task not found"}
            if task.kind not in SAFE_KINDS:
                return {"status": "error", "error": f"Unsafe task kind: {task.kind}"}
            if task.kind == "news_summary":
                params = task.params_json or {}
                result = generate_report(
                    query=params.get("query", ""),
                    max_sources=params.get("max_sources"),
                    task_id=task.id,
                    db=db,
                )
                task.last_run_at = datetime.utcnow()
                task.last_status = "success" if result.get("status") == "success" else result.get("status", "error")
                db.add(task)
                db.commit()
                return result
            return {"status": "error", "error": f"Unknown task kind: {task.kind}"}
        finally:
            db.close()

    # ---- CRUD ----

    @staticmethod
    def _serialize(t: ScheduledTask) -> Dict[str, Any]:
        return {
            "id": t.id,
            "name": t.name,
            "kind": t.kind,
            "schedule": t.schedule,
            "params": t.params_json or {},
            "enabled": bool(t.enabled),
            "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
            "last_status": t.last_status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }

    def create_task(self, name: str, schedule: str, params: Dict[str, Any],
                    kind: str = "news_summary", enabled: bool = True) -> Dict[str, Any]:
        """Create a scheduled task. Rejects unsafe kinds and bad schedules."""
        if kind not in SAFE_KINDS:
            return {"status": "error", "error": f"Loai tac vu khong duoc phep lap lich: {kind}"}
        try:
            _parse_schedule(schedule)
        except ValueError as e:
            return {"status": "error", "error": str(e)}

        db = next(get_sync_db())
        try:
            task = ScheduledTask(
                id=str(uuid.uuid4()),
                name=name or "Untitled",
                kind=kind,
                schedule=schedule,
                params_json=params or {},
                enabled=enabled,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(task)
            db.commit()
            if enabled:
                self._register_job(task.id, schedule)
            return {"status": "success", "task": self._serialize(task)}
        finally:
            db.close()

    def list_tasks(self) -> List[Dict[str, Any]]:
        db = next(get_sync_db())
        try:
            rows = db.execute(
                select(ScheduledTask).order_by(ScheduledTask.created_at.desc())
            ).scalars().all()
            return [self._serialize(t) for t in rows]
        finally:
            db.close()

    def set_enabled(self, task_id: str, enabled: bool) -> Dict[str, Any]:
        db = next(get_sync_db())
        try:
            task = db.get(ScheduledTask, task_id)
            if not task:
                return {"status": "error", "error": "Task not found"}
            task.enabled = enabled
            task.updated_at = datetime.utcnow()
            db.add(task)
            db.commit()
            if enabled:
                self._register_job(task.id, task.schedule)
            else:
                self._unregister_job(task.id)
            return {"status": "success", "task": self._serialize(task)}
        finally:
            db.close()

    def delete_task(self, task_id: str) -> Dict[str, Any]:
        db = next(get_sync_db())
        try:
            task = db.get(ScheduledTask, task_id)
            if not task:
                return {"status": "error", "error": "Task not found"}
            self._unregister_job(task_id)
            db.delete(task)
            db.commit()
            return {"status": "success", "id": task_id}
        finally:
            db.close()
