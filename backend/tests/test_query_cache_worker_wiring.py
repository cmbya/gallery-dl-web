from pathlib import Path

import pytest

from gallery_dl_web.automation import worker


@pytest.mark.parametrize("operation", ["check", "run"])
def test_worker_uses_shared_app_state_query_cache(tmp_path, monkeypatch, operation):
    captured = {}

    class Backend:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def check(self, username):
            return {"login_status": "unknown"}

    class Engine:
        def __init__(self, *args):
            pass

        def run(self, *args):
            return {"status": "completed"}

    monkeypatch.setattr(worker, "GalleryBackend", Backend)
    monkeypatch.setattr(worker, "Engine", Engine)
    database = tmp_path / "state" / "state.sqlite"
    worker.execute(
        {
            "operation": operation,
            "database": str(database),
            "downloads": str(tmp_path / "downloads"),
            "request_budget": 100,
            "timeout": 30,
            "max_pages": 2,
            "max_posts": 20,
            "subscription": {},
            "mode": "refresh",
        }
    )
    assert captured.get("query_cache_path") == Path(database).parent / "instagram-query-cache.json"
