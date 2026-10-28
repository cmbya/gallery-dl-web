"""gallery-dl 1.32.18 adapter. Only run inside the isolated automation subprocess."""

from __future__ import annotations

import re
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from gallery_dl import config, extractor, job, output

from .engine import Page, StopRound
from .query_cache import QueryCache
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


def endpoint(url: str) -> str:
    """Fixed categories only: never return usernames, IDs, hosts, queries or fragments."""
    try:
        path = urlsplit(url).path.lower().strip("/")
    except ValueError:
        return "unknown"
    if not path:
        return "home"
    if path.startswith("accounts/login"):
        return "login"
    if path.startswith(("challenge", "checkpoint")):
        return "challenge"
    if path == "api/v1/accounts/current_user":
        return "identity"
    if path == "graphql/query":
        return "graphql"
    if path.startswith("api/"):
        return "api"
    if path.startswith(("p/", "reel/", "stories/")):
        return "media"
    return "profile" if "/" not in path else "other"


def redirect_target(source: str, location: str) -> tuple[str, str | None]:
    if not location:
        return "", "redirect_missing_location"
    try:
        if any(ord(c) < 32 for c in location) or "\\\\" in location:
            return "", "redirect_invalid"
        target = urljoin(source, location)
        old, new = urlsplit(source), urlsplit(target)
        if new.username or new.password:
            return "", "redirect_invalid"
        if new.scheme != "https":
            return "", "redirect_insecure"
        if (new.hostname, new.port or 443) != (old.hostname, old.port or 443):
            return "", "redirect_external"
        if new.hostname not in {"instagram.com", "www.instagram.com", "i.instagram.com"}:
            return "", "redirect_external"
        category = endpoint(target)
        if category in {"home", "login", "challenge"}:
            return target, f"redirect_{category}"
        if new.query != old.query or new.fragment or (new.path.rstrip("/") != old.path.rstrip("/")):
            return target, "redirect_unsupported"
        return target, None
    except ValueError:
        return "", "redirect_invalid"


class ProfileDescription(HTMLParser):
    """Read only declared descriptions, not arbitrary embedded users or script text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.descriptions: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "meta" and (
            (values.get("name") or "").lower() == "description"
            or (values.get("property") or "").lower() == "og:description"
        ):
            self.descriptions.append(values.get("content") or "")


def profile_description_total(response: Any, username: str) -> int | None:
    """Conservative exact-count fallback from an already requested target homepage."""
    url = urlsplit(response.url)
    if (
        response.status_code != 200
        or url.scheme != "https"
        or url.hostname not in {"instagram.com", "www.instagram.com"}
        or url.path.strip("/").lower() != username.lower()
        or "text/html" not in response.headers.get("Content-Type", "").lower()
    ):
        return None
    parser = ProfileDescription()
    parser.feed(response.text)
    totals = set()
    for description in parser.descriptions:
        if not re.search(r"\(@" + re.escape(username) + r"\)", description, re.IGNORECASE):
            continue
        match = re.search(
            r"(?<!\S)([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)\s+posts\b",
            description,
            re.IGNORECASE,
        )
        if match:
            totals.add(int(match[1].replace(",", "")))
    return totals.pop() if len(totals) == 1 else None


class GalleryBackend:
    def __init__(
        self,
        downloads: Path,
        cookies: dict[str, Any] | None,
        request_budget: int = 100,
        timeout: float = 30,
        query_cache_path: Path | None = None,
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
        self.query_cache = QueryCache(query_cache_path) if query_cache_path is not None else None
        self.remaining = request_budget
        self.extr: Any = None
        self.last_diagnostic: dict[str, Any] | None = None
        self.observe_user: Callable[[str, int], None] | None = None

    def initialize(self, username: str) -> Any:
        if self.extr is None:
            self.username = username.lower()
            self.extr = extractor.find(f"https://www.instagram.com/{username}/posts/")
            self.extr.initialize()
            self.guard(self.extr)
            if self.query_cache is not None:
                self.query_cache.attach(self.extr)
            assign_user = self.extr._assign_user

            def observe(user: Any) -> Any:
                # Inspect BEFORE upstream normalization: 1.32.18 invents count_media=0
                # when edge_owner_to_timeline_media is absent. That is not a real total.
                if isinstance(user, dict) and str(user.get("username", "")).lower() == username:
                    edge = user.get("edge_owner_to_timeline_media")
                    candidates = [user.get("media_count")]
                    if isinstance(edge, dict):
                        candidates.append(edge.get("count"))
                    for total in candidates:
                        if type(total) is int and total >= 0:
                            if self.observe_user is not None:
                                self.observe_user(username, total)
                            break
                return assign_user(user)

            self.extr._assign_user = observe
            self.extr.login()
        return self.extr

    def guard(self, extr: Any) -> None:
        original = extr.session.send

        def send(request: Any, **kwargs: Any) -> Any:
            # Keep Requests' redirect preparation (method/body/cookie handling), but never
            # let its recursive Session.send follow a destination before our validation.
            kwargs["allow_redirects"] = False
            seen = {request.url}
            history = []
            for hop in range(4):
                if self.remaining <= 0:
                    raise StopRound("request_budget", {"stage": endpoint(request.url)})
                self.remaining -= 1
                response = original(request, **kwargs)
                self.last_diagnostic = {
                    "stage": endpoint(request.url),
                    "status": response.status_code,
                }
                if 300 <= response.status_code < 400:
                    location = response.headers.get("Location", "")
                    target, problem = redirect_target(request.url, location)
                    if problem is None and target in seen:
                        problem = "redirect_loop"
                    if problem is None and hop == 3:
                        problem = "redirect_limit"
                    if problem:
                        diagnostic = {
                            "stage": endpoint(request.url),
                            "status": response.status_code,
                            "target": endpoint(target),
                            "classification": problem,
                        }
                        response.close()
                        raise StopRound(problem, diagnostic)
                    following = response.next
                    response.close()
                    if following is None:
                        raise StopRound("redirect_unsupported")
                    history.append(response)
                    seen.add(target)
                    request = following
                    continue
                problem = response_problem(response)
                if problem:
                    response.close()
                    raise StopRound(
                        problem,
                        {
                            "stage": endpoint(request.url),
                            "status": response.status_code,
                            "classification": problem,
                        },
                    )
                response.history = history
                if self.observe_user is not None:
                    total = profile_description_total(response, self.username)
                    if total is not None:
                        self.observe_user(self.username, total)
                return response
            raise StopRound("redirect_limit")

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
                    raise StopRound(
                        "needs_reposition" if cursor else "query_error", self.last_diagnostic
                    ) from None
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
            raise StopRound(
                "needs_reposition" if cursor else "query_error", self.last_diagnostic
            ) from None
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
            "posts_status": "unknown" if username else "not_tested",
            "reason": None,
        }
        try:
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
                and isinstance(data.get("user"), dict)
                and data["user"].get("pk")
            ):
                result["login_status"] = "authenticated"
            else:
                result["reason"] = (
                    "identity_rejected"
                    if response.status_code in {401, 403}
                    else "identity_unknown"
                )
        except StopRound as exc:
            result["reason"] = exc.reason
            result["diagnostic"] = exc.diagnostic
            if exc.reason != "redirect_home":
                if exc.reason == "invalid_session":
                    result["login_status"] = "login_required"
                return result
        except requests.RequestException:
            result["reason"] = "identity_unknown"
        if username:
            try:
                # Do not attribute an identity redirect to a later posts parse failure.
                self.last_diagnostic = None
                self.page(username, None)
                result["posts_status"] = "accessible"
            except StopRound as exc:
                result["posts_status"] = (
                    "restricted" if exc.reason in {"challenge", "rate_limited"} else "error"
                )
                result["reason"] = exc.reason
                result["diagnostic"] = exc.diagnostic
                if exc.reason == "invalid_session":
                    result["login_status"] = "login_required"
        return result
