"""Instagram Reels publishing through Meta's container publishing API.

Protocol: https://developers.facebook.com/docs/instagram-platform/content-publishing/
Unified Login and Facebook Login support resumable uploads; Instagram Login uses a public video URL.
Provider state and credentials must be persisted in the encrypted credential vault.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import time
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .models import PublishedPost, PublishError, PublishUncertain, check_cancel, noop

log = logging.getLogger(__name__)
REQUEST_TIMEOUT = (10, 60)
POLL_INTERVAL = 60
POLL_ATTEMPTS = 5
_UNKNOWN = "인스타그램 게시 결과를 확인할 수 없습니다. 중복 게시를 막기 위해 다시 게시하지 않았습니다. 인스타그램 프로필에서 게시물을 확인하세요."


class _APIError(PublishError):
    def __init__(self, message, definitive=False, retriable=None):
        super().__init__(message)
        self.definitive = definitive
        self.retriable = retriable


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9]{1,64}", value) is not None


def _configuration(account_id, access_token, method, api_version, *, discover=False):
    if not discover and not _identifier(account_id):
        raise PublishError("인스타그램 프로페셔널 계정 ID를 숫자로 입력하세요.")
    if not isinstance(access_token, str) or not access_token or any(ord(c) <= 32 or ord(c) >= 127 for c in access_token):
        raise PublishError("올바른 인스타그램 액세스 토큰을 입력하세요.")
    if method not in ("resumable", "unified", "url"):
        raise PublishError("인스타그램 업로드 방식을 선택하세요.")
    if not isinstance(api_version, str) or re.fullmatch(r"v[1-9][0-9]*\.[0-9]+", api_version) is None:
        raise PublishError("인스타그램 Graph API 버전을 v26.0 형식으로 입력하세요.")
    host = "graph.facebook.com" if method != "url" else "graph.instagram.com"
    return f"https://{host}/{api_version}"


def _display_reason(value, token):
    """Show a bounded provider explanation without echoing credentials or signed URLs."""
    if not isinstance(value, str) or value.lstrip().startswith(("{", "[")):
        return ""
    text = " ".join(value.split()).replace(token, "[가림]")
    text = re.sub(r"https?://\S+", "[URL]", text, flags=re.I)
    text = re.sub(r"\b(?:authorization|access[_ -]?token|client_secret|appsecret_proof|oauth|bearer)\b.*$",
                  "[가림]", text, flags=re.I)
    text = re.sub(r"[A-Za-z0-9_.=-]{32,}", "[가림]", text)
    return text[:280]


def _request(session, method, url, token, *, stage="계정 확인", headers=None, **kwargs):
    auth = {"Authorization": f"Bearer {token}"}
    auth.update(headers or {})
    started = time.monotonic()
    try:
        response = session.request(method, url, headers=auth, timeout=REQUEST_TIMEOUT,
                                   allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise PublishError("인스타그램 연결 시간이 초과되었거나 네트워크가 끊겼습니다. 연결을 확인한 뒤 다시 시도하세요.") from None
    try:
        payload = response.json()
    except (ValueError, TypeError):
        raise PublishError("인스타그램 서버 응답을 읽을 수 없습니다. 잠시 후 다시 시도하세요.") from None
    if not isinstance(payload, dict):
        raise PublishError("인스타그램 서버 응답 형식이 올바르지 않습니다.")
    # Upload errors can use debug_info even when the HTTP request succeeded.
    debug = payload.get("debug_info")
    debug = debug if isinstance(debug, dict) else {}
    error = payload.get("error")
    if debug and not error and isinstance(debug.get("message"), str):
        try:
            nested = json.loads(debug["message"])
        except ValueError:
            nested = None
        if isinstance(nested, dict):
            error = nested.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    processing_failed = debug.get("type") == "ProcessingFailedError"
    if not 200 <= response.status_code < 300 or error or (debug and payload.get("success") is not True):
        raw_message = error.get("message") if isinstance(error, dict) else debug.get("message")
        if code in (102, 190) or response.status_code == 401:
            message = "인스타그램 액세스 토큰이 만료되었거나 유효하지 않습니다. 계정 설정에서 새 장기 토큰으로 다시 연결하세요."
        elif code in (10, 200, 294) or response.status_code == 403:
            message = "인스타그램 게시 권한이 없습니다. 프로페셔널 계정과 업로드 방식에 맞는 기본·콘텐츠 게시 권한을 확인하세요."
        elif code in (4, 17, 32, 341, 613) or response.status_code == 429:
            message = "인스타그램 API 사용 제한에 도달했습니다. 잠시 기다린 뒤 다시 시도하세요."
        elif response.status_code >= 500:
            message = "인스타그램 서버 오류입니다. 잠시 후 다시 시도하세요."
        elif isinstance(raw_message, str) and raw_message.strip().casefold() == "unauthorized user request":
            message = "Meta가 이 요청에 필요한 권한을 승인하지 않았습니다."
        elif processing_failed:
            message = "Meta가 전송 영상을 처리하지 못했습니다."
        else:
            message = "Meta가 요청을 거절했거나 예상과 다른 응답을 반환했습니다."
        details = [f"HTTP {response.status_code}"]
        if type(code) is int:
            subcode = error.get("error_subcode")
            details.append(f"Meta {code}/{subcode}" if type(subcode) is int else f"Meta {code}")
        if processing_failed:
            details.append("ProcessingFailedError")
        message = f"{stage} ({', '.join(details)}) · {message}"
        trace = (error.get("fbtrace_id") if isinstance(error, dict) else None) or payload.get("fbtrace_id")
        trace = trace or getattr(response, "headers", {}).get("x-fb-trace-id")
        if isinstance(trace, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,96}", trace) and token not in trace:
            message += f" (Meta 추적 ID {trace})"
        retriable = debug.get("retriable")
        log.warning("%s [elapsed=%.1fs, retriable=%s]", message, time.monotonic() - started,
                    retriable if isinstance(retriable, bool) else "unknown")
        definitive = (400 <= response.status_code < 500
                      and (code in (102, 190, 10, 200, 294, 4, 17, 32, 341, 613)
                           or response.status_code in (401, 403, 429)))
        if stage == "파일 전송":
            reason = _display_reason(raw_message, token)
            if reason:
                message += f"\nMeta 상세: {reason}"
        raise _APIError(message, definitive=definitive, retriable=retriable)
    return payload


def connect_instagram(account_id, access_token, method, store, api_version="v26.0",
                      progress=noop, cancel=None, session=None):
    """Verify the supplied professional account before replacing saved credentials."""
    account_id = account_id.strip() if isinstance(account_id, str) else account_id
    access_token = access_token.strip() if isinstance(access_token, str) else access_token
    discover = method == "unified" and not account_id
    base = _configuration(account_id, access_token, method, api_version, discover=discover)
    check_cancel(cancel)
    progress("instagram", 0, 100, "인스타그램 계정 확인 중")
    with requests.Session() if session is None else nullcontext(session) as client:
        if discover:
            assigned = _request(client, "GET", f"{base}/me/assigned_instagram_accounts", access_token,
                                params={"fields": "id,username", "limit": 2})
            accounts = assigned.get("data")
            if not isinstance(accounts, list) or not accounts:
                raise PublishError("통합 로그인에서 팀 Instagram 계정을 선택하고 발급한 토큰을 입력하세요.")
            if len(accounts) != 1 or (assigned.get("paging") or {}).get("next"):
                raise PublishError("연결할 Instagram 계정의 숫자 ID를 입력하세요. 여러 계정이 승인되어 있습니다.")
            profile = accounts[0]
            if not isinstance(profile, dict) or not _identifier(profile.get("id")):
                raise PublishError("승인된 Instagram 계정 ID를 확인할 수 없습니다.")
            account_id = profile["id"]
        else:
            profile = _request(client, "GET", f"{base}/{account_id}", access_token,
                               params={"fields": "id,username"})
    username = profile.get("username")
    if profile.get("id") != account_id or not isinstance(username, str) or re.fullmatch(r"[A-Za-z0-9_.]{1,30}", username) is None:
        raise PublishError("인스타그램 프로필을 확인할 수 없습니다. 프로페셔널 계정 ID와 토큰을 확인하세요.")
    # The professional IG User endpoint supports both Business and Creator.
    # Some API variants include this field without it being requested.
    if "account_type" in profile and profile["account_type"] not in ("BUSINESS", "MEDIA_CREATOR", "CREATOR"):
        raise PublishError("인스타그램 비즈니스 또는 크리에이터 프로페셔널 계정이 필요합니다.")
    check_cancel(cancel)
    credentials = {"account_id": account_id, "access_token": access_token, "username": username,
                   "method": method, "api_version": api_version}
    store.put("instagram", credentials)
    progress("instagram", 100, 100, "인스타그램 계정 연결 완료")
    return credentials


def _public_video_url(value):
    message = "인스타그램 URL 업로드에는 외부에서 접근 가능한 공개 HTTPS 영상 URL이 필요합니다."
    try:
        if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
            raise ValueError()
        parts = urlsplit(value)
        host = parts.hostname
        if (parts.scheme != "https" or not host or parts.username is not None or parts.password is not None
                or parts.fragment or "\\" in value or "%" in host or parts.port == 0):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            host = host.rstrip(".").lower()
            if ("." not in host or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))
                    or re.fullmatch(r"[0-9.]+", host) or re.fullmatch(r"[A-Za-z0-9.-]+", host) is None):
                raise ValueError()
        else:
            if not address.is_global:
                raise ValueError()
    except (ValueError, TypeError):
        raise PublishError(message) from None
    return value


def _upload_uri(value, version, container):
    expected = f"https://rupload.facebook.com/ig-api-upload/{version}/{container}"
    # Meta documents this exact endpoint. Never follow an arbitrary server URI
    # or a redirect while carrying an access token or local video bytes.
    if value is not None and value != expected:
        raise PublishError("인스타그램 업로드 서버 주소가 올바르지 않습니다. 계정을 다시 연결하세요.")
    return expected


def _profile_url(credentials):
    username = credentials.get("username", "")
    if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_.]{1,30}", username):
        return f"https://www.instagram.com/{username}/"
    return "https://www.instagram.com/"


def _permalink(value):
    if not isinstance(value, str):
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme == "https" and parts.netloc in ("www.instagram.com", "instagram.com")
                and re.fullmatch(r"/(?:reel|p|tv)/[A-Za-z0-9_-]+/", parts.path) is not None
                and not parts.query and not parts.fragment)
    except ValueError:
        return False


def _wait(cancel):
    check_cancel(cancel)
    if cancel is None:
        time.sleep(POLL_INTERVAL)
    elif cancel.wait(POLL_INTERVAL):
        check_cancel(cancel)


class _VideoReader:
    """Stream a file with cancellation checks at every HTTP read boundary."""
    def __init__(self, file, size, progress, cancel):
        self.file, self.size, self.progress, self.cancel = file, size, progress, cancel

    def __len__(self):
        return self.size

    def read(self, size=-1):
        check_cancel(self.cancel)
        data = self.file.read(size)
        self.progress("instagram", 10 + min(50, self.file.tell() * 50 // self.size), 100, "인스타그램 영상 전송 중")
        return data

    def tell(self):
        return self.file.tell()

    def seek(self, offset, whence=0):
        return self.file.seek(offset, whence)

    def fileno(self):
        return self.file.fileno()


def _status(client, base, container, token, cancel):
    check_cancel(cancel)
    payload = _request(client, "GET", f"{base}/{container}", token, stage="영상 처리 상태 조회", params={"fields": "status_code"})
    status = payload.get("status_code")
    if status not in ("IN_PROGRESS", "FINISHED", "PUBLISHED", "ERROR", "EXPIRED"):
        raise PublishError("인스타그램 영상 처리 상태 응답을 읽을 수 없습니다.")
    return status


def _discard_container(state, save_state):
    for key in ("container_id", "upload_uri", "uploaded", "video_url", "rejected_transfer"):
        state.pop(key, None)
    state["status"] = "failed"
    save_state(state)


def _processing_failure(status, state, save_state):
    if status in ("ERROR", "EXPIRED"):
        # Neither terminal state can be published. Allow a later explicit retry
        # to create a fresh container, provided no publication was attempted.
        _discard_container(state, save_state)
        if status == "EXPIRED":
            raise PublishError("인스타그램 업로드 세션이 만료되었습니다. 다시 업로드하세요.")
        raise PublishError("인스타그램 영상 처리에 실패했습니다. 영상 형식과 계정 게시 권한을 확인한 뒤 다시 시도하세요.")
    if status == "PUBLISHED":
        raise PublishUncertain(_UNKNOWN)


def publish_instagram(video: Path, draft, store, state, save_state,
                      progress=noop, cancel=None, session=None) -> PublishedPost:
    """Publish a prepared Reel, retaining receipts and reconciling uncertain writes."""
    if state.get("status") == "success":
        if not _identifier(state.get("id")) or not isinstance(state.get("url"), str):
            raise PublishUncertain(_UNKNOWN)
        return PublishedPost("instagram", state["id"], state["url"], already_uploaded=True)
    draft.validate_for("instagram")
    credentials = store.get("instagram")
    account, token = credentials.get("account_id"), credentials.get("access_token")
    method, version = credentials.get("method"), credentials.get("api_version", "v26.0")
    base = _configuration(account, token, method, version)
    if state.get("account_id", account) != account:
        raise PublishError("인스타그램 업로드 세션의 계정이 다릅니다. 원래 계정으로 다시 연결하세요.")
    check_cancel(cancel)
    uncertain=state.get("publish_attempted") or state.get("status") in ("uncertain","unknown")
    if uncertain and not _identifier(state.get("container_id")):
        raise PublishUncertain(_UNKNOWN)
    url_mode = method == "url"
    public_url = _public_video_url(draft.instagram_video_url) if url_mode else None
    progress("instagram", 0, 100, "인스타그램 업로드 준비 중")
    with requests.Session() if session is None else nullcontext(session) as client:
        container = state.get("container_id")
        status = None
        if container:
            if not _identifier(container):
                raise PublishError("저장된 인스타그램 업로드 세션이 올바르지 않습니다.")
            if uncertain:
                try:
                    _status(client, base, container, token, cancel)
                except PublishError:
                    raise PublishUncertain(_UNKNOWN) from None
                # A container ID is not a published media ID. The API does not
                # provide a safe mapping; do not guess from recent account posts.
                raise PublishUncertain(_UNKNOWN)
            status = _status(client, base, container, token, cancel)
            if state.get("rejected_transfer") == container and status in ("IN_PROGRESS", "ERROR", "EXPIRED"):
                # A manual retry may replace an explicitly rejected upload after reconciliation.
                _discard_container(state, save_state)
                container = status = None
            else:
                _processing_failure(status, state, save_state)
        if not container:
            check_cancel(cancel)
            data = {"media_type": "REELS", "caption": draft.body}
            data.update({"video_url": public_url} if url_mode else {"upload_type": "resumable"})
            created = _request(client, "POST", f"{base}/{account}/media", token, stage="업로드 세션 생성", data=data)
            container = created.get("id")
            if not _identifier(container):
                raise PublishError("인스타그램 업로드 세션 응답을 읽을 수 없습니다.")
            upload_uri = None if url_mode else _upload_uri(created.get("uri"), version, container)
            state.update(account_id=account, container_id=container, uploaded=url_mode, status="uploading")
            if public_url:
                state["video_url"] = public_url
            if upload_uri:
                state["upload_uri"] = upload_uri
            save_state(state)
        if not url_mode and not state.get("uploaded") and status != "FINISHED":
            uri = _upload_uri(state.get("upload_uri"), version, container)
            check_cancel(cancel)
            try:
                size = Path(video).stat().st_size
                if not 0 < size <= 300_000_000:
                    raise PublishError("인스타그램 영상은 0바이트보다 크고 300MB 이하여야 합니다.")
                log.info("Instagram file transfer: %d bytes", size)
                with Path(video).open("rb") as file:
                    response = _request(client, "POST", uri, token, stage="파일 전송",
                                        headers={"Authorization": f"OAuth {token}", "offset": "0",
                                                 "file_size": str(size), "Content-Type": "application/octet-stream"},
                                        data=_VideoReader(file, size, progress, cancel))
            except _APIError as error:
                if error.retriable is False:
                    state["rejected_transfer"] = container
                    save_state(state)
                raise
            except OSError:
                raise PublishError("인스타그램에 전송할 영상 파일을 읽을 수 없습니다.") from None
            if response.get("success") is not True:
                raise PublishError("인스타그램 영상 전송에 실패했습니다. 계정 토큰·게시 권한·영상 형식을 확인하세요.")
            state["uploaded"] = True
            save_state(state)
            status = None
        # An existing status result counts toward the bounded polling budget.
        for attempt in range(POLL_ATTEMPTS):
            if status is None:
                status = _status(client, base, container, token, cancel)
            _processing_failure(status, state, save_state)
            if status == "FINISHED":
                break
            progress("instagram", 65 + attempt * 4, 100, "인스타그램 영상 처리 대기 중")
            if attempt + 1 == POLL_ATTEMPTS:
                raise PublishError("인스타그램 영상 처리 대기 시간이 초과되었습니다. 잠시 후 다시 시도하면 기존 업로드를 확인합니다.")
            _wait(cancel)
            status = None
        check_cancel(cancel)
        progress("instagram", 90, 100, "인스타그램 게시 중")
        check_cancel(cancel)
        state["publish_attempted"] = True
        save_state(state)
        try:
            published = _request(client, "POST", f"{base}/{account}/media_publish", token, stage="릴스 게시", data={"creation_id": container})
            media_id = published.get("id")
            if not _identifier(media_id):
                raise PublishError("인스타그램 게시 응답을 읽을 수 없습니다.")
        except PublishError as error:
            if isinstance(error, _APIError) and error.definitive:
                # An explicit authentication/permission/rate rejection confirms
                # publication was not accepted. A renewed token may retry safely.
                state.update(publish_attempted=False, status="failed")
                save_state(state)
                raise error from None
            state["status"] = "unknown"
            save_state(state)
            raise PublishUncertain(f"{_UNKNOWN}\n{error}") from None
        # Save known success before optional permalink I/O or a late cancel.
        state.update(status="success", id=media_id, url=_profile_url(credentials))
        save_state(state)
        if cancel is None or not cancel.is_set():
            try:
                media = _request(client, "GET", f"{base}/{media_id}", token, params={"fields": "permalink"})
            except PublishError:
                pass
            else:
                if _permalink(media.get("permalink")):
                    state["url"] = media["permalink"]
                    save_state(state)
        message = "인스타그램 게시 완료" if _permalink(state["url"]) else "인스타그램 게시 완료 · 링크는 계정 프로필입니다"
        progress("instagram", 100, 100, message)
        return PublishedPost("instagram", media_id, state["url"])


def validate_video_url(value):
    """Validate the transport URL during batch preflight, before any upload."""
    return _public_video_url(value)
