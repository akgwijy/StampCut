import json
import pytest
from stampcut.core import settings as settings_mod
from stampcut.core.models import Settings


def test_release_draft_unicode_reopens_with_settings(tmp_path):
    from stampcut.core.publishing.models import ReleaseDraft
    draft=ReleaseDraft(title="경기 하이라이트",body="첫 줄\n둘째 줄 #축구",youtube=True,instagram=False,privacy="unlisted")
    settings=Settings(publish_draft=draft.to_dict())
    path=tmp_path/"settings.json"
    settings_mod.save(settings,path)
    restored=ReleaseDraft.from_dict(settings_mod.load(path).publish_draft)
    assert restored==draft
    assert json.loads(path.read_text("utf-8"))["publish_draft"]["body"]==draft.body


def test_invalid_draft_fields_are_defaulted():
    from stampcut.core.publishing.models import ReleaseDraft
    draft=ReleaseDraft.from_dict({"title":None,"youtube":"false","instagram":False,"privacy":"wrong","body":"kept","access_token":"NEVER"})
    assert draft.title=="" and draft.youtube is True and draft.instagram is False
    assert draft.privacy=="public" and draft.body=="kept"
    assert "access_token" not in draft.to_dict()
    assert ReleaseDraft.from_dict(None)==ReleaseDraft()


def test_credential_store_is_atomic_encrypted_and_returns_copies(tmp_path):
    from stampcut.core.publishing.credentials import CredentialStore
    path=tmp_path/"auth.bin"
    store=CredentialStore(path,protect=lambda data:data[::-1],unprotect=lambda data:data[::-1])
    assert store.get("youtube")=={}
    store.put("youtube",{"access_token":"s3cr3t","nested":{"a":1}})
    store.put("instagram",{"username":"demo"})
    assert b"s3cr3t" not in path.read_bytes()
    item=store.get("youtube");item["nested"]["a"]=2
    assert store.get("youtube")["nested"]["a"]==1
    store.delete("instagram")
    assert store.get("instagram")=={} and store.get("youtube")["access_token"]=="s3cr3t"
    assert list(tmp_path.iterdir())==[path]


def test_corrupt_credentials_are_not_silently_overwritten(tmp_path):
    from stampcut.core.publishing.credentials import CredentialStore
    from stampcut.core.publishing.models import PublishError
    path=tmp_path/"auth.bin";path.write_bytes(b"broken")
    store=CredentialStore(path,protect=lambda data:data,unprotect=lambda data:data)
    with pytest.raises(PublishError):store.get("youtube")
    with pytest.raises(PublishError):store.put("youtube",{"access_token":"new"})
    assert path.read_bytes()==b"broken"


def test_windows_dpapi_roundtrip(tmp_path):
    import os
    if os.name!="nt":pytest.skip("Windows DPAPI")
    from stampcut.core.publishing.credentials import CredentialStore
    path=tmp_path/"auth.bin"
    store=CredentialStore(path)
    store.put("test",{"access_token":"sample-not-a-real-token"})
    assert b"sample-not-a-real-token" not in path.read_bytes()
    assert store.get("test")["access_token"]=="sample-not-a-real-token"


def test_existing_malformed_section_is_not_treated_as_missing(tmp_path):
    from stampcut.core.publishing.credentials import CredentialStore
    from stampcut.core.publishing.models import PublishError
    store=CredentialStore(tmp_path/"vault",protect=lambda b:b,unprotect=lambda b:b)
    store._save({"release:example":None})
    with pytest.raises(PublishError):store.get("release:example")


def test_separate_processes_preserve_both_sets_of_receipts(tmp_path):
    import subprocess,sys
    from stampcut.core.publishing.credentials import CredentialStore
    code="""import sys,time
from stampcut.core.publishing.credentials import CredentialStore
class SlowStore(CredentialStore):
 def _load(self):
  data=super()._load();time.sleep(.25);return data
store=SlowStore(sys.argv[1],protect=lambda b:b,unprotect=lambda b:b)
for i in range(5):store.put(sys.argv[2]+str(i),{'status':'success'})
"""
    vault=tmp_path/"shared-vault"
    children=[subprocess.Popen([sys.executable,"-c",code,str(vault),prefix],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for prefix in ("one","two")]
    for child in children:
        out,err=child.communicate(timeout=20)
        assert child.returncode==0,err.decode(errors="replace")
    store=CredentialStore(vault,protect=lambda b:b,unprotect=lambda b:b)
    assert all(store.get(prefix+str(i))=={"status":"success"} for prefix in ("one","two") for i in range(5))
