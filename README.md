# auto-ansimtalk — 등하원차량등록

|        |                                                                                                                                                                                                                                                                                                                                                                                 |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 플랫폼 | ![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D6) ![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)                                                                                                                                                                                                                       |
| 스택   | ![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white) ![Jinja2](https://img.shields.io/badge/Jinja2-B41717?logo=jinja&logoColor=white) ![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-v3.4-06B6D4?logo=tailwindcss&logoColor=white) ![Alpine.js](https://img.shields.io/badge/Alpine.js-3.13-77C1D2?logo=alpinedotjs&logoColor=black) |

경기도청 안심톡의 **등하원 처리**와 아이파킹 스토어의 **주차 할인권 등록** 을 한 화면에서 처리하는 로컬 웹 서버입니다.

## 주요 기능

- **입소자 목록** — 출결상태 배지 · 등원/하원 시간 · 상태 필터 탭 · 이름 검색(초성 지원)
- **등하원처리** — 버튼 한 번으로 안심톡 출결 등록 (보호자 문자 발송, 성공/실패 사운드)
- **차량등록** — 입차된 입소자 차량에 주차 할인권 적용 (입차 중이면 행이 초록색)
- **등하원 상태 동기화** — 안심톡 웹을 주기적으로 조회해 배지·시간 자동 반영
- **등하원 변동 로그** — 다른 PC/키패드에서 찍힌 등하원도 실제 시각으로 자동 기록 (중복 없음)
- **차량 자동검색 + 토스트 알림** — 입차/출차 감지 시 브라우저 우측 하단 알림
- **예약 실행** — 지정 시각·요일에 등하원 등록/주차권 등록 자동 실행
- **백업 및 복원** — 입소자·예약을 JSON 파일로 저장하고 확인 후 복원, 복원 전 자동 백업
- **로그** — 실시간 표시(탭 필터), 날짜별 `logs/` 저장·복원
- **자동 업데이트** — 시작 시 GitHub Release 새 버전 확인 후 자동 적용

### Windows 입출차 알림

설정의 **입출차 알림**으로 알림을 켜고 끄며, **알림 방식** 스위치로 웹(왼쪽) 또는 Windows(오른쪽)를 선택한 뒤 저장합니다. 기본값은 Windows입니다. 웹을 선택하면 페이지 안에서만 알림을 표시합니다.

입·출차를 감지하면 Windows 기본 알림과 알림음으로 안내합니다. 브라우저를 닫거나 다른 앱을 사용해도 서버가 트레이에서 실행 중이면 알림을 받을 수 있습니다. 설정 페이지의 **알림 테스트**로 선택한 알림 방식을 확인할 수 있습니다.

팝업·소리·표시 시간은 Windows 설정을 따릅니다. 방해 금지/집중 지원 또는 알림 소리 끄기가 적용돼 있으면 팝업이나 소리가 제한될 수 있습니다. Windows 설정에서 알림 배너와 소리를 허용해 주세요. 트레이가 없거나 알림 호출이 실패한 경우에는 기존 웹 알림을 사용하며, 설정의 웹 대체 알림 표시 시간은 이 경우에만 적용됩니다.

### 입소자·예약 백업 및 복원

설정 → **백업 및 복원**에서 현재 입소자와 예약을 하나의 JSON 파일로 저장합니다.
이름·출석번호·차량번호와 예약 시간·요일·주차권 매수를 포함하며,
계정·비밀번호·앱 설정·출결 상태·처리 기록은 포함하지 않습니다.

복원할 파일을 선택하면 현재 인원·예약 수와 복원 후 개수를 비교할 수 있습니다.
**이 내용으로 복원**을 누르면 입소자와 예약 목록을 교체합니다.
복원한 예약은 모두 꺼져 있으므로 예약 관리에서 확인한 뒤 켜 주세요.
조회나 등하원·주차 처리 중에는 작업이 끝난 후 다시 시도해야 합니다.

복원 직전 데이터는 `config/backups/`에 자동 저장됩니다.
화면의 **복원 전 자동 백업**에서 최근 10개를 다운로드할 수 있으며,
이전 파일도 해당 폴더에 보관됩니다. 복원 도중 저장에 실패하면 기존 데이터를
되돌리고, 중간에 앱이 종료되면 다음 실행 때 중단된 복원을 복구합니다.
백업 파일은 최대 2MB이며 다른 형식이나 지원하지 않는 버전은 가져오지 않습니다.

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
3. **입소자 등록** — 입소자 관리에서 이름·출석번호(4자리)·차량번호 입력
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

- 등하원·차량등록 버튼은 실제 작업이 끝날 때까지 잠긴다. 같은 출석번호나 차량의 중복 요청은 서버에서도 차단하며, 예약 실행에도 적용한다. 차량은 끝 4자리를 기준으로 잠그므로 같은 끝번호의 다른 차량도 동시에 등록할 수 없다.
- 작업 상태는 SSE로 열린 탭에 반영하고, 새로고침·재연결 시 복원한다. 응답이 끊겼을 때는 상태를 조회하며 요청을 자동 재전송하지 않는다.
- 백업·복원 검사: `.venv\Scripts\python.exe -m unittest discover -s dev/test -v`
  (임시 데이터와 로컬 테스트 서버 사용, 실제 등하원·주차 작업 없음)
- UI 검사: `.venv\Scripts\python.exe dev/test/check_ui.py`
- 백업·복원 UI 검사: `.venv\Scripts\python.exe dev/test/check_ui.py --backup-only`

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
