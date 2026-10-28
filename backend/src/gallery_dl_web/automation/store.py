"""Isolated automation namespace; never imports or deletes legacy archives/files."""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def normalize_username(value: str) -> str:
    value = value.strip()
    if "://" in value:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or url.hostname not in {"instagram.com", "www.instagram.com"}
            or url.port
            or url.username
        ):
            raise ValueError("expected an Instagram profile URL")
        value = url.path.strip("/")
    else:
        value = value.removeprefix("@")
    value = value.lower()
    if (
        not re.fullmatch(r"[a-z0-9_][a-z0-9_.]{0,29}", value)
        or value in {"p", "reel", "reels", "stories", "explore", "accounts", "direct"}
        or ".." in value
    ):
        raise ValueError(
            "expected an Instagram username (1–30 letters, numbers, dots or underscores)"
        )
    return value


def safe_component(value: str, limit: int = 160) -> str:
    value = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|{}]', "_", value).strip(" .")
    return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore").rstrip(" .") or "post"


def post_folder(title: str, post_id: str) -> str:
    title = next((line.strip() for line in title.splitlines() if line.strip()), "post")
    identifier = safe_component(post_id, 64)
    return safe_component(title, 179 - len(identifier.encode())) + "_" + identifier


def post_metadata(post: dict[str, Any]) -> dict[str, Any]:
    """Allowlist durable identity/title only; never retain feed responses or CDN URLs."""
    result: dict[str, Any] = {key: str(post[key]) for key in ("id", "pk", "code") if post.get(key)}
    if post.get("is_pinned") or post.get("timeline_pinned_user_ids"):
        result["is_pinned"] = True
    caption = post.get("caption")
    title = caption.get("text", "") if isinstance(caption, dict) else caption
    if title:
        result["caption"] = {"text": str(title)}
    return result


class AutomationStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS batches(
            id TEXT PRIMARY KEY,mode TEXT NOT NULL,post_limit INTEGER NOT NULL,created_at REAL);
        CREATE TABLE IF NOT EXISTS batch_posts(
            batch_id TEXT,run_id TEXT,username TEXT,post_id TEXT,
            PRIMARY KEY(batch_id,username,post_id));
        CREATE TABLE IF NOT EXISTS refresh_pending(
            position INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT NOT NULL,
            post_id TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(username,post_id));
        CREATE TABLE IF NOT EXISTS subscriptions(
            id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,config TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS history(
            username TEXT PRIMARY KEY,cursor TEXT,status TEXT NOT NULL DEFAULT 'ready');
        CREATE TABLE IF NOT EXISTS platform_totals(
            username TEXT PRIMARY KEY,total INTEGER NOT NULL,observed_at REAL NOT NULL,
            scope TEXT NOT NULL CHECK(scope='platform_approximate'));
        CREATE TABLE IF NOT EXISTS history_feed(
            username TEXT PRIMARY KEY,terminal INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS history_pending(
            position INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT NOT NULL,
            post_id TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(username,post_id));
        CREATE TABLE IF NOT EXISTS posts(
            username TEXT,post_id TEXT,folder TEXT NOT NULL,payload TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,generation INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(username,post_id));
        CREATE TABLE IF NOT EXISTS media(
            username TEXT,post_id TEXT,media_id TEXT,PRIMARY KEY(username,post_id,media_id));
        CREATE TABLE IF NOT EXISTS runs(
            id TEXT PRIMARY KEY,username TEXT,mode TEXT,status TEXT,payload TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS active_run ON runs(username,mode)
            WHERE status IN ('queued','running');
        """)
        self.db.commit()

    def limits(self) -> dict[str, int]:
        value = self.get_state("global_limits", {"refresh": 20, "history": 20})
        return {"refresh": int(value["refresh"]), "history": int(value["history"])}

    def set_limits(self, fields: dict[str, int], cap: int = 1000) -> dict[str, int]:
        if any(
            k not in {"refresh", "history"} or type(v) is not int or not 1 <= v <= cap
            for k, v in fields.items()
        ):
            raise ValueError(f"global limits must be integers between 1 and {cap}")
        value = {**self.limits(), **fields}
        self.set_state("global_limits", value)
        return value

    def batches(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT b.*,COUNT(p.post_id) AS completed FROM batches b LEFT JOIN batch_posts p "
                "ON p.batch_id=b.id GROUP BY b.id ORDER BY b.rowid DESC LIMIT 200"
            )
        ]

    def batch_runs(self, batch_id: str) -> dict[str, dict[str, Any]]:
        """Latest attempt per member, without the UI's 200-run retention window."""
        result = {}
        for row in self.db.execute("SELECT payload FROM runs ORDER BY rowid"):
            run = json.loads(row[0])
            if run.get("batch_id") == batch_id:
                result[run["username"]] = run
        return result

    def active_batch(self, mode: str) -> str | None:
        # Persist ownership even when the last member was cancelled or interrupted.
        # Falling back to the latest batch also upgrades databases created before this key.
        latest = self.db.execute(
            "SELECT id FROM batches WHERE mode=? ORDER BY rowid DESC LIMIT 1", (mode,)
        ).fetchone()
        identifier = self.get_state(f"active_batch_{mode}", latest[0] if latest else None)
        if identifier:
            runs = self.batch_runs(identifier).values()
            eligible = {
                sub["username"]
                for sub in self.subscriptions()
                if sub["enabled"]
                and sub[f"{mode}_enabled"]
                and (mode != "history" or sub["history_status"] != "needs_reposition")
            }
            if any(r["status"] in {"queued", "running"} for r in runs) or (
                self.remaining(identifier) > 0
                and any(
                    r["status"] in {"cancelled", "interrupted"} and r["username"] in eligible
                    for r in runs
                )
            ):
                return str(identifier)
            self.set_state(f"active_batch_{mode}", None)
        return None

    def new_batch(self, mode: str) -> str:
        identifier = uuid.uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO batches VALUES (?,?,?,?)",
                (identifier, mode, self.limits()[mode], time.time()),
            )
            self.db.execute(
                "INSERT OR REPLACE INTO state VALUES (?,?)",
                (f"active_batch_{mode}", json.dumps(identifier)),
            )
        return identifier

    def remaining(self, batch_id: str | None) -> int:
        if batch_id is None:
            return 2147483647
        row = self.db.execute(
            "SELECT post_limit-(SELECT COUNT(*) FROM batch_posts WHERE batch_id=?) "
            "FROM batches WHERE id=?",
            (batch_id, batch_id),
        ).fetchone()
        return int(row[0]) if row else 0

    def run_completed(self, run_id: str) -> int:
        return int(
            self.db.execute(
                "SELECT COUNT(*) FROM batch_posts WHERE run_id=?", (run_id,)
            ).fetchone()[0]
        )

    def refresh_posts(self, username: str) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT payload FROM refresh_pending WHERE username=? ORDER BY position",
                (username,),
            )
        ]

    def save_refresh(self, username: str, posts: list[dict[str, Any]]) -> None:
        with self.db:
            for post in posts:
                identifier = str(post.get("pk") or post.get("id") or post.get("code") or "")
                if not identifier:
                    raise ValueError("post identity missing")
                if not (self.post(username, identifier) or {}).get("completed"):
                    self.db.execute(
                        "INSERT OR IGNORE INTO refresh_pending(username,post_id,payload) "
                        "VALUES (?,?,?)",
                        (username, identifier, json.dumps(post_metadata(post))),
                    )

    def rotate_refresh(self, username: str, post_id: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE refresh_pending SET position=(SELECT COALESCE(MAX(position),0)+1 "
                "FROM refresh_pending) WHERE username=? AND post_id=?",
                (username, post_id),
            )

    def remove_refresh(self, username: str, post_id: str) -> None:
        with self.db:
            self.db.execute(
                "DELETE FROM refresh_pending WHERE username=? AND post_id=?", (username, post_id)
            )

    def close(self) -> None:
        self.db.close()

    def get_state(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key: str, value: Any) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))

    def pause(self, reason: str) -> None:
        self.set_state("pause", {"paused": True, "pause_reason": reason})

    def resume(self) -> None:
        self.set_state("pause", {"paused": False, "pause_reason": None})

    def subscriptions(self) -> list[dict[str, Any]]:
        result = []
        for row in self.db.execute("SELECT * FROM subscriptions ORDER BY username"):
            value = {"id": row["id"], "username": row["username"], **json.loads(row["config"])}
            value["history_status"] = self.history_status(row["username"])
            value["progress"] = self.progress(row["username"])
            last = self.db.execute(
                "SELECT payload FROM runs WHERE username=? ORDER BY rowid DESC LIMIT 1",
                (row["username"],),
            ).fetchone()
            value["last_result"] = json.loads(last[0]) if last else None
            result.append(value)
        return result

    def observe_platform_total(self, username: str, total: Any) -> None:
        """Only reliable raw profile counts, never inferred from pages/runs/files."""
        if type(total) is not int or total < 0:
            return
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO platform_totals VALUES (?,?,?,'platform_approximate')",
                (username, total, time.time()),
            )

    def progress(self, username: str) -> dict[str, Any]:
        counts = self.db.execute(
            "SELECT COUNT(CASE WHEN completed=1 THEN 1 END),"
            "COUNT(CASE WHEN completed=0 THEN 1 END) FROM posts WHERE username=?",
            (username,),
        ).fetchone()
        total = self.db.execute(
            "SELECT total,observed_at FROM platform_totals WHERE username=?", (username,)
        ).fetchone()
        queued = self.db.execute(
            "SELECT 1 FROM history_pending WHERE username=? LIMIT 1", (username,)
        ).fetchone()
        return {
            "completed": counts[0],
            "refresh_deferred": self.db.execute(
                "SELECT COUNT(*) FROM refresh_pending WHERE username=?", (username,)
            ).fetchone()[0],
            "history_deferred": self.db.execute(
                "SELECT COUNT(*) FROM history_pending WHERE username=?", (username,)
            ).fetchone()[0],
            "platform_total": total[0] if total else None,
            "total_updated_at": total[1] if total else None,
            "history_complete": self.feed_terminal(username) and queued is None and counts[1] == 0,
        }

    def get(self, identifier: str) -> dict[str, Any]:
        for value in self.subscriptions():
            if value["id"] == identifier:
                return value
        raise KeyError(identifier)

    def upsert(
        self, fields: dict[str, Any], identifier: str | None = None, cap: int = 1000
    ) -> dict[str, Any]:
        old: dict[str, Any] = {}
        if identifier:
            old = self.get(identifier)
        username = normalize_username(fields.get("username", old.get("username", "")))
        if identifier and username != old["username"]:
            raise ValueError("username is immutable; create a separate subscription")
        existing = self.db.execute(
            "SELECT id,config FROM subscriptions WHERE username=?", (username,)
        ).fetchone()
        if existing and not old:
            identifier = existing["id"]
            old = json.loads(existing["config"])
        config = {
            "enabled": True,
            "refresh_enabled": True,
            "refresh_limit": 10,
            "history_enabled": False,
            "history_limit": 10,
        }
        config.update({k: v for k, v in old.items() if k in config})
        config.update({k: v for k, v in fields.items() if k in config})
        for key in ("refresh_limit", "history_limit"):
            if type(config[key]) is not int or not 1 <= config[key] <= cap:
                raise ValueError(f"{key} must be between 1 and {cap}")
        identifier = identifier or uuid.uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO subscriptions VALUES (?,?,?)",
                (identifier, username, json.dumps(config)),
            )
        return self.get(identifier)

    def delete(self, identifier: str) -> None:
        self.get(identifier)
        with self.db:
            self.db.execute("DELETE FROM subscriptions WHERE id=?", (identifier,))

    def cursor(self, username: str) -> str | None:
        row = self.db.execute("SELECT cursor FROM history WHERE username=?", (username,)).fetchone()
        return row[0] if row else None

    def history_status(self, username: str) -> str:
        row = self.db.execute("SELECT status FROM history WHERE username=?", (username,)).fetchone()
        return str(row[0]) if row else "ready"

    def set_cursor(self, username: str, cursor: str | None, status: str = "ready") -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO history VALUES (?,?,?)", (username, cursor, status)
            )

    def reset_history(self, username: str) -> None:
        """Explicit reposition discards discoveries, not attempted posts or media archives."""
        with self.db:
            self.db.execute("DELETE FROM history_pending WHERE username=?", (username,))
            self.db.execute("DELETE FROM history_feed WHERE username=?", (username,))
            self.db.execute("INSERT OR REPLACE INTO history VALUES (?,NULL,'ready')", (username,))

    def enqueue_posts(self, username: str, posts: list[dict[str, Any]]) -> None:
        for post in posts:
            identifier = str(post.get("pk") or post.get("id") or post.get("code") or "")
            if not identifier:
                raise ValueError("post identity missing")
            self.db.execute(
                "INSERT OR IGNORE INTO history_pending(username,post_id,payload) VALUES (?,?,?)",
                (username, identifier, json.dumps(post_metadata(post))),
            )

    def save_page(self, username: str, posts: list[dict[str, Any]], cursor: str | None) -> None:
        # Queue and continuation commit together, before the first detail/download request.
        with self.db:
            self.enqueue_posts(username, posts)
            self.db.execute(
                "INSERT OR REPLACE INTO history_feed VALUES (?,?)", (username, cursor is None)
            )
            self.db.execute(
                "INSERT OR REPLACE INTO history VALUES (?,?,'ready')", (username, cursor)
            )

    def restore_pending(self, username: str) -> None:
        # Import old attempted failures only when absent; queue order survives process restarts.
        with self.db:
            self.enqueue_posts(username, self.pending(username, 2147483647))

    def queued_posts(self, username: str) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT payload FROM history_pending WHERE username=? ORDER BY position",
                (username,),
            )
        ]

    def rotate_pending(self, username: str, post_id: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE history_pending SET position=(SELECT COALESCE(MAX(position),0)+1 "
                "FROM history_pending) WHERE username=? AND post_id=?",
                (username, post_id),
            )

    def remove_pending(self, username: str, post_id: str) -> None:
        with self.db:
            self.db.execute(
                "DELETE FROM history_pending WHERE username=? AND post_id=?", (username, post_id)
            )

    def feed_terminal(self, username: str) -> bool:
        row = self.db.execute(
            "SELECT terminal FROM history_feed WHERE username=?", (username,)
        ).fetchone()
        return bool(row[0]) if row else self.history_status(username) == "exhausted"

    def finish_history(self, username: str) -> None:
        pending = self.db.execute(
            "SELECT 1 FROM history_pending WHERE username=? LIMIT 1", (username,)
        ).fetchone()
        incomplete = self.db.execute(
            "SELECT 1 FROM posts WHERE username=? AND completed=0 LIMIT 1", (username,)
        ).fetchone()
        if self.feed_terminal(username) and pending is None and incomplete is None:
            self.set_cursor(username, None, "exhausted")

    def is_done(self, username: str, post_id: str, media_id: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM media WHERE username=? AND post_id=? AND media_id=?",
                (username, post_id, media_id),
            ).fetchone()
            is not None
        )

    def media_done(self, username: str, post_id: str, media_id: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO media VALUES (?,?,?)", (username, post_id, media_id)
            )

    def post(self, username: str, post_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM posts WHERE username=? AND post_id=?", (username, post_id)
        ).fetchone()
        return dict(row) if row else None

    def remember(
        self, username: str, post_id: str, folder: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self.db:
            self.db.execute(
                "INSERT INTO posts(username,post_id,folder,payload) VALUES (?,?,?,?) "
                "ON CONFLICT(username,post_id) DO UPDATE SET payload=excluded.payload",
                (username, post_id, folder, json.dumps(post_metadata(payload))),
            )
        result = self.post(username, post_id)
        assert result is not None
        return result

    def complete(
        self, username: str, post_id: str, batch_id: str | None = None, run_id: str | None = None
    ) -> None:
        with self.db:
            # One transaction makes finalized-post accounting survive worker loss.
            self.db.execute("BEGIN IMMEDIATE")
            old = self.post(username, post_id)
            if old and old["completed"]:
                return
            if self.remaining(batch_id) <= 0:
                raise ValueError("batch budget exhausted")
            self.db.execute(
                "UPDATE posts SET completed=1 WHERE username=? AND post_id=?", (username, post_id)
            )
            if batch_id:
                self.db.execute(
                    "INSERT INTO batch_posts VALUES (?,?,?,?)",
                    (batch_id, run_id, username, post_id),
                )

    def pending(self, username: str, limit: int) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT payload FROM posts WHERE username=? AND completed=0 ORDER BY rowid LIMIT ?",
                (username, limit),
            )
        ]

    def redownload(self, username: str, post_id: str) -> bool:
        if not self.db.execute(
            "SELECT 1 FROM posts WHERE username=? AND post_id=? "
            "UNION SELECT 1 FROM media WHERE username=? AND post_id=?",
            (username, post_id, username, post_id),
        ).fetchone():
            return False
        with self.db:
            self.db.execute("DELETE FROM media WHERE username=? AND post_id=?", (username, post_id))
            self.db.execute(
                "UPDATE posts SET completed=0,generation=generation+1 "
                "WHERE username=? AND post_id=?",
                (username, post_id),
            )
        return True

    def queue(self, username: str, mode: str, batch_id: str | None = None) -> dict[str, Any] | None:
        run = {
            "batch_id": batch_id,
            "id": uuid.uuid4().hex,
            "username": username,
            "mode": mode,
            "status": "queued",
            "checked": 0,
            "completed": 0,
            "skipped": 0,
            "failed": 0,
            "created_at": time.time(),
        }
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO runs VALUES (?,?,?,?,?)",
                    (run["id"], username, mode, "queued", json.dumps(run)),
                )
        except sqlite3.IntegrityError:
            return None
        return run

    def runs(self) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.db.execute("SELECT payload FROM runs ORDER BY rowid DESC LIMIT 200")
        ]

    def has_active_run(self, username: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM runs WHERE username=? AND status IN ('queued','running') LIMIT 1",
                (username,),
            ).fetchone()
            is not None
        )

    def queued(self) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT payload FROM runs WHERE status='queued' ORDER BY rowid"
            )
        ]

    def update_run(self, run: dict[str, Any], **fields: Any) -> None:
        run.update(fields)
        with self.db:
            self.db.execute(
                "UPDATE runs SET status=?,payload=? WHERE id=?",
                (run["status"], json.dumps(run), run["id"]),
            )

    def recover(self) -> None:
        # An interrupted run is never silently restarted; its current page and media remain durable.
        for row in self.db.execute("SELECT payload FROM runs WHERE status='running'").fetchall():
            run = json.loads(row[0])
            fields: dict[str, Any] = {"status": "interrupted", "reason": "server_restarted"}
            if run.get("batch_id"):
                fields["completed"] = self.run_completed(run["id"])
            self.update_run(run, **fields)

    def snapshot(self) -> dict[str, Any]:
        return {
            "subscriptions": self.subscriptions(),
            **self.get_state("pause", {"paused": False, "pause_reason": None}),
            "runs": self.runs(),
            "cookie_check": self.get_state("cookie_check"),
            "global_limits": self.limits(),
            "batches": self.batches(),
        }
