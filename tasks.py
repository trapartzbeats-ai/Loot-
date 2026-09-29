from __future__ import annotations

import asyncio
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from config import DATA_DIR

_tasks: Dict[str, Dict[str, Any]] = {}
_executor = ThreadPoolExecutor(max_workers=4)


def start_task(func: Callable, *args, **kwargs) -> str:
    """Start a background task. Returns task_id."""
    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = {"status": "running", "result": None, "error": None, "started_at": time.time()}

    def _run():
        try:
            result = func(*args, **kwargs)
            _tasks[task_id]["status"] = "completed"
            _tasks[task_id]["result"] = result
        except Exception as e:
            _tasks[task_id]["status"] = "failed"
            _tasks[task_id]["error"] = str(e)
        finally:
            _tasks[task_id]["completed_at"] = time.time()

    _executor.submit(_run)
    return task_id


def get_task(task_id: str) -> Dict[str, Any]:
    """Get task status and result."""
    task = _tasks.get(task_id)
    if task is None:
        return {"error": "Task not found"}
    result = dict(task)
    if result.get("result"):
        result["result"] = _make_serializable(result["result"])
    return result


def list_tasks() -> Dict[str, Any]:
    """List all tasks."""
    return {"tasks": {tid: {"status": t["status"], "started_at": t.get("started_at")} for tid, t in _tasks.items()}}


def _make_serializable(obj: Any) -> Any:
    """Make an object JSON-serializable."""
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(v) for v in obj]
    return str(obj)
