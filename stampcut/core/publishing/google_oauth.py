"""Desktop Google OAuth with PKCE and a cancellable loopback callback.

Client JSON endpoints are deliberately ignored: only Google's fixed HTTPS
endpoints receive authorization codes, refresh tokens, and access tokens.
"""
from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

import requests

from .models import PublishError, check_cancel, noop

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
DRIVE_ABOUT_URL = "https://www.googleapis.com/drive/v3/about"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
HTTP_TIMEOUT = (10, 60)
AUTH_TIMEOUT = 180
_REFRESH_LOCK = threading.RLock()


def _safe_text(value, limit=8192):
    return isinstance(value, str) and 0 < len(value) <= limit and all(ord(c) >= 32 and ord(c) != 127 for c in value)


def _callback_code(path, expected_state):
    try:
        parsed = urlsplit(path)
        values = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=16)
        received = values.get("state", [])
        if parsed.path != "/oauth2callback" or len(received) != 1 or not secrets.compare_digest(received[0], expected_state):
            raise ValueError
        codes = values.get("code", [])
        if "error" in values or len(codes) != 1 or not _safe_text(codes[0], 4096):
            raise ValueError
        return codes[0]
    except (ValueError, TypeError):
        raise PublishError("Google 인증 응답을 확인할 수 없습니다. 계정 연결을 다시 시도하세요.") from None


def _receive_authorization(make_url, state, open_browser, cancel):
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if urlsplit(self.path).path != "/oauth2callback":
                self.send_error(404)
                return
            try:
                result["code"] = _callback_code(self.path, state)
                message = b"Account authorization received. You may close this window."
                status = 200
            except PublishError as error:
                result["error"] = error
                message = b"Authorization could not be verified. Return to StampCut and try again."
                status = 400
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(message)
            except OSError:
                pass

        def log_message(self, *args):
            # The default logger includes the callback URL and authorization code.
            pass

    class LoopbackServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(2)
            return connection, address

        def handle_error(self, request, client_address):
            # Do not let socket/parser failures dump callback details to stderr.
            pass

    check_cancel(cancel)
    try:
        with LoopbackServer(("127.0.0.1", 0), Handler) as server:
            server.timeout = 0.25
            redirect = f"http://127.0.0.1:{server.server_port}/oauth2callback"
            check_cancel(cancel)
            if open_browser(make_url(redirect)) is False:
                raise PublishError("시스템 브라우저를 열지 못했습니다. 기본 브라우저 설정을 확인하세요.")
            deadline = time.monotonic() + AUTH_TIMEOUT
            while not result:
                check_cancel(cancel)
                if time.monotonic() >= deadline:
                    raise PublishError("Google 인증 대기 시간이 지났습니다. 계정 연결을 다시 시도하세요.")
                server.handle_request()
            if "error" in result:
                raise result["error"]
            check_cancel(cancel)
            return result["code"], redirect
    except OSError:
        raise PublishError("Google 인증용 로컬 연결을 열지 못했습니다. 방화벽 설정을 확인하세요.") from None


def _token_response(session, payload, cancel, scopes, service):
    check_cancel(cancel)
    try:
        response = session.post(TOKEN_URL, data=payload, timeout=HTTP_TIMEOUT, allow_redirects=False)
    except requests.RequestException:
        raise PublishError("Google 인증 서버에 연결하지 못했습니다. 네트워크를 확인하고 다시 시도하세요.") from None
    if response.status_code != 200:
        raise PublishError(f"Google 인증이 만료되었거나 거부되었습니다. {service} 계정을 다시 연결하세요.")
    try:
        data = response.json()
        if not isinstance(data, dict) or not _safe_text(data.get("access_token")):
            raise ValueError
        expires = data.get("expires_in")
        if isinstance(expires, bool):
            raise ValueError
        expires = float(expires)
        if not math.isfinite(expires) or expires <= 0:
            raise ValueError
        if data.get("token_type", "Bearer").lower() != "bearer":
            raise ValueError
        if "refresh_token" in data and not _safe_text(data["refresh_token"]):
            raise ValueError
        if "scope" in data and not set(scopes.split()).issubset(data["scope"].split()):
            raise PublishError(f"{service} 권한이 부족합니다. 계정을 다시 연결하고 요청한 권한에 동의하세요.")
        values = {"access_token": data["access_token"], "expires_at": time.time() + expires}
        if "refresh_token" in data:
            values["refresh_token"] = data["refresh_token"]
        return values
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise PublishError("Google 인증 응답이 올바르지 않습니다. 계정을 다시 연결하세요.") from None


def _desktop_client(client_file, saved=None):
    try:
        if client_file is None:
            client = saved
        else:
            data = json.loads(Path(client_file).read_text(encoding="utf-8-sig"))
            client = data.get("installed")
        if not isinstance(client, dict) or not _safe_text(client.get("client_id"), 512):
            raise ValueError
        secret = client.get("client_secret", "")
        if not isinstance(secret, str) or (secret and not _safe_text(secret, 512)):
            raise ValueError
    except (OSError, ValueError, TypeError, AttributeError):
        raise PublishError("Google Cloud에서 발급한 데스크톱 앱 OAuth 클라이언트 JSON 파일을 선택하세요.") from None
    return client["client_id"], secret


def _authorize(client, scopes, service, progress, cancel, session, open_browser):
    client_id, secret = client
    progress("auth", 0, 100, f"브라우저에서 {service} 계정 연결을 승인하세요.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")

    def make_url(redirect):
        return AUTH_URL + "?" + urlencode({"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": scopes, "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})

    code, redirect = _receive_authorization(make_url, state, open_browser or webbrowser.open, cancel)
    payload = {"client_id": client_id, "code": code, "code_verifier": verifier, "redirect_uri": redirect, "grant_type": "authorization_code"}
    if secret:
        payload["client_secret"] = secret
    values = _token_response(session, payload, cancel, scopes, service)
    if not values.get("refresh_token"):
        raise PublishError("Google 갱신 토큰을 받지 못했습니다. 앱 접근 권한을 해제한 뒤 계정을 다시 연결하세요.")
    check_cancel(cancel)
    values.update(client_id=client_id, client_secret=secret)
    return values


def _drive_api_disabled(response):
    if response.status_code != 403:
        return False
    try:
        error = response.json()["error"]
        for key in ("errors", "details"):
            entries = error.get(key)
            if isinstance(entries, list) and any(
                isinstance(entry, dict) and entry.get("reason") in ("SERVICE_DISABLED", "accessNotConfigured")
                for entry in entries
            ):
                return True
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    return False


def _drive_permission_id(values, session, cancel):
    check_cancel(cancel)
    try:
        response = session.get(
            DRIVE_ABOUT_URL,
            params={"fields": "user(permissionId)"},
            headers={"Authorization": "Bearer " + values["access_token"]},
            timeout=HTTP_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException:
        raise PublishError("Google Drive 계정 정보를 확인하지 못했습니다. 네트워크를 확인하고 다시 시도하세요.") from None
    if _drive_api_disabled(response):
        url = "https://console.cloud.google.com/apis/library/drive.googleapis.com"
        project = values["client_id"].partition("-")[0]
        if project.isascii() and project.isdecimal() and len(project) <= 32:
            url += "?project=" + project
        raise PublishError(f"Google Cloud 프로젝트에서 Google Drive API를 사용 설정한 뒤 다시 시도하세요. {url}")
    if response.status_code != 200:
        raise PublishError(f"Google Drive 계정 정보를 확인하지 못했습니다 (HTTP {response.status_code}). 계정과 Drive 접근 권한을 확인하세요.")
    try:
        permission_id = response.json()["user"]["permissionId"]
        if not _safe_text(permission_id, 256):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise PublishError("Google Drive 계정 식별자를 확인하지 못했습니다. 계정을 다시 연결하세요.") from None
    check_cancel(cancel)
    return permission_id


def connect_drive(client_file, store, progress=noop, cancel=None, session=None, open_browser=None):
    check_cancel(cancel)
    saved = None
    if client_file is None:
        saved = store.get("drive")
        if not saved.get("client_id"):
            saved = store.get("youtube")
    client = _desktop_client(client_file, saved)
    session = session if session is not None else requests.Session()
    values = _authorize(client, DRIVE_SCOPE, "Google Drive", progress, cancel, session, open_browser)
    values["permission_id"] = _drive_permission_id(values, session, cancel)
    values["scope"] = DRIVE_SCOPE
    store.put("drive", values)
    progress("auth", 100, 100, "Google Drive 계정을 연결했습니다.")
    return values


def connect_youtube(client_file, store, progress=noop, cancel=None, session=None, open_browser=None):
    check_cancel(cancel)
    client = _desktop_client(client_file)
    session = session if session is not None else requests.Session()
    values = _authorize(client, SCOPES, "유튜브", progress, cancel, session, open_browser)
    try:
        response = session.get(CHANNELS_URL, params={"part": "snippet", "mine": "true"}, headers={"Authorization": "Bearer " + values["access_token"]}, timeout=HTTP_TIMEOUT, allow_redirects=False)
    except requests.RequestException:
        raise PublishError("유튜브 채널 정보를 확인하지 못했습니다. 네트워크를 확인하고 다시 연결하세요.") from None
    if response.status_code != 200:
        raise PublishError("유튜브 채널 정보를 읽을 권한이 없습니다. 계정과 동의 권한을 확인하세요.")
    try:
        items = response.json()["items"]
        if not isinstance(items, list) or not items:
            raise ValueError
        channel = items[0]
        channel_id, title = channel["id"], channel["snippet"]["title"]
        if not _safe_text(channel_id, 128) or not isinstance(title, str):
            raise ValueError
    except (ValueError, KeyError, IndexError, TypeError):
        raise PublishError("연결된 유튜브 채널을 찾을 수 없습니다. 채널이 있는 계정을 선택하세요.") from None
    values.update(channel_id=channel_id, channel_title=title)
    check_cancel(cancel)
    store.put("youtube", values)
    progress("auth", 100, 100, "유튜브 계정을 연결했습니다.")
    return values


def youtube_credentials(store, session=None, progress=noop, cancel=None):
    return _credentials(store, "youtube", SCOPES, "유튜브", session, progress, cancel)


def drive_credentials(store, session=None, progress=noop, cancel=None):
    with _REFRESH_LOCK:
        values = _credentials(store, "drive", DRIVE_SCOPE, "Google Drive", session, progress, cancel)
        if not _safe_text(values.get("permission_id"), 256):
            session = session if session is not None else requests.Session()
            values["permission_id"] = _drive_permission_id(values, session, cancel)
            store.put("drive", values)
        return values


def _credentials(store, section, scopes, service, session, progress, cancel):
    check_cancel(cancel)
    with _REFRESH_LOCK:
        values = store.get(section)
        if section == "drive" and values.get("scope") != DRIVE_SCOPE:
            raise PublishError("Google Drive 계정을 연결하고 파일 업로드 권한에 동의하세요.")
        if not _safe_text(values.get("client_id"), 512) or not _safe_text(values.get("refresh_token")):
            raise PublishError(f"먼저 {service} 계정을 연결하세요.")
        try:
            expires_at = float(values.get("expires_at", 0))
        except (ValueError, TypeError, OverflowError):
            expires_at = 0
        if _safe_text(values.get("access_token")) and math.isfinite(expires_at) and expires_at > time.time() + 60:
            return values
        progress("auth", 0, 100, f"{service} 인증을 갱신하고 있습니다.")
        payload = {"client_id": values["client_id"], "refresh_token": values["refresh_token"], "grant_type": "refresh_token"}
        if values.get("client_secret"):
            if not _safe_text(values["client_secret"], 512):
                raise PublishError("저장된 Google 인증 정보가 올바르지 않습니다. 계정을 다시 연결하세요.")
            payload["client_secret"] = values["client_secret"]
        session = session if session is not None else requests.Session()
        refreshed = _token_response(session, payload, cancel, scopes, service)
        values.update(refreshed)
        store.put(section, values)
        progress("auth", 100, 100, f"{service} 인증을 갱신했습니다.")
        return values
