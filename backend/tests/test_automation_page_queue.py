"""History discovers a page once and resumes its safe, durable identifiers offline."""

import json
import sqlite3

import pytest

from gallery_dl_web.automation.engine import Engine, Page, StopRound
from gallery_dl_web.automation.store import AutomationStore


class Backend:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.details = []
        self.fail = set()
        self.stop = None

    def page(self, username, cursor):
        self.calls.append(cursor)
        return self.pages[cursor]

    def download(self, username, post, folder, done, success, generation):
        identifier = str(post.get("id") or post["pk"])
        self.details.append(identifier)
        if self.stop == identifier:
            raise StopRound("request_budget")
        for media in (identifier + "a", identifier + "b"):
            if not done(media) and media not in self.fail:
                success(media)
        return all(done(identifier + suffix) for suffix in ("a", "b"))


def posts(count):
    return [
        {
            "id": str(i),
            "code": f"Code{i}",
            "caption": {"text": f"Title {i}"},
            "image_versions2": {"url": "https://cdn.test/signed?secret=token"},
            "cookie": "sensitive",
        }
        for i in range(count)
    ]


def run(path, backend, **kwargs):
    store = AutomationStore(path)
    sub = store.upsert({"username": "alice", "history_limit": 2})
    result = Engine(store, backend, **kwargs).run(sub, "history")
    store.close()
    return result


def test_page_queue_across_new_instances_and_next_page(tmp_path):
    path = tmp_path / "state.sqlite"
    backend = Backend(
        {None: Page(posts(12), "next"), "next": Page([{"id": "12", "code": "C12"}], None)}
    )
    assert run(path, backend)["completed"] == 2
    assert run(path, backend)["completed"] == 2
    assert backend.calls == [None]
    for _ in range(4):
        assert run(path, backend)["completed"] == 2
    assert backend.calls == [None]
    assert run(path, backend)["completed"] == 1
    assert backend.calls == [None, "next"]
    assert run(path, backend)["reason"] == "exhausted"
    assert backend.calls == [None, "next"]


def test_interrupted_discovery_and_no_raw_payload_persistence(tmp_path):
    path = tmp_path / "state.sqlite"
    backend = Backend({None: Page(posts(4), None)})
    backend.stop = "0"
    assert run(path, backend)["reason"] == "request_budget"
    with sqlite3.connect(path) as db:
        dump = "\n".join(db.iterdump())
    assert "signed" not in dump and "sensitive" not in dump and "image_versions2" not in dump
    backend.stop = None
    assert run(path, backend)["completed"] == 2
    assert run(path, backend)["completed"] == 2
    assert backend.calls == [None]


def test_failed_queue_rotates_without_starvation_and_retries_media(tmp_path):
    path = tmp_path / "state.sqlite"
    backend = Backend({None: Page(posts(4), "next"), "next": Page([], None)})
    backend.fail = {"0b"}
    assert run(path, backend, max_posts=1)["failed"] == 1
    assert run(path, backend)["completed"] == 2
    assert backend.details == ["0", "1", "2"]
    assert run(path, backend)["completed"] == 1
    store = AutomationStore(path)
    assert store.history_status("alice") != "exhausted"
    assert store.is_done("alice", "0", "0a")
    store.close()
    backend.fail.clear()
    assert run(path, backend)["completed"] == 1
    assert backend.calls == [None, "next"]
    assert backend.details.count("0") == 3


def test_refresh_completed_queue_is_skipped_without_detail(tmp_path):
    path = tmp_path / "state.sqlite"
    backend = Backend({None: Page(posts(4), None)})
    run(path, backend)
    store = AutomationStore(path)
    sub = store.upsert({"username": "alice", "refresh_limit": 4})
    Engine(store, backend).run(sub, "refresh")
    details = list(backend.details)
    result = Engine(store, backend).run(sub, "history")
    assert result["skipped"] == 2
    assert backend.details == details
    assert backend.calls == [None, None]
    assert store.history_status("alice") == "exhausted"
    store.close()


def test_old_cursor_replayed_once_and_reposition_preserves_attempted(tmp_path):
    path = tmp_path / "state.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE history(username TEXT PRIMARY KEY,cursor TEXT,"
            "status TEXT NOT NULL DEFAULT 'ready')"
        )
        db.execute("INSERT INTO history VALUES ('alice','old','ready')")
    backend = Backend({"old": Page(posts(5), None), None: Page([], None)})
    run(path, backend)
    run(path, backend)
    assert backend.calls == ["old"]
    store = AutomationStore(path)
    store.remember("alice", "failed", "stable", {"id": "failed", "code": "Failed"})
    store.media_done("alice", "failed", "x")
    store.reset_history("alice")
    assert store.queued_posts("alice") == []
    assert store.pending("alice", 10) == [{"id": "failed", "code": "Failed"}]
    assert store.is_done("alice", "failed", "x")
    assert store.post("alice", "0")["completed"]
    assert store.cursor("alice") is None
    store.close()


def test_atomic_page_save_rolls_back_queue_and_cursor(tmp_path):
    store = AutomationStore(tmp_path / "state.sqlite")
    store.set_cursor("alice", "old")
    with pytest.raises(ValueError, match="identity"):
        store.save_page("alice", [posts(1)[0], {}], "next")
    assert store.cursor("alice") == "old"
    assert store.queued_posts("alice") == []
    assert not store.feed_terminal("alice")
    store.close()


def test_delete_subscription_preserves_discovered_queue_and_folder(tmp_path):
    store = AutomationStore(tmp_path / "state.sqlite")
    sub = store.upsert({"username": "alice"})
    store.save_page("alice", posts(2), None)
    store.remember("alice", "0", "stable-folder", posts(1)[0])
    store.media_done("alice", "0", "0a")
    store.complete("alice", "0")
    store.delete(sub["id"])
    assert len(store.queued_posts("alice")) == 2
    store.upsert({"username": "alice"})
    store.remember("alice", "0", "changed-folder", {"id": "0", "code": "Code0"})
    assert store.post("alice", "0")["folder"] == "stable-folder"
    assert store.post("alice", "0")["completed"]
    assert store.is_done("alice", "0", "0a")
    store.close()


@pytest.mark.parametrize("field", ["payload"])
def test_remember_persists_only_safe_metadata(tmp_path, field):
    store = AutomationStore(tmp_path / "state.sqlite")
    store.remember("alice", "0", "stable", posts(1)[0])
    payload = json.loads(store.post("alice", "0")[field])
    assert payload == {"id": "0", "code": "Code0", "caption": {"text": "Title 0"}}
    store.close()
