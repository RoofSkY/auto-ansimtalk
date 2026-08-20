# auto-ansimtalk — 등하원차량등록

|        |                                                                                                                                                                                                                                                                                                                                                                                 |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 플랫폼 | ![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D6) ![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)                                                                                                                                                                                                                       |
| 스택   | ![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white) ![Jinja2](https://img.shields.io/badge/Jinja2-B41717?logo=jinja&logoColor=white) ![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-v3.4-06B6D4?logo=tailwindcss&logoColor=white) ![Alpine.js](https://img.shields.io/badge/Alpine.js-3.13-77C1D2?logo=alpinedotjs&logoColor=black) |

경기도청 안심톡의 **등하원 처리**와 아이파킹 스토어의 **주차 할인권 등록** 을 한 화면에서 처리하는 로컬 웹 서버입니다.

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

### **수동 설치 (for DEV)** — Windows, Python 3.12+

프로젝트 폴더에 가상환경(`.venv`)을 만들어 쓴다. `start.bat` 과 `dev\build_release.bat`
모두 `.venv` 가 있으면 자동으로 그쪽 파이썬을 사용한다.

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install pyinstaller   :: 릴리스 빌드용 (선택)
```

실행은 `start.bat` (백그라운드) 또는 `.venv\Scripts\python.exe server.py`.

## 초기 설정 — 설정페이지

1. **안심톡 계정** — 아이디/비밀번호 입력·저장
2. **아이파킹 계정** — 설정에서 스토어 ID(주차장 아이디)·아이디·비밀번호 입력·저장
3. **원생 등록** — 원생 관리에서 이름·출석번호(4자리)·차량번호 입력
   (차량번호는 콤마로 여러 대, 기본은 끝 4자리 — 같은 번호가 겹치면 `00가0000` 처럼 전체 입력)

## 프로젝트 구조

```
auto-ansimtalk/
├─ server.py           # 진입점 런처 (실제 구현은 src/app.py — 위치 고정)
├─ version.py          # 앱 버전 (릴리스 시 여기만 수정)
├─ src/                # 소스
│   ├─ app.py              # FastAPI 웹 서버 (메인)
│   ├─ ansim.py            # 안심톡 agent API — 등하원 등록
│   ├─ ansim_web.py        # 안심톡 웹 포털 — 상태/시간 조회
│   ├─ iparking.py         # 아이파킹 STORE — 로그인·입차 조회·할인권 등록/취소
│   ├─ updater.py          # GitHub Release 자동 업데이트
│   └─ autostart.py        # Windows 시작 시 자동 실행
├─ dev/                # 개발·빌드 전용 (배포본에는 미포함)
│   ├─ build_release.bat, build_css.bat
│   ├─ tailwind.config.js, tailwind.input.css
│   ├─ installer/, tools/   # 설치 프로그램·빌드 스크립트
│   └─ bin/                 # tailwindcss.exe (gitignore)
├─ docs/RELEASE.md     # 릴리스 가이드
├─ templates/, static/ # 화면 (Jinja2 + Tailwind + Alpine.js)
├─ sound/              # 성공/실패 알림음
├─ config/             # 설정·데이터 (gitignore)
└─ logs/               # 날짜별 로그 (gitignore)
```

## 개발 참고

- 개발·빌드 관련 파일은 모두 [dev/](dev/) 에 모여 있음 (배포 zip 에는 포함되지 않음)
- 화면의 Tailwind 클래스를 수정하면 `dev\build_css.bat` 로 CSS 재생성
  (Tailwind CLI 는 용량이 커서 git 에 없음 — [릴리스](https://github.com/tailwindlabs/tailwindcss/releases) 에서 받아 `dev\bin\tailwindcss.exe` 로 배치)
- Release 만드는 방법: [docs/RELEASE.md](docs/RELEASE.md)
- 빌드·실행 스크립트는 `.venv` 를 우선 사용 — 없으면 PATH 의 파이썬으로 폴백
  (릴리스 zip 에는 `.venv` 가 포함되지 않아 사용자 PC 동작은 그대로)
- 아이파킹(주차) 연동은 순수 HTTP REST API (`iparking.py`) — 세션 토큰은 만료 시 자동 재발급

## 문제 해결

| 증상                               | 확인                                                                                           |
| ---------------------------------- | ---------------------------------------------------------------------------------------------- |
| 등하원 등록 "학생정보 없음" 실패   | 설정 → 안심톡 계정 확인 (빈 계정으로 로그인되면 엉뚱한 시설로 붙음)                            |
| 등하원 등록 "자격증명 미설정" 실패 | 설정 → 안심톡 계정에 아이디/비밀번호 저장                                                      |
| 차량 조회가 계속 실패              | 아이파킹 세션/계정 확인 — 설정에서 아이파킹 계정 재저장 (또는 `config/iparking.json` 확인)     |
| 상태 배지가 갱신되지 않음          | 설정 → "등하원 상태 동기화" 스위치 확인, 새로고침 버튼으로 즉시 갱신                           |
| 세션이 꼬인 것 같을 때             | `config/ansimtalk.json`·`config/iparking.json` 의 `session` 키만 지우거나 설정에서 계정 재저장 |
