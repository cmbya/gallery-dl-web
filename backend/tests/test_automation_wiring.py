"""Worker wiring and CLI error codes, entirely offline."""

import asyncio
import io
import json

import pytest

from gallery_dl_web.automation import __main__ as cli
from gallery_dl_web.automation import worker
from gallery_dl_web.automation.engine import Page, StopRound
from gallery_dl_web.automation.service import AutomationService
from gallery_dl_web.cookies.store import CookieStore
from tests.test_automation import settings
from tests.test_automation_adapter import raw_post


@pytest.mark.parametrize(
    ("result", "code"),
    [
        ({"accepted": 1, "skipped": 0, "paused": False}, 0),
        ({"accepted": 0, "skipped": 1, "paused": True}, 2),
    ],
)
def test_cli_acceptance_is_not_completion(monkeypatch, capsys, result, code):
    monkeypatch.setattr(
        "sys.argv", ["automation", "trigger", "refresh", "--subscription-id", "abc"]
    )
    calls = []

    def send(request, timeout):
        calls.append(request)
        return io.BytesIO(json.dumps(result).encode())

    monkeypatch.setattr(cli, "urlopen", send)
    assert cli.main() == code
    assert calls[0].full_url == "http://127.0.0.1:8000/api/automation/trigger"
    assert json.loads(calls[0].data) == {"mode": "refresh", "subscription_id": "abc"}
    assert "completed" not in capsys.readouterr().out


def test_cli_connection_error_nonzero(monkeypatch):
    monkeypatch.setattr("sys.argv", ["automation", "trigger", "history"])

    def fail(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(cli, "urlopen", fail)
    assert cli.main() == 1


@pytest.mark.parametrize("reason", ["invalid_session", "challenge", "redirect"])
def test_worker_check_sanitized(monkeypatch, tmp_path, reason):
    class Adapter:
        def __init__(self, *args):
            pass

        def check(self, username):
            raise StopRound(reason)

    monkeypatch.setattr(worker, "GalleryBackend", Adapter)
    result = worker.execute(
        {"downloads": str(tmp_path), "operation": "check", "request_budget": 4, "timeout": 20}
    )
    assert result["reason"] == reason
    assert result["login_status"] == (
        "login_required" if reason == "invalid_session" else "unknown"
    )


def test_worker_drives_same_engine(monkeypatch, tmp_path):
    class Adapter:
        def __init__(self, *args):
            pass

        def page(self, username, cursor):
            return Page([raw_post(11)], None)

        def download(self, username, post, folder, done, success, generation):
            success("11")
            return True

    monkeypatch.setattr(worker, "GalleryBackend", Adapter)
    result = worker.execute(
        {
            "downloads": str(tmp_path),
            "database": str(tmp_path / "state.sqlite"),
            "request_budget": 4,
            "timeout": 20,
            "max_pages": 1,
            "max_posts": 10,
            "mode": "refresh",
            "subscription": {"username": "alice", "refresh_limit": 1},
        }
    )
    assert result["completed"] == 1


async def test_subprocess_stdin_and_reaping(monkeypatch, tmp_path):
    cfg = settings(tmp_path)
    service = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock())
    calls = []

    class Process:
        returncode = None

        async def communicate(self, data):
            calls.append(data)
            self.returncode = 0
            return b'{"status":"completed","completed":2}', b""

    async def spawn(*args, **kwargs):
        assert "sessionid" not in repr(args)
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    result = await service._subprocess({"cookies": {"sessionid": "fake-secret"}})
    assert result["completed"] == 2
    assert b"fake-secret" in calls[0]
    assert service.process is None
    await service.close()


async def test_subprocess_timeout_terminates(monkeypatch, tmp_path):
    cfg = settings(tmp_path)
    cfg.automation_run_timeout_seconds = 0
    service = AutomationService(cfg, CookieStore(cfg.cookies_path), asyncio.Lock())
    calls = []

    class Process:
        returncode = None

        async def communicate(self, data):
            await asyncio.Event().wait()

        def terminate(self):
            calls.append("terminate")
            self.returncode = -15

        async def wait(self):
            return self.returncode

    async def spawn(*args, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert (await service._subprocess({}))["reason"] == "time_budget"
    assert calls == ["terminate"]
    await service.close()
