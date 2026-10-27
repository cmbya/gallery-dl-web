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


class AutomationStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS subscriptions(
            id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,config TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS history(
            username TEXT PRIMARY KEY,cursor TEXT,status TEXT NOT NULL DEFAULT 'ready');
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
            last = self.db.execute(
                "SELECT payload FROM runs WHERE username=? ORDER BY rowid DESC LIMIT 1",
                (row["username"],),
            ).fetchone()
            value["last_result"] = json.loads(last[0]) if last else None
            result.append(value)
        return result

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
                (username, post_id, folder, json.dumps(payload)),
            )
        result = self.post(username, post_id)
        assert result is not None
        return result

    def complete(self, username: str, post_id: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE posts SET completed=1 WHERE username=? AND post_id=?", (username, post_id)
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

    def queue(self, username: str, mode: str) -> dict[str, Any] | None:
        run = {
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
            self.update_run(json.loads(row[0]), status="interrupted", reason="server_restarted")

    def snapshot(self) -> dict[str, Any]:
        return {
            "subscriptions": self.subscriptions(),
            **self.get_state("pause", {"paused": False, "pause_reason": None}),
            "runs": self.runs(),
            "cookie_check": self.get_state("cookie_check"),
        }
