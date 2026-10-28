"""Persistent explicit queue with one global Instagram execution lock."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from gallery_dl_web.config import Settings
from gallery_dl_web.cookies.store import CookieStore
from gallery_dl_web.profiles.store import ProfileStore

from .engine import GLOBAL_STOPS
from .store import AutomationStore, normalize_username

Runner = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


class CooldownError(Exception):
    pass


class AutomationService:
    def __init__(
        self,
        settings: Settings,
        cookies: CookieStore,
        instagram_lock: asyncio.Lock,
        runner: Runner | None = None,
    ) -> None:
        self.settings, self.cookies, self.instagram_lock = settings, cookies, instagram_lock
        self.database = settings.data_dir / "automation-v1" / "state.sqlite"
        self.store = AutomationStore(self.database)
        self.store.recover()
        self.runner = runner or self._run_worker
        self.task: asyncio.Task[None] | None = None
        self.current: str | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.closed = False
        self.cookie_generation = 0
        cookies.on_instagram_change = self.invalidate_cookie_check

    def invalidate_cookie_check(self) -> None:
        self.cookie_generation += 1
        self.store.set_state("cookie_check", None)

    def kick(self) -> None:
        if (
            not self.closed
            and (self.task is None or self.task.done())
            and not self.store.snapshot()["paused"]
        ):
            self.task = asyncio.create_task(self._drain())

    async def trigger(self, mode: str, subscription_id: str | None = None) -> dict[str, Any]:
        if mode not in {"refresh", "history"}:
            raise ValueError("invalid mode")
        subscriptions = (
            [self.store.get(subscription_id)] if subscription_id else self.store.subscriptions()
        )
        if self.store.snapshot()["paused"]:
            return {"accepted": 0, "skipped": len(subscriptions), "paused": True}
        # Reuse interrupted ownership; a terminal prefix must never acquire a fresh budget.
        batch_id = self.store.active_batch(mode)
        members = self.store.batch_runs(batch_id) if batch_id else {}
        if any(r["status"] in {"queued", "running"} for r in members.values()):
            return {"accepted": 0, "skipped": len(subscriptions), "paused": False}
        if batch_id:
            subscriptions = [
                sub
                for sub in subscriptions
                if sub["username"] in members
                and members[sub["username"]]["status"] in {"cancelled", "interrupted"}
            ]
        else:
            batch_id = self.store.new_batch(mode)
        after = self.store.get_state(f"rotation_{mode}", "")
        subscriptions = sorted(
            subscriptions, key=lambda sub: (sub["username"] <= after, sub["username"])
        )
        accepted = skipped = 0
        for sub in subscriptions:
            if (
                not sub["enabled"]
                or not sub[f"{mode}_enabled"]
                or (
                    mode == "history"
                    and (
                        sub["history_status"] == "needs_reposition"
                        or (
                            sub["history_status"] == "exhausted"
                            and not self.store.pending(sub["username"], 1)
                        )
                    )
                )
            ):
                skipped += 1
            elif self.store.queue(sub["username"], mode, batch_id):
                accepted += 1
            else:
                skipped += 1
        self.kick()
        return {"accepted": accepted, "skipped": skipped, "paused": False}

    async def _drain(self) -> None:
        while not self.closed and not self.store.snapshot()["paused"]:
            queue = self.store.queued()
            if not queue:
                return
            run = queue[0]
            sub = next(
                (s for s in self.store.subscriptions() if s["username"] == run["username"]), None
            )
            if sub is None or not sub["enabled"] or not sub[f"{run['mode']}_enabled"]:
                self.store.update_run(
                    run, status="cancelled", reason="subscription_disabled_or_deleted"
                )
                continue
            async with self.instagram_lock:
                if self.closed or self.store.snapshot()["paused"]:
                    return
                # A cancel can arrive while this run is waiting for a manual Instagram job.
                latest = next((r for r in self.store.runs() if r["id"] == run["id"]), None)
                if latest is None or latest["status"] != "queued":
                    continue
                if self.store.remaining(run.get("batch_id")) <= 0:
                    self.store.update_run(run, status="stopped", reason="global_post_limit")
                    continue
                self.current = run["id"]
                self.store.set_state(f"rotation_{run['mode']}", run["username"])
                self.store.update_run(run, status="running")
                try:
                    result = await self.runner(run, sub)
                    self.store.update_run(run, **result)
                    if result.get("reason") in GLOBAL_STOPS:
                        self.store.pause(result["reason"])
                except asyncio.CancelledError:
                    self.store.update_run(
                        run,
                        status="interrupted" if self.closed else "cancelled",
                        reason="server_shutdown" if self.closed else "operator",
                    )
                    raise
                except Exception:
                    self.store.update_run(run, status="failed", reason="runner_error")
                finally:
                    if run.get("batch_id"):
                        self.store.update_run(run, completed=self.store.run_completed(run["id"]))
                    self.current = None
                    # Include partial/cancelled runs; finalized files must be visible immediately.
                    with contextlib.suppress(Exception):
                        await ProfileStore(self.settings).reconcile("instagram", sub["username"])

    def payload(self) -> dict[str, Any]:
        return {
            "database": str(self.database),
            "downloads": str(self.settings.downloads_dir),
            "cookies": self.cookies.get_for_platform("instagram"),
            "max_pages": self.settings.automation_max_pages,
            "max_posts": self.settings.automation_max_posts,
            "request_budget": self.settings.automation_request_budget,
            "timeout": self.settings.http_timeout_seconds,
        }

    async def _subprocess(self, payload: dict[str, Any]) -> dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            self.settings.worker_python or sys.executable,
            "-m",
            "gallery_dl_web.automation.worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.process = proc
        try:
            async with asyncio.timeout(self.settings.automation_run_timeout_seconds):
                stdout, _ = await proc.communicate(json.dumps(payload).encode())
            if proc.returncode or len(stdout) > 64000:
                return {"status": "failed", "reason": "worker_error"}
            value: Any = json.loads(stdout)
            if not isinstance(value, dict):
                raise ValueError("invalid worker response")
            reason = value.get("reason")
            if reason in {
                "redirect_home",
                "redirect_login",
                "redirect_challenge",
                "redirect_external",
                "redirect_insecure",
                "redirect_invalid",
                "redirect_missing_location",
                "redirect_unsupported",
                "redirect_loop",
                "redirect_limit",
                "query_error",
                "request_budget",
                "scan_budget",
                "challenge",
                "invalid_session",
                "rate_limited",
            }:
                logging.getLogger(__name__).warning("Automation stopped: %s", reason)
            return value
        except TimeoutError:
            return {"status": "stopped", "reason": "time_budget"}
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 3)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
            self.process = None

    async def _run_worker(
        self, run: dict[str, Any], subscription: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._subprocess(
            {
                **self.payload(),
                "mode": run["mode"],
                "subscription": subscription,
                "batch_id": run.get("batch_id"),
                "run_id": run["id"],
            }
        )

    async def wait_idle(self) -> None:
        if self.task:
            await self.task

    async def cancel(self, identifier: str) -> None:
        run = next((r for r in self.store.runs() if r["id"] == identifier), None)
        if run is None:
            raise KeyError(identifier)
        if run["status"] not in {"queued", "running"}:
            raise ValueError("run is already terminal")
        if self.current == identifier and self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        else:
            self.store.update_run(run, status="cancelled", reason="operator")
        self.kick()

    async def pause(self, reason: str = "operator") -> None:
        self.store.pause(reason)
        if self.current:
            await self.cancel(self.current)

    async def resume(self) -> None:
        self.store.resume()
        self.kick()

    async def check_cookies(self, username: str | None = None) -> dict[str, Any]:
        if username is not None:
            username = username.strip() or None
        if username is not None:
            username = normalize_username(username)
        else:
            username = next(
                (sub["username"] for sub in self.store.subscriptions() if sub["enabled"]), None
            )
        now = time.time()
        if (
            now - self.store.get_state("cookie_check_requested_at", 0)
            < self.settings.automation_cookie_check_cooldown_seconds
        ):
            raise CooldownError()
        self.store.set_state("cookie_check_requested_at", now)
        generation = self.cookie_generation
        cookies = self.cookies.get_for_platform("instagram")
        if (
            not cookies
            or not isinstance(cookies.get("sessionid"), str)
            or not cookies["sessionid"].strip()
            or any(c in cookies["sessionid"] for c in "\r\n\t")
        ):
            result: dict[str, Any] = {
                "login_status": "login_required",
                "posts_status": "unknown",
                "reason": "local_session_missing_or_malformed",
            }
        else:
            async with self.instagram_lock:
                result = await self._subprocess(
                    {
                        **self.payload(),
                        "operation": "check",
                        "username": username,
                        # Use the configured finite budget: bootstrap/token discovery may
                        # require more than four sends. The worker timeout still applies.
                        "request_budget": self.settings.automation_request_budget,
                    }
                )
            result = {
                "login_status": result.get("login_status", "error"),
                "posts_status": result.get("posts_status", "unknown"),
                "reason": result.get("reason"),
                "diagnostic": result.get("diagnostic"),
            }
            if result.get("reason") in GLOBAL_STOPS:
                self.store.pause(result["reason"])
        result["tested_username"] = username
        result["checked_at"] = now
        if generation != self.cookie_generation:
            return {
                "login_status": "unknown",
                "posts_status": "unknown",
                "checked_at": now,
                "reason": "cookies_changed_during_check",
            }
        self.store.set_state("cookie_check", result)
        return result

    async def close(self) -> None:
        self.closed = True
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self.cookies.on_instagram_change = None
        self.store.close()
