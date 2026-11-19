import copy
import importlib
import threading

import pytest
import requests

from stampcut.core.ffmpeg import Cancelled
from stampcut.core.publishing.credentials import CredentialStore
from stampcut.core.publishing.models import PublishError, PublishUncertain, ReleaseDraft

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=private-session"
CHUNK = 256 * 1024


def youtube():
    return importlib.import_module("stampcut.core.publishing.youtube")


class Response:
    def __init__(self, status=200, data=None, headers=None, action=None):
        self.status_code = status
        self.data = data
        self.headers = headers or {}
        self.action = action

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class Session:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def _call(self, method, url, **kwargs):
        assert kwargs.get("timeout"), "HTTP timeouts must be finite"
        assert kwargs.get("allow_redirects") is False, "Redirects must not receive tokens"
        self.calls.append((method, url, copy.deepcopy(kwargs)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if response.action:
            response.action()
        return response

    def post(self, url, **kwargs):
        return self._call("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._call("PUT", url, **kwargs)


def setup(tmp_path, monkeypatch):
    mod = youtube()
    monkeypatch.setattr(mod, "CHUNK_SIZE", CHUNK)
    monkeypatch.setattr(mod, "_pause", lambda seconds, cancel: mod.check_cancel(cancel))
    store = CredentialStore(tmp_path / "test-vault", protect=lambda b: b, unprotect=lambda b: b)
    store.put("youtube", {"client_id": "id", "access_token": "access", "refresh_token": "renew", "expires_at": 9999999999, "channel_id": "UCchannel"})
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"a" * CHUNK + b"tail")
    return mod, store, path


def started():
    return Response(headers={"Location": UPLOAD_URL})


def success(action=None):
    return Response(201, {"kind": "youtube#video", "id": "AbCdEfGhI12"}, action=action)


def test_metadata_chunks_progress_and_success_receipt(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    state, saved, progress = {}, [], []
    session = Session([started(), Response(308, headers={"Range": "bytes=0-262143"}), success()])
    draft = ReleaseDraft(title="한글 제목", body="설명\n#shorts", privacy="unlisted", made_for_kids=True)
    result = mod.publish_youtube(path, draft, store, state, lambda v: saved.append(copy.deepcopy(v)), session=session, progress=lambda *args: progress.append(args))
    assert result.id == "AbCdEfGhI12"
    assert result.url == "https://www.youtube.com/watch?v=AbCdEfGhI12"
    assert state["status"] == "success"
    assert saved[-1]["id"] == result.id
    assert any(v.get("final_attempted") and v.get("status") != "success" for v in saved)
    payload = session.calls[0][2]["json"]
    assert payload == {"snippet": {"title": "한글 제목", "description": "설명\n#shorts", "categoryId": "22"}, "status": {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": True}}
    assert session.calls[0][2]["params"] == {"uploadType": "resumable", "part": "snippet,status"}
    assert session.calls[1][2]["headers"]["Content-Range"] == "bytes 0-262143/262148"
    assert session.calls[1][2]["data"] == b"a" * CHUNK
    assert session.calls[2][2]["headers"]["Content-Range"] == "bytes 262144-262147/262148"
    assert session.calls[2][2]["data"] == b"tail"
    assert progress[-1][0:3] == ("youtube", 100, 100)
    assert all(0 <= p[1] <= p[2] == 100 for p in progress if p[0] == "youtube")
    assert "access" not in repr(progress)


def test_completed_receipt_is_reused_even_if_cancelled_without_network(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    event = threading.Event()
    event.set()
    result = mod.publish_youtube(path, ReleaseDraft(title="title"), store, {"status": "success", "id": "AbCdEfGhI12", "url": "https://evil.invalid"}, lambda v: None, session=Session(), cancel=event)
    assert result.already_uploaded is True
    assert result.url == "https://www.youtube.com/watch?v=AbCdEfGhI12"


def test_resume_probes_server_and_does_not_trust_local_offset(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    state = {"upload_url": UPLOAD_URL, "offset": 999999, "size": path.stat().st_size}
    session = Session([Response(308, headers={"Range": "bytes=0-262143"}), success()])
    mod.publish_youtube(path, ReleaseDraft(title="title"), store, state, lambda v: None, session=session)
    assert session.calls[0][2]["headers"]["Content-Range"] == "bytes */262148"
    assert session.calls[0][2]["data"] == b""
    assert session.calls[1][2]["data"] == b"tail"


def test_probe_finds_prior_success_without_sending_file(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    state = {"upload_url": UPLOAD_URL, "final_attempted": True}
    session = Session([success()])
    result = mod.publish_youtube(path, ReleaseDraft(title="title"), store, state, lambda v: None, session=session)
    assert result.id == "AbCdEfGhI12"
    assert len(session.calls) == 1
    assert state["status"] == "success"


def test_server_error_probes_then_resumes_acknowledged_bytes(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    session = Session([started(), Response(503), Response(308, headers={"Range": "bytes=0-262143"}), success()])
    result = mod.publish_youtube(path, ReleaseDraft(title="title"), store, {}, lambda v: None, session=session)
    assert result.id == "AbCdEfGhI12"
    assert session.calls[2][2]["headers"]["Content-Range"] == "bytes */262148"
    assert session.calls[3][2]["data"] == b"tail"


def test_lost_final_response_reconciles_completion(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    session = Session([started(), Response(308, headers={"Range": "bytes=0-262143"}), requests.Timeout("SECRET"), success()])
    state = {}
    result = mod.publish_youtube(path, ReleaseDraft(title="title"), store, state, lambda v: None, session=session)
    assert result.id == "AbCdEfGhI12"
    assert state["status"] == "success"
    assert len([c for c in session.calls if c[0] == "POST"]) == 1


def test_ambiguous_final_expiry_is_never_recreated_on_retry(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    session = Session([started(), Response(308, headers={"Range": "bytes=0-262143"}), requests.Timeout("SECRET"), Response(404)])
    state = {}
    with pytest.raises(PublishUncertain) as error:
        mod.publish_youtube(path, ReleaseDraft(title="title"), store, state, lambda v: None, session=session)
    assert "Studio" in str(error.value)
    assert "SECRET" not in str(error.value)
    assert "private-session" not in str(error.value)
    retry = Session([Response(404)])
    with pytest.raises(PublishUncertain):
        mod.publish_youtube(path, ReleaseDraft(title="title"), store, state, lambda v: None, session=retry)
    assert all(call[0] == "PUT" for call in retry.calls)


def test_cancel_after_success_response_still_saves_success(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    event = threading.Event()
    session = Session([started(), Response(308, headers={"Range": "bytes=0-262143"}), success(event.set)])
    state, saved = {}, []
    result = mod.publish_youtube(path, ReleaseDraft(title="title"), store, state, lambda v: saved.append(copy.deepcopy(v)), session=session, cancel=event)
    assert result.id == "AbCdEfGhI12"
    assert saved[-1]["status"] == "success"


def test_cancel_between_chunks_keeps_session_for_resume(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    event = threading.Event()
    session = Session([started(), Response(308, headers={"Range": "bytes=0-262143"}, action=event.set)])
    state = {}
    with pytest.raises(Cancelled):
        mod.publish_youtube(path, ReleaseDraft(title="title"), store, state, lambda v: None, session=session, cancel=event)
    assert state["upload_url"] == UPLOAD_URL
    assert state["offset"] == CHUNK
    assert not state.get("final_attempted")


@pytest.mark.parametrize("header", ["bytes=1-12", "bytes=0-999999999", "not-a-range", "bytes=0--1"])
def test_bad_server_offset_stops_before_sending_unacknowledged_data(tmp_path, monkeypatch, header):
    mod, store, path = setup(tmp_path, monkeypatch)
    session = Session([Response(308, headers={"Range": header})])
    with pytest.raises(PublishError):
        mod.publish_youtube(path, ReleaseDraft(title="title"), store, {"upload_url": UPLOAD_URL}, lambda v: None, session=session)
    assert len(session.calls) == 1


@pytest.mark.parametrize("response", [Response(201, {}), Response(201, ValueError("SECRET")), Response(201, {"id": "https://evil.invalid/?SECRET"})])
def test_malformed_completion_is_uncertain_and_keeps_final_marker(tmp_path, monkeypatch, response):
    mod, store, path = setup(tmp_path, monkeypatch)
    path.write_bytes(b"video")
    state = {}
    with pytest.raises(PublishUncertain) as error:
        mod.publish_youtube(path, ReleaseDraft(title="title"), store, state, lambda v: None, session=Session([started(), response]))
    assert "SECRET" not in str(error.value)
    assert state["final_attempted"] is True


@pytest.mark.parametrize("url", ["http://www.googleapis.com/upload/youtube/v3/videos", "https://www.googleapis.com.evil.invalid/upload/youtube/v3/videos", "https://user:pass@www.googleapis.com/upload/youtube/v3/videos", "https://www.googleapis.com/other"])
def test_invalid_session_url_never_receives_access_token(tmp_path, monkeypatch, url):
    mod, store, path = setup(tmp_path, monkeypatch)
    session = Session()
    with pytest.raises(PublishError):
        mod.publish_youtube(path, ReleaseDraft(title="title"), store, {"upload_url": url}, lambda v: None, session=session)
    assert session.calls == []


def test_missing_session_after_final_attempt_requires_studio_check(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    with pytest.raises(PublishUncertain):
        mod.publish_youtube(path, ReleaseDraft(title="title"), store, {"final_attempted": True}, lambda v: None, session=Session())


def test_invalid_metadata_does_not_start_upload(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    with pytest.raises(PublishError):
        mod.publish_youtube(path, ReleaseDraft(title=""), store, {}, lambda v: None, session=Session())


def test_expired_nonfinal_session_can_restart_without_duplicate_publication(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    state = {"upload_url": UPLOAD_URL, "final_attempted": False}
    session = Session([Response(404), started(), Response(308, headers={"Range": "bytes=0-262143"}), success()])
    result = mod.publish_youtube(path, ReleaseDraft(title="title"), store, state, lambda v: None, session=session)
    assert result.id == "AbCdEfGhI12"
    assert len([c for c in session.calls if c[0] == "POST"]) == 1


def test_persistent_server_errors_stop_after_bounded_status_probes(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    session = Session([Response(503) for _ in range(4)])
    with pytest.raises(PublishUncertain):
        mod.publish_youtube(path, ReleaseDraft(title="title"), store, {"upload_url": UPLOAD_URL, "final_attempted": True}, lambda v: None, session=session)
    assert len(session.calls) == 4


def test_repeated_zero_progress_stops_without_infinite_reupload(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    session = Session([started()] + [Response(308) for _ in range(4)])
    with pytest.raises(PublishError):
        mod.publish_youtube(path, ReleaseDraft(title="title"), store, {}, lambda v: None, session=session)
    assert len(session.calls) == 5


def test_upload_401_refreshes_token_once_and_retries_same_request(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    session = Session([Response(401), Response(data={"access_token": "fresh-access", "expires_in": 3600}), started(), Response(308, headers={"Range": "bytes=0-262143"}), success()])
    result = mod.publish_youtube(path, ReleaseDraft(title="title"), store, {}, lambda v: None, session=session)
    assert result.id == "AbCdEfGhI12"
    assert session.calls[2][2]["headers"]["Authorization"] == "Bearer fresh-access"
    assert session.calls[0][2]["json"] == session.calls[2][2]["json"]


def test_extremely_long_server_range_is_a_sanitized_protocol_error(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    with pytest.raises(PublishError):
        mod.publish_youtube(path, ReleaseDraft(title="title"), store, {"upload_url": UPLOAD_URL}, lambda v: None, session=Session([Response(308, headers={"Range": "bytes=0-" + "9" * 5000})]))


def test_early_completion_never_skips_the_remaining_video_bytes(tmp_path, monkeypatch):
    mod, store, path = setup(tmp_path, monkeypatch)
    state = {}
    with pytest.raises(PublishUncertain):
        mod.publish_youtube(path, ReleaseDraft(title="title"), store, state, lambda v: None, session=Session([started(), success()]))
    assert state.get("status") != "success"


def test_cancel_after_lost_final_response_reports_uncertain(tmp_path,monkeypatch):
    mod,store,path=setup(tmp_path,monkeypatch)
    event=threading.Event()
    class LostFinalSession(Session):
        def put(self,url,**kwargs):
            if kwargs.get("headers",{}).get("Content-Range")=="bytes 262144-262147/262148":
                event.set()
            return super().put(url,**kwargs)
    session=LostFinalSession([started(),Response(308,headers={"Range":"bytes=0-262143"}),requests.Timeout("SECRET")])
    state={}
    with pytest.raises(PublishUncertain) as error:
        mod.publish_youtube(path,ReleaseDraft(title="title"),store,state,lambda v:None,session=session,cancel=event)
    assert state["status"]=="uncertain" and state["final_attempted"]
    assert "SECRET" not in str(error.value)


def test_cancel_during_final_token_refresh_does_not_dispatch_final_put(tmp_path,monkeypatch):
    mod,store,path=setup(tmp_path,monkeypatch)
    event=threading.Event();count=[0]
    def credentials(*args,**kwargs):
        count[0]+=1
        if count[0]==3:event.set()
        return {"access_token":"dummy"}
    monkeypatch.setattr(mod,"youtube_credentials",credentials)
    state={};session=Session([started(),Response(308,headers={"Range":"bytes=0-262143"}),success()])
    with pytest.raises(Cancelled):
        mod.publish_youtube(path,ReleaseDraft(title="title"),store,state,lambda v:None,session=session,cancel=event)
    assert len(session.calls)==2 and not state.get("final_attempted")


def test_cancel_after_server_confirms_incomplete_final_chunk_is_cancelled(tmp_path,monkeypatch):
    mod,store,path=setup(tmp_path,monkeypatch)
    event=threading.Event()
    session=Session([started(),Response(308,headers={"Range":"bytes=0-262143"}),Response(308,headers={"Range":"bytes=0-262143"},action=event.set)])
    state={}
    with pytest.raises(Cancelled):
        mod.publish_youtube(path,ReleaseDraft(title="title"),store,state,lambda v:None,session=session,cancel=event)
    assert not state.get("final_attempted")
