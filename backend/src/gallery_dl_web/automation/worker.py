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
    logging.disable(logging.CRITICAL)
    backend = GalleryBackend(
        Path(payload["downloads"]),
        payload.get("cookies"),
        payload["request_budget"],
        payload["timeout"],
    )
    if payload.get("operation") == "check":
        try:
            return backend.check(payload.get("username"))
        except StopRound as exc:
            return {
                "login_status": "login_required" if exc.reason == "invalid_session" else "unknown",
                "posts_status": "restricted" if exc.reason in GLOBAL_STOPS else "unknown",
                "reason": exc.reason,
            }
        except Exception:
            return {"login_status": "error", "posts_status": "unknown", "reason": "check_error"}
    store = AutomationStore(Path(payload["database"]))
    try:
        return Engine(store, backend, payload["max_pages"], payload["max_posts"]).run(
            payload["subscription"], payload["mode"]
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
