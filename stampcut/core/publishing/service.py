from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
from stampcut.core.ffmpeg import Cancelled
from .credentials import CredentialStore
from .media import inspect_video, prepare_instagram, validate_media
from .models import ReleaseDraft, PublishedPost, PublishError, PublishUncertain, check_cancel, noop


@dataclass
class PublishOutcome:
    platform: str
    status: str
    post: PublishedPost | None = None
    error: str = ""


@dataclass
class BatchResult:
    outcomes: list[PublishOutcome] = field(default_factory=list)
    cancelled: bool = False


def file_stamp(video):
    try:
        stat=Path(video).stat()
        return f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        return ""


def file_fingerprint(video: Path, cancel=None) -> str:
    before = video.stat()
    digest = hashlib.sha256()
    with video.open("rb") as file:
        while block := file.read(1024 * 1024):
            check_cancel(cancel)
            digest.update(block)
    after = video.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise PublishError("확인 중 영상 파일이 변경됐습니다. 완성된 파일을 다시 선택하세요.")
    return digest.hexdigest()


def _confirmed(platform, state):
    if state.get("status") == "success" and state.get("id") and state.get("url"):
        return PublishedPost(platform, state["id"], state["url"])
    return None


def _ambiguous(state):
    return state.get("final_attempted") or state.get("publish_attempted") or state.get("status") in ("uncertain", "unknown")


def _close_drive_share(store, state, save_state, result):
    if not (state.get("drive_permission_id") or state.get("drive_share_pending")):
        return
    from .drive import revoke_video_share
    try:
        revoke_video_share(store, state, save_state)
    except PublishError as error:
        warning = f"Google Drive 공유 해제 실패 · {error}. 다시 시도하거나 Drive에서 파일의 링크 공유를 해제하세요."
        if result.outcomes and result.outcomes[-1].platform == "instagram":
            outcome = result.outcomes[-1]
            outcome.error = "\n".join(filter(None, (outcome.error, warning)))
        else:
            result.outcomes.append(PublishOutcome("instagram", "error", error=warning))


class ReleaseService:
    def __init__(self, store=None, paths=None, providers=None, inspector=None, preparer=None):
        self.store = store if store is not None else CredentialStore()
        self.paths = paths
        self.providers = providers or {}
        self.inspector = inspector or inspect_video
        self.preparer = preparer or prepare_instagram

    def _provider(self, platform):
        if platform in self.providers:
            return self.providers[platform]
        if platform == "youtube":
            from .youtube import publish_youtube
            return publish_youtube
        from .instagram import publish_instagram
        return publish_instagram

    def publish(self, video: Path, draft: ReleaseDraft, progress=noop, cancel=None) -> BatchResult:
        video = Path(video)
        if not video.is_file() or video.suffix.lower() not in (".mp4", ".mov") or video.stat().st_size == 0:
            raise PublishError("완성된 MP4/MOV 영상 파일을 선택하세요.")
        targets = [p for p, selected in (("youtube", draft.youtube), ("instagram", draft.instagram)) if selected]
        if not targets:
            raise PublishError("업로드할 플랫폼을 하나 이상 선택하세요.")
        accounts = {}
        for platform in targets:
            draft.validate_for(platform)
            account = self.store.get(platform)
            account_id = account.get("channel_id" if platform == "youtube" else "account_id")
            if not account_id or not (account.get("access_token") or (platform == "youtube" and account.get("refresh_token"))):
                raise PublishError(("유튜브" if platform == "youtube" else "인스타그램") + " 계정을 먼저 연결하세요.")
            accounts[platform] = account
        drive_mode = "instagram" in targets and draft.instagram_use_drive
        if drive_mode and not self.store.get("drive").get("refresh_token"):
            raise PublishError("계정 연결 / 설정에서 Google Drive를 먼저 연결하세요.")
        url_mode = "instagram" in targets and not drive_mode and (accounts["instagram"].get("method") == "url" or bool(draft.instagram_video_url))
        if url_mode:
            from .instagram import validate_video_url
            validate_video_url(draft.instagram_video_url)
        result = BatchResult()
        try:
            check_cancel(cancel)
            progress("prepare", 0, 0, "업로드할 영상 확인 중")
            info = self.inspector(video, self.paths)
            instagram_file = Path(draft.instagram_prepared_path) if url_mode and draft.instagram_prepared_path else video
            if url_mode and not instagram_file.is_file():
                raise PublishError("인스타그램 호환 파일이 없습니다. 릴스 호환 파일 준비를 다시 누르세요.")
            for platform in targets:
                target_info = self.inspector(instagram_file, self.paths) if platform == "instagram" and instagram_file != video else info
                if platform == "instagram" and url_mode and not target_info.same_geometry(info):
                    raise PublishError("릴스 호환 파일의 해상도 또는 화면 비율이 원본과 다릅니다. 호환 파일을 다시 준비하세요.")
                method = accounts[platform].get("method", "resumable")
                if platform == "instagram":
                    method = "resumable" if drive_mode else "url" if url_mode else method
                validate_media(target_info, platform, method)
            fingerprint = file_fingerprint(video, cancel)
            if url_mode:
                if (draft.instagram_source_fingerprint and draft.instagram_source_fingerprint != fingerprint) or (draft.video_stamp and draft.video_stamp != file_stamp(video)):
                    raise PublishError("선택한 영상이 변경되었습니다. 영상을 다시 선택하고 해당 영상의 공개 URL을 입력하세요.")
                draft.instagram_source_fingerprint = fingerprint
            for index, platform in enumerate(targets):
                check_cancel(cancel)
                account = accounts[platform]
                account_id = account["channel_id" if platform == "youtube" else "account_id"]
                metadata = {"title": draft.title, "body": draft.body, "privacy": draft.privacy, "made_for_kids": draft.made_for_kids} if platform == "youtube" else {"body": draft.body}
                key = "release:" + hashlib.sha256(json.dumps([fingerprint, platform, account_id, metadata], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
                try:
                    with self.store.operation(key), ExitStack() as cleanup:
                        state = self.store.get(key)
                        saved_success = [False]
                        def save_state(updated):
                            state.update(updated)
                            self.store.put(key, state)
                            saved_success[0] = _confirmed(platform, state) is not None
                        def target_progress(stage, done, total, message):
                            fraction = min(1, max(0, done / total)) if total else 0
                            progress("release", index * 100 + int(fraction * 100), len(targets) * 100, message)
                        if platform == "instagram":
                            cleanup.callback(_close_drive_share, self.store, state, save_state, result)
                        post = _confirmed(platform, state)
                        if post:
                            post.already_uploaded = True
                            result.outcomes.append(PublishOutcome(platform, "success", post))
                            target_progress(platform, 1, 1, "이미 완료된 업로드 확인")
                            continue
                        try:
                            publish_draft = draft
                            upload = instagram_file if platform == "instagram" and url_mode else video
                            if platform == "instagram" and drive_mode:
                                ready = state.get("status") == "ready" and state.get("container_id") and state.get("video_url")
                                if ready or _ambiguous(state):
                                    # Instagram checks this container again before publishing.
                                    publish_draft = replace(draft, instagram_video_url=state.get("video_url", ""))
                                else:
                                    from .drive import ensure_shared_video
                                    upload = self.preparer(video, self.paths, fingerprint, progress=target_progress, cancel=cancel)
                                    link = ensure_shared_video(upload, self.store, state, save_state,
                                                               progress=target_progress, cancel=cancel,
                                                               display_name=f"{video.stem} · Instagram.mp4")
                                    publish_draft = replace(draft, instagram_video_url=link)
                            elif platform == "instagram" and not url_mode:
                                upload = self.preparer(video, self.paths, fingerprint, progress=target_progress, cancel=cancel)
                                if not _ambiguous(state):
                                    upload_fingerprint = file_fingerprint(upload, cancel)
                                    if state.get("container_id") and state.get("upload_fingerprint") != upload_fingerprint:
                                        for field in ("container_id", "upload_uri", "uploaded", "video_url"):
                                            state.pop(field, None)
                                    state["upload_fingerprint"] = upload_fingerprint
                            check_cancel(cancel)
                            post = self._provider(platform)(upload, publish_draft, self.store, state, save_state, progress=target_progress, cancel=cancel)
                            state.update(status="success", id=post.id, url=post.url)
                            if not saved_success[0]:
                                save_state(state)
                            result.outcomes.append(PublishOutcome(platform, "success", post))
                            target_progress(platform, 1, 1, "업로드 완료")
                        except Exception as error:
                            post = _confirmed(platform, state)
                            if post:
                                warning = "게시 완료 · 로컬 완료 기록을 저장하지 못했습니다. 게시 링크를 확인하고 동일 영상을 다시 올리지 마세요." if not isinstance(error, Cancelled) else ""
                                result.outcomes.append(PublishOutcome(platform, "success", post, warning))
                            elif isinstance(error, PublishUncertain) or _ambiguous(state):
                                state["status"] = "uncertain"
                                try:save_state(state)
                                except PublishError:pass
                                message = str(error) if isinstance(error, PublishUncertain) else "게시 여부를 확인할 수 없습니다. 플랫폼에서 결과를 확인하세요. 중복 방지를 위해 자동으로 다시 게시하지 않습니다."
                                result.outcomes.append(PublishOutcome(platform, "uncertain", error=message))
                            elif isinstance(error, Cancelled):
                                result.outcomes.append(PublishOutcome(platform, "cancelled"))
                            else:
                                message = str(error) if isinstance(error, PublishError) else "업로드를 완료하지 못했습니다. 계정 연결과 네트워크를 확인하세요."
                                result.outcomes.append(PublishOutcome(platform, "error", error=message))
                            if isinstance(error, Cancelled):
                                result.cancelled = True
                                break
                except PublishError as error:
                    result.outcomes.append(PublishOutcome(platform, "error", error=str(error)))
            if cancel is not None and cancel.is_set():
                result.cancelled = True
        except Cancelled:
            result.cancelled = True
        return result
