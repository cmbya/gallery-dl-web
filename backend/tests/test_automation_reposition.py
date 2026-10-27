"""Explicit history reposition is local, guarded and archive-preserving."""

import pytest
from fastapi.testclient import TestClient

from gallery_dl_web.api.app import create_app
from tests.test_automation import settings


@pytest.mark.parametrize("status", ["needs_reposition", "exhausted", "ready"])
def test_reposition_only_resets_history(tmp_path, status):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        srv = app.state.automation
        sub = srv.store.upsert({"username": "alice", "history_enabled": True})
        srv.store.set_cursor("alice", "expired", status)
        srv.store.set_cursor("bob", "other", "exhausted")
        srv.store.remember("alice", "1", "stable-folder", {"id": "1"})
        srv.store.media_done("alice", "1", "m1")
        srv.store.complete("alice", "1")
        before_post = srv.store.post("alice", "1")
        before_snapshot = srv.store.snapshot()
        media = tmp_path / "existing.jpg"
        media.write_bytes(b"original")
        url = f"/api/automation/subscriptions/{sub['id']}/reposition"
        for _ in range(2):
            response = client.post(url, json={})
            assert response.status_code == 200
            assert response.json() == {"reset": True}
            assert srv.store.cursor("alice") is None
            assert srv.store.history_status("alice") == "ready"
        assert srv.store.cursor("bob") == "other"
        assert srv.store.history_status("bob") == "exhausted"
        assert srv.store.post("alice", "1") == before_post
        assert srv.store.is_done("alice", "1", "m1")
        assert media.read_bytes() == b"original"
        after_snapshot = srv.store.snapshot()
        before_snapshot["subscriptions"][0]["history_status"] = "ready"
        assert after_snapshot == before_snapshot


@pytest.mark.parametrize("blocker", ["lock", "queued", "running", "old_queued"])
def test_reposition_conflicts_leave_cursor_unchanged(tmp_path, blocker):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        srv = app.state.automation
        sub = srv.store.upsert({"username": "alice"})
        srv.store.set_cursor("alice", "expired", "needs_reposition")
        if blocker == "lock":
            client.portal.call(srv.instagram_lock.acquire)
        else:
            run = srv.store.queue("alice", "refresh")
            if blocker == "running":
                srv.store.update_run(run, status="running")
            if blocker == "old_queued":
                for index in range(201):
                    other = srv.store.queue(f"other{index}", "history")
                    srv.store.update_run(other, status="completed")
        try:
            response = client.post(f"/api/automation/subscriptions/{sub['id']}/reposition", json={})
            assert response.status_code == 409
            assert srv.store.cursor("alice") == "expired"
            assert srv.store.history_status("alice") == "needs_reposition"
        finally:
            if blocker == "lock":
                srv.instagram_lock.release()


def test_reposition_validation_and_other_users(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        srv = app.state.automation
        sub = srv.store.upsert({"username": "alice"})
        url = f"/api/automation/subscriptions/{sub['id']}/reposition"
        assert client.post(url, json={"cursor": "injected"}).status_code == 422
        assert client.post(url, json=None).status_code == 422
        assert (
            client.post("/api/automation/subscriptions/missing/reposition", json={}).status_code
            == 404
        )
        srv.store.queue("bob", "history")
        assert client.post(url, json={}).status_code == 200
