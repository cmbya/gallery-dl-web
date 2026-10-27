"""Bounded post orchestration, shared by the real worker and injected offline tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from gallery_dl.exception import TerminateExtraction

from .store import AutomationStore, post_folder

GLOBAL_STOPS = frozenset({"rate_limited", "challenge", "invalid_session"})


class StopRound(TerminateExtraction):  # type: ignore[misc]
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class Page:
    posts: list[dict[str, Any]]
    next_cursor: str | None


class Backend(Protocol):
    def page(self, username: str, cursor: str | None) -> Page: ...
    def download(
        self,
        username: str,
        post: dict[str, Any],
        folder: str,
        done: Callable[[str], bool],
        success: Callable[[str], None],
        generation: int,
    ) -> bool: ...


class Engine:
    def __init__(
        self, store: AutomationStore, backend: Backend, max_pages: int = 10, max_posts: int = 1000
    ) -> None:
        self.store, self.backend = store, backend
        self.max_pages, self.max_posts = max_pages, max_posts

    def run(self, subscription: dict[str, Any], mode: str) -> dict[str, Any]:
        username = subscription["username"]
        limit = subscription[f"{mode}_limit"]
        result: dict[str, Any] = {
            "status": "completed",
            "checked": 0,
            "completed": 0,
            "skipped": 0,
            "failed": 0,
            "reason": None,
        }
        seen: set[str] = set()
        cursor = self.store.cursor(username) if mode == "history" else None
        if mode == "history" and self.store.history_status(username) == "needs_reposition":
            result["reason"] = "needs_reposition"
            return result
        exhausted = mode == "history" and self.store.history_status(username) == "exhausted"

        def process(post: dict[str, Any]) -> None:
            post_id = str(post.get("pk") or post.get("id") or post.get("code") or "")
            if not post_id:
                raise StopRound("unsupported_post")
            if post_id in seen:
                return
            seen.add(post_id)
            result["checked"] += 1
            old = self.store.post(username, post_id)
            if old and old["completed"]:
                result["skipped"] += 1
                return
            caption = post.get("caption") or {}
            title = caption.get("text", "") if isinstance(caption, dict) else str(caption)
            record = self.store.remember(
                username, post_id, post_folder(title, str(post.get("code") or post_id)), post
            )
            ok = self.backend.download(
                username,
                post,
                record["folder"],
                lambda media: self.store.is_done(username, post_id, media),
                lambda media: self.store.media_done(username, post_id, media),
                record["generation"],
            )
            if ok:
                self.store.complete(username, post_id)
                result["completed"] += 1
            else:
                result["failed"] += 1

        try:
            # Retry durable failed posts before walking farther. Resolve fresh URLs in the adapter.
            # Persisted payloads identify posts; CDN URLs are always refreshed by the adapter.
            if mode == "history":
                for pending in self.store.pending(username, self.max_posts):
                    if result["checked"] >= self.max_posts:
                        raise StopRound("scan_budget")
                    process(pending)
                    if result["completed"] >= limit:
                        return result
                if exhausted:
                    result["reason"] = "exhausted"
                    return result
            newest = 0
            for _ in range(self.max_pages):
                page = self.backend.page(username, cursor)
                for index, post in enumerate(page.posts):
                    if result["checked"] >= self.max_posts:
                        raise StopRound("scan_budget")
                    pinned = bool(post.get("timeline_pinned_user_ids") or post.get("is_pinned"))
                    process(post)
                    if mode == "refresh":
                        if not pinned:
                            newest += 1
                        if newest >= limit:
                            return result
                    elif result["completed"] >= limit:
                        # Replay the page if there is any unprocessed suffix OR pending failure.
                        # Cursor advances only after exhausting a page, never on post N.
                        if index == len(page.posts) - 1 and not self.store.pending(username, 1):
                            self.store.set_cursor(
                                username,
                                page.next_cursor,
                                "ready" if page.next_cursor else "exhausted",
                            )
                        return result
                if mode == "history":
                    self.store.set_cursor(
                        username, page.next_cursor, "ready" if page.next_cursor else "exhausted"
                    )
                if page.next_cursor is None:
                    return result
                if page.next_cursor == cursor:
                    raise StopRound("needs_reposition")
                cursor = page.next_cursor
            raise StopRound("scan_budget")
        except StopRound as exc:
            result.update(status="stopped", reason=exc.reason)
            if mode == "history" and exc.reason == "needs_reposition":
                self.store.set_cursor(username, cursor, "needs_reposition")
            if exc.reason in GLOBAL_STOPS:
                self.store.pause(exc.reason)
        return result
