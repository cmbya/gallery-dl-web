"""Private stdin-only worker protocol; stdout contains only sanitized result JSON."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from .adapter import GalleryBackend
from .engine import GLOBAL_STOPS, Engine, StopRound
from .store import AutomationStore


def execute(payload: dict[str, Any]) -> dict[str, Any]:
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        return _execute(payload)
    finally:
        logging.disable(previous)


def _execute(payload: dict[str, Any]) -> dict[str, Any]:
    backend = GalleryBackend(
        Path(payload["downloads"]),
        payload.get("cookies"),
        payload["request_budget"],
        payload["timeout"],
        query_cache_path=Path(payload["database"]).parent / "instagram-query-cache.json",
        pacing=payload.get("pacing"),
    )
    if payload.get("operation") == "check":
        try:
            return backend.check(payload.get("username"))
        except StopRound as exc:
            return {
                "login_status": "login_required" if exc.reason == "invalid_session" else "unknown",
                "posts_status": "restricted" if exc.reason in GLOBAL_STOPS else "unknown",
                "reason": exc.reason,
                "diagnostic": exc.diagnostic,
            }
        except Exception:
            return {"login_status": "error", "posts_status": "unknown", "reason": "check_error"}
    store = AutomationStore(Path(payload["database"]))
    try:
        backend.observe_user = store.observe_platform_total
        return Engine(store, backend, payload["max_pages"], payload["max_posts"]).run(
            payload["subscription"], payload["mode"], payload.get("batch_id"), payload.get("run_id")
        )
    except Exception:
        return {
            "status": "failed",
            "reason": "worker_error",
            "checked": 0,
            "completed": 0,
            "skipped": 0,
            "failed": 1,
        }
    finally:
        store.close()


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        result = execute(payload)
    except Exception:
        result = {"status": "failed", "reason": "worker_error"}
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
