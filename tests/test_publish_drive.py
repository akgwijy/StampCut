import hashlib
from copy import deepcopy

import pytest
import requests
import responses

from stampcut.core.publishing import google_oauth
from stampcut.core.publishing.models import PublishError

FILES = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SESSION = UPLOAD + "?upload_id=opaque"
PUBLIC = "https://drive.google.com/uc?export=download&id=unique"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"a" * 48
SHA = hashlib.sha256(MP4).hexdigest()
META = {"id": "unique", "size": str(len(MP4)), "sha256Checksum": SHA,
        "mimeType": "video/mp4", "webContentLink": PUBLIC}
PERM = {"id": "anyoneWithLink", "type": "anyone", "role": "reader", "allowFileDiscovery": False}


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(google_oauth, "drive_credentials", lambda *a, **kw: {"access_token": "secret", "permission_id": "account"}, raising=False)
    path = tmp_path / "movie.mp4"
    path.write_bytes(MP4)
    return path


@responses.activate
def test_retry_reuses_uploaded_bytes_and_only_revokes_created_permission(source):
    from stampcut.core.publishing.drive import ensure_shared_video, revoke_video_share

    state = {}
    saved = []
    def save(current):
        saved.append(deepcopy(current))

    responses.get(FILES + "/generateIds", json={"ids": ["unique"]})
    responses.post(UPLOAD, headers={"Location": SESSION})
    responses.put(SESSION, json=META)
    responses.get(FILES + "/unique", json=META)
    responses.get(FILES + "/unique/permissions", json={"permissions": []})
    responses.post(FILES + "/unique/permissions", json=PERM)
    responses.get(PUBLIC, body=MP4, content_type="video/mp4")
    assert ensure_shared_video(source, None, state, save) == PUBLIC
    assert saved[0]["drive_file_id"] == "unique"
    assert any(s.get("drive_share_pending") for s in saved)
    assert state["drive_permission_id"] == "anyoneWithLink"
    put = [c.request for c in responses.calls if c.request.method == "PUT"]
    assert len(put) == 1 and put[0].body == MP4
    assert put[0].headers["Content-Range"] == f"bytes 0-{len(MP4) - 1}/{len(MP4)}"
    assert "Authorization" not in responses.calls[-1].request.headers
    assert "Cookie" not in responses.calls[-1].request.headers
    assert b'"allowFileDiscovery": false' in next(c.request.body for c in responses.calls
                                                  if c.request.method == "POST" and "/permissions" in c.request.url)

    responses.replace(responses.GET, FILES + "/unique/permissions", json={"permissions": [PERM]})
    assert ensure_shared_video(source, None, state, save) == PUBLIC
    assert sum(c.request.method == "PUT" for c in responses.calls) == 1
    responses.delete(FILES + "/unique/permissions/anyoneWithLink", status=204)
    revoke_video_share(None, state, save)
    revoke_video_share(None, state, save)
    assert not state.get("drive_share_pending") and not state.get("drive_permission_id")
    assert state["drive_file_id"] == "unique"
    assert sum(c.request.method == "DELETE" for c in responses.calls) == 1


@responses.activate
def test_mismatched_remote_bytes_cannot_be_shared(source):
    from stampcut.core.publishing.drive import ensure_shared_video

    state = {"drive_file_id": "unique", "drive_sha256": SHA}
    responses.get(FILES + "/unique", json={**META, "sha256Checksum": "f"*64})
    with pytest.raises(PublishError, match="파일|일치"):
        ensure_shared_video(source, None, state, lambda s: None)
    assert all(call.request.method == "GET" for call in responses.calls)


@responses.activate
def test_denies_external_upload_session_before_sending_bearer(source):
    from stampcut.core.publishing.drive import ensure_shared_video

    responses.get(FILES + "/generateIds", json={"ids": ["unique"]})
    responses.post(UPLOAD, headers={"Location": "https://example.com/upload"})
    with pytest.raises(PublishError):
        ensure_shared_video(source, None, {}, lambda s: None)
    assert all(c.request.url.startswith("https://www.googleapis.com/") for c in responses.calls)


@responses.activate
def test_unknown_permission_left_untouched(source):
    from stampcut.core.publishing.drive import ensure_shared_video, revoke_video_share

    state = {"drive_file_id": "unique", "drive_sha256": SHA}
    responses.get(FILES + "/unique", json=META)
    responses.get(FILES + "/unique/permissions", json={"permissions": [PERM]})
    with pytest.raises(PublishError, match="공유"):
        ensure_shared_video(source, None, state, lambda s: None)
    revoke_video_share(None, state, lambda s: None)
    assert not any(c.request.method in ("POST", "DELETE") for c in responses.calls)


@responses.activate
def test_cleanup_after_permission_create_lost_response(source):
    from stampcut.core.publishing.drive import ensure_shared_video, revoke_video_share

    state = {"drive_file_id": "unique", "drive_sha256": SHA}
    responses.get(FILES + "/unique", json=META)
    responses.get(FILES + "/unique/permissions", json={"permissions": []})
    responses.post(FILES + "/unique/permissions", body=requests.Timeout("secret"))
    with pytest.raises(PublishError) as error:
        ensure_shared_video(source, None, state, lambda s: None)
    assert "secret" not in str(error.value)
    assert state.get("drive_share_pending")
    responses.replace(responses.GET, FILES + "/unique/permissions", json={"permissions": [PERM]})
    responses.delete(FILES + "/unique/permissions/anyoneWithLink", status=204)
    revoke_video_share(None, state, lambda s: None)
    assert not state.get("drive_share_pending")
    assert responses.calls[-1].request.method == "DELETE"



@responses.activate
def test_resumes_from_acknowledged_offset_and_confirms_large_file_without_cookies(source):
    from stampcut.core.publishing.drive import ensure_shared_video

    state = {"drive_file_id": "unique", "drive_sha256": SHA, "drive_session": SESSION}
    responses.get(FILES + "/unique", status=404)
    responses.put(SESSION, status=308, headers={"Range": "bytes=0-15"})
    responses.put(SESSION, json=META)
    responses.get(FILES + "/unique/permissions", json={"permissions": []})
    responses.post(FILES + "/unique/permissions", json=PERM)
    responses.get(PUBLIC, body="<html>confirm download</html>", content_type="text/html",
                  headers={"Set-Cookie": "download=secret; Secure; Domain=.google.com"})
    direct = "https://drive.usercontent.google.com/download?id=unique&export=download&confirm=t"
    cdn = "https://abc.googleusercontent.com/videofile"
    responses.get(direct, status=302, headers={"Location": cdn, "Set-Cookie": "id=secret"})
    responses.get(cdn, body=MP4, content_type="application/octet-stream")
    assert ensure_shared_video(source, None, state, lambda s: None) == direct
    puts = [c.request for c in responses.calls if c.request.method == "PUT"]
    assert len(puts) == 2
    assert puts[0].headers["Content-Range"] == f"bytes */{len(MP4)}"
    assert puts[1].body == MP4[16:]
    for call in responses.calls[-3:]:
        assert "Authorization" not in call.request.headers and "Cookie" not in call.request.headers


@responses.activate
def test_workspace_policy_and_disabled_api_errors_are_actionable(source):
    from stampcut.core.publishing.drive import ensure_shared_video

    disabled = {"error": {"details": [{"reason": "SERVICE_DISABLED"}]}}
    responses.get(FILES + "/generateIds", status=403, json=disabled)
    with pytest.raises(PublishError, match="Drive API"):
        ensure_shared_video(source, None, {}, lambda s: None)

    state = {"drive_file_id": "unique", "drive_sha256": SHA}
    responses.get(FILES + "/unique", json=META)
    responses.get(FILES + "/unique/permissions", json={"permissions": []})
    responses.post(FILES + "/unique/permissions", status=403,
                   json={"error": {"errors": [{"reason": "domainPolicy"}]}})
    with pytest.raises(PublishError, match="외부 링크 공유"):
        ensure_shared_video(source, None, state, lambda s: None)


@responses.activate
def test_cleanup_keeps_marker_when_original_account_is_disconnected(source):
    from stampcut.core.publishing.drive import revoke_video_share

    state = {"drive_file_id": "unique", "drive_account": "other", "drive_permission_id": "anyoneWithLink"}
    with pytest.raises(PublishError, match="계정"):
        revoke_video_share(None, state, lambda s: None)
    assert state["drive_permission_id"] == "anyoneWithLink"
    assert not responses.calls



@responses.activate
def test_browser_login_redirect_falls_back_to_verified_confirmation_form(source):
    from stampcut.core.publishing.drive import ensure_shared_video

    responses.get(FILES + "/generateIds", json={"ids": ["unique"]})
    responses.post(UPLOAD, headers={"Location": SESSION})
    responses.put(SESSION, json=META)
    responses.get(FILES + "/unique/permissions", json={"permissions": []})
    responses.post(FILES + "/unique/permissions", json=PERM)
    responses.get(PUBLIC, status=302, headers={"Location": "https://accounts.google.com/ServiceLogin"})
    direct = "https://drive.usercontent.google.com/download?id=unique&export=download&confirm=t"
    html = """<form id="download-form" action="https://drive.usercontent.google.com/download" method="get">
              <input type="hidden" name="id" value="unique"><input type="hidden" name="confirm" value="t">
              <input type="hidden" name="uuid" value="opaque-id"><input type="hidden" name="export" value="download">
              </form>"""
    responses.get(direct, body=html, content_type="text/html")
    confirmed = "https://drive.usercontent.google.com/download?id=unique&confirm=t&uuid=opaque-id&export=download"
    responses.get(confirmed, body=MP4, content_type="video/mp4")
    assert ensure_shared_video(source, None, {}, lambda s: None, display_name="클립 · Instagram.mp4") == confirmed
    import json
    create = next(c.request for c in responses.calls if c.request.method == "POST" and c.request.url.startswith(UPLOAD))
    assert json.loads(create.body)["name"] == "클립 · Instagram.mp4"
    assert not any(c.request.url.startswith("https://accounts.google.com/") for c in responses.calls)
