"""Nested automation folders must remain compatible with the existing gallery API."""

import io
import zipfile

from fastapi.testclient import TestClient
from PIL import Image

from gallery_dl_web.api.app import create_app
from tests.test_automation import settings


def test_nested_profile_urls_thumbnails_zip_delete(tmp_path):
    cfg = settings(tmp_path)
    folder = cfg.downloads_dir / "instagram" / "alice" / "Title_Code"
    folder.mkdir(parents=True)
    Image.new("RGB", (20, 10), color="red").save(folder / "101.jpg")
    Image.new("RGB", (20, 10), color="blue").save(folder.parent / "101.jpg")
    app = create_app(cfg)
    with TestClient(app) as client:
        metadata = client.get("/api/profiles/instagram/alice").json()
        nested = next(entry for entry in metadata["images"] if "Title_Code" in entry["path"])
        assert client.get(nested["file_url"]).status_code == 200
        thumb = client.get(nested["thumb_url"])
        assert thumb.status_code == 200
        assert Image.open(io.BytesIO(thumb.content)).getpixel((0, 0))[0] > 200
        assert client.get(metadata["avatar_url"]).status_code == 200
        summaries = client.get("/api/profiles").json()
        assert client.get(summaries[0]["avatar_url"]).status_code == 200
        zipped = client.get("/api/profiles/instagram/alice/zip")
        with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
            assert "Title_Code/101.jpg" in archive.namelist()
        index = client.get("/api/files").json()
        assert "Title_Code" in str(index)
        assert client.delete("/api/profiles/instagram/alice").status_code == 204
        assert not folder.exists()
        assert client.get("/api/profiles").json() == []
