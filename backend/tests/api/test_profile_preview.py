from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from gallery_dl_web.config import Settings


@pytest.mark.parametrize("filename,mime", [("图 片.jpg", "image/jpeg"), ("clip.mp4", "video/mp4")])
async def test_preview_inline_and_download_attachment(
    app: FastAPI, tmp_settings: Settings, filename: str, mime: str
) -> None:
    folder = tmp_settings.downloads_dir / "facebook" / "Jane Public" / "nested"
    folder.mkdir(parents=True)
    (folder / filename).write_bytes(b"0123456789")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        base = "/api/profiles/facebook/Jane%20Public"
        preview = await client.get(f"{base}/preview/nested/{filename}")
        assert preview.status_code == 200
        assert preview.headers["content-type"] == mime
        assert preview.headers["content-disposition"].startswith("inline;")
        assert preview.headers["x-content-type-options"] == "nosniff"
        assert preview.content == b"0123456789"
        download = await client.get(f"{base}/file/nested/{filename}")
        assert download.headers["content-disposition"].startswith("attachment;")
        generic = await client.get(
            "/api/files/download", params={"path": f"facebook/Jane Public/nested/{filename}"}
        )
        assert generic.headers["content-disposition"].startswith("attachment;")
        archive = await client.get(f"{base}/zip")
        assert archive.status_code == 200
        assert archive.headers["content-disposition"].startswith("attachment;")


async def test_preview_ranges_and_head(app: FastAPI, tmp_settings: Settings) -> None:
    folder = tmp_settings.downloads_dir / "instagram" / "demo"
    folder.mkdir(parents=True)
    (folder / "clip.mp4").write_bytes(b"0123456789")
    url = "/api/profiles/instagram/demo/preview/clip.mp4"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        full = await client.get(url)
        assert full.status_code == 200
        assert full.headers["accept-ranges"] == "bytes"
        for value, expected, content_range in [
            ("bytes=2-5", b"2345", "bytes 2-5/10"),
            ("bytes=-3", b"789", "bytes 7-9/10"),
        ]:
            partial = await client.get(
                url, headers={"Range": value, "If-Range": full.headers["etag"]}
            )
            assert partial.status_code == 206
            assert partial.content == expected
            assert partial.headers["content-range"] == content_range
            assert int(partial.headers["content-length"]) == len(expected)
        invalid = await client.get(url, headers={"Range": "bytes=50-"})
        assert invalid.status_code == 416
        assert invalid.headers["content-range"] == "bytes */10"
        stale = await client.get(url, headers={"Range": "bytes=2-5", "If-Range": '"stale"'})
        assert stale.status_code == 200
        assert stale.content == full.content
        head = await client.head(url)
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["content-length"] == "10"


@pytest.mark.parametrize(
    "filename",
    [
        "missing.jpg",
        "metadata.json",
        "page.html",
        "script.svg",
        "folder",
        "%2e%2e/outside.jpg",
        "escape.jpg",
    ],
)
async def test_preview_rejects_missing_nonmedia_and_escape(
    app: FastAPI, tmp_settings: Settings, filename: str
) -> None:
    folder = tmp_settings.downloads_dir / "facebook" / "demo"
    folder.mkdir(parents=True)
    for name in ["metadata.json", "page.html", "script.svg"]:
        (folder / name).write_text("not media")
    (folder / "folder").mkdir()
    outside = tmp_settings.data_dir / "outside.jpg"
    outside.write_bytes(b"secret")
    (folder / "escape.jpg").symlink_to(outside)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        result = await client.get(f"/api/profiles/facebook/demo/preview/{filename}")
        assert result.status_code == 404
        assert b"secret" not in result.content
