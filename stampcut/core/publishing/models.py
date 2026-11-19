from __future__ import annotations

from dataclasses import asdict,dataclass,fields
from typing import Callable
from stampcut.core.ffmpeg import Cancelled

ProgressFn=Callable[[str,int,int,str],None]

def noop(stage,done,total,message):
    pass

def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()

class PublishError(RuntimeError):
    """An actionable publishing failure; messages must not contain credentials."""

class PublishUncertain(PublishError):
    """The server may have published; reconcile instead of blindly reposting."""

@dataclass
class ReleaseDraft:
    title: str=""
    body: str=""
    youtube: bool=True
    instagram: bool=True
    privacy: str="public"
    made_for_kids: bool=False
    video_path: str=""
    instagram_video_url: str=""
    instagram_prepared_path: str=""
    instagram_source_fingerprint: str=""
    video_stamp: str=""

    @classmethod
    def from_dict(cls,data):
        if not isinstance(data,dict):
            return cls()
        default=cls()
        values={f.name:data[f.name] for f in fields(cls) if f.name in data and type(data[f.name]) is type(getattr(default,f.name))}
        if values.get("privacy") not in (None,"public","private","unlisted"):
            values.pop("privacy",None)
        return cls(**values)

    def to_dict(self):
        return asdict(self)

    def validate_for(self,platform):
        if platform=="youtube":
            if not self.title.strip() or len(self.title)>100 or any(c in self.title for c in "<>"):
                raise PublishError("유튜브 제목은 1~100자이며 < > 문자를 사용할 수 없습니다.")
            if len(self.body.encode("utf-8"))>5000 or any(c in self.body for c in "<>"):
                raise PublishError("유튜브 설명은 UTF-8 기준 5,000바이트 이하이며 < > 문자를 사용할 수 없습니다.")
            if self.privacy not in ("public","private","unlisted"):
                raise PublishError("유튜브 공개 범위를 선택하세요.")
        elif platform=="instagram":
            if len(self.body)>2200:
                raise PublishError("인스타그램 글 내용은 2,200자 이하로 입력하세요.")
        else:
            raise PublishError("지원하지 않는 업로드 대상입니다.")

@dataclass
class PublishedPost:
    platform: str
    id: str
    url: str
    already_uploaded: bool=False
