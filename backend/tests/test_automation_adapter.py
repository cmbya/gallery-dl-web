"""Production gallery-dl pagination/download exercised offline, not a detached fake engine."""

import json
from types import SimpleNamespace

import pytest
import requests
from gallery_dl import config
from gallery_dl.extractor.instagram import InstagramAPI

from gallery_dl_web.automation.adapter import GalleryBackend, response_problem
from gallery_dl_web.automation.engine import Engine, Page, StopRound
from gallery_dl_web.automation.store import AutomationStore


def response(data, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode()
    result._content_consumed = True
    result.headers["Content-Type"] = "application/json"
    result.url = "https://www.instagram.com/graphql/query"
    return result


def raw_post(i, media_url="https://cdn.test/image.jpg"):
    return {
        "pk": str(i),
        "id": str(i),
        "code": f"Code{i}",
        "caption": {"text": "Title / first\nSecond"},
        "user": {"pk": "9", "username": "alice", "full_name": "Alice"},
        "taken_at": 1700000000,
        "image_versions2": {"candidates": [{"url": media_url, "width": 100, "height": 100}]},
    }


def test_actual_graphql_page_does_not_fetch_next_page(tmp_path, monkeypatch):
    monkeypatch.setattr(InstagramAPI, "user", lambda self, handle: {"username": handle})
    monkeypatch.setattr(InstagramAPI, "_extract_fb_tokens", lambda *args: ("lsd", "dtsg"))
    monkeypatch.setattr(InstagramAPI, "_extract_opname_info", lambda *args: ("123", ()))
    calls = []

    def send(self, request, **kwargs):
        calls.append(request)
        assert kwargs["allow_redirects"] is False
        return response(
            {
                "data": {
                    "xdt_api__v1__feed__user_timeline_graphql_connection": {
                        "edges": [{"node": raw_post(i)} for i in [3, 2]],
                        "page_info": {"has_next_page": True, "end_cursor": "next"},
                    }
                }
            }
        )

    monkeypatch.setattr(requests.Session, "send", send)
    backend = GalleryBackend(tmp_path, None)
    page = backend.page("alice", "start")
    assert len(calls) == 1 and len(page.posts) == 2 and page.next_cursor == "next"
    assert "start" in calls[0].body
    assert config.get(("extractor",), "retries") == 0
    assert backend.extr._cursor is None  # pinned upstream behavior


@pytest.mark.parametrize(
    ("status", "data", "reason"),
    [
        (429, {}, "rate_limited"),
        (302, {}, "redirect_missing_location"),
        (200, {"message": "challenge_required"}, "challenge"),
        (200, {"message": "login_required"}, "invalid_session"),
        (200, {"status": "fail", "message": "Please wait a few minutes"}, "rate_limited"),
    ],
)
def test_transport_stops_before_upstream_retry(tmp_path, monkeypatch, status, data, reason):
    calls = []

    def send(self, request, **kwargs):
        calls.append(request)
        return response(data, status)

    monkeypatch.setattr(requests.Session, "send", send)
    backend = GalleryBackend(tmp_path, None)
    extr = backend.initialize("alice")
    with pytest.raises(StopRound) as caught:
        extr.request("https://www.instagram.com/example")
    assert caught.value.reason == reason
    assert len(calls) == 1


def test_request_budget_cannot_be_bypassed(tmp_path, monkeypatch):
    monkeypatch.setattr(requests.Session, "send", lambda *args, **kwargs: response({}))
    backend = GalleryBackend(tmp_path, None, request_budget=1)
    extr = backend.initialize("alice")
    extr.request("https://www.instagram.com/example")
    with pytest.raises(StopRound, match="request_budget"):
        extr.request("https://www.instagram.com/example")


def test_real_downloadjob_partial_archive_and_deleted_file(tmp_path, monkeypatch):
    import io

    monkeypatch.setattr(InstagramAPI, "media", lambda self, code: [raw_post(11)])
    calls = []

    def send(self, request, **kwargs):
        calls.append(request.url)
        r = response({})
        r.url = request.url
        r.status_code = 200
        r.headers["Content-Type"] = "image/jpeg"
        r.headers["Content-Length"] = "4"
        from urllib3.response import HTTPResponse

        r._content = False
        r._content_consumed = False
        r.raw = HTTPResponse(body=io.BytesIO(b"jpeg"), preload_content=False)
        return r

    monkeypatch.setattr(requests.Session, "send", send)
    backend = GalleryBackend(tmp_path / "downloads", None)
    store = AutomationStore(tmp_path / "state.sqlite")
    sub = store.upsert({"username": "alice", "refresh_limit": 1})
    monkeypatch.setattr(backend, "page", lambda username, cursor: Page([raw_post(11)], None))
    result = Engine(store, backend).run(sub, "refresh")
    assert result["completed"] == 1
    files = list((tmp_path / "downloads").rglob("*.jpg"))
    assert len(files) == 1 and files[0].read_bytes() == b"jpeg"
    assert files[0].name == "11.jpg"
    files[0].unlink()
    assert Engine(store, backend).run(sub, "refresh")["skipped"] == 1
    assert len(calls) == 1
    assert store.redownload("alice", "11")
    assert Engine(store, backend).run(sub, "refresh")["completed"] == 1
    assert list((tmp_path / "downloads").rglob("11_redownload1.jpg"))
    store.close()


def test_streamed_media_does_not_parse_json():
    media = SimpleNamespace(status_code=200, headers={"Content-Type": "image/jpeg"})
    media.json = lambda: (_ for _ in ()).throw(AssertionError("media must not be buffered"))
    assert response_problem(media) is None
