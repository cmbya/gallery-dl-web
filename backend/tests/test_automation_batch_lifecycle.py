"""Offline service→engine regressions for unfinished batch ownership."""

import asyncio

import pytest

from gallery_dl_web.automation.engine import Engine, Page, StopRound
from gallery_dl_web.automation.service import AutomationService
from gallery_dl_web.cookies.store import CookieStore
from tests.test_automation import Backend, post, settings


@pytest.mark.parametrize("action", ["pause", "cancel", "restart", "crash"])
@pytest.mark.parametrize("names", [("alice",), ("alice", "bob")])
async def test_last_interrupted_blogger_reuses_remaining_budget(tmp_path, action, names):
    cfg = settings(tmp_path)
    entered = asyncio.Event()
    waiting = True
    service = None

    async def runner(run, sub):
        if waiting and sub["username"] == names[-1]:
            result = Engine(
                service.store, Backend({None: Page([post(i) for i in range(19)], None)})
            ).run(sub, "refresh", run["batch_id"], run["id"])
            entered.set()
            await asyncio.Event().wait()
            return result
        items = [] if waiting else [post(i) for i in range(19, 50)]
        return Engine(service.store, Backend({None: Page(items, None)})).run(
            sub, "refresh", run["batch_id"], run["id"]
        )

    service = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock(), runner)
    for name in names:
        service.store.upsert({"username": name, "refresh_limit": 100})
    try:
        await service.trigger("refresh")
        await entered.wait()
        batch = service.store.batches()[0]["id"]
        assert service.store.remaining(batch) == 1
        if action == "pause":
            await service.pause()
            await service.resume()
        elif action == "cancel":
            await service.cancel(service.current)
        else:
            await service.close()
            if action == "crash":
                # Reproduce a process killed before the service can persist interruption.
                from gallery_dl_web.automation.store import AutomationStore

                store = AutomationStore(service.database)
                run = store.runs()[0]
                store.update_run(run, status="running")
                store.close()
            service = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock(), runner)
        await service.wait_idle()
        waiting = False
        assert (await service.trigger("refresh"))["accepted"] == 1
        await service.wait_idle()
        assert len(service.store.batches()) == 1
        assert service.store.batches()[0]["completed"] == 20
        assert service.store.progress(names[-1])["completed"] == 20
        assert service.store.active_batch("refresh") is None
        # A budget-completed batch must not permanently lock future triggers.
        assert (await service.trigger("refresh"))["accepted"] == len(names)
        await service.wait_idle()
        assert len(service.store.batches()) == 2
        if len(names) > 1:
            newest = service.store.batches()[0]["id"]
            runs = [r for r in reversed(service.store.runs()) if r["batch_id"] == newest]
            assert runs[0]["username"] == "alice"
    finally:
        await service.close()


async def test_cancel_all_remaining_retries_only_unfinished_members(tmp_path):
    cfg = settings(tmp_path)
    entered = asyncio.Event()
    waiting = True
    service = None

    async def runner(run, sub):
        count = 19 if waiting else 30
        result = Engine(
            service.store, Backend({None: Page([post(i) for i in range(count)], None)})
        ).run(sub, "refresh", run["batch_id"], run["id"])
        if waiting:
            entered.set()
            await asyncio.Event().wait()
        return result

    service = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock(), runner)
    for name in ("alice", "bob"):
        service.store.upsert({"username": name, "refresh_limit": 100})
    try:
        await service.trigger("refresh")
        await entered.wait()
        bob = next(r for r in service.store.queued() if r["username"] == "bob")
        await service.cancel(bob["id"])
        await service.cancel(service.current)
        await service.wait_idle()
        waiting = False
        assert (await service.trigger("refresh"))["accepted"] == 2
        await service.wait_idle()
        assert len(service.store.batches()) == 1
        assert service.store.batches()[0]["completed"] == 20
        # Rotation resumes at Bob rather than re-running Alice's prefix first.
        assert service.store.progress("alice")["completed"] == 19
        assert service.store.progress("bob")["completed"] == 1
    finally:
        await service.close()


@pytest.mark.parametrize("tail", [Page([post(99)], None), StopRound("rate_limited")])
async def test_history_global_limit_does_not_request_next_page(tmp_path, tail):
    cfg = settings(tmp_path)
    backend = Backend({None: Page([post(i) for i in range(20)], "next"), "next": tail})
    service = None

    async def runner(run, sub):
        return Engine(service.store, backend).run(sub, "history", run["batch_id"], run["id"])

    service = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock(), runner)
    service.store.upsert({"username": "alice", "history_enabled": True, "history_limit": 100})
    try:
        await service.trigger("history")
        await service.wait_idle()
        assert backend.calls == [None]
        run = service.store.runs()[0]
        assert run["reason"] == "global_post_limit"
        assert run["completed"] == 20
        assert not service.store.snapshot()["paused"]
        assert service.store.cursor("alice") == "next"
        assert service.store.queued_posts("alice") == []
    finally:
        await service.close()


@pytest.mark.parametrize("remove", [False, True])
async def test_unavailable_interrupted_member_does_not_lock_mode(tmp_path, remove):
    cfg = settings(tmp_path)
    service = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock())
    try:
        sub = service.store.upsert({"username": "alice"})
        batch = service.store.new_batch("refresh")
        run = service.store.queue("alice", "refresh", batch)
        service.store.update_run(run, status="cancelled", reason="operator")
        if remove:
            service.store.delete(sub["id"])
        else:
            service.store.upsert({"enabled": False}, sub["id"])
        assert service.store.active_batch("refresh") is None
    finally:
        await service.close()


async def test_normally_completed_batch_allows_new_batch(tmp_path):
    cfg = settings(tmp_path)
    service = None

    async def runner(run, sub):
        return Engine(service.store, Backend({None: Page([], None)})).run(
            sub, "refresh", run["batch_id"], run["id"]
        )

    service = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock(), runner)
    service.store.upsert({"username": "alice"})
    try:
        for _ in range(2):
            assert (await service.trigger("refresh"))["accepted"] == 1
            await service.wait_idle()
        assert len(service.store.batches()) == 2
    finally:
        await service.close()


@pytest.mark.parametrize("username", [None, "", "   "])
async def test_empty_cookie_check_selects_enabled_username(tmp_path, username):
    cfg = settings(tmp_path)
    service = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock())
    service.store.upsert({"username": "aaa", "enabled": False})
    service.store.upsert({"username": "bob"})
    try:
        result = await service.check_cookies(username)
        assert result["tested_username"] == "bob"
        assert service.store.get_state("cookie_check")["tested_username"] == "bob"
    finally:
        await service.close()
