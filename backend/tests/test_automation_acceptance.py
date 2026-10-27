"""Further acceptance cases, written before their regression fixes."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gallery_dl_web.api.app import create_app
from gallery_dl_web.automation.engine import Engine, Page, StopRound
from gallery_dl_web.automation.service import AutomationService
from gallery_dl_web.automation.store import AutomationStore
from gallery_dl_web.cookies.store import CookieStore
from gallery_dl_web.profiles.store import ProfileStore
from tests.test_automation import Backend, post, settings


def test_exhausted_history_retries_pending_without_rescan(tmp_path):
    store = AutomationStore(tmp_path / "state.sqlite")
    sub = store.upsert({"username": "alice", "history_enabled": True, "history_limit": 3})
    backend = Backend({None: Page([post(2), post(1)], None)}, fail=["2b"])
    engine = Engine(store, backend)
    first = engine.run(sub, "history")
    assert first["completed"] == 1 and first["failed"] == 1
    assert store.history_status("alice") == "exhausted"
    backend.fail.clear()
    second = engine.run(sub, "history")
    assert second["completed"] == 1 and backend.calls == [None]
    store.close()


@pytest.mark.parametrize("reason", ["rate_limited", "challenge", "invalid_session", "redirect"])
def test_engine_global_stop_classification(tmp_path, reason):
    store = AutomationStore(tmp_path / "state.sqlite")
    sub = store.upsert({"username": "alice"})
    result = Engine(store, Backend({None: StopRound(reason)})).run(sub, "refresh")
    assert result["reason"] == reason
    assert store.snapshot()["paused"] == (reason != "redirect")
    store.close()


async def test_cancel_queued_and_active_and_manual_lock(tmp_path):
    cfg = settings(tmp_path)
    lock = asyncio.Lock()
    await lock.acquire()
    gate = asyncio.Event()

    async def runner(run, subscription):
        await gate.wait()
        return {"status": "completed"}

    service = AutomationService(cfg, CookieStore(cfg.cookies_path), lock, runner)
    service.store.upsert({"username": "alice"})
    service.store.upsert({"username": "bob"})
    await service.trigger("refresh")
    await asyncio.sleep(0)
    assert all(run["status"] == "queued" for run in service.store.runs())
    queued = service.store.runs()[0]
    await service.cancel(queued["id"])
    lock.release()
    await asyncio.sleep(0)
    assert service.current
    await service.cancel(service.current)
    await service.wait_idle()
    assert all(run["status"] == "cancelled" for run in service.store.runs())
    with pytest.raises(ValueError):
        await service.cancel(queued["id"])
    await service.close()


async def test_cookie_check_identity_posts_and_lock(tmp_path):
    cfg = settings(tmp_path)
    cookies = CookieStore(cfg.cookies_path)
    cookies.update(ig_sessionid="offline-fixture")
    lock = asyncio.Lock()
    await lock.acquire()
    service = AutomationService(cfg, cookies, lock)
    calls = []

    async def check(payload):
        calls.append(payload)
        return {
            "login_status": "authenticated",
            "posts_status": "restricted",
            "reason": "rate_limited",
        }

    service._subprocess = check
    task = asyncio.create_task(service.check_cookies("Alice"))
    await asyncio.sleep(0)
    assert calls == []
    lock.release()
    result = await task
    assert result["login_status"] == "authenticated" and result["posts_status"] == "restricted"
    assert calls[0]["username"] == "alice" and calls[0]["request_budget"] <= 4
    assert service.store.snapshot()["paused"]
    cookies.update(ig_sessionid="replacement")
    assert service.store.snapshot()["cookie_check"] is None
    await service.close()


async def test_successful_service_reconciles_nested_media(tmp_path):
    cfg = settings(tmp_path)
    folder = cfg.downloads_dir / "instagram" / "alice" / "Title_Code"
    folder.mkdir(parents=True)
    Image.new("RGB", (12, 12)).save(folder / "101.jpg")
    profiles = ProfileStore(cfg)
    await profiles.reconcile("instagram", "alice")

    async def runner(run, subscription):
        Image.new("RGB", (12, 12)).save(folder / "102.jpg")
        return {"status": "completed", "completed": 1}

    service = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock(), runner)
    service.store.upsert({"username": "alice"})
    await service.trigger("refresh")
    await service.wait_idle()
    assert profiles.load("instagram", "alice")["image_count"] == 2
    await service.close()


def test_recover_run_and_validation(tmp_path):
    store = AutomationStore(tmp_path / "state.sqlite")
    sub = store.upsert({"username": "alice"})
    run = store.queue("alice", "history")
    store.update_run(run, status="running")
    store.recover()
    assert store.runs()[0]["status"] == "interrupted"
    with pytest.raises(ValueError):
        store.upsert({"username": "bob"}, sub["id"])
    with pytest.raises(ValueError):
        store.upsert({"username": "bob", "history_limit": 1001})
    assert store.queue("alice", "history")
    store.close()


def test_api_errors_and_redownload(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        sub = client.post(
            "/api/automation/subscriptions", json={"username": "alice", "enabled": False}
        ).json()
        root = "/api/automation/subscriptions/" + sub["id"]
        assert client.patch(root, json={"enabled": None}).status_code == 422
        assert (
            client.post("/api/automation/subscriptions", json={"username": "../bad"}).status_code
            == 422
        )
        assert (
            client.post(
                "/api/automation/subscriptions", json={"username": "bob", "refresh_limit": 1001}
            ).status_code
            == 422
        )
        assert client.patch(root, json={"username": "bob"}).status_code == 422
        assert client.patch("/api/automation/subscriptions/missing", json={}).status_code == 404
        assert client.delete("/api/automation/subscriptions/missing").status_code == 404
        assert client.post("/api/automation/trigger", json={"mode": "bad"}).status_code == 422
        assert (
            client.post(
                "/api/automation/trigger", json={"mode": "history", "subscription_id": "missing"}
            ).status_code
            == 404
        )
        assert client.post("/api/automation/runs/missing/cancel", json={}).status_code == 404
        assert client.post(root + "/redownload", json={"post_id": "missing"}).status_code == 404
        app.state.automation.store.remember("alice", "1", "Title_1", post(1))
        app.state.automation.store.media_done("alice", "1", "1a")
        assert client.post(root + "/redownload", json={"post_id": "1"}).json()["reset"]
        assert (
            client.post(
                "/api/automation/cookies/check", json={"username": "https://evil.test/a"}
            ).status_code
            == 422
        )
        assert (
            client.post("/api/automation/trigger", json={"mode": "refresh"}).json()["accepted"] == 0
        )
        body = json.dumps(client.get("/api/automation").json())
        assert str(tmp_path) not in body and "sessionid" not in body
