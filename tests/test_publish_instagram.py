from __future__ import annotations

import copy
import importlib
import threading

import pytest
import requests

from stampcut.core.ffmpeg import Cancelled
from stampcut.core.publishing.credentials import CredentialStore
from stampcut.core.publishing.models import PublishError, PublishUncertain, ReleaseDraft


TOKEN = "test-only-private-token"
BASE = "https://graph.facebook.com/v26.0"
UPLOAD = "https://rupload.facebook.com/ig-api-upload/v26.0/200"
LINK = "https://www.instagram.com/reel/abc123/"


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Session:
    """External HTTP boundary only; streams are consumed like requests does."""
    def __init__(self, *responses, on_request=None):
        self.responses = list(responses)
        self.calls = []
        self.on_request = on_request

    def request(self, method, url, **kwargs):
        call = {"method": method, "url": url, **kwargs}
        data = kwargs.get("data")
        if hasattr(data, "read"):
            chunks = []
            while chunk := data.read(2):
                chunks.append(chunk)
            call["body"] = b"".join(chunks)
        self.calls.append(call)
        if self.on_request:
            self.on_request(call)
        assert self.responses, "unexpected HTTP request"
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def store(tmp_path):
    vault = CredentialStore(tmp_path / "vault.bin", protect=lambda b: b, unprotect=lambda b: b)
    vault.put("instagram", {"account_id": "100", "access_token": TOKEN,
                           "username": "sample_user", "method": "resumable", "api_version": "v26.0"})
    return vault


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "sample.mp4"
    path.write_bytes(b"synthetic-mp4-bytes")
    return path


def provider():
    return importlib.import_module("stampcut.core.publishing.instagram")


def happy_session():
    return Session(Response({"id": "200", "uri": UPLOAD}), Response({"success": True}),
                   Response({"id": "200", "status_code": "FINISHED"}),
                   Response({"id": "300"}), Response({"id": "300", "permalink": LINK}))


def publish(video, store, session, state=None, **kwargs):
    state = {} if state is None else state
    snapshots = []
    result = provider().publish_instagram(video, ReleaseDraft(body="한글 caption #reels"), store,
                                          state, lambda s: snapshots.append(copy.deepcopy(s)),
                                          session=session, **kwargs)
    return result, state, snapshots


@pytest.mark.parametrize("method,host", [("resumable", "graph.facebook.com"), ("url", "graph.instagram.com")])
def test_connect_verifies_profile_before_storing(method, host, store):
    session = Session(Response({"id": "123", "username": "new_user"}))
    result = provider().connect_instagram("123", TOKEN, method, store, session=session)
    assert result["username"] == "new_user"
    assert store.get("instagram")["account_id"] == "123"
    assert session.calls[0]["url"] == f"https://{host}/v26.0/123"
    assert session.calls[0]["params"] == {"fields": "id,username"}
    assert session.calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in session.calls[0]["url"]


@pytest.mark.parametrize("account,token,method,version", [
    ("../100", TOKEN, "resumable", "v26.0"), ("100", "", "resumable", "v26.0"),
    ("100", TOKEN, "bad", "v26.0"), ("100", TOKEN, "url", "v26.0/evil"),
    ("100", "bad\r\nheader", "url", "v26.0"),
])
def test_connect_rejects_invalid_configuration_before_http(account, token, method, version, store):
    session = Session()
    with pytest.raises(PublishError):
        provider().connect_instagram(account, token, method, store, api_version=version, session=session)
    assert not session.calls
    assert store.get("instagram")["account_id"] == "100"


@pytest.mark.parametrize("payload", [{"id": "123"}, {"id": "456", "username": "other"},
                                    {"id": "123", "username": "../evil"}, [], ValueError(TOKEN)])
def test_connect_preserves_existing_account_on_invalid_profile(payload, store):
    with pytest.raises(PublishError) as error:
        provider().connect_instagram("123", TOKEN, "url", store, session=Session(Response(payload)))
    assert TOKEN not in str(error.value)
    assert store.get("instagram")["account_id"] == "100"


@pytest.mark.parametrize("method", ["resumable", "unified"])
def test_resumable_upload_posts_bytes_and_records_receipt_before_permalink(video, store, method):
    store.put("instagram", {**store.get("instagram"), "method": method})
    session = happy_session()
    updates = []
    result, state, snapshots = publish(video, store, session,
                                       progress=lambda *args: updates.append(args))
    assert result.id == "300" and result.url == LINK and not result.already_uploaded
    assert session.calls[0]["data"] == {"media_type": "REELS", "caption": "한글 caption #reels", "upload_type": "resumable"}
    upload = session.calls[1]
    assert upload["url"] == UPLOAD and upload["body"] == video.read_bytes()
    assert upload["headers"]["Authorization"] == f"OAuth {TOKEN}"
    assert upload["headers"]["offset"] == "0"
    assert upload["headers"]["file_size"] == str(video.stat().st_size)
    assert session.calls[3]["data"] == {"creation_id": "200"}
    assert snapshots[-2]["status"] == "success" and snapshots[-2]["id"] == "300"
    assert state["status"] == "success" and state["url"] == LINK
    assert all(call["timeout"] for call in session.calls)
    assert all(call["allow_redirects"] is False for call in session.calls)
    assert all(stage == "instagram" and 0 <= done <= total == 100 and TOKEN not in message
               for stage, done, total, message in updates)


def test_url_transport_uses_instagram_host_without_uploading_local_bytes(video, store):
    credentials = store.get("instagram")
    credentials["method"] = "url"
    store.put("instagram", credentials)
    session = Session(Response({"id": "200"}), Response({"status_code": "FINISHED"}),
                      Response({"id": "300"}), Response({"permalink": LINK}))
    state = {}
    result = provider().publish_instagram(video, ReleaseDraft(body="Caption", instagram_video_url="https://cdn.example.com/video.mp4"),
                                          store, state, lambda s: None, session=session)
    assert result.url == LINK
    assert session.calls[0]["url"] == "https://graph.instagram.com/v26.0/100/media"
    assert session.calls[0]["data"] == {"media_type": "REELS", "caption": "Caption", "video_url": "https://cdn.example.com/video.mp4"}
    assert all("rupload" not in call["url"] for call in session.calls)


@pytest.mark.parametrize("url", ["http://cdn.example.com/a.mp4", "https://localhost/a", "https://127.0.0.1/a",
                                "https://[::1]/a", "https://10.0.0.1/a", "https://127.1/a", "https://server.local/a",
                                "https://name:password@cdn.example.com/a", "https://example.com/a#fragment"])
def test_url_mode_rejects_nonpublic_or_malformed_video_urls(url, video, store):
    credentials = store.get("instagram")
    credentials["method"] = "url"
    store.put("instagram", credentials)
    session = Session()
    with pytest.raises(PublishError):
        provider().publish_instagram(video, ReleaseDraft(instagram_video_url=url), store, {}, lambda s: None, session=session)
    assert not session.calls


@pytest.mark.parametrize("uri", ["https://rupload.facebook.com.evil.example/ig-api-upload/v26.0/200",
                                "https://evil.example/ig-api-upload/v26.0/200", "http://rupload.facebook.com/ig-api-upload/v26.0/200",
                                "https://rupload.facebook.com/ig-api-upload/v26.0/200?token=secret",
                                "https://rupload.facebook.com/ig-api-upload/v26.0/201"])
def test_rejects_untrusted_upload_uri_without_transmitting_credentials(uri, video, store):
    session = Session(Response({"id": "200", "uri": uri}))
    with pytest.raises(PublishError):
        publish(video, store, session)
    assert len(session.calls) == 1


def test_retry_reuses_container_after_upload_timeout(video, store):
    state = {}
    first = Session(Response({"id": "200", "uri": UPLOAD}), requests.Timeout(TOKEN))
    with pytest.raises(PublishError) as error:
        publish(video, store, first, state)
    assert TOKEN not in str(error.value) and state["container_id"] == "200"
    retry = Session(Response({"status_code": "FINISHED"}), Response({"id": "300"}), Response({"permalink": LINK}))
    result, _, _ = publish(video, store, retry, state)
    assert result.id == "300"
    assert retry.calls[0]["url"] == BASE + "/200"
    assert not any(call["url"].endswith("/media") or "rupload" in call["url"] for call in retry.calls)


def test_retry_reuploads_same_unfinished_container_from_zero(monkeypatch, video, store):
    monkeypatch.setattr(provider(), "_wait", lambda cancel: pytest.fail("waited on stale pre-upload status"))
    state = {"container_id": "200", "uploaded": False}
    session = Session(Response({"status_code": "IN_PROGRESS"}), Response({"success": True}),
                      Response({"status_code": "FINISHED"}), Response({"id": "300"}), Response({"permalink": LINK}))
    result, _, _ = publish(video, store, session, state)
    assert result.id == "300"
    assert session.calls[1]["url"] == UPLOAD and session.calls[1]["headers"]["offset"] == "0"


def test_completed_receipt_skips_all_network_even_when_cancelled(video, store):
    cancel = threading.Event()
    cancel.set()
    session = Session()
    result, _, _ = publish(video, store, session, {"status": "success", "id": "300", "url": LINK}, cancel=cancel)
    assert result.already_uploaded and result.id == "300" and not session.calls


def test_publication_marker_is_saved_before_ambiguous_post_and_retry_never_reposts(video, store):
    state = {"container_id": "200", "uploaded": True}
    persisted = []
    def inspect_request(call):
        if call["url"].endswith("/media_publish"):
            assert persisted[-1]["publish_attempted"] is True
    session = Session(Response({"status_code": "FINISHED"}), requests.Timeout(TOKEN), on_request=inspect_request)
    with pytest.raises(PublishUncertain) as error:
        provider().publish_instagram(video, ReleaseDraft(), store, state,
                                     lambda s: persisted.append(copy.deepcopy(s)), session=session)
    assert TOKEN not in str(error.value) and state["publish_attempted"]
    retry = Session(Response({"status_code": "PUBLISHED"}))
    with pytest.raises(PublishUncertain, match="프로필"):
        publish(video, store, retry, state)
    assert len(retry.calls) == 1 and retry.calls[0]["method"] == "GET"


@pytest.mark.parametrize("status", ["FINISHED", "IN_PROGRESS", "EXPIRED", "ERROR"])
def test_ambiguous_previous_attempt_never_reposts_for_any_status(status, video, store):
    session = Session(Response({"status_code": status}))
    with pytest.raises(PublishUncertain):
        publish(video, store, session, {"container_id": "200", "publish_attempted": True})
    assert len(session.calls) == 1


def test_known_success_survives_cancellation_during_publish_response(video, store):
    cancel = threading.Event()
    def cancel_after_write(call):
        if call["url"].endswith("/media_publish"):
            cancel.set()
    session = Session(Response({"status_code": "FINISHED"}), Response({"id": "300"}), on_request=cancel_after_write)
    result, state, _ = publish(video, store, session, {"container_id": "200", "uploaded": True}, cancel=cancel)
    assert result.id == "300" and state["status"] == "success"
    assert result.url == "https://www.instagram.com/sample_user/"


@pytest.mark.parametrize("status,fragment", [("ERROR", "처리"), ("EXPIRED", "만료")])
def test_processing_terminal_errors_stop_without_publishing(status, fragment, video, store):
    session = Session(Response({"status_code": status, "status": TOKEN}))
    with pytest.raises(PublishError, match=fragment) as error:
        publish(video, store, session, {"container_id": "200", "uploaded": True})
    assert TOKEN not in str(error.value) and len(session.calls) == 1


def test_poll_limit_is_bounded_and_can_retry_existing_container(monkeypatch, video, store):
    monkeypatch.setattr(provider(), "POLL_INTERVAL", 0)
    session = Session(*[Response({"status_code": "IN_PROGRESS"}) for _ in range(5)])
    state = {"container_id": "200", "uploaded": True}
    with pytest.raises(PublishError, match="시간"):
        publish(video, store, session, state)
    assert len(session.calls) == 5 and state["container_id"] == "200"


def test_cancel_during_poll_stops_before_publish(video, store):
    cancel = threading.Event()
    session = Session(Response({"status_code": "IN_PROGRESS"}), on_request=lambda call: cancel.set())
    with pytest.raises(Cancelled):
        publish(video, store, session, {"container_id": "200", "uploaded": True}, cancel=cancel)
    assert len(session.calls) == 1


@pytest.mark.parametrize("response,fragment", [
    (Response({"error": {"code": 190, "message": TOKEN}}, 400), "토큰"),
    (Response({"error": {"code": 10, "message": TOKEN}}, 403), "권한"),
    (Response({"error": {"code": 4, "message": TOKEN}}, 429), "제한"),
    (Response(ValueError(TOKEN)), "응답"), (Response([]), "응답"),
    (Response({}, 502), "서버"), (Response({}, 302), "응답"),
])
def test_http_failures_are_actionable_and_redacted(response, fragment, store):
    with pytest.raises(PublishError, match=fragment) as error:
        provider().connect_instagram("123", TOKEN, "resumable", store, session=Session(response))
    assert TOKEN not in str(error.value)


def test_publish_malformed_response_is_uncertain_and_not_retried(video, store):
    session = Session(Response({"status_code": "FINISHED"}), Response(ValueError(TOKEN)))
    state = {"container_id": "200", "uploaded": True}
    with pytest.raises(PublishUncertain):
        publish(video, store, session, state)
    assert state["publish_attempted"] and len(session.calls) == 2


def test_permalink_failure_preserves_success_and_uses_profile_link(video, store):
    session = Session(Response({"status_code": "FINISHED"}), Response({"id": "300"}), requests.Timeout(TOKEN))
    result, state, _ = publish(video, store, session, {"container_id": "200", "uploaded": True})
    assert result.id == "300" and state["status"] == "success"
    assert result.url == "https://www.instagram.com/sample_user/"


def test_overlong_caption_fails_before_any_http(video, store):
    session = Session()
    with pytest.raises(PublishError):
        provider().publish_instagram(video, ReleaseDraft(body="x" * 2201), store, {}, lambda s: None, session=session)
    assert not session.calls


def test_definitive_auth_rejection_allows_retry_after_reconnecting(video, store):
    state = {"container_id": "200", "uploaded": True}
    rejected = Session(Response({"status_code": "FINISHED"}),
                       Response({"error": {"code": 190, "message": TOKEN}}, 400))
    with pytest.raises(PublishError, match="토큰") as error:
        publish(video, store, rejected, state)
    assert not isinstance(error.value, PublishUncertain)
    assert state.get("publish_attempted") is not True
    retry = Session(Response({"status_code": "FINISHED"}), Response({"id": "300"}), Response({"permalink": LINK}))
    result, _, _ = publish(video, store, retry, state)
    assert result.id == "300"



@pytest.mark.parametrize("method", ["resumable", "url"])
@pytest.mark.parametrize("account_type", ["BUSINESS", "MEDIA_CREATOR", "CREATOR"])
def test_connect_accepts_business_and_creator_profiles(method, account_type, store):
    profile = {"id": "123", "username": "team_football", "account_type": account_type}
    result = provider().connect_instagram("123", TOKEN, method, store, session=Session(Response(profile)))
    assert result["account_id"] == "123" and result["username"] == "team_football"


@pytest.mark.parametrize("method", ["resumable", "url"])
def test_connect_rejects_explicit_personal_account(method, store):
    profile = {"id": "123", "username": "team_football", "account_type": "PERSONAL"}
    with pytest.raises(PublishError, match="크리에이터"):
        provider().connect_instagram("123", TOKEN, method, store, session=Session(Response(profile)))
    assert store.get("instagram")["account_id"] == "100"


def test_cancellation_at_final_progress_boundary_prevents_publication(video, store):
    cancel = threading.Event()
    def progress(stage, done, total, message):
        if done == 90:
            cancel.set()
    session = Session(Response({"status_code": "FINISHED"}))
    state = {"container_id": "200", "uploaded": True}
    with pytest.raises(Cancelled):
        publish(video, store, session, state, progress=progress, cancel=cancel)
    assert len(session.calls) == 1 and not state.get("publish_attempted")


def test_cancellation_during_upload_keeps_container_without_attempting_publish(video, store):
    cancel = threading.Event()
    def progress(stage, done, total, message):
        if 10 < done < 60:
            cancel.set()
    session = Session(Response({"id": "200", "uri": UPLOAD}), Response({"success": True}))
    state = {}
    with pytest.raises(Cancelled):
        publish(video, store, session, state, progress=progress, cancel=cancel)
    assert state["container_id"] == "200" and not state.get("uploaded") and not state.get("publish_attempted")


def test_explicit_uncertain_record_without_marker_never_reposts(tmp_path):
    from stampcut.core.publishing.instagram import publish_instagram
    from stampcut.core.publishing.credentials import CredentialStore
    from stampcut.core.publishing.models import ReleaseDraft,PublishUncertain
    store=CredentialStore(tmp_path/"vault",protect=lambda b:b,unprotect=lambda b:b)
    store.put("instagram",{"account_id":"123","access_token":"test","username":"demo","method":"url","api_version":"v26.0"})
    class NoNetwork:
        def request(self,*a,**k):raise AssertionError("must not request or publish")
        def get(self,*a,**k):raise AssertionError("must not request")
        def post(self,*a,**k):raise AssertionError("must not publish")
    with pytest.raises(PublishUncertain):
        publish_instagram(tmp_path/"video.mp4",ReleaseDraft(youtube=False,instagram_video_url="https://media.example/file.mp4"),store,{"status":"uncertain"},lambda v:None,session=NoNetwork())

def test_unified_login_resolves_single_assigned_account(store):
    session = Session(Response({"data": [{"id": "123", "username": "team"}]}))
    result = provider().connect_instagram("", TOKEN, "unified", store, api_version="v27.0", session=session)
    assert result["account_id"] == "123" and result["username"] == "team"
    assert session.calls[0]["url"] == "https://graph.facebook.com/v27.0/me/assigned_instagram_accounts"


def test_unified_login_requires_explicit_choice_for_multiple_accounts(store):
    session = Session(Response({"data": [{"id": "123", "username": "a"}, {"id": "456", "username": "b"}]}))
    with pytest.raises(PublishError, match="ID"):
        provider().connect_instagram("", TOKEN, "unified", store, session=session)
    assert store.get("instagram")["account_id"] == "100"

@pytest.mark.parametrize("response", [
    Response({"debug_info": {"type": "ProcessingFailedError", "message":
        '{"success":false,"error":{"message":"unauthorized user request"}}'}}, 400),
    Response({"debug_info": {"type": "ProcessingFailedError", "message":
        '{"success":false,"error":{"message":"unauthorized user request"}}'}}, 200),
])
def test_rupload_failure_identifies_transfer_and_meta_reason(video, store, response):
    session = Session(Response({"id": "200", "uri": UPLOAD}), response)
    state = {}
    with pytest.raises(PublishError) as exc:
        publish(video, store, session, state=state)
    assert "파일 전송" in str(exc.value)
    assert "ProcessingFailedError" in str(exc.value)
    assert "권한" in str(exc.value)
    assert len(session.calls) == 2
    assert state["container_id"] == "200" and not state.get("publish_attempted")


def test_graph_rejection_identifies_stage_and_numeric_codes_without_raw_data(video, store):
    error = {"code": 100, "error_subcode": 33, "message": TOKEN}
    with pytest.raises(PublishError) as exc:
        publish(video, store, Session(Response({"error": error}, 400)))
    message = str(exc.value)
    assert "세션 생성" in message and "HTTP 400" in message and "100/33" in message
    assert TOKEN not in message


def test_rupload_failure_logs_duration_and_safe_codes_without_raw_response(video, store, caplog):
    session = Session(Response({"id": "200", "uri": UPLOAD}), Response({
        "debug_info": {"type": "ProcessingFailedError", "retriable": False, "message":
            '{"error":{"code":352,"error_subcode":2207026,"message":"private-value"}}'},
    }, 400))

    with pytest.raises(PublishError):
        publish(video, store, session)

    assert "파일 전송 (HTTP 400, Meta 352/2207026, ProcessingFailedError)" in caplog.text
    assert "elapsed=" in caplog.text and "retriable=False" in caplog.text
    assert TOKEN not in caplog.text and "private-value" not in caplog.text and UPLOAD not in caplog.text


def test_transfer_error_shows_provider_reason_and_trace_without_token(video, store, caplog):
    response = Response({"debug_info": {"type": "ProcessingFailedError", "retriable": False,
                        "message": "Invalid video format: access_token=" + TOKEN}}, 400)
    response.headers = {"x-fb-trace-id": "trace_1234"}
    session = Session(Response({"id": "200", "uri": UPLOAD}), response)
    with pytest.raises(PublishError) as exc:
        publish(video, store, session)
    assert "Invalid video format" in str(exc.value) and "trace_1234" in str(exc.value)
    assert TOKEN not in str(exc.value) + caplog.text
    assert session.calls[1]["headers"]["Content-Type"] == "application/octet-stream"


def test_manual_retry_replaces_rejected_transfer_only_after_checking_status(video, store):
    state = {}
    failure = Response({"debug_info": {"type": "ProcessingFailedError", "retriable": False}}, 400)
    with pytest.raises(PublishError):
        publish(video, store, Session(Response({"id": "200", "uri": UPLOAD}), failure), state=state)
    retry = Session(Response({"status_code": "IN_PROGRESS"}), Response({"id": "201"}),
                    Response({"success": True}), Response({"status_code": "FINISHED"}),
                    Response({"id": "300"}), Response({"permalink": LINK}))
    publish(video, store, retry, state=state)
    assert retry.calls[0]["url"] == f"{BASE}/200"
    assert retry.calls[1]["url"].endswith("/media")
    assert retry.calls[2]["url"].endswith("/201")


def test_retry_checks_existing_container_before_transmitting_video_again(video, store):
    first = Session(Response({"id": "200", "uri": UPLOAD}), Response({
        "debug_info": {"type": "ProcessingFailedError", "message": "Request processing failed"},
    }, 400))
    state = {}
    with pytest.raises(PublishError, match="ProcessingFailedError"):
        publish(video, store, first, state=state)

    retry = Session(Response({"status_code": "FINISHED"}), Response({"id": "300"}), Response({"permalink": LINK}))
    post, _, _ = publish(video, store, retry, state=state)

    assert retry.calls[0]["url"] == f"{BASE}/200" and retry.calls[0]["method"] == "GET"
    assert not any("body" in call for call in retry.calls)
    assert post.url == LINK
