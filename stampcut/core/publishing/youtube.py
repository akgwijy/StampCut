"""YouTube resumable uploads with durable completion and ambiguity guards."""
from __future__ import annotations

import mimetypes
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import requests

from .google_oauth import HTTP_TIMEOUT, youtube_credentials
from stampcut.core.ffmpeg import Cancelled
from .models import PublishedPost, PublishError, PublishUncertain, check_cancel, noop

UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/videos"
CHUNK_SIZE = 8 * 1024 * 1024  # All nonfinal chunks must be multiples of 256 KiB.
MAX_RETRIES = 4
_RETRYABLE = {429, 500, 502, 503, 504}
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_RANGE = re.compile(r"bytes=0-([0-9]+)\Z")
_UNKNOWN = "유튜브 업로드 결과를 확인할 수 없습니다. YouTube Studio에서 게시 여부를 확인한 뒤 다시 시도하세요. 중복 업로드를 방지하기 위해 새 업로드를 시작하지 않았습니다."


def _pause(seconds, cancel):
    if cancel is not None:
        cancel.wait(seconds)
    else:
        time.sleep(seconds)
    check_cancel(cancel)


def _valid_session_url(url):
    try:
        parsed = urlsplit(url)
        return (isinstance(url, str) and not any(ord(c) < 32 or ord(c) == 127 for c in url)
                and parsed.scheme == "https" and parsed.hostname in {"www.googleapis.com", "youtube.googleapis.com"}
                and parsed.port in {None, 443} and parsed.username is None and parsed.password is None
                and parsed.path == "/upload/youtube/v3/videos" and not parsed.fragment)
    except (TypeError, ValueError, AttributeError):
        return False


def _valid_id(value):
    return isinstance(value, str) and _VIDEO_ID.fullmatch(value) is not None


def _post(video_id, already=False):
    return PublishedPost("youtube", video_id, "https://www.youtube.com/watch?v=" + video_id, already_uploaded=already)


class _Upload:
    def __init__(self, video, draft, store, state, save_state, progress, cancel, session):
        self.video, self.draft, self.store = Path(video), draft, store
        self.state, self.save_state = state, save_state
        self.progress, self.cancel = progress, cancel
        self.session = session
        try:
            self.total = self.video.stat().st_size
        except OSError:
            raise PublishError("업로드할 영상 파일을 읽을 수 없습니다.") from None
        if not self.video.is_file() or self.total <= 0:
            raise PublishError("업로드할 영상 파일이 비어 있거나 올바르지 않습니다.")
        self.mime = mimetypes.guess_type(self.video.name)[0] or "application/octet-stream"
        if not self.mime.startswith("video/"):
            self.mime = "application/octet-stream"

    def save(self, **values):
        self.state.update(values)
        self.save_state(dict(self.state))

    def fail(self, message):
        if self.state.get("final_attempted"):
            self.save(status="uncertain")
            raise PublishUncertain(_UNKNOWN)
        raise PublishError(message)

    def request(self, method, url, final=False, **kwargs):
        check_cancel(self.cancel)
        values = youtube_credentials(self.store, session=self.session, progress=self.progress, cancel=self.cancel)
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = "Bearer " + values["access_token"]
        function = getattr(self.session, method)
        def dispatch():
            check_cancel(self.cancel)
            previous=self.state.get("final_attempted",False)
            if final:
                self.save(final_attempted=True)
                if self.cancel is not None and self.cancel.is_set():
                    self.save(final_attempted=previous)
                    check_cancel(self.cancel)
            return function(url, headers=headers, timeout=HTTP_TIMEOUT, allow_redirects=False, **kwargs)
        response = dispatch()
        if response.status_code == 401:
            if final:self.save(final_attempted=False)
            check_cancel(self.cancel)
            values["expires_at"] = 0
            self.store.put("youtube", values)
            values = youtube_credentials(self.store, session=self.session, progress=self.progress, cancel=self.cancel)
            headers["Authorization"] = "Bearer " + values["access_token"]
            check_cancel(self.cancel)
            response = dispatch()
        return response

    def complete(self, response):
        try:
            data = response.json()
            video_id = data.get("id")
            if not _valid_id(video_id):
                raise ValueError
        except (ValueError, AttributeError, TypeError):
            self.save(final_attempted=True, status="uncertain")
            raise PublishUncertain(_UNKNOWN) from None
        result = _post(video_id)
        # Save confirmed publication before observing cancellation from a late click.
        self.save(status="success", id=result.id, url=result.url, offset=self.total)
        self.progress("youtube", 100, 100, "유튜브 업로드를 완료했습니다.")
        return result

    def offset(self, response, maximum):
        header = response.headers.get("Range")
        if header is None:
            return 0
        match = _RANGE.fullmatch(header) if isinstance(header, str) and len(header) <= 64 else None
        if match is None:
            self.fail("유튜브 서버의 업로드 위치가 올바르지 않습니다. 잠시 후 다시 시도하세요.")
        offset = int(match.group(1)) + 1
        if offset > maximum:
            self.fail("유튜브 서버의 업로드 위치가 영상 크기를 벗어났습니다. 잠시 후 다시 시도하세요.")
        return offset

    def probe(self):
        """Return server-confirmed offset, completion, or None for safe expiry."""
        for attempt in range(MAX_RETRIES):
            try:
                response = self.request("put", self.state["upload_url"], headers={"Content-Length": "0", "Content-Range": f"bytes */{self.total}"}, data=b"")
            except requests.RequestException:
                response = None
            if response is not None:
                if response.status_code in {200, 201}:
                    return self.complete(response)
                if response.status_code == 308:
                    offset = self.offset(response, self.total)
                    if offset < self.total:
                        self.save(offset=offset,final_attempted=False)
                        return offset
                    # The bytes are present, but only a video resource proves success.
                elif response.status_code in {404, 410}:
                    if self.state.get("final_attempted"):
                        self.fail(_UNKNOWN)
                    return None
                elif response.status_code not in _RETRYABLE:
                    self.http_error(response.status_code)
            if attempt < MAX_RETRIES - 1:
                _pause(2 ** attempt, self.cancel)
        self.fail("유튜브 업로드 상태를 확인하지 못했습니다. 네트워크를 확인하고 다시 시도하세요.")

    def http_error(self, status):
        if status == 401:
            message = "유튜브 인증이 만료되었습니다. 계정을 다시 연결하세요."
        elif status == 403:
            message = "유튜브 업로드 권한 또는 API 할당량을 확인하세요. Google Cloud 설정과 계정 권한을 확인한 뒤 다시 시도하세요."
        elif status in {404, 410}:
            message = "유튜브 업로드 세션이 만료되었습니다. 다시 시도하세요."
        else:
            message = "유튜브 업로드 요청이 거부되었습니다. 영상과 게시 설정을 확인하고 다시 시도하세요."
        self.fail(message)

    def start(self):
        payload = {"snippet": {"title": self.draft.title, "description": self.draft.body, "categoryId": "22"}, "status": {"privacyStatus": self.draft.privacy, "selfDeclaredMadeForKids": self.draft.made_for_kids}}
        try:
            response = self.request("post", UPLOAD_ENDPOINT, params={"uploadType": "resumable", "part": "snippet,status"}, json=payload, headers={"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Length": str(self.total), "X-Upload-Content-Type": self.mime})
        except requests.RequestException:
            raise PublishError("유튜브 업로드를 시작하지 못했습니다. 네트워크를 확인하고 다시 시도하세요.") from None
        if response.status_code not in {200, 201}:
            self.http_error(response.status_code)
        url = response.headers.get("Location")
        if not _valid_session_url(url):
            raise PublishError("유튜브 업로드 세션을 받지 못했습니다. 잠시 후 다시 시도하세요.")
        self.save(status="uploading", upload_url=url, offset=0, size=self.total, final_attempted=False)
        return 0

    def run(self):
        if self.state.get("size", self.total) != self.total:
            self.fail("업로드 중인 영상 크기가 변경되었습니다. 원래 영상 파일로 다시 시도하세요.")
        url = self.state.get("upload_url")
        if url:
            if not _valid_session_url(url):
                self.fail("저장된 유튜브 업로드 세션이 올바르지 않습니다.")
            offset = self.probe()
            if isinstance(offset, PublishedPost):
                return offset
            if offset is None:
                offset = self.start()
        else:
            if self.state.get("final_attempted") or self.state.get("status") == "uncertain":
                raise PublishUncertain(_UNKNOWN)
            offset = self.start()
        self.progress("youtube", min(99, offset * 100 // self.total), 100, "유튜브에 영상을 업로드하고 있습니다.")
        stalled = 0
        failures = 0
        try:
            with self.video.open("rb") as file:
                while offset < self.total:
                    check_cancel(self.cancel)
                    file.seek(offset)
                    chunk = file.read(min(CHUNK_SIZE, self.total - offset))
                    if not chunk or len(chunk) != min(CHUNK_SIZE, self.total - offset):
                        self.fail("업로드 중 영상 파일이 변경되었습니다. 원래 영상 파일로 다시 시도하세요.")
                    end = offset + len(chunk)
                    try:
                        response = self.request("put", self.state["upload_url"], final=end==self.total, headers={"Content-Type": self.mime, "Content-Length": str(len(chunk)), "Content-Range": f"bytes {offset}-{end - 1}/{self.total}"}, data=chunk)
                    except requests.RequestException:
                        response = None
                    if response is not None and response.status_code in {200, 201}:
                        if end != self.total:
                            self.save(final_attempted=True, status="uncertain")
                            raise PublishUncertain(_UNKNOWN)
                        return self.complete(response)
                    if response is not None and response.status_code == 308:
                        next_offset = self.offset(response, end)
                        if next_offset < offset:
                            self.fail("유튜브 서버의 업로드 위치가 뒤로 이동했습니다. 잠시 후 다시 시도하세요.")
                        if next_offset < self.total:
                            self.save(offset=next_offset,final_attempted=False)
                        else:
                            next_offset = self.probe()
                    elif response is None or response.status_code in _RETRYABLE:
                        failures += 1
                        if failures > MAX_RETRIES:
                            self.fail("유튜브 업로드 연결이 반복해서 끊어졌습니다. 잠시 후 다시 시도하세요.")
                        _pause(2 ** (failures - 1), self.cancel)
                        next_offset = self.probe()
                    else:
                        self.http_error(response.status_code)
                    if isinstance(next_offset, PublishedPost):
                        return next_offset
                    if next_offset is None:
                        # No final request was sent, so no video could have been created.
                        raise PublishError("유튜브 업로드 세션이 만료되었습니다. 다시 시도하면 새 세션으로 업로드합니다.")
                    if next_offset <= offset:
                        stalled += 1
                        if stalled >= MAX_RETRIES:
                            self.fail("유튜브 업로드가 진행되지 않습니다. 네트워크를 확인하고 다시 시도하세요.")
                        _pause(2 ** (stalled - 1), self.cancel)
                    else:
                        stalled = 0
                    offset = next_offset
                    self.progress("youtube", min(99, offset * 100 // self.total), 100, "유튜브에 영상을 업로드하고 있습니다.")
        except OSError:
            self.fail("업로드할 영상 파일을 읽을 수 없습니다.")
        self.fail("유튜브 업로드 완료 응답을 확인하지 못했습니다.")


def publish_youtube(video, draft, store, state, save_state, progress=noop, cancel=None, session=None):
    if state.get("status") == "success":
        if not _valid_id(state.get("id")):
            raise PublishUncertain(_UNKNOWN)
        return _post(state["id"], already=True)
    check_cancel(cancel)
    draft.validate_for("youtube")
    session = session if session is not None else requests.Session()
    upload=_Upload(video, draft, store, state, save_state, progress, cancel, session)
    try:
        return upload.run()
    except Cancelled:
        if state.get("final_attempted") and state.get("status")!="success":
            upload.save(status="uncertain")
            raise PublishUncertain(_UNKNOWN) from None
        raise
