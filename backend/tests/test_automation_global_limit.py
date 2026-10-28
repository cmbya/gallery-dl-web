"""Global budgets exercise the service -> worker -> durable Engine path offline."""

import asyncio
import json

import pytest

from gallery_dl_web.automation import worker
from gallery_dl_web.automation.engine import Page
from gallery_dl_web.automation.service import AutomationService
from gallery_dl_web.cookies.store import CookieStore
from tests.test_automation import Backend, post, settings


def wire(tmp_path, monkeypatch, feeds):
    cfg = settings(tmp_path)
    backends = []

    class OfflineBackend(Backend):
        def __init__(self, *args, **kwargs):
            super().__init__({})
            backends.append(self)

        def page(self, username, cursor):
            self.calls.append((username, cursor))
            return Page(feeds[username], None)

    monkeypatch.setattr(worker, "GalleryBackend", OfflineBackend)
    service = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock())

    async def subprocess(payload):
        return worker.execute(payload)

    service._subprocess = subprocess
    return service, backends


@pytest.mark.parametrize("count,expected,pending", [(19, 19, 0), (22, 20, 2)])
async def test_round_caps_successful_posts_and_durably_defers(
    tmp_path, monkeypatch, count, expected, pending
):
    feeds = {"alice": [post(i) for i in range(count)]}
    service, backends = wire(tmp_path, monkeypatch, feeds)
    service.store.upsert({"username": "alice", "refresh_limit": 30})
    await service.trigger("refresh")
    await service.wait_idle()
    assert service.store.progress("alice")["completed"] == expected
    assert len(service.store.refresh_posts("alice")) == pending
    assert len(backends[0].downloads) == expected * 2  # files aren't the budget unit
    assert service.store.batches()[0]["completed"] == expected
    if pending:
        assert service.store.runs()[0]["reason"] == "global_post_limit"
        await service.close()
        service, _ = wire(tmp_path, monkeypatch, {"alice": [post(100)]})
        await service.trigger("refresh")
        await service.wait_idle()
        assert service.store.progress("alice")["completed"] == count + 1
        assert service.store.post("alice", str(count - 1))["completed"]
    await service.close()


async def test_multi_blogger_rotation_duplicates_and_repeat_trigger(tmp_path, monkeypatch):
    service, _ = wire(
        tmp_path,
        monkeypatch,
        {"alice": [post(i) for i in range(22)], "bob": [post(i) for i in range(22)]},
    )
    for name in ("alice", "bob"):
        service.store.upsert({"username": name, "refresh_limit": 30})
    await service.trigger("refresh")
    assert (await service.trigger("refresh"))["accepted"] == 0
    await service.wait_idle()
    assert sum(s["progress"]["completed"] for s in service.store.subscriptions()) == 20
    await service.trigger("refresh")
    await service.wait_idle()
    assert service.store.progress("bob")["completed"] == 20  # persistent round-robin
    await service.trigger("refresh")
    await service.wait_idle()
    assert sum(s["progress"]["completed"] for s in service.store.subscriptions()) == 44
    assert service.store.batches()[0]["completed"] == 4  # archive duplicates free
    await service.close()


async def test_history_independent_limit_and_pins_cannot_bypass(tmp_path, monkeypatch):
    service, _ = wire(tmp_path, monkeypatch, {"alice": [post(i, pinned=True) for i in range(22)]})
    service.store.set_limits({"refresh": 2, "history": 3})
    service.store.upsert({"username": "alice", "history_enabled": True, "history_limit": 30})
    await service.trigger("refresh")
    await service.wait_idle()
    assert service.store.progress("alice")["completed"] == 2
    await service.trigger("history")
    await service.wait_idle()
    assert service.store.progress("alice")["completed"] == 5
    assert service.store.batches()[0]["completed"] == 3
    assert "media" not in json.dumps(service.store.refresh_posts("alice"))
    await service.close()
