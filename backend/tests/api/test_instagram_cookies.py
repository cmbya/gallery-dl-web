import httpx

from gallery_dl_web.gallerydl.config_builder import _cookie_for
from tests.cookies.test_instagram_import import IG_TEXT


async def test_import_api_worker_and_no_disclosure(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        response = await client.put("/api/settings/cookies", json={"ig_cookies_text": IG_TEXT})
        assert response.status_code == 200
        assert response.json()["has_ig"] is True
        assert "test-session" not in response.text
        cookies = app.state.cookie_store.get_for_platform("instagram")
        assert _cookie_for("instagram", cookies)["csrftoken"] == "test-csrf"
        status = await client.get("/api/settings")
        assert "test-csrf" not in status.text
