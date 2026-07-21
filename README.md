# auto-ansimtalk — 등하원차량등록

|        |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 플랫폼 | ![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white) ![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D6)                                                                                                                                                                                                                                                                                                                      |
| 스택   | ![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white) ![Jinja2](https://img.shields.io/badge/Jinja2-B41717?logo=jinja&logoColor=white) ![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-v3.4-06B6D4?logo=tailwindcss&logoColor=white) ![Alpine.js](https://img.shields.io/badge/Alpine.js-3.13-77C1D2?logo=alpinedotjs&logoColor=black) ![SSE](https://img.shields.io/badge/SSE-%EC%8B%A4%EC%8B%9C%EA%B0%84_%EB%B0%98%EC%98%81-FF8C00) |
| 연동   | ![안심톡](https://img.shields.io/badge/%EC%95%88%EC%8B%AC%ED%86%A1-%EA%B2%BD%EA%B8%B0%EB%8F%84_%EB%93%B1%ED%95%98%EC%9B%90-1E90FF) ![Nicepark](https://img.shields.io/badge/Nicepark-%EC%A3%BC%EC%B0%A8_%ED%95%A0%EC%9D%B8%EA%B6%8C-2E8B57)                                                                                                                                                                                                                                    |
| 용도   | ![시설 내부용](https://img.shields.io/badge/%EC%82%AC%EC%9A%A9%EB%B2%94%EC%9C%84-%EC%8B%9C%EC%84%A4_%EB%82%B4%EB%B6%80%EC%9A%A9-lightgrey)                                                                                                                                                                                                                                                                                                                                     |

지역아동센터의 **등하원 처리(안심톡)** 와 **주차 할인권 등록(Nicepark)** 을 한 화면에서 처리하는 로컬 웹 서버입니다.

## 주요 기능

- **원생 목록** — 출결상태 배지 · 등원/하원 시간 · 상태 필터 탭 · 이름 검색(초성 지원)
- **등하원처리** — 버튼 한 번으로 안심톡 출결 등록 (보호자 문자 발송, 성공/실패 사운드)
- **차량등록** — 입차된 원생 차량에 주차 할인권 적용 (입차 중이면 행이 초록색)
- **등하원 상태 동기화** — 안심톡 웹을 주기적으로 조회해 배지·시간 자동 반영
- **등하원 변동 로그** — 다른 PC/키패드에서 찍힌 등하원도 실제 시각으로 자동 기록 (중복 없음)
- **차량 자동검색 + 토스트 알림** — 입차/출차 감지 시 브라우저 우측 하단 알림
- **예약 실행** — 지정 시각·요일에 등하원 등록/주차권 등록 자동 실행
- **로그** — 실시간 표시(탭 필터), 날짜별 `logs/` 저장·복원
- **자동 업데이트** — 시작 시 GitHub Release 새 버전 확인 후 자동 적용

## 설치

### **자동 설치 (권장)**

[Releases](https://github.com/RoofSkY/auto-ansimtalk/releases) 에서 `AnsimTalk-Setup.exe` 다운로드 후 실행. 파이썬이 없어도 자동으로 설치됩니다.
제거는 Windows 설정 → 앱 → 설치된 앱 → 등하원차량등록 (데이터 삭제 여부 선택 가능).

### **수동 설치 (개발용)** — Windows, Python 3.12+

```bat
pip install -r requirements.txt
```

## 초기 설정 — 설정페이지

1. **안심톡 계정** — 아이디/비밀번호 입력·저장 (시설 정보는 자동 조회)
2. **Nicepark 로그인 (최초 1회)** — "쿠키 수동 갱신" 클릭 → 열리는 브라우저에서 로그인하면 자동 저장
3. **원생 등록** — 원생 관리에서 이름·출석번호(4자리)·차량번호 입력
   (차량번호는 콤마로 여러 대, 기본은 끝 4자리 — 같은 번호가 겹치면 `00가0000` 처럼 전체 입력)

## 프로젝트 구조

```
auto-ansimtalk/
├─ server.py           # FastAPI 웹 서버 (메인)
├─ ansim.py            # 안심톡 agent API — 등하원 등록
├─ ansim_web.py        # 안심톡 웹 포털 — 상태/시간 조회
├─ npdc.py             # Nicepark 입차 조회·할인권 등록
├─ auth.py             # Nicepark 로그인 세션 관리
├─ updater.py          # GitHub Release 자동 업데이트
├─ autostart.py        # Windows 시작 시 자동 실행
├─ version.py          # 앱 버전 (릴리스 시 여기만 수정)
├─ installer/, tools/  # 설치 프로그램·빌드 스크립트
├─ templates/, static/ # 화면 (Jinja2 + Tailwind + Alpine.js)
├─ sound/              # 성공/실패 알림음
├─ config/             # 설정·데이터 (gitignore — 자격증명·개인정보)
└─ logs/               # 날짜별 로그 (gitignore)
```

## 개발 참고

- 화면의 Tailwind 클래스를 수정하면 `build_css.bat` 로 CSS 재생성 (`_dev/tailwindcss.exe` 필요)
- Release 만드는 방법: [RELEASE.md](RELEASE.md)
- 등하원 상태는 메모리로만 관리 — 재시작하면 첫 동기화가 다시 채움
- Nicepark 로그인 브라우저는 크로미움이 없으면 Edge 로 자동 폴백 (`playwright install chromium` 불필요)

## 문제 해결

| 증상                               | 확인                                                                       |
| ---------------------------------- | -------------------------------------------------------------------------- |
| 등하원 등록 "학생정보 없음" 실패   | 설정 → 안심톡 계정 확인 (빈 계정으로 로그인되면 엉뚱한 시설로 붙음)        |
| 등하원 등록 "자격증명 미설정" 실패 | 설정 → 안심톡 계정에 아이디/비밀번호 저장                                  |
| 차량 조회가 계속 실패              | Nicepark 세션 만료 — 설정 → "쿠키 수동 갱신"                               |
| 상태 배지가 갱신되지 않음          | 설정 → "등하원 상태 동기화" 스위치 확인, 새로고침 버튼으로 즉시 갱신       |
| 세션이 꼬인 것 같을 때             | `config/ansim_session.json`, `config/cookies.json` 삭제 후 재시작/재로그인 |
