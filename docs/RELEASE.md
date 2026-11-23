# 릴리즈 계정 준비와 사용

StampCut에서 완성 영상을 YouTube Shorts와 Instagram Reels에 게시하는 기능입니다. 최초 계정 설정 후에는 릴리즈 창의 글을 고쳐 업로드할 수 있습니다. 입력한 글은 앱 설정에 자동 저장됩니다. 두 플랫폼을 선택하면 같은 글을 YouTube 설명과 Instagram 캡션으로 사용합니다. 본문 제한은 YouTube UTF-8 5,000바이트, Instagram 2,200자이며 화면에 현재 길이가 표시됩니다.

## YouTube 연결

댓글 수집용 API 키와 업로드용 OAuth는 별개입니다.

1. [Google Cloud Console](https://console.cloud.google.com/)에서 프로젝트를 선택하고 **YouTube Data API v3**를 사용 설정합니다.
2. **Google Auth Platform**에서 앱 이름·대상·데이터 액세스를 설정합니다. 테스트 상태라면 업로드에 쓸 Google 계정을 테스트 사용자에 추가합니다. 화면 명칭과 공개 앱 검증 요건은 Google 콘솔 안내를 따릅니다.
3. OAuth 클라이언트를 **데스크톱 앱**으로 만들고 JSON 파일을 다운로드합니다. 실제 인증 파일은 프로젝트 저장소 밖에 보관하세요.
4. StampCut **릴리즈 → 계정 연결 / 설정…**에서 JSON을 선택하고 **Google 계정 연결**을 누릅니다.
5. 열린 시스템 브라우저에서 업로드할 채널의 계정으로 로그인·승인합니다. 앱에 표시된 채널 이름을 확인합니다.

앱은 `youtube.upload`, `youtube.readonly` 권한을 요청합니다. 읽기 권한은 연결한 채널 식별에 사용합니다. PKCE와 로컬 콜백으로 인증하고 갱신 토큰을 암호화해 보관합니다. Google 앱의 테스트 상태·계정 권한·할당량 정책에 따라 재연결이 필요할 수 있습니다. 업로드 후 공개 범위와 처리는 YouTube의 실제 결과를 확인하세요.

공식 문서: [데스크톱 OAuth](https://developers.google.com/identity/protocols/oauth2/native-app), [업로드 API](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert), [재개 가능한 업로드](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol), [3분 Shorts](https://support.google.com/youtube/answer/15424877?hl=ko).

## Google Drive 자동 업로드

1. YouTube 연결에 사용한 Google Cloud 프로젝트에서 **Google Drive API**를 사용 설정합니다. 데스크톱 OAuth 클라이언트는 함께 사용할 수 있습니다. [Drive API 사용 설정](https://console.cloud.google.com/apis/library/drive.googleapis.com)
2. **릴리즈 → 계정 연결 / 설정…**에서 Google Drive를 연결하고 브라우저에서 계정을 선택합니다. StampCut은 앱이 만든 파일을 관리하는 `drive.file` 권한을 요청합니다. [Drive 권한 안내](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
3. 게시할 영상을 선택하고 **Instagram Reels → Google Drive 자동 업로드**를 선택합니다. 앱이 선택한 MP4 파일을 그대로 **내 드라이브 → 문성FC영상** 폴더에 업로드합니다. 폴더가 없으면 첫 업로드 때 생성합니다. 파일을 따로 올리거나 URL을 복사할 필요가 없습니다.
4. Meta가 영상을 가져올 동안 해당 파일에만 **링크가 있는 모든 사용자 · 뷰어** 권한이 적용됩니다. 작업이 끝나거나 취소되면 앱이 만든 공유 권한을 회수하고 Drive 파일은 보관합니다. 공유 해제 실패가 표시되면 같은 릴리즈에서 다시 시도하거나 Drive에서 해당 파일의 공유를 **제한됨**으로 변경하세요.

조직 정책이 외부 링크 공유를 제한하거나 Google이 다운로드 확인 화면만 제공하면 앱이 이를 알리고 게시를 중단합니다.

## Instagram 연결

**비즈니스와 크리에이터 계정 모두 가능합니다.** 조기축구팀의 공식 계정이라면 비즈니스, 카테고리는 스포츠 팀을 권합니다. 일반 개인 계정은 API 게시 대상이 아닙니다. 계정 유형 변경만으로 API 연결까지 완료되는 것은 아닙니다. Meta 개발자 앱에서 게시 권한과 액세스 토큰을 준비해야 합니다.

개인 계정에서 전환할 때는 Instagram 프로필의 **☰ → 설정 및 활동 → 계정 유형 및 도구 → 프로페셔널 계정으로 전환**에서 카테고리와 유형을 고릅니다. 앱 버전에 따라 프로필 수정 안에 전환 메뉴가 보일 수 있습니다. 계정 전환 자체는 무료입니다.

### PC 파일 직접 전송 · Instagram 통합 로그인 (팀 계정용)

Facebook 페이지 없이 PC의 영상을 릴스로 보내는 방법입니다. 공개 파일 주소는 필요하지 않습니다. 팀 Instagram 비즈니스·크리에이터 계정과 Meta 비즈니스 포트폴리오를 준비하세요.

1. [Meta 내 앱](https://developers.facebook.com/apps/)에서 비즈니스 포트폴리오 소유 앱을 열고 **Use cases → Manage messaging & content on Instagram → Customize → API setup with Unified login**을 여세요. 이 탭은 Facebook Login for Business를 사용하는 포트폴리오 소유 앱에 표시됩니다.
2. **Configure your app → Create new configuration**에서 Instagram 계정과 콘텐츠 게시 권한(`instagram_content_publish`)을 포함하고 **Use this configuration**을 선택합니다. Meta의 설정 마법사에서 요구하는 HTTPS 리디렉션 주소 등은 앱 관리자가 설정합니다.
3. 팀 자체 계정으로 개발 모드에서 연결하려면 **Generate access tokens → Add account**로 팀 Instagram 계정을 초대합니다. 팀 계정으로 Instagram 웹에 로그인하여 **Settings → Website permissions → Apps and websites → Tester Invites**에서 수락하세요. Meta 앱으로 돌아와 **Generate token**을 눌러 팀 포트폴리오와 팀 계정을 승인하고 표시된 토큰을 복사합니다.
4. StampCut **릴리즈 → 계정 연결 / 설정 → PC 파일 직접 전송 · Instagram 통합 로그인**에서 토큰을 입력하고 **Instagram 계정 확인·연결**을 누릅니다. 한 계정만 승인했다면 ID 칸을 비워도 됩니다. 여러 계정이라면 Meta의 `GET /me/assigned_instagram_accounts?fields=id,username` 응답에서 팀 사용자 이름에 해당하는 숫자 ID를 입력하세요.
5. 연결된 팀 사용자 이름을 확인합니다. 이후 릴리즈에서 Instagram Reels를 선택하고 업로드하면 준비된 MP4를 Meta로 전송합니다. 실제 운영용 브라우저 인증은 HTTPS 콜백과 서버 측 코드 교환이 필요합니다. 현재 StampCut은 위 단계에서 발급한 토큰을 입력받습니다.

[Meta 통합 로그인 설정 안내](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/get-started) · [Meta 콘텐츠 게시 안내](https://developers.facebook.com/documentation/instagram-platform/content-publishing/)

### 방식 A: PC 파일 직접 전송 · 기존 Facebook 페이지 연결

이미 팀 Facebook 페이지에 연결된 계정에서 사용할 수 있는 방식입니다.

1. 프로페셔널 Instagram 계정과 관리 권한이 있는 Facebook 페이지를 연결합니다.
2. [Meta for Developers](https://developers.facebook.com/)에서 앱을 만들고 **Instagram API with Facebook Login / Facebook Login for Business**에 맞게 설정합니다. 개발 모드에서는 해당 계정·페이지가 앱 역할과 테스트 요건을 충족해야 합니다.
3. `instagram_basic`, `instagram_content_publish`, `pages_read_engagement` 권한을 부여한 장기 Facebook **페이지 액세스 토큰**을 준비합니다. 페이지 목록 조회에는 `pages_show_list`도 사용합니다. Business Manager 역할 등에 따라 추가 권한이 필요할 수 있으므로 Meta 대시보드 안내를 따릅니다.
4. Meta Graph API Explorer에서 사용자 토큰으로 `me/accounts?fields=id,name,access_token,instagram_business_account`를 조회해 연결된 페이지를 찾습니다. `instagram_business_account.id`가 Instagram 계정 ID이며 페이지 ID와 다릅니다. 해당 페이지의 토큰을 준비합니다. 토큰을 채팅·로그·저장소에 붙여 넣지 마세요.
5. StampCut의 업로드 방식을 **PC 파일 직접 전송 · Facebook 페이지 연결**로 선택하고 Instagram 계정 ID와 토큰을 입력해 **Instagram 계정 확인·연결**을 누릅니다. 표시되는 사용자 이름을 확인합니다.

릴리즈 시 앱이 원본을 보존한 H264/AAC 128k, 30fps, faststart MP4 사본을 캐시에 준비해 Meta로 전송합니다. 준비 작업과 전송은 취소할 수 있습니다. Meta 처리 완료까지 수 분이 걸릴 수 있습니다.

### 방식 B: 공개 영상 URL

**Instagram API with Instagram Login**을 설정하고 `instagram_business_basic`, `instagram_business_content_publish` 권한이 있는 장기 Instagram 사용자 토큰과 프로페셔널 계정 ID를 준비합니다. 이 방식은 Facebook 페이지 연결이 필요하지 않습니다.

1. 계정 설정에서 **공개 영상 URL · Instagram 연결**을 선택하고 ID·토큰을 확인·저장합니다.
2. 릴리즈에서 게시할 로컬 영상을 선택하고 **릴스 호환 파일 준비**를 누릅니다.
3. **릴스 호환 파일** 칸에 표시되는 사본을 본인의 외부 저장소에 올립니다. 위쪽 원본 선택은 유지되며 YouTube는 계속 원본을 사용합니다.
4. 로그인 없이 외부에서 실제 MP4를 내려받을 수 있는 **공개 HTTPS 직접 주소**를 릴리즈 창에 입력합니다. YouTube 시청 주소나 파일 공유 웹페이지 주소는 사용할 수 없습니다. **릴스 호환 파일**이 있으면 그 사본, 없으면 선택한 원본과 주소의 파일이 같아야 합니다.
5. 글을 확인하고 업로드합니다. 영상 경로나 파일 내용이 바뀌면 이전 URL을 다시 사용하지 않도록 확인합니다. 원본 지문을 확인한 URL만 다시 불러오며, 확인 전 닫으면 URL은 다시 입력해야 합니다. 글과 해시태그는 그대로 저장됩니다.

토큰 만료·권한 해제 후에는 새 장기 토큰으로 다시 연결합니다. 앱 역할 밖의 다른 사람도 이용하게 하려면 Meta의 앱 검토·액세스 수준 요건을 확인하세요. StampCut은 Meta 앱 생성, 토큰 발급, 외부 파일 호스팅을 대신하지 않습니다.

공식 문서: [프로페셔널 계정과 Instagram Login](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login), [콘텐츠 게시와 전송 방식](https://developers.facebook.com/documentation/instagram-platform/content-publishing/), [릴스 파일 규격](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media/).

## 저장·재시도·취소

- 제목·글·플랫폼·공개 범위·아동용 여부 등은 `%APPDATA%\StampCut\settings.json`에 저장됩니다. 창 닫기·취소·재시작 후에도 사용할 수 있으며 새 편집 작업에도 유지됩니다.
- 토큰·클라이언트 정보·업로드 세션·성공 기록은 `publish-secrets.bin`에 Windows DPAPI로 암호화됩니다. 현재 Windows 사용자 환경에서 사용하며 다른 계정/PC로 복사하는 설정 이전 방식은 지원하지 않습니다.
- 같은 파일 내용·계정·플랫폼별 글과 옵션의 성공 기록이 있으면 재시도 시 건너뜁니다. URL 만료 등으로 전송 주소만 바꿔도 같은 원본·계정·글의 성공/불확실 기록은 유지됩니다. 글을 바꾸거나 영상을 새로 만들면 새 게시 요청입니다. 이미 게시한 글의 원격 수정 기능은 포함하지 않습니다.
- 앱을 여러 번 실행해도 계정 저장은 프로세스 간 잠금으로 보호하고, 같은 영상·계정·게시 옵션의 동시 업로드는 한 작업만 시작합니다.
- 두 플랫폼 중 한 곳만 실패하면 결과를 확인하고 **미완료 대상 재시도**를 누릅니다. 성공한 동일 요청은 건너뜁니다.
- 전송 도중 취소해도 이미 게시된 영상은 삭제하지 않습니다. 진행 중인 HTTP 요청은 반환/시간 초과 후 취소가 반영됩니다.
- 최종 응답이 끊겨 게시 여부가 불확실하면 **상태 확인 필요**를 표시합니다. 앱은 중복 게시를 피하도록 동일한 불확실 요청을 무작정 다시 게시하지 않습니다. YouTube Studio/Instagram에서 결과를 먼저 확인하세요.
- 게시 성공 후 로컬 기록 저장만 실패하면 게시 링크와 저장 경고를 표시합니다. 게시 결과를 플랫폼에서 확인한 뒤 저장 경로 문제를 해결하세요.
- 설정 저장 실패 시 입력은 메모리에 유지하며 다시 저장 / 저장하지 않고 닫기 / 계속 편집을 선택할 수 있습니다.
- Instagram이 이전 컨테이너를 게시 완료로 응답해도 개별 미디어 ID를 복구할 수 없으면 현재는 **상태 확인 필요**로 남을 수 있습니다. 동일 요청을 새로 게시하지 않습니다.
- **앱에서 연결 해제**는 로컬 연결만 삭제합니다. 서비스의 앱 권한 철회는 Google/Meta 계정 설정에서 별도로 처리합니다.

검증은 임시 설정·가짜 계정·모의 HTTP 응답과 합성 영상으로 진행합니다. 실제 계정 연결과 게시 결과 확인은 계정 준비 후 수행해야 합니다.
