import base64
import hashlib
import importlib
import json
import threading
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from stampcut.core.ffmpeg import Cancelled
from stampcut.core.publishing.credentials import CredentialStore
from stampcut.core.publishing.models import PublishError


def oauth():
    return importlib.import_module("stampcut.core.publishing.google_oauth")


class Response:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self.data = data

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class Session:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def _call(self, method, url, **kwargs):
        assert kwargs.get("timeout"), "Every HTTP operation needs a finite timeout"
        assert kwargs.get("allow_redirects") is False, "Never forward tokens through redirects"
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url, **kwargs):
        return self._call("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self._call("GET", url, **kwargs)


def vault(tmp_path):
    return CredentialStore(tmp_path / "test-vault", protect=lambda b: b, unprotect=lambda b: b)


def client_file(tmp_path):
    path = tmp_path / "desktop.json"
    path.write_text(json.dumps({"installed": {"client_id": "desktop-id", "client_secret": "desktop-secret", "token_uri": "https://evil.invalid"}}))
    return path


def test_connect_uses_pkce_state_offline_access_and_saves_verified_channel(tmp_path, monkeypatch):
    mod = oauth()
    store = vault(tmp_path)
    captured = {}

    def receive(make_url, state, browser, cancel):
        redirect = "http://127.0.0.1:41234/oauth2callback"
        captured.update(parse_qs(urlsplit(make_url(redirect)).query))
        assert captured["state"] == [state]
        return mod._callback_code("/oauth2callback?state=" + state + "&code=one-use-code", state), redirect

    monkeypatch.setattr(mod, "_receive_authorization", receive)
    monkeypatch.setattr(mod.time, "time", lambda: 1000)
    session = Session([Response(data={"access_token": "fresh", "refresh_token": "renew", "expires_in": 3600, "token_type": "Bearer"}), Response(data={"items": [{"id": "UCchannel", "snippet": {"title": "Test Channel"}}]})])
    result = mod.connect_youtube(client_file(tmp_path), store, session=session, open_browser=lambda url: True)
    payload = session.calls[0][2]["data"]
    verifier = payload["code_verifier"]
    assert 43 <= len(verifier) <= 128
    assert captured["code_challenge"] == [base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")]
    assert captured["code_challenge_method"] == ["S256"]
    assert captured["access_type"] == ["offline"]
    assert captured["prompt"] == ["consent"]
    assert "https://www.googleapis.com/auth/youtube.upload" in captured["scope"][0]
    assert "https://www.googleapis.com/auth/youtube.readonly" in captured["scope"][0]
    assert session.calls[0][1] == "https://oauth2.googleapis.com/token"
    assert payload["code"] == "one-use-code"
    assert payload["redirect_uri"] == "http://127.0.0.1:41234/oauth2callback"
    assert result["expires_at"] == 4600
    assert result["channel_id"] == "UCchannel"
    assert result["channel_title"] == "Test Channel"
    assert store.get("youtube") == result


@pytest.mark.parametrize("path", ["/oauth2callback?code=secret&state=other", "/oauth2callback?code=secret", "/oauth2callback?state=expected&state=other&code=secret", "/oauth2callback?state=expected&error=access_denied", "/wrong?state=expected&code=secret"])
def test_callback_rejects_bad_state_denial_and_wrong_path_without_exposing_code(path):
    with pytest.raises(PublishError) as error:
        oauth()._callback_code(path, "expected")
    assert "secret" not in str(error.value)


def test_cancel_before_oauth_never_opens_browser_or_reads_client(tmp_path):
    event = threading.Event()
    event.set()
    with pytest.raises(Cancelled):
        oauth().connect_youtube(tmp_path / "missing", vault(tmp_path), cancel=event, open_browser=lambda url: pytest.fail("opened browser"))


def test_loopback_wait_can_be_cancelled_and_closes_listener(monkeypatch):
    mod = oauth()
    event = threading.Event()
    opened = []
    def browser(url):
        opened.append(url)
        event.set()
        return True
    with pytest.raises(Cancelled):
        mod._receive_authorization(lambda redirect: "https://accounts.google.com?redirect=" + redirect, "state", browser, event)
    assert len(opened) == 1


def test_refreshes_near_expiry_without_losing_refresh_token_or_identity(tmp_path, monkeypatch):
    mod = oauth()
    monkeypatch.setattr(mod.time, "time", lambda: 1000)
    store = vault(tmp_path)
    store.put("youtube", {"client_id": "id", "client_secret": "secret", "access_token": "old", "refresh_token": "renew", "expires_at": 1020, "channel_id": "UCchannel", "channel_title": "Name"})
    session = Session([Response(data={"access_token": "fresh", "expires_in": 1800, "token_type": "Bearer"})])
    result = mod.youtube_credentials(store, session=session)
    assert result["access_token"] == "fresh"
    assert result["refresh_token"] == "renew"
    assert result["channel_title"] == "Name"
    assert result["expires_at"] == 2800
    assert store.get("youtube") == result
    assert session.calls[0][2]["data"]["grant_type"] == "refresh_token"


def test_unexpired_credentials_make_no_http_request(tmp_path, monkeypatch):
    mod = oauth()
    monkeypatch.setattr(mod.time, "time", lambda: 1000)
    store = vault(tmp_path)
    values = {"client_id": "id", "access_token": "fresh", "refresh_token": "renew", "expires_at": 2000, "channel_id": "UCchannel"}
    store.put("youtube", values)
    assert mod.youtube_credentials(store, session=Session()) == values


@pytest.mark.parametrize("reply", [Response(400, {"error": "invalid_grant", "error_description": "SECRET"}), Response(data={"access_token": "SECRET", "expires_in": -1}), Response(data={"access_token": "SECRET", "expires_in": "nan"}), Response(data=ValueError("SECRET")), requests.ConnectionError("SECRET")])
def test_bad_refresh_is_actionable_and_does_not_replace_existing_credentials(tmp_path, reply):
    mod = oauth()
    store = vault(tmp_path)
    original = {"client_id": "id", "access_token": "old", "refresh_token": "renew", "expires_at": 0}
    store.put("youtube", original)
    with pytest.raises(PublishError) as error:
        mod.youtube_credentials(store, session=Session([reply]))
    assert "SECRET" not in str(error.value)
    assert store.get("youtube") == original


@pytest.mark.parametrize("payload", [{"web": {"client_id": "id"}}, {"installed": {}}, [], {"installed": {"client_id": "id\r\nsecret"}}])
def test_rejects_invalid_desktop_client_without_http(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(PublishError):
        oauth().connect_youtube(path, vault(tmp_path), session=Session())


def test_oauth_cancel_closes_the_loopback_server(monkeypatch):
    mod = oauth()
    event = threading.Event()
    closed = []

    class Server:
        server_port = 41234
        def __init__(self, address, handler):
            assert address == ("127.0.0.1", 0)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            closed.append(True)
        def handle_request(self):
            pytest.fail("Cancellation should be observed before accepting another request")

    monkeypatch.setattr(mod, "HTTPServer", Server)
    with pytest.raises(Cancelled):
        mod._receive_authorization(lambda redirect: "https://accounts.google.com", "state", lambda url: event.set(), event)
    assert closed == [True]


@pytest.mark.parametrize("secret", [None, 0, False, [], {}])
def test_desktop_client_secret_must_be_a_string(tmp_path, secret):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"installed": {"client_id": "id", "client_secret": secret}}))
    with pytest.raises(PublishError):
        oauth().connect_youtube(path, vault(tmp_path), session=Session(), open_browser=lambda url: pytest.fail("Invalid client opened a browser"))


def test_missing_channel_does_not_overwrite_previously_connected_account(tmp_path, monkeypatch):
    mod = oauth()
    store = vault(tmp_path)
    store.put("youtube", {"channel_id": "existing", "refresh_token": "old"})
    monkeypatch.setattr(mod, "_receive_authorization", lambda *args: ("code", "http://127.0.0.1:41234/oauth2callback"))
    session = Session([Response(data={"access_token": "fresh", "refresh_token": "renew", "expires_in": 3600}), Response(data={"items": []})])
    with pytest.raises(PublishError):
        mod.connect_youtube(client_file(tmp_path), store, session=session)
    assert store.get("youtube") == {"channel_id": "existing", "refresh_token": "old"}
