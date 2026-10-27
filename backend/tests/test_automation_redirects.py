"""Real Requests redirect preparation with an offline adapter; no Instagram traffic."""

import io
import json
from types import SimpleNamespace

import pytest
import requests
from requests.adapters import BaseAdapter
from urllib3.response import HTTPResponse

from gallery_dl_web.automation.adapter import GalleryBackend
from gallery_dl_web.automation.engine import StopRound


class Transport(BaseAdapter):
    def __init__(self, locations):
        self.locations = iter(locations)
        self.calls = []

    def send(self, request, **kwargs):
        self.calls.append(request)
        location = next(self.locations)
        response = requests.Response()
        response.status_code = 200 if location is None else 302
        response.headers["Content-Type"] = "application/json"
        if location is not None:
            response.headers["Location"] = location
        response.url = request.url
        response.request = request
        response.raw = HTTPResponse(body=io.BytesIO(b"{}"), preload_content=False)
        return response

    def close(self):
        pass


def guarded(tmp_path, locations, budget=10):
    session = requests.Session()
    session.trust_env = False
    session.cookies.set("sessionid", "TOP_SECRET", domain=".instagram.com")
    session.headers["Authorization"] = "Bearer TOP_SECRET"
    transport = Transport(locations)
    session.mount("https://", transport)
    session.mount("http://", transport)
    backend = GalleryBackend(tmp_path, None, request_budget=budget)
    backend.guard(SimpleNamespace(session=session))
    return backend, session, transport


def test_real_requests_canonical_redirect_then_graphql(tmp_path):
    backend, session, transport = guarded(tmp_path, ["/private_user/", None, None])
    assert session.get("https://www.instagram.com/private_user").status_code == 200
    assert session.post("https://www.instagram.com/graphql/query", data="secret").status_code == 200
    assert len(transport.calls) == 3
    assert backend.remaining == 7
    assert transport.calls[1].url == "https://www.instagram.com/private_user/"


@pytest.mark.parametrize(
    ("location", "reason"),
    [
        ("/", "redirect_home"),
        ("/accounts/login/?next=SECRET", "redirect_login"),
        ("/challenge/123/?secret=SECRET", "redirect_challenge"),
        ("/checkpoint/123/", "redirect_challenge"),
        ("https://evil.test/private_user/", "redirect_external"),
        ("http://www.instagram.com/private_user/", "redirect_insecure"),
        ("https://www.instagram.com:444/private_user/", "redirect_external"),
        ("https://user:password@www.instagram.com/private_user/", "redirect_invalid"),
        ("/other_user/", "redirect_unsupported"),
        ("/private_user/?secret=SECRET", "redirect_unsupported"),
        ("", "redirect_missing_location"),
    ],
)
def test_redirect_stops_before_any_destination_transport(tmp_path, location, reason):
    _, session, transport = guarded(tmp_path, [location])
    with pytest.raises(StopRound) as caught:
        session.get("https://www.instagram.com/private_user")
    assert caught.value.reason == reason
    assert len(transport.calls) == 1
    diagnostic = caught.value.diagnostic
    assert diagnostic["stage"] == "profile"
    assert diagnostic["status"] == 302
    serialized = json.dumps(diagnostic)
    for secret in ["private_user", "SECRET", "password", "evil.test", "123"]:
        assert secret not in serialized


def test_redirect_loop_and_global_budget(tmp_path):
    _, session, transport = guarded(tmp_path, ["/private_user/", "/private_user"])
    with pytest.raises(StopRound) as caught:
        session.get("https://www.instagram.com/private_user")
    assert caught.value.reason == "redirect_loop"
    assert len(transport.calls) == 2
    _, session, transport = guarded(tmp_path, ["/private_user/"], budget=1)
    with pytest.raises(StopRound) as caught:
        session.get("https://www.instagram.com/private_user")
    assert caught.value.reason == "request_budget"
    assert len(transport.calls) == 1


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_graphql_canonical_preserves_requests_method_rules(tmp_path, status):
    _, session, transport = guarded(tmp_path, ["/graphql/query/", None])
    original = transport.send

    def send(request, **kwargs):
        response = original(request, **kwargs)
        if len(transport.calls) == 1:
            response.status_code = status
        return response

    transport.send = send
    session.post("https://www.instagram.com/graphql/query", data="SECRET_BODY")
    follow = transport.calls[1]
    assert follow.method == ("POST" if status in {307, 308} else "GET")
    assert follow.body == ("SECRET_BODY" if status in {307, 308} else None)


async def test_worker_engine_service_snapshot_diagnostic(tmp_path, monkeypatch):
    import asyncio

    from gallery_dl_web.automation import worker
    from gallery_dl_web.automation.service import AutomationService
    from gallery_dl_web.cookies.store import CookieStore
    from tests.test_automation import settings

    class OfflineBackend(GalleryBackend):
        def page(self, username, cursor):
            _, session, _ = guarded(tmp_path, ["/accounts/login/?next=SECRET"])
            session.get("https://www.instagram.com/private_user")

    monkeypatch.setattr(worker, "GalleryBackend", OfflineBackend)
    cfg = settings(tmp_path)

    async def runner(run, sub):
        return worker.execute(
            {
                "downloads": str(tmp_path),
                "database": str(tmp_path / "worker.sqlite"),
                "request_budget": 4,
                "timeout": 20,
                "max_pages": 1,
                "max_posts": 10,
                "mode": run["mode"],
                "subscription": sub,
            }
        )

    service = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock(), runner)
    service.store.upsert({"username": "alice", "refresh_limit": 1})
    await service.trigger("refresh")
    await service.task
    run = service.store.snapshot()["runs"][0]
    assert run["reason"] == "redirect_login"
    assert run["diagnostic"] == {
        "stage": "profile",
        "target": "login",
        "status": 302,
        "classification": "redirect_login",
    }
    assert "SECRET" not in json.dumps(run)
    await service.close()


async def test_cookie_worker_service_snapshot_diagnostic(tmp_path, monkeypatch):
    import asyncio

    from gallery_dl_web.automation import worker
    from gallery_dl_web.automation.service import AutomationService
    from gallery_dl_web.cookies.store import CookieStore
    from tests.test_automation import settings

    class OfflineBackend(GalleryBackend):
        def initialize(self, username):
            _, session, _ = guarded(tmp_path, ["/"])
            return SimpleNamespace(session=session)

    monkeypatch.setattr(worker, "GalleryBackend", OfflineBackend)
    cfg = settings(tmp_path)
    cookies = CookieStore(cfg.cookies_path)
    monkeypatch.setattr(cookies, "get_for_platform", lambda platform: {"sessionid": "FAKE"})
    service = AutomationService(cfg, cookies, asyncio.Lock())

    async def subprocess(payload):
        return worker.execute(payload)

    monkeypatch.setattr(service, "_subprocess", subprocess)
    result = await service.check_cookies(None)
    assert result["reason"] == "redirect_home"
    assert result["diagnostic"]["stage"] == "identity"
    assert result["diagnostic"]["target"] == "home"
    assert result["login_status"] == "unknown"
    assert service.store.snapshot()["cookie_check"] == result
    await service.close()


def test_finite_hop_limit(tmp_path):
    _, session, transport = guarded(
        tmp_path, ["/private_user/", "/private_user//", "/private_user///", "/private_user////"]
    )
    with pytest.raises(StopRound) as caught:
        session.get("https://www.instagram.com/private_user")
    assert caught.value.reason == "redirect_limit"
    assert len(transport.calls) == 4
