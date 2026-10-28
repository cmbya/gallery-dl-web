"""Persist only public Instagram operation metadata, never upstream cache blobs."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from gallery_dl import __version__
from gallery_dl.extractor.instagram import QUERIES

from .engine import StopRound

log = logging.getLogger(__name__)
TTL = 86400
MAX_BYTES = 65536
VERSION = f"instagram-public-queries-v1/gallery-dl-{__version__}"
Info = tuple[str, list[str] | None]


def safe_map(value: Any) -> dict[str, Info]:
    """Closed operation allowlist and bounded public module-name grammar."""
    if not isinstance(value, dict) or not value or len(value) > len(QUERIES):
        raise ValueError("invalid_map")
    result = {}
    for name, info in value.items():
        if name not in QUERIES or not isinstance(info, (list, tuple)) or len(info) != 2:
            raise ValueError("invalid_operation")
        docid, params = info
        if not isinstance(docid, str) or not re.fullmatch(r"[0-9]{1,32}", docid):
            raise ValueError("invalid_docid")
        if params is not None and (
            not isinstance(params, list)
            or len(params) > 64
            or any(
                not isinstance(p, str) or not re.fullmatch(r"Polaris[A-Za-z0-9_]{1,160}", p)
                for p in params
            )
        ):
            raise ValueError("invalid_parameters")
        result[name] = (docid, params)
    return result


class QueryCache:
    def __init__(self, path: Path) -> None:
        # Explicit caller-owned app-state path; never derive from download/NAS roots.
        self.path = path

    @contextmanager
    def locked(self) -> Iterator[bool]:
        fd = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("not_regular")
            os.fchmod(fd, 0o600)  # enforce even with inherited default ACLs
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                fd = None
                raise StopRound("query_cache_busy") from None
        except OSError:
            log.warning("instagram_query_cache_unavailable")
            if fd is not None:
                os.close(fd)
                fd = None
        try:
            yield fd is not None
        finally:
            if fd is not None:
                os.close(fd)

    def read(self) -> dict[str, Info] | None:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("not_regular")
                os.fchmod(stream.fileno(), 0o600)
                record = json.loads(stream.read(MAX_BYTES + 1))
            if set(record) != {"version", "expires", "operations"}:
                raise ValueError("invalid_schema")
            now = time.time()
            if record["version"] != VERSION:
                log.info("instagram_query_cache_version_miss")
                return None
            if (
                type(record["expires"]) not in (int, float)
                or not now < record["expires"] <= now + TTL
            ):
                log.info("instagram_query_cache_expired")
                return None
            return safe_map(record["operations"])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError):
            log.warning("instagram_query_cache_invalid")
            return None

    def write(self, operations: dict[str, Info]) -> None:
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=".instagram-query-", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(
                    {"version": VERSION, "expires": time.time() + TTL, "operations": operations},
                    stream,
                    separators=(",", ":"),
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            log.warning("instagram_query_cache_write_failed")
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def resolve(self, opname: str, discover: Callable[[], Any]) -> Info:
        with self.locked() as usable:
            operations = self.read() if usable else None
            if operations is not None and opname in operations:
                return operations[opname]
            # One normal upstream discovery only. No stale fallback or query-error retry.
            found = discover()
            try:
                operations = safe_map(found)
            except ValueError:
                log.warning("instagram_query_cache_rejected_metadata")
                return cast(Info, found[opname])  # normal upstream behavior, no persistence
            if usable:
                self.write(operations)
            return operations[opname]

    def attach(self, extr: Any) -> None:
        original = extr.cache

        def cached(func: Any, *args: Any, **kwargs: Any) -> Any:
            if (
                getattr(func, "__self__", None) is extr.api
                and getattr(func, "__name__", "") == "_extract_opname_info"
            ):
                opname, path = args
                return self.resolve(opname, lambda: extr.api._extract_opname_map(path))
            return original(func, *args, **kwargs)

        extr.cache = cached
