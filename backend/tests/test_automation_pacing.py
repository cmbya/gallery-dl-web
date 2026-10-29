import pytest

from gallery_dl_web.automation.engine import GLOBAL_STOPS
from gallery_dl_web.automation.store import AutomationStore


def test_pacing_persisted_independent(tmp_path):
    path = tmp_path / "state.sqlite"
    store = AutomationStore(path)
    assert store.pacing()["refresh"]["request_min"] == 5
    store.set_pacing({"refresh": {"request_min": 7}})
    store.close()
    store = AutomationStore(path)
    assert store.pacing()["refresh"]["request_min"] == 7
    assert store.pacing()["history"]["request_min"] == 5
    store.close()


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "3", None])
def test_invalid_pacing(tmp_path, value):
    store = AutomationStore(tmp_path / "state.sqlite")
    with pytest.raises(ValueError):
        store.set_pacing({"refresh": {"media_interval": value}})
    store.close()


def test_pacing_range_atomic(tmp_path):
    store = AutomationStore(tmp_path / "state.sqlite")
    with pytest.raises(ValueError):
        store.set_pacing({"refresh": {"request_min": 11}})
    assert store.pacing()["refresh"]["request_min"] == 5
    store.close()


@pytest.mark.parametrize(
    "reason", ["redirect_home", "redirect_login", "redirect_challenge", "redirect_unsupported"]
)
def test_suspicious_redirect_pauses(reason):
    assert reason in GLOBAL_STOPS


def test_api_pacing_validation_restart(tmp_path):
    from fastapi.testclient import TestClient

    from gallery_dl_web.api.app import create_app
    from tests.test_automation import settings

    with TestClient(create_app(settings(tmp_path))) as client:
        for body in (
            {"refresh": None},
            {"other": {}},
            {"refresh": {"bad": 1}},
            {"refresh": {"request_min": 20}},
            {"history": {"media_interval": True}},
            {"history": {"media_interval": "3"}},
            {"history": {"media_interval": 1801}},
        ):
            assert client.patch("/api/automation/pacing", json=body).status_code == 422
        assert (
            client.patch(
                "/api/automation/pacing", json={"history": {"media_interval": 4}}
            ).status_code
            == 200
        )
        assert client.get("/api/automation").json()["pacing"]["history"]["media_interval"] == 4
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/api/automation").json()["pacing"]["history"]["media_interval"] == 4


@pytest.mark.parametrize("status,reason", [(429, "rate_limited"), (302, "redirect_login")])
def test_real_downloader_stop_not_swallowed(tmp_path, monkeypatch, status, reason):
    import requests
    from gallery_dl.extractor.instagram import InstagramAPI

    from gallery_dl_web.automation.adapter import GalleryBackend
    from gallery_dl_web.automation.engine import Engine, Page
    from tests.test_automation_adapter import raw_post, response

    monkeypatch.setattr(InstagramAPI, "media", lambda self, code: [raw_post(11)])
    calls = []

    def send(self, request, **kwargs):
        calls.append(request.url)
        assert kwargs["stream"] is True
        result = response({}, status)
        result.url = request.url
        if status == 302:
            result.headers["Location"] = "/accounts/login/"
            # Trusted Instagram media source for redirect classification.
        return result

    monkeypatch.setattr(requests.Session, "send", send)
    backend = GalleryBackend(tmp_path / "downloads", None)
    monkeypatch.setattr(
        InstagramAPI,
        "media",
        lambda self, code: [raw_post(11, "https://www.instagram.com/media.jpg")],
    )
    store = AutomationStore(tmp_path / "state.sqlite")
    sub = store.upsert({"username": "alice"})
    monkeypatch.setattr(backend, "page", lambda username, cursor: Page([raw_post(11)], None))
    result = Engine(store, backend).run(sub, "refresh")
    assert result["reason"] == reason
    assert store.snapshot()["paused"]
    assert len(calls) == 1
    assert not store.is_done("alice", "11", "11")
    store.close()


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.delays = []

    def time(self):
        return self.now

    def sleep(self, delay):
        self.delays.append(delay)
        self.now += delay


def test_actual_session_redirect_child_media_shared_pacer(tmp_path):
    from types import SimpleNamespace

    import requests
    from gallery_dl import config

    from gallery_dl_web.automation.pacing import DEFAULTS, Pacer
    from tests.test_automation_redirects import Transport, guarded

    backend, session, transport = guarded(tmp_path, ["/alice/", None, None])
    clock = Clock()
    backend.pacer = Pacer(
        {**DEFAULTS, "request_min": 5, "request_max": 5, "media_interval": 9},
        clock.time,
        clock.sleep,
    )
    assert config.get(("extractor",), "sleep-request") == 0
    session.get("https://www.instagram.com/alice")
    session.post("https://www.instagram.com/graphql/query")
    child = requests.Session()
    child.mount("https://", Transport([None, None]))
    backend.guard(SimpleNamespace(session=child))
    backend.guard(SimpleNamespace(session=child))  # no double wrapping
    child.get("https://cdn.example/media.jpg", stream=True)
    child.get("https://cdn.example/media2.jpg", stream=True)
    assert clock.delays == [5, 5, 5, 9]
    assert len(transport.calls) == 3
    assert backend.remaining == 5


@pytest.mark.parametrize("reason", sorted(GLOBAL_STOPS))
async def test_global_pause_resume_retains_batch(tmp_path, reason):
    import asyncio

    from gallery_dl_web.automation.service import AutomationService
    from gallery_dl_web.cookies.store import CookieStore
    from tests.test_automation import settings

    cfg = settings(tmp_path)
    calls = []

    async def runner(run, sub):
        calls.append(sub["username"])
        return {"status": "stopped", "reason": reason}

    srv = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock(), runner)
    srv.store.upsert({"username": "alice"})
    srv.store.upsert({"username": "bob"})
    await srv.trigger("refresh")
    await srv.wait_idle()
    batch = srv.store.batches()[0]["id"]
    assert calls == ["alice"]
    assert srv.store.snapshot()["paused"]
    assert (await srv.trigger("refresh"))["accepted"] == 0
    assert srv.store.active_batch("refresh") == batch
    # Stop remaining queued member, then verify the stopped member still owns the budget.
    for run in srv.store.queued():
        srv.store.update_run(run, status="cancelled")
    srv.store.resume()
    assert srv.store.active_batch("refresh") == batch
    srv.kick = lambda: None
    assert (await srv.trigger("refresh"))["accepted"] == 2
    assert all(r["batch_id"] == batch for r in srv.store.queued())
    await srv.close()


async def test_interval_fake_clock_restart_and_lock_release(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from gallery_dl_web.automation import service
    from gallery_dl_web.cookies.store import CookieStore
    from tests.test_automation import settings

    clock = Clock()
    monkeypatch.setattr(service, "time", SimpleNamespace(time=clock.time, monotonic=clock.time))
    real_sleep = asyncio.sleep

    async def sleep(delay):
        assert not srv.instagram_lock.locked()
        clock.sleep(delay)
        await real_sleep(0)

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
        starts.append(clock.now)
        return {"status": "completed"}

    srv = service.AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock(), runner)
    srv.store.set_pacing({"refresh": {"blogger_min": 60, "blogger_max": 60}})
    srv.store.upsert({"username": "alice"})
    await srv.trigger("refresh")
    await srv.wait_idle()
    assert srv.store.get_state("next_run_at") == 1060
    await srv.close()
    srv = service.AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock(), runner)
    await srv.trigger("refresh")
    await srv.wait_idle()
    assert starts == [1000, 1060]
    assert sum(clock.delays) == 60
    await srv.close()


@pytest.mark.parametrize("action", ["cancel", "pause", "close"])
async def test_waiting_interruptible(tmp_path, action):
    import asyncio
    import time

    from gallery_dl_web.automation.service import AutomationService
    from gallery_dl_web.cookies.store import CookieStore
    from tests.test_automation import settings

    cfg = settings(tmp_path)
    calls = []

    async def runner(run, sub):
        calls.append(run)
        return {"status": "completed"}

    srv = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock(), runner)
    srv.store.upsert({"username": "alice"})
    srv.store.set_state("next_run_at", time.time() + 120)
    await srv.trigger("refresh")
    await asyncio.sleep(0)
    assert not srv.instagram_lock.locked()
    assert srv.store.queued()[0]["waiting_reason"] == "blogger_interval"
    if action == "cancel":
        await srv.cancel(srv.store.queued()[0]["id"])
        await asyncio.wait_for(srv.wait_idle(), 1)
    elif action == "pause":
        await asyncio.wait_for(srv.pause(), 1)
        assert srv.store.queued()
    else:
        await asyncio.wait_for(srv.close(), 1)
    assert not calls
    if action != "close":
        await srv.close()


@pytest.mark.parametrize(
    "stage,posts,paused",
    [
        ("identity", "accessible", False),
        ("identity", "not_tested", False),
        ("profile", "error", True),
    ],
)
async def test_cookie_identity_exception(tmp_path, monkeypatch, stage, posts, paused):
    import asyncio

    from gallery_dl_web.automation.service import AutomationService
    from gallery_dl_web.cookies.store import CookieStore
    from tests.test_automation import settings

    cfg = settings(tmp_path)
    cookies = CookieStore(cfg.cookies_path)
    monkeypatch.setattr(cookies, "get_for_platform", lambda _: {"sessionid": "FAKE"})
    srv = AutomationService(cfg, cookies, asyncio.Lock())

    async def subprocess(payload):
        return {
            "login_status": "unknown",
            "posts_status": posts,
            "reason": "redirect_home",
            "diagnostic": {"stage": stage},
        }

    srv._subprocess = subprocess
    await srv.check_cookies("alice")
    assert srv.store.snapshot()["paused"] is paused
    srv.invalidate_cookie_check()
    assert srv.store.snapshot()["paused"] is paused
    await srv.close()
