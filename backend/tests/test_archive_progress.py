"""Cumulative archive facts, never sums of per-run counters (offline)."""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from gallery_dl.extractor.instagram import InstagramAPI

from gallery_dl_web.api.app import create_app
from gallery_dl_web.automation.adapter import GalleryBackend
from gallery_dl_web.automation.engine import Engine, Page
from gallery_dl_web.automation.store import AutomationStore
from tests.test_automation import Backend, post, settings
from tests.test_automation_adapter import response


def test_unique_complete_posts_shared_across_modes_and_redownload(tmp_path):
    store = AutomationStore(tmp_path / "state.sqlite")
    sub = store.upsert({"username": "alice", "history_enabled": True})
    backend = Backend({None: Page([post(2), post(1)], None)}, fail=["1b"])
    engine = Engine(store, backend)
    engine.run(sub, "refresh")
    assert store.get(sub["id"])["progress"] == {
        "completed": 1,
        "refresh_deferred": 1,
        "history_deferred": 0,
        "platform_total": None,
        "total_updated_at": None,
        "history_complete": False,
    }
    engine.run(sub, "history")
    assert store.progress("alice")["completed"] == 1
    assert not store.progress("alice")["history_complete"]
    backend.fail.clear()
    engine.run(sub, "history")
    assert store.progress("alice")["completed"] == 2
    assert store.progress("alice")["history_complete"]
    engine.run(sub, "refresh")
    assert store.progress("alice")["completed"] == 2
    assert store.redownload("alice", "1")
    assert store.progress("alice")["completed"] == 1
    assert not store.progress("alice")["history_complete"]
    engine.run(sub, "history")
    assert store.progress("alice")["history_complete"]
    store.close()


def test_total_observations_validate_replace_and_do_not_determine_completion(tmp_path):
    path = tmp_path / "state.sqlite"
    store = AutomationStore(path)
    store.observe_platform_total("alice", 44)
    first = store.progress("alice")
    assert first["platform_total"] == 44 and isinstance(first["total_updated_at"], float)
    for value in [True, -1, 1.5, "44", None]:
        store.observe_platform_total("alice", value)
        assert store.progress("alice") == first
    store.observe_platform_total("alice", 0)
    assert store.progress("alice")["platform_total"] == 0
    assert not store.progress("alice")["history_complete"]
    store.remember("alice", "1", "folder", post(1))
    store.complete("alice", "1")
    store.save_page("alice", [post(1)], None)
    assert not store.progress("alice")["history_complete"]  # terminal but local queue
    store.remove_pending("alice", "1")
    assert store.progress("alice")["history_complete"]  # completed > platform total is valid
    store.close()
    store = AutomationStore(path)
    assert store.progress("alice")["platform_total"] == 0
    assert store.progress("alice")["completed"] == 1
    store.close()


def test_additive_migration_and_api_manual_history_toggle(tmp_path):
    cfg = settings(tmp_path)
    path = cfg.data_dir / "automation-v1" / "state.sqlite"
    # Reopening an existing store must not disturb cursor, queue, media, pause or completed posts.
    path.parent.mkdir(parents=True)
    store = AutomationStore(path)
    sub = store.upsert({"username": "alice", "history_enabled": True})
    store.save_page("alice", [post(2)], "next")
    store.remember("alice", "1", "kept", post(1))
    store.complete("alice", "1")
    store.media_done("alice", "1", "1a")
    store.pause("manual")
    store.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE IF EXISTS platform_totals")
    store = AutomationStore(path)
    assert store.cursor("alice") == "next"
    assert store.queued_posts("alice") and store.is_done("alice", "1", "1a")
    assert store.snapshot()["paused"]
    assert store.get(sub["id"])["progress"]["completed"] == 1
    store.close()
    with TestClient(create_app(cfg)) as client:
        data = client.get("/api/automation").json()
        assert data["subscriptions"][0]["progress"]["completed"] == 1
        result = client.patch(
            f"/api/automation/subscriptions/{sub['id']}", json={"history_enabled": False}
        )
        assert result.status_code == 200
        saved = client.get("/api/automation").json()["subscriptions"][0]
        assert not saved["history_enabled"] and saved["refresh_enabled"] and saved["enabled"]
        assert saved["progress"]["completed"] == 1


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"media_count": 44}, 44),
        ({"edge_owner_to_timeline_media": {"count": 44}}, 44),
        ({"media_count": 0}, 0),
        ({"media_count": True}, None),
        ({"media_count": -1}, None),
        ({"media_count": "44"}, None),
        ({"count_media": 44}, None),  # upstream normalization is not reliable
        ({"username": "someone_else", "media_count": 44}, None),
        ({}, None),
    ],
)
def test_real_adapter_observes_existing_user_metadata_without_extra_http(
    tmp_path, monkeypatch, metadata, expected
):
    import requests

    calls = []
    monkeypatch.setattr(
        InstagramAPI,
        "user_by_web",
        lambda self, handle: {"id": "9", "username": handle, **metadata},
    )
    monkeypatch.setattr(
        InstagramAPI, "user_by_id", lambda self, uid: {"id": uid, "username": "alice"}
    )
    monkeypatch.setattr(InstagramAPI, "_extract_fb_tokens", lambda *args: ("lsd", "dtsg"))
    monkeypatch.setattr(InstagramAPI, "_extract_opname_info", lambda *args: ("123", ()))

    def send(self, request, **kwargs):
        calls.append(request)
        return response(
            {
                "data": {
                    "xdt_api__v1__feed__user_timeline_graphql_connection": {
                        "edges": [],
                        "page_info": {"has_next_page": False},
                    }
                }
            }
        )

    monkeypatch.setattr(requests.Session, "send", send)
    store = AutomationStore(tmp_path / "state.sqlite")
    backend = GalleryBackend(tmp_path, None)
    backend.observe_user = store.observe_platform_total
    backend.page("alice", None)
    assert store.progress("alice")["platform_total"] == expected
    assert len(calls) == 1
    store.close()
