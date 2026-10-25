# SideMemo 클라우드 동기화 설정

PC 앱과 웹 앱이 같은 메모를 함께 쓰려면 Supabase 프로젝트가 하나 필요합니다. 무료 플랜으로 충분합니다.

## 1. Supabase 프로젝트 만들기

1. https://supabase.com 에 접속해 무료 계정으로 로그인합니다.
2. **New project**를 누르고 이름과 데이터베이스 비밀번호, 리전을 정해 만듭니다. (리전은 Northeast Asia(Seoul)를 권장합니다.)
3. 프로젝트가 준비될 때까지 1~2분 기다립니다.

## 2. 표와 권한 만들기

1. 왼쪽 메뉴에서 **SQL Editor**를 엽니다.
2. 이 저장소의 `web/supabase-schema.sql` 파일 내용을 전부 복사해 붙여넣습니다.
3. **Run**을 누릅니다. `sidememo_notes`, `sidememo_settings` 표와 행 수준 보안 정책이 만들어집니다.

## 3. 접속 정보 복사하기

**Project Settings → API**에서 두 값을 복사합니다.

- **Project URL** — `https://<프로젝트>.supabase.co` 형태
- **anon public** 키 — `eyJ...` 형태의 긴 문자열

anon 키는 공개되어도 안전합니다. 행 수준 보안 덕분에 다른 사람은 본인 데이터에 접근할 수 없습니다.

## 4. PC 앱에 입력하기

1. SideMemo를 실행하고 **⚙ 설정 → 계정**으로 갑니다.
2. **Supabase 주소**와 **Supabase anon key**를 붙여넣고 **적용**을 누릅니다.
3. **로그인 / 계정**을 눌러 **회원가입**합니다.
4. 받은 확인 메일의 링크를 누른 뒤 다시 **로그인**합니다.
5. 로그인하면 자동 동기화가 켜지고, 그때부터 설정한 주기마다 메모가 올라갑니다.

이미 다른 PC나 웹에서 쓴 메모가 있다면, 로그인 직후 그 내용을 먼저 내려받습니다. 이 PC에서 아직 아무것도 입력하지 않은 상태라면 기본 메모가 클라우드 내용으로 바뀝니다.

## 5. 웹에서 쓰기

1. `web/config.js`를 열어 `supabaseUrl`, `supabaseAnonKey`를 채웁니다.
2. GitHub 저장소의 **Settings → Pages**에서 Source를 `Deploy from a branch`, 브랜치를 `main`, 폴더를 `/web`으로 지정하고 저장합니다.
3. 잠시 뒤 `https://<사용자>.github.io/SideMemo/` 에서 웹 앱이 열립니다.
4. 같은 이메일과 비밀번호로 로그인하면 PC의 메모가 그대로 나타납니다.

웹에서 수정한 내용은 실시간 채널로 PC에 전달되고, PC에서 수정한 내용도 웹에 반영됩니다.

## 문제가 생기면

- **테이블을 찾을 수 없습니다** — 2번의 SQL을 실행하지 않았습니다. SQL Editor에서 다시 실행해 주세요.
- **가입 확인 메일이 오지 않습니다** — 스팸함을 확인하고, Supabase의 **Authentication → Providers → Email**에서 Confirm email 설정을 확인해 주세요.
- **동기화가 되지 않습니다** — 설정의 자동 동기화가 켜져 있는지, 주소와 anon key가 정확한지 확인한 뒤 트레이 메뉴의 **지금 동기화**를 눌러 보세요.
- **첨부 이미지가 웹에 안 보입니다** — 1.5MB를 넘는 이미지는 동기화에서 제외됩니다.

