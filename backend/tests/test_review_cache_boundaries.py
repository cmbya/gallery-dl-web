"""Offline boundary regressions from the query-cache/count review."""

import asyncio
import html
import json
from unittest.mock import Mock

import pytest
from gallery_dl.extractor.instagram import QUERIES

from gallery_dl_web.automation.adapter import profile_description_total
from gallery_dl_web.automation.query_cache import MAX_BYTES, VERSION, QueryCache, safe_map
from gallery_dl_web.automation.service import AutomationService
from gallery_dl_web.cookies.store import CookieStore
from tests.test_automation import settings

OP = "PolarisProfilePostsTabContentQuery_connection"
PUBLIC = {OP: ("123456", ["PolarisIsLoggedIn"])}


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("1,200 Followers, 35 Following - Alice (@alice) on Instagram: 999 Posts about art", None),
        ("Alice (@alice) on Instagram: 999 Posts about art", None),
        ("About 999 Posts - Alice (@alice)", None),
        ("1,200 Followers, 35 Following, 1.2K Posts - Alice (@alice): 999 Posts", None),
        ("1,200 Followers, 35 Following, 1,23 Posts - Alice (@alice)", None),
        ("1,200 Followers, 35 Following, 44 Posts - Alice (@alice) on Instagram: art", 44),
        ("1.2K Followers, 35 Following, 44 Posts - Alice (@alice) on Instagram", 44),
        ("1,200 Followers, 35 Following, 1,234 Posts - Alice (@alice)", 1234),
        ("0 Followers, 0 Following, 0 Posts - Alice (@ALICE)", 0),
        ("7 Posts - Alice (@alice)", 7),
    ],
)
def test_profile_count_must_be_in_statistics_prefix(description, expected):
    response = Mock(
        status_code=200,
        url="https://www.instagram.com/alice/",
        headers={"Content-Type": "text/html"},
        text=f'<meta name="description" content="{html.escape(description, quote=True)}">',
    )
    assert profile_description_total(response, "alice") == expected


def test_deep_json_is_invalid_and_discovers_once_without_logging_contents(tmp_path, caplog):
    path = tmp_path / "cache.json"
    path.write_text("[" * 20000 + "0" + "]" * 20000)
    discover = Mock(return_value=PUBLIC)
    cache = QueryCache(path)
    assert cache.resolve(OP, discover) == PUBLIC[OP]
    discover.assert_called_once_with()
    assert "instagram_query_cache_invalid" in caplog.text
    assert "[[[[" not in caplog.text
    assert cache.resolve(OP, lambda: pytest.fail("must hit repaired cache")) == PUBLIC[OP]


def oversized_map():
    return {name: ("1" * 32, ["Polaris" + "X" * 160] * 64) for name in list(QUERIES)[:7]}


@pytest.mark.parametrize("existing", [False, True])
def test_oversized_safe_map_is_used_without_persisting_or_overwriting(tmp_path, caplog, existing):
    path = tmp_path / "cache.json"
    cache = QueryCache(path)
    operations = safe_map(oversized_map())
    assert len(json.dumps(operations, separators=(",", ":")).encode()) > MAX_BYTES
    before = None
    if existing:
        other = next(name for name in QUERIES if name != OP)
        cache.write({other: ("123", None)})
        before = path.read_bytes()
    discover = Mock(return_value=operations)
    assert cache.resolve(OP, discover) == operations[OP]
    discover.assert_called_once_with()
    assert (path.read_bytes() if path.exists() else None) == before
    assert "instagram_query_cache_too_large" in caplog.text
    assert not list(tmp_path.glob(".instagram-query-*"))


@pytest.mark.parametrize("padding", [0, 1])
def test_cache_read_enforces_exact_byte_limit(tmp_path, padding):
    path = tmp_path / "cache.json"
    cache = QueryCache(path)
    cache.write(PUBLIC)
    record = path.read_bytes()
    path.write_bytes(record + b" " * (MAX_BYTES - len(record) + padding))
    assert cache.read() == (PUBLIC if padding == 0 else None)


def test_cache_write_enforces_serialized_byte_limit(tmp_path, monkeypatch):
    from gallery_dl_web.automation import query_cache

    monkeypatch.setattr(query_cache.time, "time", lambda: 1000.0)
    record = json.dumps(
        {"version": VERSION, "expires": 1000.0 + query_cache.TTL, "operations": PUBLIC},
        separators=(",", ":"),
    ).encode()
    path = tmp_path / "cache.json"
    cache = QueryCache(path)
    monkeypatch.setattr(query_cache, "MAX_BYTES", len(record))
    cache.write(PUBLIC)
    assert path.read_bytes() == record
    assert cache.read() == PUBLIC
    path.unlink()
    monkeypatch.setattr(query_cache, "MAX_BYTES", len(record) - 1)
    cache.write(PUBLIC)
    assert not path.exists()


@pytest.mark.parametrize("username", ["", " ", "\t\n"])
async def test_explicit_blank_cookie_username_selects_enabled_subscription(tmp_path, username):
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
        result = await service.check_cookies(username)
        assert calls[0]["username"] == "alice"
        assert result["tested_username"] == "alice"
    finally:
        await service.close()
