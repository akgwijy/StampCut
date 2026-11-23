from pathlib import Path
import pytest
from stampcut.core.publishing.models import ReleaseDraft,PublishedPost,PublishError,PublishUncertain
from stampcut.core.publishing.credentials import CredentialStore
from stampcut.core.ffmpeg import Cancelled


def setup(tmp_path,providers,**kwargs):
    from stampcut.core.publishing.media import MediaInfo
    from stampcut.core.publishing.service import ReleaseService
    video=tmp_path/"video.mp4";video.write_bytes(b"video-content")
    store=CredentialStore(tmp_path/"vault.bin",protect=lambda b:b[::-1],unprotect=lambda b:b[::-1])
    store.put("youtube",{"channel_id":"chan","access_token":"YT-TEST"})
    store.put("instagram",{"account_id":"123","access_token":"IG-TEST","method":"resumable"})
    service=ReleaseService(store,paths=None,providers=providers,inspector=lambda *a:MediaInfo(1080,1920,10,13,True),preparer=lambda video,*a,**k:video,**kwargs)
    return service,video,store


@pytest.mark.parametrize("first_fails", [False, True])
def test_drive_hosts_prepared_file_and_revokes_link_even_on_failure(tmp_path, monkeypatch, first_fails):
    from stampcut.core.publishing import drive
    events = []
    link = "https://drive.google.com/uc?id=demo"
    def share(upload, store, state, save_state, **kwargs):
        events.append(("share", upload))
        state["drive_permission_id"] = "reader"
        save_state(state)
        return link
    def revoke(store, state, save_state):
        events.append(("revoke", state.pop("drive_permission_id")))
        save_state(state)
    def provider(upload, draft, store, state, save_state, **kwargs):
        events.append(("instagram", draft.instagram_video_url))
        if first_fails:
            raise PublishError("transfer failed")
        return PublishedPost("instagram", "300", "https://www.instagram.com/reel/test/")
    monkeypatch.setattr(drive, "ensure_shared_video", share)
    monkeypatch.setattr(drive, "revoke_video_share", revoke)
    service, video, store = setup(tmp_path, {"instagram": provider})
    store.put("drive", {"refresh_token": "demo"})
    prepared = tmp_path / "prepared.mp4"
    prepared.write_bytes(b"prepared")
    service.preparer = lambda *args, **kwargs: prepared
    draft = ReleaseDraft(youtube=False, instagram_use_drive=True)
    result = service.publish(video, draft)
    assert events == [("share", prepared), ("instagram", link), ("revoke", "reader")]
    assert result.outcomes[0].status == ("error" if first_fails else "success")
    assert not draft.instagram_video_url
    if not first_fails:
        assert service.publish(video, draft).outcomes[0].post.already_uploaded
        assert len(events) == 3


def test_ready_drive_container_resumes_without_resharing(tmp_path, monkeypatch):
    from stampcut.core.publishing import drive
    shares = []
    link = "https://drive.google.com/uc?id=demo"
    def share(*args, **kwargs):
        shares.append(1)
        return link
    def provider(upload, draft, store, state, save_state, **kwargs):
        assert draft.instagram_video_url == link
        if not state.get("container_id"):
            state.update(status="ready", container_id="123", video_url=link)
            save_state(state)
            raise Cancelled()
        return PublishedPost("instagram", "300", "https://www.instagram.com/reel/test/")
    monkeypatch.setattr(drive, "ensure_shared_video", share)
    service, video, store = setup(tmp_path, {"instagram": provider})
    store.put("drive", {"refresh_token": "demo"})
    draft = ReleaseDraft(youtube=False, instagram_use_drive=True)
    assert service.publish(video, draft).cancelled
    assert service.publish(video, draft).outcomes[0].status == "success"
    assert shares == [1]


def test_drive_cleanup_retry_does_not_publish_again(tmp_path, monkeypatch):
    from stampcut.core.publishing import drive
    calls = []
    def share(upload, store, state, save_state, **kwargs):
        state["drive_permission_id"] = "reader"
        save_state(state)
        return "https://drive.google.com/uc?id=demo"
    def revoke(store, state, save_state):
        calls.append("revoke")
        if calls.count("revoke") == 1:
            raise PublishError("연결 오류")
        del state["drive_permission_id"]
        save_state(state)
    def provider(*args, **kwargs):
        calls.append("publish")
        return PublishedPost("instagram", "300", "https://www.instagram.com/reel/test/")
    monkeypatch.setattr(drive, "ensure_shared_video", share)
    monkeypatch.setattr(drive, "revoke_video_share", revoke)
    service, video, store = setup(tmp_path, {"instagram": provider})
    store.put("drive", {"refresh_token": "demo"})
    draft = ReleaseDraft(youtube=False, instagram_use_drive=True)
    first = service.publish(video, draft).outcomes[0]
    assert first.status == "success" and "Drive 공유 해제 실패" in first.error
    second = service.publish(video, draft).outcomes[0]
    assert second.post.already_uploaded and not second.error
    assert calls == ["publish", "revoke", "revoke"]


def test_partial_success_retry_does_not_duplicate_completed_target(tmp_path):
    calls=[];fail=[True]
    def youtube(video,draft,store,state,save_state,**kwargs):
        calls.append("youtube")
        return PublishedPost("youtube","ABCDEFGHIJK","https://www.youtube.com/watch?v=ABCDEFGHIJK")
    def instagram(video,draft,store,state,save_state,**kwargs):
        calls.append("instagram")
        if fail[0]:raise PublishError("permission")
        return PublishedPost("instagram","1234","https://www.instagram.com/reel/test/")
    service,video,store=setup(tmp_path,{"youtube":youtube,"instagram":instagram})
    draft=ReleaseDraft(title="title",body="내용\n#축구")
    first=service.publish(video,draft)
    assert [o.status for o in first.outcomes]==["success","error"]
    fail[0]=False
    second=service.publish(video,draft)
    assert calls==["youtube","instagram","instagram"]
    assert second.outcomes[0].post.already_uploaded
    assert [o.status for o in second.outcomes]==["success","success"]
    assert b"YT-TEST" not in store.path.read_bytes()


@pytest.mark.parametrize("retry_case", ["legacy", "changed", "same", "uncertain"])
def test_instagram_retry_reconciles_prepared_file_without_repeating_youtube(tmp_path, retry_case):
    uploads = []
    instagram_states = []

    def youtube(*args, **kwargs):
        uploads.append("youtube")
        return PublishedPost("youtube", "abcdEFGH123", "https://www.youtube.com/watch?v=abcdEFGH123")

    def instagram(video, draft, store, state, save_state, **kwargs):
        uploads.append(video.read_bytes())
        instagram_states.append(dict(state))
        if len(instagram_states) == 1:
            state.update(container_id="200", upload_uri="stored-uri", uploaded=False)
            if retry_case == "legacy":
                state.pop("upload_fingerprint", None)
            if retry_case == "uncertain":
                state["publish_attempted"] = True
            save_state(state)
            raise PublishError("file transfer failed")
        if state.get("publish_attempted"):
            raise PublishUncertain("check Instagram")
        return PublishedPost("instagram", "300", "https://www.instagram.com/reel/test/")

    service, video, _ = setup(tmp_path, {"youtube": youtube, "instagram": instagram})
    old = tmp_path / "old.mp4"
    old.write_bytes(b"old")
    new = tmp_path / "new.mp4"
    new.write_bytes(b"new")
    service.preparer = lambda *args, **kwargs: old
    draft = ReleaseDraft(title="title")
    first = service.publish(video, draft)
    assert first.outcomes[0].status == "success"
    service.preparer = lambda *args, **kwargs: old if retry_case == "same" else new

    retry = service.publish(video, draft)

    assert retry.outcomes[0].post.already_uploaded
    assert uploads == ["youtube", b"old", b"old" if retry_case == "same" else b"new"]
    if retry_case in ("legacy", "changed"):
        assert not {"container_id", "upload_uri", "uploaded"} & instagram_states[1].keys()
    else:
        assert instagram_states[1]["container_id"] == "200"
    assert retry.outcomes[1].status == ("uncertain" if retry_case == "uncertain" else "success")


def test_missing_selected_account_is_checked_before_any_upload(tmp_path):
    calls=[]
    service,video,store=setup(tmp_path,{"youtube":lambda *a,**k:calls.append(1)})
    store.delete("instagram")
    with pytest.raises(PublishError):service.publish(video,ReleaseDraft(title="title"))
    assert calls==[]


def test_cancel_keeps_completed_upload_and_stops_next_target(tmp_path):
    import threading
    cancel=threading.Event();calls=[]
    def youtube(video,draft,store,state,save_state,**kwargs):
        calls.append("youtube");cancel.set()
        return PublishedPost("youtube","ABCDEFGHIJK","https://www.youtube.com/watch?v=ABCDEFGHIJK")
    service,video,store=setup(tmp_path,{"youtube":youtube,"instagram":lambda *a,**k:calls.append("instagram")})
    result=service.publish(video,ReleaseDraft(title="title"),cancel=cancel)
    assert result.cancelled and calls==["youtube"]
    assert result.outcomes[0].post.id=="ABCDEFGHIJK"


def test_uncertain_state_is_preserved_and_reconciled_by_provider(tmp_path):
    seen=[]
    def youtube(video,draft,store,state,save_state,**kwargs):
        seen.append(dict(state))
        state["final_attempted"]=True;save_state(state)
        raise PublishUncertain("check Studio")
    service,video,store=setup(tmp_path,{"youtube":youtube})
    draft=ReleaseDraft(title="title",instagram=False)
    assert service.publish(video,draft).outcomes[0].status=="uncertain"
    service.publish(video,draft)
    assert seen[1]["final_attempted"] is True


def test_validation_does_not_publish_wrong_shorts_or_overlong_caption(tmp_path):
    from stampcut.core.publishing.media import MediaInfo
    calls=[]
    service,video,store=setup(tmp_path,{"youtube":lambda *a,**k:calls.append(1)})
    service.inspector=lambda *a:MediaInfo(1920,1080,10,13,True)
    with pytest.raises(PublishError):service.publish(video,ReleaseDraft(title="title",instagram=False))
    service.inspector=lambda *a:MediaInfo(1080,1920,10,13,True)
    with pytest.raises(PublishError):service.publish(video,ReleaseDraft(title="title",body="가"*2201))
    assert calls==[]


def test_late_cancel_after_provider_saved_success_is_reported_as_success(tmp_path):
    def youtube(video,draft,store,state,save_state,**kwargs):
        state.update(status="success",id="ABCDEFGHIJK",url="https://www.youtube.com/watch?v=ABCDEFGHIJK")
        save_state(state)
        raise Cancelled()
    service,video,store=setup(tmp_path,{"youtube":youtube})
    result=service.publish(video,ReleaseDraft(title="title",instagram=False))
    assert result.cancelled and result.outcomes[0].status=="success"


@pytest.mark.parametrize("marker",["final_attempted","publish_attempted"])
def test_cancel_after_ambiguous_final_request_preserves_uncertain_result(tmp_path,marker):
    import threading
    cancel=threading.Event()
    def provider(video,draft,store,state,save_state,**kwargs):
        state[marker]=True;save_state(state);cancel.set()
        raise Cancelled()
    service,video,store=setup(tmp_path,{"youtube":provider})
    result=service.publish(video,ReleaseDraft(title="title",instagram=False),cancel=cancel)
    assert result.cancelled and result.outcomes[0].status=="uncertain"
    assert result.outcomes[0].error


def test_changed_instagram_transport_url_does_not_republish_success(tmp_path):
    calls=[]
    def provider(video,draft,store,state,save_state,**kwargs):
        calls.append(draft.instagram_video_url)
        return PublishedPost("instagram","1234","https://www.instagram.com/reel/test/")
    service,video,store=setup(tmp_path,{"instagram":provider})
    store.put("instagram",{"account_id":"123","access_token":"IG-TEST","method":"url"})
    draft=ReleaseDraft(youtube=False,instagram_video_url="https://media.example/clip.mp4?expires=1")
    assert service.publish(video,draft).outcomes[0].status=="success"
    draft.instagram_video_url="https://media.example/clip.mp4?expires=2"
    result=service.publish(video,draft)
    assert len(calls)==1 and result.outcomes[0].post.already_uploaded


def test_changed_instagram_transport_url_preserves_ambiguous_session(tmp_path):
    seen=[]
    def provider(video,draft,store,state,save_state,**kwargs):
        seen.append(dict(state))
        state.update(publish_attempted=True,container_id="9876");save_state(state)
        raise PublishUncertain("check Instagram")
    service,video,store=setup(tmp_path,{"instagram":provider})
    store.put("instagram",{"account_id":"123","access_token":"IG-TEST","method":"url"})
    draft=ReleaseDraft(youtube=False,instagram_video_url="https://media.example/old.mp4")
    assert service.publish(video,draft).outcomes[0].status=="uncertain"
    draft.instagram_video_url="https://media.example/new.mp4"
    assert service.publish(video,draft).outcomes[0].status=="uncertain"
    assert seen[1].get("publish_attempted") is True and seen[1].get("container_id")=="9876"


def test_missing_instagram_url_is_preflighted_before_youtube_upload(tmp_path):
    calls=[]
    service,video,store=setup(tmp_path,{"youtube":lambda *a,**k:calls.append("youtube"),"instagram":lambda *a,**k:calls.append("instagram")})
    store.put("instagram",{"account_id":"123","access_token":"test","method":"url"})
    with pytest.raises(PublishError):service.publish(video,ReleaseDraft(title="title"))
    assert calls==[]


def test_url_source_fingerprint_mismatch_is_rejected_before_any_upload(tmp_path):
    calls=[]
    service,video,store=setup(tmp_path,{"instagram":lambda *a,**k:calls.append("instagram")})
    store.put("instagram",{"account_id":"123","access_token":"test","method":"url"})
    draft=ReleaseDraft(youtube=False,instagram_video_url="https://media.example/old.mp4")
    draft.instagram_source_fingerprint="0"*64
    with pytest.raises(PublishError):service.publish(video,draft)
    assert calls==[]


@pytest.mark.parametrize("provider_saves",[False,True])
def test_confirmed_publication_survives_success_receipt_write_failure(tmp_path,monkeypatch,provider_saves):
    def provider(video,draft,store,state,save_state,**kwargs):
        if provider_saves:
            state.update(status="success",id="ABCDEFGHIJK",url="https://www.youtube.com/watch?v=ABCDEFGHIJK")
            save_state(state)
        return PublishedPost("youtube","ABCDEFGHIJK","https://www.youtube.com/watch?v=ABCDEFGHIJK")
    service,video,store=setup(tmp_path,{"youtube":provider})
    original=store.put
    def put(section,value):
        if section.startswith("release:") and value.get("status")=="success":raise PublishError("disk full")
        return original(section,value)
    monkeypatch.setattr(store,"put",put)
    result=service.publish(video,ReleaseDraft(title="title",instagram=False))
    outcome=result.outcomes[0]
    assert outcome.status=="success" and outcome.post.id=="ABCDEFGHIJK" and outcome.error


def test_success_receipt_is_not_written_twice(tmp_path,monkeypatch):
    writes=[]
    def provider(video,draft,store,state,save_state,**kwargs):
        state.update(status="success",id="ABCDEFGHIJK",url="https://www.youtube.com/watch?v=ABCDEFGHIJK");save_state(state)
        return PublishedPost("youtube",state["id"],state["url"])
    service,video,store=setup(tmp_path,{"youtube":provider})
    original=store.put
    def put(section,value):
        if section.startswith("release:"):writes.append(value.copy())
        return original(section,value)
    monkeypatch.setattr(store,"put",put)
    service.publish(video,ReleaseDraft(title="title",instagram=False))
    assert len(writes)==1


def test_overlapping_identical_release_is_claimed_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    entered=threading.Event();release=threading.Event();calls=[]
    def provider(*args,**kwargs):
        calls.append(1);entered.set();release.wait(2)
        return PublishedPost("youtube","ABCDEFGHIJK","https://www.youtube.com/watch?v=ABCDEFGHIJK")
    service,video,store=setup(tmp_path,{"youtube":provider})
    draft=ReleaseDraft(title="title",instagram=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(service.publish,video,draft)
        assert entered.wait(2)
        try:
            second=pool.submit(service.publish,video,draft).result(timeout=4)
            assert len(calls)==1 and second.outcomes[0].status=="error"
        finally:release.set()
        assert first.result(timeout=4).outcomes[0].status=="success"
