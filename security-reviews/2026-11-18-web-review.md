# SideMemo 웹 보안 검사 결과

검사일: 2026-11-18 (Asia/Seoul). 대상은 `docs/` 웹 클라이언트의 로컬 소스와 미커밋 변경. Edge headless 격리 페이지, 네트워크 차단 모의 Supabase, 로컬 SQL 코드 검토를 사용했다. 운영 계정·메모·DB를 읽거나 변경하지 않았다. 공개 사이트의 HTTP 응답 헤더/현행 배포 내용은 확인하지 않았다.

실행: `node security-reviews/check_web.cjs` (11개 사례: HTML 정화·서식·첨부 URL·최초 pull 오류·동시 편집·계정 응답 격리·로그아웃·payload 형식·CSP); `node --check docs/app.js`. Supabase JS `2.119.0` 고정 UMD 파일의 실제 CDN 바이트 SHA-384를 별도로 확인해 HTML SRI 값과 대조했다. `docs/supabase-schema.sql`의 RLS 정책을 읽기 전용으로 검토했다.

| ID | 상태 | 근거·영향 |
|---|---|---|
| WEB-SEC-01 | 통과 | `clean()` 허용 목록 테스트에서 script/event/SVG/iframe/링크 제거, 한글 서식 보존 |
| WEB-SEC-02 | 통과 | 외부 이미지 URL 미표시, 허용된 raster data URI 형식만 렌더링 |
| WEB-SEC-03 | 통과 | 원격 payload 타입·크기·범위 검사. 비정상 HTML, 크기, 테마, 첨부, 위치는 적용하지 않음 |
| WEB-SEC-04 | 통과 | 로그아웃 후 DOM/메모 배열 비움; 계정 전환 이후 도착한 이전 사용자 응답 거부 |
| WEB-SEC-05 | 미검증 | 로컬 SQL은 notes/settings에 `auth.uid() = user_id` USING/WITH CHECK와 RLS 활성화를 선언. 운영 DB 정책·권한은 접근하지 않아 확인 불가 |
| WEB-SEC-06 | 통과 (로컬) / 미검증 (호스팅) | CSP meta 브라우저 인라인 script 차단, Referrer-Policy, 정확한 CDN 버전과 SRI 확인. GitHub Pages 실제 응답 헤더와 프레임 삽입 방어는 미검증 |
| WEB-SEC-07 | 실패 | Supabase 브라우저 SDK 기본 세션 지속 저장을 사용해 토큰이 JS 접근 가능한 브라우저 저장소에 남을 수 있음. XSS가 발생하면 세션 탈취 위험이 커짐. 현재 HTML 정화/CSP 검사에서는 테스트 XSS가 실행되지 않았지만 HttpOnly 쿠키 방식은 아님 |
| WEB-SEC-08 | 미검증 | 검사한 소스는 로컬 변경본이며 GitHub Pages 배포 반영·실제 공개 응답을 아직 확인하지 않음 |

판정: 통과 4, 실패 1, 미검증 3, 해당 없음 0 (WEB-SEC-06은 로컬 정책만 통과). 가장 중요한 남은 위험은 JS에서 읽을 수 있는 Supabase 세션 저장 방식이다. 실제 DB RLS와 공개 호스팅 헤더를 확인하지 않은 상태이므로 운영 권한 격리를 보증하지 않는다. 앱 소스 수정은 했으며 이 보고서 시점에는 릴리스/Pages 배포 확인 전이다.
