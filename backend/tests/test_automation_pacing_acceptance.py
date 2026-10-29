"""Offline acceptance of scheduling races and real worker sleep cancellation."""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from gallery_dl_web.automation import service
from gallery_dl_web.cookies.store import CookieStore
from tests.test_automation import settings


@pytest.mark.parametrize("rollback", [False, True])
async def test_wall_clock_rollback_has_bounded_monotonic_wait(tmp_path, monkeypatch, rollback):
    wall, mono = [1000.0], [0.0]
    delays = []
    real_sleep = asyncio.sleep

    async def sleep(delay):
        delays.append(delay)
        mono[0] += delay
        wall[0] -= 100 if rollback else 0
        await real_sleep(0)

    monkeypatch.setattr(
        service, "time", SimpleNamespace(time=lambda: wall[0], monotonic=lambda: mono[0])
    )
    monkeypatch.setattr(
        service,
        "asyncio",
        SimpleNamespace(
            **{k: getattr(asyncio, k) for k in dir(asyncio) if k != "sleep"}, sleep=sleep
        ),
    )
    cfg = settings(tmp_path)
    starts = []

    async def runner(run, sub):
        starts.append(mono[0])
        return {"status": "completed"}

    srv = service.AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock(), runner)
    try:
        srv.store.upsert({"username": "alice"})
        srv.store.set_state("next_run_at", 100000 if not rollback else 1060)
        await srv.trigger("refresh")
        await srv.wait_idle()
        assert starts == [60 if rollback else 1800]
        assert sum(delays) == starts[0]
    finally:
        await srv.close()


@pytest.mark.parametrize("change", ["disable", "mode_disable", "delete", "budget"])
async def test_revalidate_after_waiting_for_instagram_lock(tmp_path, change):
    cfg = settings(tmp_path)
    calls = []

    async def runner(run, sub):
        calls.append(run)
        return {"status": "completed"}

    lock = asyncio.Lock()
    srv = service.AutomationService(cfg, CookieStore(cfg.cookies_path), lock, runner)
    try:
        sub = srv.store.upsert({"username": "alice"})
        await lock.acquire()
        await srv.trigger("refresh")
        await asyncio.sleep(0)
        assert not srv.task.done() and srv.current is None
        run = srv.store.queued()[0]
        if change == "delete":
            srv.store.delete(sub["id"])
        elif change == "budget":
            with srv.store.db:
                srv.store.db.execute(
                    "UPDATE batches SET post_limit=0 WHERE id=?", (run["batch_id"],)
                )
            assert srv.store.remaining(run["batch_id"]) == 0
        else:
            srv.store.upsert(
                {"enabled" if change == "disable" else "refresh_enabled": False}, sub["id"]
            )
        lock.release()
        await asyncio.wait_for(srv.wait_idle(), 1)
        assert calls == []
        result = srv.store.runs()[0]
        assert result["reason"] == (
            "global_post_limit" if change == "budget" else "subscription_disabled_or_deleted"
        )
    finally:
        if lock.locked():
            lock.release()
        await srv.close()


async def test_cookie_pause_cancels_lock_waiter_without_deadlock(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    cookies = CookieStore(cfg.cookies_path)
    monkeypatch.setattr(cookies, "get_for_platform", lambda _: {"sessionid": "OFFLINE"})
    lock = asyncio.Lock()
    srv = service.AutomationService(cfg, cookies, lock)
    entered, release = asyncio.Event(), asyncio.Event()

    async def check(payload):
        assert lock.locked()
        entered.set()
        await release.wait()
        return {"reason": "rate_limited"}

    monkeypatch.setattr(srv, "_subprocess", check)
    try:
        srv.store.upsert({"username": "alice"})
        check_task = asyncio.create_task(srv.check_cookies("alice"))
        await entered.wait()
        await srv.trigger("refresh")
        await asyncio.sleep(0)
        waiter = srv.task
        assert waiter is not None and not waiter.done() and srv.current is None
        release.set()
        await asyncio.wait_for(check_task, 1)
        assert waiter.cancelled()
        assert not lock.locked()
        assert srv.store.snapshot()["paused"]
        assert srv.store.queued()
    finally:
        release.set()
        await srv.close()


@pytest.mark.parametrize("action", ["pause", "cancel", "close"])
async def test_real_worker_pacer_sleep_is_terminated(tmp_path, monkeypatch, action):
    # sitecustomize is only injected into this test's child interpreter. Keep the
    # real worker/Engine/Pacer/process lifecycle; replace page transport offline.
    hook = tmp_path / "hooks"
    hook.mkdir()
    marker = tmp_path / "sleeping"
    hook.joinpath("sitecustomize.py").write_text("""
import os
import socket
import time
from pathlib import Path
from gallery_dl_web.automation.adapter import GalleryBackend
from gallery_dl_web.automation.pacing import Pacer

def blocked(*args, **kwargs):
    raise AssertionError("network forbidden in worker acceptance test")
socket.socket.connect = blocked
socket.create_connection = blocked

def page(self, username, cursor):
    def sleep(delay):
        Path(os.environ["PACING_SLEEP_MARKER"]).write_text(str(delay))
        time.sleep(delay)
    self.pacer.sleep = sleep
    self.pacer.send()
    self.pacer.send()
    raise AssertionError("sleep must be interrupted")
GalleryBackend.page = page
""")
    from pathlib import Path

    monkeypatch.setenv("PYTHONPATH", str(hook) + ":" + str(Path(service.__file__).parents[2]))
    monkeypatch.setenv("PACING_SLEEP_MARKER", str(marker))
    cfg = settings(tmp_path)
    cfg.worker_python = sys.executable
    srv = service.AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock())
    closed = False
    try:
        srv.store.set_pacing({"refresh": {"request_min": 120, "request_max": 120}})
        srv.store.upsert({"username": "alice"})
        await srv.trigger("refresh")
        async with asyncio.timeout(10):
            while not marker.exists():
                assert srv.task is not None and not srv.task.done()
                await asyncio.sleep(0.01)
        assert 119 < float(marker.read_text()) <= 120
        proc = srv.process
        assert proc is not None and proc.returncode is None
        run_id = srv.current
        if action == "cancel":
            await asyncio.wait_for(srv.cancel(run_id), 5)
        elif action == "pause":
            await asyncio.wait_for(srv.pause(), 5)
        else:
            await asyncio.wait_for(srv.close(), 5)
            closed = True
        assert proc.returncode is not None
        assert srv.process is None
        assert not srv.instagram_lock.locked()
        if not closed:
            assert srv.store.runs()[0]["status"] == "cancelled"
    finally:
        if not closed:
            await srv.close()


@pytest.mark.parametrize("body", [[], [1], "text", 12, True, None, {"refresh": []}])
def test_pacing_api_rejects_non_mapping_shapes(tmp_path, body):
    import json

    from fastapi.testclient import TestClient

    from gallery_dl_web.api.app import create_app

    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.patch(
            "/api/automation/pacing",
            content=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422


def test_pause_timestamp_persisted_and_cleared(tmp_path):
    from gallery_dl_web.automation.store import AutomationStore

    path = tmp_path / "state.sqlite"
    store = AutomationStore(path)
    store.pause("rate_limited")
    timestamp = store.snapshot()["paused_at"]
    assert isinstance(timestamp, (int, float)) and timestamp > 0
    store.close()
    store = AutomationStore(path)
    assert store.snapshot()["paused_at"] == timestamp
    store.resume()
    assert store.snapshot()["paused_at"] is None
    store.close()
