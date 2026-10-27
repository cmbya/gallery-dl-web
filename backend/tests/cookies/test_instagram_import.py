import pytest

from gallery_dl_web.cookies.store import CookieStore

IG_TEXT = (
    ".instagram.com\tTRUE\t/\tTRUE\t1900000000\tsessionid\ttest-session\n"
    "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1900000000\tcsrftoken\ttest-csrf\n"
    ".instagram.com\tTRUE\t/\tTRUE\t1900000000\tds_user_id\t123\n"
    ".example.com\tTRUE\t/\tTRUE\t1900000000\tunrelated\tsecret\n"
)


def test_import_roundtrip_filters_domains(tmp_path):
    store = CookieStore(tmp_path / "c.json")
    store.update(ig_cookies_text=IG_TEXT)
    loaded = CookieStore(store.path)
    loaded.load()
    assert loaded.has_ig()
    # Legacy clients replacing sessionid must not retain stale companion cookies.
    store.update(ig_sessionid="replacement")
    assert store.get_for_platform("instagram") == {"sessionid": "replacement"}
    store.update(ig_cookies_text="")
    assert not store.has_ig()
    assert loaded.get_for_platform("instagram") == {
        "sessionid": "test-session",
        "csrftoken": "test-csrf",
        "ds_user_id": "123",
    }


@pytest.mark.parametrize(
    "text",
    [
        "garbage-secret",
        IG_TEXT.replace("sessionid", "other"),
        IG_TEXT.replace("instagram.com", "evilinstagram.com"),
    ],
)
def test_invalid_import_keeps_previous_cookie(tmp_path, text):
    store = CookieStore(tmp_path / "c.json")
    store.update(ig_sessionid="old")
    before = store.path.read_bytes()
    with pytest.raises(ValueError) as exc:
        store.update(ig_cookies_text=text)
    assert "garbage-secret" not in str(exc.value)
    assert store.get_for_platform("instagram") == {"sessionid": "old"}
    assert store.path.read_bytes() == before


def test_conflicting_inputs_rejected(tmp_path):
    store = CookieStore(tmp_path / "c.json")
    with pytest.raises(ValueError):
        store.update(ig_sessionid="sid", ig_cookies_text=IG_TEXT)
