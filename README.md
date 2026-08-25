# StampCut

유튜브 댓글에 적힌 타임스탬프("7:05 기훈 선방")를 모아 릴스/쇼츠용 9:16 하이라이트 영상을 자동으로 만드는 Windows 데스크톱 앱.

## 설치

```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
python -m stampcut
```

(API 키 발급과 사용법은 구현 후 추가)
