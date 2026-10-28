"""Budget persistence and API validation regressions, authored before fixes."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from gallery_dl_web.api.app import create_app
from gallery_dl_web.automation.engine import Engine, Page
from gallery_dl_web.automation.service import AutomationService
from gallery_dl_web.automation.store import AutomationStore
from gallery_dl_web.cookies.store import CookieStore
from tests.test_automation import Backend, post, settings


def test_global_limit_api_validation_and_readback(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/api/automation").json()["global_limits"] == {
            "refresh": 20,
            "history": 20,
        }
        for bad in (0, -1, True, 1.5, None, 1001):
            assert client.patch("/api/automation/limits", json={"refresh": bad}).status_code == 422
        assert client.patch("/api/automation/limits", json={"other": 2}).status_code == 422
        assert client.patch("/api/automation/limits", json={"refresh": 7}).json() == {
            "refresh": 7,
            "history": 20,
        }
        assert client.get("/api/automation").json()["global_limits"]["refresh"] == 7
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/api/automation").json()["global_limits"]["refresh"] == 7


def test_recovery_uses_persistent_success_not_lost_worker_response(tmp_path):
    path = tmp_path / "state.sqlite"
    store = AutomationStore(path)
    sub = store.upsert({"username": "alice", "refresh_limit": 30})
    batch = store.new_batch("refresh")
    run = store.queue("alice", "refresh", batch)
    store.update_run(run, status="running")

    class Broken(Backend):
        def download(self, username, item, *args):
            if item["id"] == "19":
                raise RuntimeError("worker disappeared after nineteen finalized posts")
            return super().download(username, item, *args)

    with pytest.raises(RuntimeError):
        Engine(store, Broken({None: Page([post(i) for i in range(22)], None)})).run(
            sub, "refresh", batch, run["id"]
        )
    store.close()
    store = AutomationStore(path)
    store.recover()
    assert store.runs()[0]["completed"] == 19
    assert store.remaining(batch) == 1
    result = Engine(store, Backend({None: Page([], None)})).run(sub, "refresh", batch, "retry")
    assert result["completed"] == 1
    assert store.remaining(batch) == 0
    assert len(store.refresh_posts("alice")) == 2
    store.close()


@pytest.mark.parametrize("action", ["cancel", "pause", "restart"])
async def test_cancel_finalized_prefix_keeps_batch_budget_for_queued_blogger(tmp_path, action):
    cfg = settings(tmp_path)
    entered = asyncio.Event()
    service = None

    async def runner(run, sub):
        if sub["username"] == "alice":
            Engine(service.store, Backend({None: Page([post(i) for i in range(19)], None)})).run(
                sub, "refresh", run["batch_id"], run["id"]
            )
            entered.set()
            await asyncio.Event().wait()
        return Engine(service.store, Backend({None: Page([post(i) for i in range(4)], None)})).run(
            sub, "refresh", run["batch_id"], run["id"]
        )

    service = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock(), runner)
    for name in ("alice", "bob"):
        service.store.upsert({"username": name, "refresh_limit": 30})
    await service.trigger("refresh")
    await entered.wait()
    if action == "pause":
        await service.pause()
        assert (await service.trigger("refresh"))["paused"]
        await service.resume()
    elif action == "restart":
        await service.close()
        service = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock(), runner)
        assert (await service.trigger("refresh"))["accepted"] == 0
        service.kick()
    else:
        await service.cancel(service.current)
    await service.wait_idle()
    assert service.store.progress("alice")["completed"] == 19
    assert service.store.progress("bob")["completed"] == 1
    assert service.store.batches()[0]["completed"] == 20
    assert next(r for r in service.store.runs() if r["username"] == "alice")["completed"] == 19
    await service.close()


def test_failed_refresh_prefix_rotates_without_starving_deferred_suffix(tmp_path):
    store = AutomationStore(tmp_path / "state.sqlite")
    sub = store.upsert({"username": "alice", "refresh_limit": 1})
    store.save_refresh("alice", [post(1), post(2)])
    backend = Backend({None: Page([], None)}, fail=["1b"])
    first = Engine(store, backend).run(sub, "refresh")
    assert first["failed"] == 1
    second = Engine(store, backend).run(sub, "refresh")
    assert second["completed"] == 1
    assert store.post("alice", "2")["completed"]
    store.close()


def test_partial_failure_does_not_spend_success_budget_and_scans_stay_bounded(tmp_path):
    store = AutomationStore(tmp_path / "state.sqlite")
    sub = store.upsert({"username": "alice", "refresh_limit": 30})
    store.set_limits({"refresh": 2})
    batch = store.new_batch("refresh")
    backend = Backend({None: Page([post(i) for i in range(10)], None)}, fail=["0b"])
    result = Engine(store, backend, max_posts=4).run(sub, "refresh", batch, "fixture")
    assert result["completed"] == 2 and result["failed"] == 1
    assert result["reason"] == "global_post_limit"
    assert store.remaining(batch) == 0
    assert len(backend.calls) == 1
    store.close()


def test_deferred_refresh_respects_original_per_blogger_limit(tmp_path):
    store = AutomationStore(tmp_path / "state.sqlite")
    sub = store.upsert({"username": "alice", "refresh_limit": 3})
    store.save_refresh("alice", [post(1), post(2)])
    result = Engine(store, Backend({None: Page([post(i) for i in range(10, 20)], None)})).run(
        sub, "refresh"
    )
    assert result["completed"] == 3
    store.close()
