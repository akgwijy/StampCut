"""Temporarily host one prepared Reel in the user's Google Drive."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import urlencode, urljoin, urlsplit

import requests

from . import google_oauth
from .models import PublishError, check_cancel, noop

FILES = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FIELDS = "id,size,sha256Checksum,mimeType,webContentLink,resourceKey,trashed"
CHUNK_SIZE = 8 * 1024 * 1024
TIMEOUT = (10, 90)
PUBLIC_ERROR = "Google Drive에서 로그인 없이 MP4 원본을 받을 수 없습니다. 외부 공유, 다운로드 제한과 다운로드 할당량을 확인하세요."


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
        raise PublishError("Google Drive 파일 또는 권한 ID를 확인할 수 없습니다.")
    return value


def _url(value, *, upload=False):
    try:
        parts = urlsplit(value)
        if (not isinstance(value, str) or len(value) > 8192 or not value.isascii()
                or any(ord(c) < 33 or ord(c) == 127 for c in value) or "\\" in value
                or parts.scheme != "https" or parts.port not in (443, None)
                or parts.username is not None or parts.password is not None or parts.fragment):
            raise ValueError
        if upload:
            if parts.hostname != "www.googleapis.com" or parts.path != "/upload/drive/v3/files":
                raise ValueError
        elif (parts.hostname not in ("drive.google.com", "drive.usercontent.google.com")
              and not (parts.hostname or "").endswith(".googleusercontent.com")):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise PublishError("Google Drive가 반환한 전송 주소를 확인할 수 없습니다.") from None
    return value


def _call(method, url, *, token=None, headers=None, **kwargs):
    headers = dict(headers or {})
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        # A fresh request per hop carries no cookies. Explicit auth prevents
        # requests from using .netrc credentials while preserving system proxies.
        return requests.request(method, url, headers=headers, auth=lambda request: request,
                                allow_redirects=False, timeout=TIMEOUT, **kwargs)
    except requests.RequestException:
        raise PublishError("Google Drive에 연결하지 못했습니다. 네트워크를 확인하고 다시 시도하세요.") from None


def _json(response):
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    raise PublishError("Google Drive 응답을 읽을 수 없습니다. 다시 시도하세요.")


def _ok(response, *, sharing=False):
    if response.status_code in (200, 201, 204):
        return
    try:
        error = response.json()["error"]
        reasons = {item.get("reason") for item in error.get("errors", []) + error.get("details", [])
                   if isinstance(item, dict)}
    except (ValueError, KeyError, TypeError, AttributeError):
        reasons = set()
    if reasons.intersection({"accessNotConfigured", "SERVICE_DISABLED"}):
        raise PublishError("OAuth 클라이언트의 Google Cloud 프로젝트에서 Google Drive API를 사용 설정하세요.")
    if response.status_code == 401:
        raise PublishError("Google Drive 인증이 만료되었습니다. 계정을 다시 연결하세요.")
    if response.status_code == 429 or reasons.intersection({"rateLimitExceeded", "userRateLimitExceeded", "downloadQuotaExceeded"}):
        raise PublishError("Google Drive 요청 한도에 도달했습니다. 잠시 후 다시 시도하세요.")
    if "storageQuotaExceeded" in reasons:
        raise PublishError("Google Drive 저장 공간을 확보한 뒤 다시 시도하세요.")
    if response.status_code == 403 and sharing:
        raise PublishError("Google Drive 공유 권한이 거부됐습니다. Workspace 관리자의 외부 링크 공유 정책과 파일 권한을 확인하세요.")
    raise PublishError(f"Google Drive 요청을 완료하지 못했습니다 (HTTP {response.status_code}). 계정 권한과 네트워크를 확인하세요.")


def _local_file(video, cancel):
    digest = hashlib.sha256()
    with video.open("rb") as source:
        prefix = source.read(32)
        if len(prefix) < 12 or prefix[4:8] != b"ftyp":
            raise PublishError("릴스 호환 MP4 파일을 다시 준비하세요.")
        digest.update(prefix)
        size = len(prefix)
        while block := source.read(CHUNK_SIZE):
            check_cancel(cancel)
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest(), prefix


def _session_offset(response, size):
    received = response.headers.get("Range")
    if received is None:
        return 0
    match = re.fullmatch(r"bytes=0-(\d+)", received)
    if not match or int(match[1]) >= size:
        raise PublishError("Google Drive 전송 위치를 확인할 수 없습니다. 다시 시도하세요.")
    return int(match[1]) + 1


def _upload(video, display_name, size, token, state, save_state, progress, cancel):
    session = state.get("drive_session")
    offset = 0
    if session:
        session = _url(session, upload=True)
        response = _call("PUT", session, token=token, data=b"", headers={"Content-Range": f"bytes */{size}"})
        if response.status_code in (200, 201):
            return _json(response)
        if response.status_code == 308:
            offset = _session_offset(response, size)
        elif response.status_code in (404, 410):
            session = None
        else:
            _ok(response)
    if not session:
        response = _call("POST", UPLOAD, token=token, params={"uploadType": "resumable", "fields": FIELDS},
                         json={"id": state["drive_file_id"], "name": display_name or video.name, "mimeType": "video/mp4"},
                         headers={"X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"})
        _ok(response)
        session = _url(response.headers.get("Location"), upload=True)
        state["drive_session"] = session
        save_state(state)
    with video.open("rb") as source:
        while offset < size:
            check_cancel(cancel)
            source.seek(offset)
            block = source.read(min(CHUNK_SIZE, size - offset))
            if not block:
                raise PublishError("전송 중 MP4 파일이 변경됐습니다. 호환 파일을 다시 준비하세요.")
            end = offset + len(block)
            response = _call("PUT", session, token=token, data=block, headers={
                "Content-Type": "video/mp4", "Content-Range": f"bytes {offset}-{end - 1}/{size}"})
            if response.status_code in (200, 201):
                progress("drive", size, size, "Google Drive 업로드 완료")
                return _json(response)
            if response.status_code != 308:
                _ok(response)
            confirmed = _session_offset(response, size)
            if not offset < confirmed <= end:
                raise PublishError("Google Drive 전송 위치를 확인할 수 없습니다. 다시 시도하세요.")
            offset = confirmed
            progress("drive", offset, size, "Google Drive에 호환 MP4 업로드 중")
    raise PublishError("Google Drive가 업로드 완료를 확인하지 않았습니다. 다시 시도하세요.")


def _permissions(file_url, token):
    result = []
    page = None
    while True:
        params = {"fields": "nextPageToken,permissions(id,type,role,allowFileDiscovery)", "pageSize": 100}
        if page:
            params["pageToken"] = page
        response = _call("GET", file_url + "/permissions", token=token, params=params)
        _ok(response, sharing=True)
        data = _json(response)
        entries = data.get("permissions")
        if not isinstance(entries, list) or not all(isinstance(p, dict) for p in entries):
            raise PublishError("Google Drive 공유 권한을 확인할 수 없습니다.")
        result.extend(p for p in entries if p.get("type") == "anyone")
        page = data.get("nextPageToken")
        if not page:
            return result


def _owned_permission(public, state):
    tracked = state.get("drive_permission_id")
    if len(public) == 1 and (tracked or state.get("drive_share_pending")):
        perm = public[0]
        if (perm.get("role") == "reader" and perm.get("allowFileDiscovery") is False
                and (not tracked or perm.get("id") == tracked)):
            return _identifier(perm.get("id"))
    raise PublishError("Google Drive 파일의 공유 권한이 외부에서 변경됐습니다. Drive에서 링크 공유 상태를 확인하세요.")


def _share(file_url, token, state, save_state):
    public = _permissions(file_url, token)
    if public:
        permission = _owned_permission(public, state)
    else:
        state.pop("drive_permission_id", None)
        state["drive_share_pending"] = True
        save_state(state)
        response = _call("POST", file_url + "/permissions", token=token, params={"fields": "id"},
                         json={"type": "anyone", "role": "reader", "allowFileDiscovery": False})
        _ok(response, sharing=True)
        permission = _identifier(_json(response).get("id"))
    state["drive_permission_id"] = permission
    state.pop("drive_share_pending", None)
    save_state(state)


class _DownloadForm(HTMLParser):
    """Read only the known Drive download confirmation form."""
    def __init__(self):
        super().__init__()
        self.active = False
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.active = (attrs.get("id") == "download-form" and (attrs.get("method") or "get").lower() == "get"
                           and attrs.get("action") == "https://drive.usercontent.google.com/download")
        elif tag == "input" and self.active and attrs.get("type") == "hidden":
            key, value = attrs.get("name"), attrs.get("value")
            if key in {"id", "export", "confirm", "uuid", "at", "resourcekey"} and isinstance(value, str) and len(value) <= 2048:
                self.fields[key] = value

    def handle_endtag(self, tag):
        if tag == "form":
            self.active = False


def _probe_link(link, file_id, prefix, cancel):
    """Return whether the URL serves the MP4 and any anonymous confirmation URL."""
    candidate = link
    for _ in range(6):
        check_cancel(cancel)
        try:
            candidate = _url(candidate)
        except PublishError:
            # A login redirect is a failed anonymous probe. The canonical
            # download endpoint may still work without following that redirect.
            break
        with _call("GET", candidate, stream=True) as response:
            if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("Location"):
                candidate = urljoin(candidate, response.headers["Location"])
                continue
            if response.status_code != 200:
                break
            is_html = response.headers.get("Content-Type", "").lower().startswith("text/html")
            buffer = bytearray()
            try:
                for block in response.iter_content(8192):
                    check_cancel(cancel)
                    buffer.extend(block)
                    if len(buffer) >= len(prefix):
                        if buffer.startswith(prefix):
                            return True, None
                        if not is_html or len(buffer) >= 65536:
                            break
            except requests.RequestException:
                raise PublishError(PUBLIC_ERROR) from None
            if is_html:
                form = _DownloadForm()
                form.feed(buffer.decode("utf-8", errors="replace"))
                if form.fields.get("id") == file_id and form.fields.get("confirm"):
                    return False, "https://drive.usercontent.google.com/download?" + urlencode(form.fields)
            break
    return False, None


def _public_link(meta, prefix, cancel):
    # Prefer stable entry URLs to per-download confirmation URLs or CDN redirects.
    file_id = _identifier(meta["id"])
    params = {"id": file_id, "export": "download", "confirm": "t"}
    if meta.get("resourceKey"):
        params["resourcekey"] = meta["resourceKey"]
    direct = "https://drive.usercontent.google.com/download?" + urlencode(params)
    confirmations = []
    candidates = [link for link in (meta.get("webContentLink"), direct) if link]
    for link in dict.fromkeys(candidates):
        valid, confirmation = _probe_link(link, file_id, prefix, cancel)
        if valid:
            return link
        if confirmation and confirmation not in candidates:
            confirmations.append(confirmation)
    for link in dict.fromkeys(confirmations):
        valid, _ = _probe_link(link, file_id, prefix, cancel)
        if valid:
            return link
    raise PublishError(PUBLIC_ERROR)

def _clear_transfer(state):
    for field in ("drive_file_id", "drive_sha256", "drive_session", "drive_complete", "drive_account"):
        state.pop(field, None)


def ensure_shared_video(video: Path, store, state: dict, save_state, progress=noop, cancel=None, *, display_name: str | None = None) -> str:
    """Return an anonymously verified URL for these exact prepared bytes."""
    check_cancel(cancel)
    video = Path(video)
    size, sha, prefix = _local_file(video, cancel)
    credentials = google_oauth.drive_credentials(store, progress=progress, cancel=cancel)
    token = credentials["access_token"]
    account = credentials["permission_id"]
    if state.get("drive_account", account) != account or state.get("drive_sha256", sha) != sha:
        if state.get("drive_permission_id") or state.get("drive_share_pending"):
            revoke_video_share(store, state, save_state)
        _clear_transfer(state)
    meta = None
    if state.get("drive_file_id"):
        file_url = FILES + "/" + _identifier(state["drive_file_id"])
        response = _call("GET", file_url, token=token, params={"fields": FIELDS})
        if response.status_code != 404:
            _ok(response)
            meta = _json(response)
        if (meta and meta.get("trashed")) or (meta is None and state.get("drive_complete")):
            if state.get("drive_permission_id") or state.get("drive_share_pending"):
                raise PublishError("공유 중인 Google Drive 파일을 찾을 수 없습니다. 휴지통과 연결된 계정을 확인하세요.")
            _clear_transfer(state)
            meta = None
    if not state.get("drive_file_id"):
        response = _call("GET", FILES + "/generateIds", token=token, params={"count": 1, "space": "drive"})
        _ok(response)
        ids = _json(response).get("ids")
        if not isinstance(ids, list) or len(ids) != 1:
            raise PublishError("Google Drive 파일 ID를 발급받지 못했습니다.")
        state.update(drive_file_id=_identifier(ids[0]), drive_sha256=sha, drive_account=account)
        save_state(state)
    file_url = FILES + "/" + _identifier(state["drive_file_id"])
    if meta is None:
        check_cancel(cancel)
        progress("drive", 0, size, "Google Drive에 호환 MP4 업로드 중")
        meta = _upload(video, display_name, size, token, state, save_state, progress, cancel)
    if (meta.get("id") != state["drive_file_id"] or meta.get("sha256Checksum") != sha
            or meta.get("size") != str(size) or meta.get("mimeType") != "video/mp4"):
        if not (state.get("drive_permission_id") or state.get("drive_share_pending")):
            _clear_transfer(state)
            save_state(state)
        raise PublishError("Google Drive 파일이 준비한 MP4와 일치하지 않습니다. 파일을 확인하고 다시 시도하세요.")
    state["drive_complete"] = True
    state.pop("drive_session", None)
    save_state(state)
    check_cancel(cancel)
    _share(file_url, token, state, save_state)
    progress("drive", size, size, "Drive 링크에서 MP4 다운로드 확인 중")
    return _public_link(meta, prefix, cancel)


def revoke_video_share(store, state: dict, save_state) -> None:
    """Remove the app's unlisted reader permission, keeping the video in Drive."""
    if not (state.get("drive_permission_id") or state.get("drive_share_pending")):
        return
    credentials = google_oauth.drive_credentials(store)
    if state.get("drive_account", credentials["permission_id"]) != credentials["permission_id"]:
        raise PublishError("임시 공유를 해제하려면 파일을 업로드한 Google Drive 계정을 다시 연결하세요.")
    token = credentials["access_token"]
    file_url = FILES + "/" + _identifier(state.get("drive_file_id"))
    public = _permissions(file_url, token)
    if public:
        permission = _owned_permission(public, state)
        response = _call("DELETE", file_url + "/permissions/" + permission, token=token)
        if response.status_code != 404:
            _ok(response, sharing=True)
    state.pop("drive_permission_id", None)
    state.pop("drive_share_pending", None)
    save_state(state)
