"""Automation-only pacing. Shared by all extractor/downloader sessions in a worker."""

from __future__ import annotations

import math
import random
import time
from typing import Any

DEFAULTS = dict(
    request_min=5.0, request_max=10.0, media_interval=3.0, blogger_min=60.0, blogger_max=120.0
)


def validate(fields: dict[str, Any], current: dict[str, float]) -> dict[str, float]:
    if not isinstance(fields, dict) or any(
        key not in DEFAULTS
        or type(value) not in (int, float)
        or not math.isfinite(value)
        or not 0 <= value <= 1800
        for key, value in fields.items()
    ):
        raise ValueError("pacing seconds must be finite numbers between 0 and 1800")
    result = {**current, **fields}
    if any(result[f"{kind}_min"] > result[f"{kind}_max"] for kind in ("request", "blogger")):
        raise ValueError("minimum interval cannot exceed maximum")
    return result


class Pacer:
    def __init__(
        self,
        values: dict[str, float],
        clock: Any = time.monotonic,
        sleep: Any = time.sleep,
        uniform: Any = random.uniform,
    ) -> None:
        self.values, self.clock, self.sleep, self.uniform = values, clock, sleep, uniform
        self.last_send: float | None = None
        self.media_due = 0.0
        self.last_media: float | None = None

    def media(self) -> None:
        # Only called by downloader sends, not archive/file skips.
        if self.last_media is not None:
            self.media_due = self.last_media + self.values["media_interval"]

    def send(self, media: bool = False) -> None:
        if media:
            self.media()
        due = self.media_due if media else 0.0
        if self.last_send is not None:
            due = max(
                due,
                self.last_send
                + self.uniform(self.values["request_min"], self.values["request_max"]),
            )
        delay = due - self.clock()
        if delay > 0:
            self.sleep(delay)
        self.last_send = self.clock()
        if media:
            self.last_media = self.last_send
