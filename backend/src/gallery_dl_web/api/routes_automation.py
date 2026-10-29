"""Automation API. No raw metadata, credentials, or host paths are returned."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from gallery_dl_web.automation.service import AutomationService, CooldownError

router = APIRouter(prefix="/automation", tags=["automation"])


class Subscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    enabled: bool = True
    refresh_enabled: bool = True
    refresh_limit: int = Field(default=10, ge=1, strict=True)
    history_enabled: bool = False
    history_limit: int = Field(default=10, ge=1, strict=True)


class PatchSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str | None = None
    enabled: bool | None = None
    refresh_enabled: bool | None = None
    refresh_limit: int | None = Field(default=None, ge=1, strict=True)
    history_enabled: bool | None = None
    history_limit: int | None = Field(default=None, ge=1, strict=True)


class Trigger(BaseModel):
    mode: Literal["refresh", "history"]
    subscription_id: str | None = None


class Pause(BaseModel):
    reason: str = Field(default="operator", max_length=200)


class CookieCheck(BaseModel):
    username: str | None = None


class Reposition(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Redownload(BaseModel):
    post_id: str = Field(min_length=1, max_length=100)


def service(request: Request) -> AutomationService:
    result: AutomationService = request.app.state.automation
    return result


def error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=404 if isinstance(exc, KeyError) else 422,
        detail="not found" if isinstance(exc, KeyError) else str(exc),
    )


@router.get("")
async def snapshot(request: Request) -> dict[str, Any]:
    return service(request).store.snapshot()


class GlobalLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh: int | None = Field(default=None, ge=1, strict=True)
    history: int | None = Field(default=None, ge=1, strict=True)


@router.patch("/pacing")
async def update_pacing(body: dict[str, Any], request: Request) -> dict[str, Any]:
    try:
        return service(request).store.set_pacing(body)
    except ValueError as exc:
        raise error(exc) from None


@router.patch("/limits")
async def update_limits(body: GlobalLimits, request: Request) -> dict[str, int]:
    fields = body.model_dump(exclude_unset=True)
    if any(value is None for value in fields.values()):
        raise HTTPException(422, "fields cannot be null")
    try:
        return service(request).store.set_limits(
            fields, service(request).settings.automation_limit_cap
        )
    except ValueError as exc:
        raise error(exc) from None


@router.post("/subscriptions")
async def create(body: Subscription, request: Request) -> dict[str, Any]:
    try:
        return service(request).store.upsert(
            body.model_dump(), cap=service(request).settings.automation_limit_cap
        )
    except ValueError as exc:
        raise error(exc) from None


@router.patch("/subscriptions/{identifier}")
async def update(identifier: str, body: PatchSubscription, request: Request) -> dict[str, Any]:
    if any(v is None for v in body.model_dump(exclude_unset=True).values()):
        raise HTTPException(422, "fields cannot be null")
    try:
        return service(request).store.upsert(
            body.model_dump(exclude_unset=True),
            identifier,
            service(request).settings.automation_limit_cap,
        )
    except (ValueError, KeyError) as exc:
        raise error(exc) from None


@router.delete("/subscriptions/{identifier}")
async def delete(identifier: str, request: Request) -> dict[str, bool]:
    try:
        srv = service(request)
        sub = srv.store.get(identifier)
        for run in srv.store.runs():
            if run["username"] == sub["username"] and run["status"] in {"queued", "running"}:
                await srv.cancel(run["id"])
        srv.store.delete(identifier)
        return {"deleted": True}
    except KeyError as exc:
        raise error(exc) from None


@router.post("/trigger")
async def trigger(body: Trigger, request: Request) -> dict[str, Any]:
    try:
        return await service(request).trigger(body.mode, body.subscription_id)
    except (KeyError, ValueError) as exc:
        raise error(exc) from None


@router.post("/pause")
async def pause(body: Pause, request: Request) -> dict[str, bool]:
    await service(request).pause(body.reason)
    return {"paused": True}


@router.post("/resume")
async def resume(request: Request) -> dict[str, bool]:
    await service(request).resume()
    return {"paused": False}


@router.post("/runs/{identifier}/cancel")
async def cancel(identifier: str, request: Request) -> dict[str, bool]:
    try:
        await service(request).cancel(identifier)
        return {"cancelled": True}
    except (KeyError, ValueError) as exc:
        raise error(exc) from None


@router.post("/cookies/check")
async def check(body: CookieCheck, request: Request) -> dict[str, Any]:
    try:
        return await service(request).check_cookies(body.username)
    except CooldownError:
        raise HTTPException(
            429,
            "cookie check cooldown",
            headers={
                "Retry-After": str(
                    service(request).settings.automation_cookie_check_cooldown_seconds
                )
            },
        ) from None
    except ValueError as exc:
        raise error(exc) from None


@router.post("/subscriptions/{identifier}/reposition")
async def reposition(identifier: str, body: Reposition, request: Request) -> dict[str, bool]:
    srv = service(request)
    try:
        sub = srv.store.get(identifier)
        if srv.instagram_lock.locked() or srv.store.has_active_run(sub["username"]):
            raise HTTPException(409, "wait for Instagram jobs to finish or cancel them first")
        # No await between the guard and write: workers cannot start in between.
        srv.store.reset_history(sub["username"])
        return {"reset": True}
    except KeyError as exc:
        raise error(exc) from None


@router.post("/subscriptions/{identifier}/redownload")
async def redownload(identifier: str, body: Redownload, request: Request) -> dict[str, bool]:
    srv = service(request)
    try:
        sub = srv.store.get(identifier)
        if srv.instagram_lock.locked() or any(
            r["username"] == sub["username"] and r["status"] in {"queued", "running"}
            for r in srv.store.runs()
        ):
            raise HTTPException(409, "wait for Instagram jobs to finish or cancel them first")
        if not srv.store.redownload(sub["username"], body.post_id):
            raise HTTPException(404, "post not found")
        return {"reset": True}
    except KeyError as exc:
        raise error(exc) from None
