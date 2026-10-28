"""Security and bounded-failure contracts for the public query-only cache."""

import fcntl
import json
import os
import stat

import pytest

from gallery_dl_web.automation.engine import StopRound
from gallery_dl_web.automation.query_cache import QueryCache, safe_map

OP = "PolarisProfilePostsTabContentQuery_connection"
PUBLIC = {OP: ("123456", ["PolarisIsLoggedIn"])}


@pytest.mark.parametrize(
    "payload",
    [
        {"secret": ("123", [])},
        {OP: ("COOKIE_SECRET", [])},
        {OP: ("123", ["https://private/?token=SECRET"])},
        {OP: ("123", ["PolarisSecret?token=SECRET"])},
        {OP: ("123", ["PolarisX"] * 65)},
    ],
)
def test_reject_non_public_metadata(payload):
    with pytest.raises(ValueError):
        safe_map(payload)


def test_hit_does_not_extend_ttl_and_repairs_permissions(tmp_path):
    path = tmp_path / "query.json"
    cache = QueryCache(path)
    assert cache.resolve(OP, lambda: PUBLIC) == PUBLIC[OP]
    before = path.read_bytes()
    os.chmod(path, 0o666)
    assert cache.resolve(OP, lambda: pytest.fail("must not discover")) == PUBLIC[OP]
    assert path.read_bytes() == before
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_discovery_failure_propagates_once_without_retry(tmp_path):
    calls = []

    def fail():
        calls.append(1)
        raise StopRound("rate_limited")

    with pytest.raises(StopRound, match="rate_limited"):
        QueryCache(tmp_path / "query.json").resolve(OP, fail)
    assert calls == [1]
    assert not (tmp_path / "query.json").exists()


def test_unavailable_cache_does_not_retry_discovery(tmp_path):
    parent = tmp_path / "not_directory"
    parent.write_text("file")
    calls = []
    result = QueryCache(parent / "cache.json").resolve(OP, lambda: calls.append(1) or PUBLIC)
    assert result == PUBLIC[OP] and calls == [1]


def test_busy_lock_is_bounded_and_does_not_leak_descriptors(tmp_path):
    path = tmp_path / "query.json"
    lock = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = len(os.listdir("/proc/self/fd"))
        for _ in range(3):
            with pytest.raises(StopRound, match="query_cache_busy"):
                QueryCache(path).resolve(OP, lambda: pytest.fail("no network while busy"))
        assert len(os.listdir("/proc/self/fd")) == before
    finally:
        os.close(lock)


def test_corrupt_cache_emits_redacted_diagnostic(tmp_path, caplog):
    path = tmp_path / "query.json"
    path.write_text("COOKIE_SECRET")
    QueryCache(path).resolve(OP, lambda: PUBLIC)
    assert "instagram_query_cache_invalid" in caplog.text
    assert "COOKIE_SECRET" not in caplog.text
    assert json.loads(path.read_text())["operations"][OP][0] == "123456"
