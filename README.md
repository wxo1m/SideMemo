# SideMemo

> `dist\\SideMemo\\SideMemo.exe`를 직접 배포할 때는 `_internal` 폴더도 반드시 같은 위치에 함께 복사해야 합니다. 설치 파일(`installer\\SideMemo-Setup.exe`)은 두 항목을 자동으로 함께 설치합니다.

Windows 화면 가장자리에서 열고 닫는 상단 고정 메모 앱입니다.

## 실행 파일 만들기

1. `build_exe.bat`를 실행합니다.
2. `dist\SideMemo\SideMemo.exe`가 생성됩니다.
3. Inno Setup 6에서 `SideMemo.iss`를 열어 Compile하면 `installer\SideMemo-Setup.exe` 설치 파일이 생성됩니다.

## 기본 사용법

- `Ctrl + Alt + M`: 창 표시 및 앞으로 가져오기
- 메모에서 마우스를 떼면 설정된 시간 후 세로 탭 상태로 접힙니다.
- 탭을 클릭하면 메모를 열고, ⚙에서 전체·탭별 설정을 변경합니다.
- 창을 닫으면 종료되지 않고 트레이로 숨습니다.
- 백업 내보내기는 메모와 첨부 이미지를 하나의 `.zip` 파일로 저장합니다.

## 클라우드 동기화와 웹 사용

같은 계정으로 PC와 웹에서 메모를 함께 쓸 수 있습니다. Supabase 무료 프로젝트를 하나 만들면 됩니다.

1. https://supabase.com 에서 무료 계정을 만들고 **New project**로 프로젝트를 생성합니다.
2. 프로젝트의 **SQL Editor**를 열고 `docs/supabase-schema.sql` 내용을 전체 붙여넣은 뒤 **Run**을 누릅니다. 표와 접근 권한이 한 번에 만들어집니다.
3. **Project Settings → API**에서 `Project URL`과 `anon public` 키를 복사합니다.
4. PC 앱의 **설정 → 계정**에서 두 값을 붙여넣고 **로그인 / 계정**을 눌러 회원가입한 뒤 로그인합니다.
5. 웹은 https://wxo1m.github.io/SideMemo/ 에서 바로 열립니다. 페이지 오른쪽 위의 **연결 설정**을 눌러 같은 주소와 anon key를 넣으면 PC와 같은 메모가 나타납니다.

동기화는 설정한 주기마다 자동으로 실행되며, 트레이 메뉴의 **지금 동기화**로 즉시 맞출 수 있습니다. 첨부 이미지는 1.5MB 이하일 때만 함께 올라갑니다.

## 설치할 때 Windows 경고가 뜰 수 있습니다

이 앱은 아직 코드 서명 인증서가 적용되지 않아, 설치 파일을 내려받으면 Windows가 "알 수 없는 게시자" 또는 SmartScreen 경고를 표시할 수 있습니다. 실행 파일 자체에는 게시자·제품 정보가 포함되어 있지만, 인증서가 없으면 경고 문구는 사라지지 않습니다.

안전하게 설치하는 방법:

1. SmartScreen 창에서 "추가 정보"를 누른 뒤 "실행"을 선택합니다.
2. 브라우저가 파일을 차단하면 다운로드 목록에서 "계속"을 선택합니다.

Windows Defender가 오탐으로 차단하면 다음 순서로 처리합니다.

1. "Windows 보안" → "바이러스 및 위협 방지" → "제외 항목"에서 설치 폴더를 추가합니다.
2. Microsoft에 오탐 신고: https://www.microsoft.com/en-us/wdsi/filesubmission

경고를 완전히 없애려면 코드 서명 인증서(OV 또는 EV)가 필요합니다. 인증서를 준비한 뒤 아래 환경 변수를 설정하면 빌드 시 자동으로 서명됩니다.

- `SIDEMEMO_SIGN_CERT`: .pfx/.p12 인증서 경로
- `SIDEMEMO_SIGN_PASSWORD`: .pfx 암호 (해당하는 경우)
- `SIDEMEMO_SIGN_THUMBPRINT`: 인증서 저장소에 설치된 인증서의 지문
