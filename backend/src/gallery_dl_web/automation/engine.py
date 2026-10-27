"""Bounded post orchestration, shared by the real worker and injected offline tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from gallery_dl.exception import TerminateExtraction

from .store import AutomationStore, post_folder

GLOBAL_STOPS = frozenset({"rate_limited", "challenge", "invalid_session"})


class StopRound(TerminateExtraction):  # type: ignore[misc]
    def __init__(self, reason: str, diagnostic: dict[str, Any] | None = None) -> None:
        self.reason = reason
        self.diagnostic = diagnostic
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
            if mode == "history":
                self.store.restore_pending(username)
                pages = 0
                cursors = {cursor}
                while True:
                    for pending in self.store.queued_posts(username):
                        post_id = str(pending.get("pk") or pending.get("id") or pending.get("code"))
                        if post_id in seen:
                            continue
                        if result["checked"] >= self.max_posts:
                            raise StopRound("scan_budget")
                        # Rotate before work so interruptions cannot repeatedly starve the suffix.
                        self.store.rotate_pending(username, post_id)
                        process(pending)
                        record = self.store.post(username, post_id)
                        if record and record["completed"]:
                            self.store.remove_pending(username, post_id)
                        self.store.finish_history(username)
                        if result["completed"] >= limit:
                            return result
                    if self.store.feed_terminal(username):
                        self.store.finish_history(username)
                        if self.store.history_status(username) == "exhausted":
                            result["reason"] = "exhausted"
                        return result
                    if result["checked"] >= self.max_posts or pages >= self.max_pages:
                        raise StopRound("scan_budget")
                    page = self.backend.page(username, cursor)
                    # Validate before committing a continuation that would lose malformed items.
                    if any(not (p.get("pk") or p.get("id") or p.get("code")) for p in page.posts):
                        raise StopRound("unsupported_post")
                    if page.next_cursor is not None and page.next_cursor in cursors:
                        raise StopRound("needs_reposition")
                    self.store.save_page(username, page.posts, page.next_cursor)
                    pages += 1
                    cursor = page.next_cursor
                    cursors.add(cursor)
            newest = 0
            for _ in range(self.max_pages):
                page = self.backend.page(username, cursor)
                for post in page.posts:
                    if result["checked"] >= self.max_posts:
                        raise StopRound("scan_budget")
                    pinned = bool(post.get("timeline_pinned_user_ids") or post.get("is_pinned"))
                    process(post)
                    if mode == "refresh":
                        if not pinned:
                            newest += 1
                        if newest >= limit:
                            return result
                if page.next_cursor is None:
                    return result
                if page.next_cursor == cursor:
                    raise StopRound("needs_reposition")
                cursor = page.next_cursor
            raise StopRound("scan_budget")
        except StopRound as exc:
            result.update(status="stopped", reason=exc.reason)
            if exc.diagnostic:
                result["diagnostic"] = exc.diagnostic
            if mode == "history" and exc.reason == "needs_reposition":
                self.store.set_cursor(username, cursor, "needs_reposition")
            if exc.reason in GLOBAL_STOPS:
                self.store.pause(exc.reason)
        return result
