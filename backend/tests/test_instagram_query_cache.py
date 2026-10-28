"""Offline real gallery-dl discovery, including fresh Python interpreters."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = r"""
import json, sys
from pathlib import Path
import requests
from gallery_dl.extractor.common import CACHE_MEMORY
from gallery_dl_web.automation.adapter import GalleryBackend
OP = "PolarisProfilePostsTabContentQuery_connection"
calls = []
def send(self, request, **kwargs):
    calls.append(request.url)
    r = requests.Response()
    r.status_code = 200
    r.url = request.url
    r._content_consumed = True
    r.headers["Content-Type"] = "text/html"
    if "rsrc.php" in request.url:
        content = '__d("'+OP+'_instagramRelayOperation",[],(function(a,b){b.exports="123456789"}));'
        content += '__d("'+OP+'.graphql",["PolarisIsLoggedIn.relayprovider"]'
        content += 'RAW_JS_SECRET https://cdn.invalid/image?signature=SIGNATURE_SECRET'
    else:
        content = '<script id="__eqmc">{"l":"LSD_SECRET","f":"DTSG_SECRET"}</script>'
        content += '<link href="https://static.cdninstagram.com/rsrc.php/mock.js">'
        content += ' PRIVATE_HTML_SECRET'
    r._content = content.encode()
    return r
requests.Session.send = send
results = []
for username in sys.argv[2:]:
    CACHE_MEMORY.clear()
    b = GalleryBackend(Path(sys.argv[1]).parent / "downloads",
                       {"sessionid":"COOKIE_SECRET"}, query_cache_path=Path(sys.argv[1]))
    e = b.initialize(username)
    headers, body = e.api._prepare_graphql(OP, {}, path="/posts")
    assert body["doc_id"] == "123456789"
    assert body["lsd"] == "LSD_SECRET" and body["fb_dtsg"] == "DTSG_SECRET"
    assert "PolarisIsLoggedInrelayprovider" in body["variables"]
    results.append(body["doc_id"])
print(json.dumps({"js":sum("rsrc.php" in u for u in calls),
                  "home":sum("rsrc.php" not in u for u in calls), "results":results}))
"""


def run(cache: Path, *users: str):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src"))
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(cache), *users],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_real_library_reuses_across_backends_and_processes_without_secrets(tmp_path):
    path = tmp_path / "query.json"
    assert run(path, "alice", "bob") == {"js": 1, "home": 2, "results": ["123456789"] * 2}
    assert run(path, "charlie")["js"] == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.with_suffix(".json.lock").stat().st_mode) == 0o600
    data = path.read_text()
    for secret in ("SECRET", "alice", "bob", "charlie", "https://", "<script", "exports"):
        assert secret not in data


@pytest.mark.parametrize("change", ["expired", "version", "corrupt"])
def test_invalid_cache_rediscovers_once(tmp_path, change):
    path = tmp_path / "query.json"
    assert run(path, "alice")["js"] == 1
    if change == "corrupt":
        path.write_text("not json")
    else:
        data = json.loads(path.read_text())
        if change == "expired":
            data["expires"] = 1
        else:
            data["version"] = "other-version"
        path.write_text(json.dumps(data))
    assert run(path, "bob")["js"] == 1
    assert run(path, "charlie")["js"] == 0
