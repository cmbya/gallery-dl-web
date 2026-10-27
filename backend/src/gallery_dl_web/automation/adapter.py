"""gallery-dl 1.32.18 adapter. Only run inside the isolated automation subprocess."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from gallery_dl import config, extractor, job, output

from .engine import Page, StopRound
from .store import safe_component


def response_problem(response: Any) -> str | None:
    if response.status_code == 429:
        return "rate_limited"
    if 300 <= response.status_code < 400:
        return "redirect"  # a 302 is NOT evidence of an invalid session
    if "json" not in response.headers.get("Content-Type", "").lower():
        return None
    try:
        data = response.json()
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    message = str(data.get("message", "")).lower()
    if (
        data.get("challenge")
        or data.get("checkpoint_url")
        or "challenge_required" in message
        or "checkpoint_required" in message
    ):
        return "challenge"
    if message in {"login_required", "invalid session", "session expired"}:
        return "invalid_session"
    if data.get("status") == "fail" and ("please wait" in message or "try again later" in message):
        return "rate_limited"
    return None


class GalleryBackend:
    def __init__(
        self,
        downloads: Path,
        cookies: dict[str, Any] | None,
        request_budget: int = 100,
        timeout: float = 30,
    ) -> None:
        config.clear()
        config.set(("cache",), "file", ":memory:")
        config.set(("extractor",), "cookies", cookies or {})
        config.set(("extractor",), "retries", 0)
        config.set(("extractor",), "timeout", min(timeout, 60))
        config.set(("extractor",), "sleep-request", 1)
        config.set(("extractor",), "sleep-429", 0)
        config.set(("extractor",), "browser", None)
        config.set(("extractor", "instagram"), "videos", "original")
        config.set(("extractor", "instagram"), "include", "posts")
        config.set(("extractor",), "async", False)
        output.select = output.NullOutput
        self.downloads = downloads
        self.remaining = request_budget
        self.extr: Any = None

    def initialize(self, username: str) -> Any:
        if self.extr is None:
            self.extr = extractor.find(f"https://www.instagram.com/{username}/posts/")
            self.extr.initialize()
            self.guard(self.extr)
            self.extr.login()
        return self.extr

    def guard(self, extr: Any) -> None:
        original = extr.session.send

        def send(request: Any, **kwargs: Any) -> Any:
            if self.remaining <= 0:
                raise StopRound("request_budget")
            self.remaining -= 1
            kwargs["allow_redirects"] = False
            response = original(request, **kwargs)
            problem = response_problem(response)
            if problem:
                response.close()
                raise StopRound(problem)
            return response

        extr.session.send = send

    def page(self, username: str, cursor: str | None) -> Page:
        extr = self.initialize(username)
        original_request = extr.request
        captured: dict[str, Any] = {}
        original_config = extr.config

        def cfg(key: str, default: Any = None) -> Any:
            return cursor if key == "cursor" else original_config(key, default)

        def request(url: str, **kwargs: Any) -> Any:
            response = original_request(url, **kwargs)
            if "/graphql/query" in url:
                try:
                    data = response.json()["data"][
                        "xdt_api__v1__feed__user_timeline_graphql_connection"
                    ]
                    if "edges" not in data:
                        data = next(
                            v for v in data.values() if isinstance(v, dict) and "edges" in v
                        )
                    captured.update(data)
                except (ValueError, KeyError, StopIteration, TypeError):
                    raise StopRound("needs_reposition" if cursor else "query_error") from None
            return response

        extr.config, extr.request = cfg, request
        iterator: Any = None
        try:
            iterator = iter(extr.posts())
            first = next(iterator, None)
            if first is None:
                if not captured or captured.get("page_info", {}).get("has_next_page"):
                    raise StopRound("needs_reposition" if cursor else "query_error")
                return Page([], None)
            count = len(captured.get("edges", []))
            if not count:
                raise StopRound("query_error")
            posts = [first]
            # Upstream updates its cursor only AFTER a page is exhausted. Do not request the
            # next element to discover page boundaries: use the captured real response length.
            for _ in range(count - 1):
                posts.append(next(iterator))
            info = captured.get("page_info") or {}
            next_cursor = info.get("end_cursor") if info.get("has_next_page") else None
            if info.get("has_next_page") and not next_cursor:
                raise StopRound("needs_reposition")
            return Page(posts, next_cursor)
        except StopRound:
            raise
        except Exception:
            raise StopRound("needs_reposition" if cursor else "query_error") from None
        finally:
            if iterator is not None and hasattr(iterator, "close"):
                iterator.close()
            extr.config, extr.request = original_config, original_request

    def download(
        self,
        username: str,
        post: dict[str, Any],
        folder: str,
        done: Callable[[str], bool],
        success: Callable[[str], None],
        generation: int,
    ) -> bool:
        # Refresh metadata and CDN URLs on every partial retry. The profile page payload is used
        # only to identify the post; it is never treated as a permanent download URL.
        extr = self.initialize(username)
        shortcode = str(post.get("code") or "")
        if not shortcode:
            raise StopRound("unsupported_post")
        raw = list(extr.api.media(shortcode))
        child: Any = extractor.find(f"https://www.instagram.com/p/{shortcode}/")
        child.initialize()
        self.guard(child)
        child.posts = lambda: iter(raw)
        child.login = lambda: None
        config.set(("extractor",), "base-directory", str(self.downloads))
        config.set(("extractor",), "directory", ["instagram", username, folder])
        suffix = f"_redownload{generation}" if generation else ""
        config.set(("extractor",), "filename", "{media_id}" + suffix + ".{extension}")
        config.set(("extractor",), "archive", None)
        config.set(("extractor",), "skip", True)
        expected: set[str] = {
            str(item["pk"])
            for entry in raw
            for item in (entry.get("carousel_media") or [entry])
            if item.get("pk")
        }
        target = self.downloads / "instagram" / username / folder
        try:
            target.resolve().relative_to(self.downloads.resolve())
        except ValueError:
            raise StopRound("unsafe_output_path") from None
        # Reject links even if they point inside downloads: outputs must not overwrite another post.
        for component in [target, *target.parents]:
            if component == self.downloads:
                break
            if component.is_symlink():
                raise StopRound("unsafe_output_path")
        if target.exists() and any(path.is_symlink() for path in target.iterdir()):
            raise StopRound("unsafe_output_path")

        class DurableDownload(job.DownloadJob):  # type: ignore[misc]
            hooks: Any

            def handle_url(self, url: str, kwdict: dict[str, Any]) -> None:
                media = str(kwdict.get("media_id") or "")
                if not media or safe_component(media, 100) != media:
                    raise StopRound("unsupported_media_id")
                expected.add(media)
                if done(media):
                    return
                finalized = False

                def after(pathfmt: Any) -> None:
                    nonlocal finalized
                    finalized = True

                if not isinstance(self.hooks, dict):
                    self.hooks = {}
                self.hooks.setdefault("after", []).append(after)
                try:
                    super().handle_url(url, kwdict)
                finally:
                    self.hooks["after"].remove(after)
                # Only the after-finalize hook is durable evidence, never prepare/file/skip.
                if finalized:
                    success(media)

        download = DurableDownload(child)
        download.run()
        return bool(expected) and all(done(media) for media in expected)

    def check(self, username: str | None) -> dict[str, Any]:
        extr = self.initialize(username or "instagram")
        result: dict[str, Any] = {
            "login_status": "unknown",
            "posts_status": "unknown",
            "reason": None,
        }
        response = extr.session.get(
            "https://www.instagram.com/api/v1/accounts/current_user/",
            headers={"X-IG-App-ID": "936619743392459"},
            timeout=20,
            allow_redirects=False,
        )
        try:
            data = response.json()
        except ValueError:
            data = {}
        if (
            response.status_code == 200
            and isinstance(data, dict)
            and data.get("user", {}).get("pk")
        ):
            result["login_status"] = "authenticated"
        elif response.status_code in {401, 403}:
            result["login_status"] = "unknown"
            result["reason"] = "identity_rejected"
        else:
            result["reason"] = "identity_unknown"
        if username:
            try:
                self.page(username, None)
                result["posts_status"] = "accessible"
            except StopRound as exc:
                result["posts_status"] = (
                    "restricted" if exc.reason in {"challenge", "rate_limited"} else "error"
                )
                result["reason"] = exc.reason
                if exc.reason == "invalid_session":
                    result["login_status"] = "login_required"
        return result
