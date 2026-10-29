"""Offline acceptance tests exercise the production scheduler and durable engine."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from gallery_dl_web.api.app import create_app
from gallery_dl_web.automation.engine import Engine, Page, StopRound
from gallery_dl_web.automation.service import AutomationService
from gallery_dl_web.automation.store import AutomationStore, normalize_username, post_folder
from gallery_dl_web.config import Settings
from gallery_dl_web.cookies.store import CookieStore


def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "data",
        downloads_dir=tmp_path / "downloads",
        cookies_path=tmp_path / "cookies.json",
    )


def test_normalization_and_names():
    assert normalize_username("https://www.instagram.com/Some.User/?x=1") == "some.user"
    assert normalize_username("@Some.User") == "some.user"
    for value in [
        "../foo",
        "https://evil.test/foo",
        "https://instagram.com/p/code/",
        "a/b",
        "",
        "a" * 31,
    ]:
        with pytest.raises(ValueError):
            normalize_username(value)
    folder = post_folder("\n hello/世界:*\nsecond", "Ab_c-1")
    assert folder.endswith("_Ab_c-1") and "/" not in folder
    assert len(post_folder("界" * 400, "123").encode()) <= 180


def test_durable_config_and_archive(tmp_path):
    store = AutomationStore(tmp_path / "state.sqlite")
    sub = store.upsert({"username": "@Alice", "history_enabled": True})
    assert sub["username"] == "alice"
    assert store.upsert({"username": "ALICE"})["id"] == sub["id"]
    store.media_done("alice", "1", "m1")
    store.pause("rate_limited")
    store.close()
    store = AutomationStore(tmp_path / "state.sqlite")
    assert store.is_done("alice", "1", "m1")
    assert store.snapshot()["paused"]
    store.delete(sub["id"])
    assert store.is_done("alice", "1", "m1")
    assert store.redownload("alice", "1")
    assert not store.is_done("alice", "1", "m1")
    assert not store.redownload("alice", "missing")
    store.close()


class Backend:
    def __init__(self, pages, fail=()):
        self.pages = pages
        self.calls = []
        self.downloads = []
        self.fail = set(fail)

    def page(self, username, cursor):
        self.calls.append(cursor)
        value = self.pages[cursor]
        if isinstance(value, Exception):
            raise value
        return value

    def download(self, username, post, folder, done, success, generation):
        # Like the real adapter, resolve fresh detail from safe identifiers.
        media_ids = [f"{post['id']}a", f"{post['id']}b"]
        for media in media_ids:
            if not done(media):
                self.downloads.append(media)
                if media not in self.fail:
                    success(media)
        return all(done(media) for media in media_ids)


def post(i, pinned=False):
    return {
        "id": str(i),
        "code": f"code{i}",
        "caption": {"text": "Title"},
        "media": [f"{i}a", f"{i}b"],
        "timeline_pinned_user_ids": [1] if pinned else [],
    }


def test_history_queues_current_page_and_partial_media(tmp_path):
    store = AutomationStore(tmp_path / "state.sqlite")
    sub = store.upsert({"username": "alice", "history_enabled": True, "history_limit": 1})
    backend = Backend(
        {None: Page([post(3), post(2)], "next"), "next": Page([post(1)], None)}, fail=["3b"]
    )
    engine = Engine(store, backend, max_pages=4, max_posts=20)
    first = engine.run(sub, "history")
    assert first["completed"] == 1 and first["failed"] == 1
    assert store.cursor("alice") == "next"  # suffix/failures are durable locally
    backend.fail.clear()
    second = engine.run(sub, "history")
    assert second["completed"] == 1
    assert backend.downloads.count("3a") == 1
    assert backend.downloads.count("3b") == 2
    third = engine.run(sub, "history")
    assert third["completed"] == 1
    assert store.history_status("alice") == "exhausted"
    assert backend.calls == [None, "next"]  # queue/retry needs no boundary page
    store.close()


def test_refresh_pins_do_not_consume_newest_budget(tmp_path):
    store = AutomationStore(tmp_path / "state.sqlite")
    sub = store.upsert({"username": "alice", "refresh_limit": 1})
    backend = Backend({None: Page([post(1, True), post(9), post(8)], None)})
    result = Engine(store, backend).run(sub, "refresh")
    assert result["completed"] == 2  # pinned checked separately, plus newest one
    assert "8a" not in backend.downloads
    store.close()


def test_expired_cursor_no_rescan_and_scan_budget(tmp_path):
    store = AutomationStore(tmp_path / "state.sqlite")
    sub = store.upsert({"username": "alice", "history_enabled": True})
    store.set_cursor("alice", "expired")
    backend = Backend({"expired": StopRound("needs_reposition")})
    result = Engine(store, backend).run(sub, "history")
    assert result["reason"] == "needs_reposition"
    assert backend.calls == ["expired"]
    assert store.history_status("alice") == "needs_reposition"
    store.set_cursor("alice", None)
    backend = Backend({None: Page([post(1)], "next"), "next": Page([post(2)], "last")})
    result = Engine(store, backend, max_pages=1).run(sub, "history")
    assert result["reason"] == "scan_budget"
    assert backend.calls == [None]
    store.close()


async def test_serial_duplicate_pause_and_restart(tmp_path):
    cfg = settings(tmp_path)
    cookies = CookieStore(cfg.cookies_path)
    calls = []
    gate = asyncio.Event()

    async def runner(run, subscription):
        calls.append(run["id"])
        await gate.wait()
        return {
            "status": "failed",
            "reason": "rate_limited",
            "checked": 0,
            "completed": 0,
            "skipped": 0,
            "failed": 0,
        }

    service = AutomationService(cfg, cookies, asyncio.Lock(), runner=runner)
    service.store.upsert({"username": "alice"})
    service.store.upsert({"username": "bob"})
    assert (await service.trigger("refresh"))["accepted"] == 2
    assert (await service.trigger("refresh"))["accepted"] == 0
    await asyncio.sleep(0)
    gate.set()
    await service.wait_idle()
    assert len(calls) == 1
    assert service.store.snapshot()["paused"]
    assert (await service.trigger("refresh"))["paused"]
    await service.close()
    service = AutomationService(cfg, cookies, asyncio.Lock(), runner=runner)
    assert service.store.snapshot()["paused"]
    await service.resume()
    await service.wait_idle()
    assert len(calls) == 2
    await service.close()


def test_api_contract_and_cookie_invalidation(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        initial = client.get("/api/automation").json()
        assert set(initial) == {
            "subscriptions",
            "paused",
            "pause_reason",
            "runs",
            "cookie_check",
            "global_limits",
            "pacing",
            "next_run_at",
            "batches",
        }
        result = client.post(
            "/api/automation/subscriptions",
            json={
                "username": "https://instagram.com/Alice/",
                "enabled": True,
                "refresh_enabled": True,
                "refresh_limit": 3,
                "history_enabled": True,
                "history_limit": 2,
            },
        )
        assert result.status_code == 200
        sub = result.json()
        assert sub["username"] == "alice"
        assert (
            client.patch(
                "/api/automation/subscriptions/" + sub["id"], json={"refresh_limit": 0}
            ).status_code
            == 422
        )
        assert (
            client.patch(
                "/api/automation/subscriptions/" + sub["id"], json={"enabled": False}
            ).json()["enabled"]
            is False
        )
        assert client.post("/api/automation/pause", json={}).status_code == 200
        assert client.post("/api/automation/trigger", json={"mode": "refresh"}).json()["paused"]
        assert client.post("/api/automation/resume", json={}).status_code == 200
        check = client.post("/api/automation/cookies/check", json={})
        assert check.json()["login_status"] == "login_required"
        assert client.post("/api/automation/cookies/check", json={}).status_code == 429
        app.state.cookie_store.update(ig_sessionid="not-real-no-network")
        assert client.get("/api/automation").json()["cookie_check"] is None
        assert client.delete("/api/automation/subscriptions/" + sub["id"]).status_code == 200
        assert client.get("/api/automation").json()["subscriptions"] == []
