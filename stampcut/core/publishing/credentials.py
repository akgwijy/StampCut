from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import time
import ctypes
import json
import os
import tempfile
import threading
from pathlib import Path
from stampcut.core import settings as settings_mod
from .models import PublishError

_MAGIC=b"STAMPCUT-PUBLISH\x01"
_LOCK=threading.RLock()

@contextmanager
def _process_lock(identity, wait=True):
    """OS-owned locks disappear on process exit, including abnormal termination."""
    name=hashlib.sha256(identity.encode("utf-8")).hexdigest()
    message="다른 StampCut 창에서 같은 릴리즈 또는 계정 저장 작업이 진행 중입니다. 완료 후 다시 시도하세요."
    if os.name=="nt":
        from ctypes import wintypes
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
        kernel.CreateMutexW.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype=wintypes.DWORD
        kernel.ReleaseMutex.argtypes=[wintypes.HANDLE]
        kernel.ReleaseMutex.restype=wintypes.BOOL
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        kernel.CloseHandle.restype=wintypes.BOOL
        handle=kernel.CreateMutexW(None,False,"Local\\StampCut-publish-"+name)
        if not handle:raise PublishError(message)
        acquired=False
        try:
            result=kernel.WaitForSingleObject(handle,30000 if wait else 0)
            acquired=result in (0,0x80)
            if not acquired:raise PublishError(message)
            yield
        finally:
            if acquired:kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
    else:
        # Tests with injected codecs can also run on POSIX CI.
        import fcntl
        path=Path(tempfile.gettempdir())/("stampcut-publish-"+name+".lock")
        with path.open("a+b") as file:
            deadline=time.monotonic()+30
            while True:
                try:
                    fcntl.flock(file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if not wait or time.monotonic()>=deadline:raise PublishError(message) from None
                    time.sleep(.05)
            try:yield
            finally:fcntl.flock(file.fileno(),fcntl.LOCK_UN)


class _Blob(ctypes.Structure):
    _fields_=[("size",ctypes.c_uint32),("data",ctypes.POINTER(ctypes.c_ubyte))]

def _dpapi(data: bytes,encrypt: bool)->bytes:
    if os.name!="nt":
        raise PublishError("계정 정보 암호화 저장은 Windows에서 지원합니다.")
    from ctypes import wintypes
    crypt=ctypes.WinDLL("crypt32",use_last_error=True)
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    source=ctypes.create_string_buffer(data)
    inp=_Blob(len(data),ctypes.cast(source,ctypes.POINTER(ctypes.c_ubyte)))
    out=_Blob()
    function=crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    function.argtypes=[ctypes.POINTER(_Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(_Blob)]
    function.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    kernel.LocalFree.restype=ctypes.c_void_p
    if not function(ctypes.byref(inp),None,None,None,None,1,ctypes.byref(out)):
        raise PublishError("이 Windows 사용자 계정에서 인증 정보를 복호화하거나 암호화할 수 없습니다.")
    try:
        return ctypes.string_at(out.data,out.size)
    finally:
        kernel.LocalFree(ctypes.cast(out.data,ctypes.c_void_p))

class CredentialStore:
    """Atomic per-Windows-user encrypted credentials and resumable upload state."""
    def __init__(self,path=None,protect=None,unprotect=None):
        self.path=Path(path) if path is not None else settings_mod.config_dir()/"publish-secrets.bin"
        self._identity=os.path.normcase(str(self.path.resolve()))
        self._protect=protect or (lambda data:_dpapi(data,True))
        self._unprotect=unprotect or (lambda data:_dpapi(data,False))

    def _load(self):
        if not self.path.exists():
            return {}
        try:
            blob=self.path.read_bytes()
            if not blob.startswith(_MAGIC):
                raise ValueError("invalid format")
            data=json.loads(self._unprotect(blob[len(_MAGIC):]).decode("utf-8"))
            if not isinstance(data,dict) or any(not isinstance(value,dict) for value in data.values()):
                raise ValueError("invalid object")
            return data
        except Exception:
            raise PublishError("저장된 인증 정보를 읽을 수 없습니다. 원래 Windows 사용자 계정인지 확인하세요.") from None

    def _save(self,data):
        temp=None
        try:
            encrypted=_MAGIC+self._protect(json.dumps(data,ensure_ascii=False,separators=(",",":")).encode("utf-8"))
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent,prefix=self.path.name+".",suffix=".tmp",delete=False) as file:
                temp=Path(file.name)
                file.write(encrypted)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp,self.path)
        except PublishError:
            raise
        except Exception:
            raise PublishError("인증 정보를 안전하게 저장하지 못했습니다.") from None
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def get(self,section):
        with _LOCK,_process_lock(self._identity):
            value=self._load().get(section,{})
            return copy.deepcopy(value)

    def put(self,section,value):
        if not isinstance(value,dict):
            raise TypeError("credential section must be a dictionary")
        with _LOCK,_process_lock(self._identity):
            data=self._load()
            data[section]=copy.deepcopy(value)
            self._save(data)

    def delete(self,section):
        with _LOCK,_process_lock(self._identity):
            data=self._load()
            if section in data:
                del data[section]
                self._save(data)

    @contextmanager
    def operation(self,key):
        """Claim a platform operation across StampCut processes without waiting."""
        with _process_lock(self._identity+":"+key,wait=False):
            yield
