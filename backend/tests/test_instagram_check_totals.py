"""Offline regressions for reused profile HTML and independent Cookie probes."""

import asyncio
from unittest.mock import Mock

import pytest
import requests

from gallery_dl_web.automation.adapter import GalleryBackend, profile_description_total
from gallery_dl_web.automation.engine import StopRound
from gallery_dl_web.automation.service import AutomationService
from gallery_dl_web.cookies.store import CookieStore
from tests.test_automation import settings


@pytest.mark.parametrize(
    ("url", "description", "expected"),
    [
        ("alice/", "1,200 Followers, 35 Following, 127 Posts - Alice (@alice) on Instagram", 127),
        ("alice/", "0 followers, 0 following, 0 posts - Alice (@ALICE) on Instagram", 0),
        ("alice/", "2 Followers, 3 Following, 1,234 Posts - Alice (@alice)", 1234),
        ("alice/", "2 Followers, 3 Following, 1.2K Posts - Alice (@alice)", None),
        ("alice/", "2 Followers, 3 Following, 12M Posts - Alice (@alice)", None),
        ("alice/", "127 Followers, 35 Following - Alice (@alice)", None),
        ("alice/", "1,23 Posts - Alice (@alice)", None),
        ("alice/", "1.234 Posts - Alice (@alice)", None),
        ("alice/", "2 Followers, 3 Following, 9 Posts - Alice (@alice)", 9),
        ("alice/", "127 Posts - Bob (@bob)", None),
        ("bob/", "127 Posts - Alice (@alice)", None),
        ("accounts/login/", "127 Posts - Alice (@alice)", None),
        ("challenge/", "127 Posts - Alice (@alice)", None),
    ],
)
def test_existing_homepage_response_supplies_only_exact_target_posts(
    tmp_path, monkeypatch, url, description, expected
):
    calls = []

    def send(session, request, **kwargs):
        calls.append(request.url)
        result = requests.Response()
        result.status_code = 200
        result.url = request.url
        result.headers["Content-Type"] = "text/html; charset=utf-8"
        result._content = f'<html><meta name="description" content="{description}"></html>'.encode()
        result._content_consumed = True
        return result

    monkeypatch.setattr(requests.Session, "send", send)
    backend = GalleryBackend(tmp_path, None)
    seen = []
    backend.observe_user = lambda username, count: seen.append((username, count))
    extr = backend.initialize("alice")
    extr.session.get("https://www.instagram.com/" + url)
    assert seen == ([] if expected is None else [("alice", expected)])
    assert len(calls) == 1


@pytest.mark.parametrize(
    "reason", ["redirect_home", "challenge", "rate_limited", "invalid_session"]
)
def test_identity_stop_does_not_skip_posts_unless_security_stop(tmp_path, monkeypatch, reason):
    backend = GalleryBackend(tmp_path, None)
    diagnostic = {"stage": "identity", "classification": reason, "status": 302}
    extr = Mock()
    extr.session.get.side_effect = StopRound(reason, diagnostic)
    monkeypatch.setattr(backend, "initialize", lambda username: extr)
    page = Mock()
    monkeypatch.setattr(backend, "page", page)
    result = backend.check("alice")
    if reason == "redirect_home":
        page.assert_called_once_with("alice", None)
        assert result["login_status"] == "unknown"
        assert result["posts_status"] == "accessible"
        assert result["diagnostic"] == diagnostic
    else:
        page.assert_not_called()
        assert result["reason"] == reason
        assert result["posts_status"] == "unknown"


async def test_blank_cookie_target_selects_enabled_subscription_and_configured_budget(tmp_path):
    cfg = settings(tmp_path)
    cookies = CookieStore(cfg.cookies_path)
    cookies.update(ig_sessionid="offline-fixture")
    service = AutomationService(cfg, cookies, asyncio.Lock())
    service.store.upsert({"username": "disabled", "enabled": False})
    service.store.upsert({"username": "alice"})
    calls = []

    async def check(payload):
        calls.append(payload)
        return {"login_status": "unknown", "posts_status": "accessible"}

    service._subprocess = check
    try:
        result = await service.check_cookies()
        assert calls[0]["username"] == "alice"
        assert calls[0]["request_budget"] == cfg.automation_request_budget
        assert result["tested_username"] == "alice"
    finally:
        await service.close()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('<meta property="og:description" content="7 Posts - A (@alice)">', 7),
        (
            '<meta name="description" content="7 Posts - A (@alice)">'
            '<meta property="og:description" content="7 Posts - A (@alice)">',
            7,
        ),
        (
            '<meta name="description" content="7 Posts - A (@alice)">'
            '<meta property="og:description" content="8 Posts - A (@alice)">',
            None,
        ),
        ("<script>7 Posts - A (@alice)</script>", None),
        ('<meta name="description" content="Log in to Instagram">', None),
    ],
)
def test_profile_meta_is_conservative_about_missing_and_conflicting_totals(body, expected):
    result = requests.Response()
    result.status_code = 200
    result.url = "https://www.instagram.com/alice/"
    result.headers["Content-Type"] = "text/html"
    result._content = body.encode()
    assert profile_description_total(result, "alice") == expected


async def test_service_no_enabled_subscription_keeps_target_absent(tmp_path):
    cfg = settings(tmp_path)
    cookies = CookieStore(cfg.cookies_path)
    cookies.update(ig_sessionid="offline-fixture")
    service = AutomationService(cfg, cookies, asyncio.Lock())
    service.store.upsert({"username": "disabled", "enabled": False})

    async def check(payload):
        assert payload["username"] is None
        return {"login_status": "authenticated", "posts_status": "not_tested"}

    service._subprocess = check
    try:
        result = await service.check_cookies()
        assert result["posts_status"] == "not_tested"
        assert result["tested_username"] is None
        assert service.store.snapshot()["cookie_check"] == result
    finally:
        await service.close()


def test_identity_redirect_diagnostic_is_not_reused_for_posts_failure(tmp_path, monkeypatch):
    backend = GalleryBackend(tmp_path, None)
    extr = Mock()
    extr.session.get.side_effect = StopRound("redirect_home", {"stage": "identity"})
    monkeypatch.setattr(backend, "initialize", lambda username: extr)

    def page(*args):
        assert backend.last_diagnostic is None
        raise StopRound("query_error")

    monkeypatch.setattr(backend, "page", page)
    result = backend.check("alice")
    assert result["reason"] == "query_error"
    assert result["diagnostic"] is None


def test_no_target_is_explicitly_not_tested(tmp_path, monkeypatch):
    backend = GalleryBackend(tmp_path, None)
    extr = Mock()
    extr.session.get.return_value.status_code = 200
    extr.session.get.return_value.json.return_value = {"user": {"pk": "1"}}
    monkeypatch.setattr(backend, "initialize", lambda username: extr)
    assert backend.check(None)["posts_status"] == "not_tested"
