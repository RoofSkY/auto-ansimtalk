# auto-ansimtalk — 등하원차량등록

| | |
| --- | --- |
| 플랫폼 | ![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white) ![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D6) |
| 스택 | ![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white) ![Jinja2](https://img.shields.io/badge/Jinja2-B41717?logo=jinja&logoColor=white) ![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-v3.4-06B6D4?logo=tailwindcss&logoColor=white) ![Alpine.js](https://img.shields.io/badge/Alpine.js-3.13-77C1D2?logo=alpinedotjs&logoColor=black) ![SSE](https://img.shields.io/badge/SSE-%EC%8B%A4%EC%8B%9C%EA%B0%84_%EB%B0%98%EC%98%81-FF8C00) |
| 연동 | ![안심톡](https://img.shields.io/badge/%EC%95%88%EC%8B%AC%ED%86%A1-%EA%B2%BD%EA%B8%B0%EB%8F%84_%EB%93%B1%ED%95%98%EC%9B%90-1E90FF) ![Nicepark](https://img.shields.io/badge/Nicepark-%EC%A3%BC%EC%B0%A8_%ED%95%A0%EC%9D%B8%EA%B6%8C-2E8B57) |
| 용도 | ![시설 내부용](https://img.shields.io/badge/%EC%82%AC%EC%9A%A9%EB%B2%94%EC%9C%84-%EC%8B%9C%EC%84%A4_%EB%82%B4%EB%B6%80%EC%9A%A9-lightgrey) |

지역아동센터의 **원생 등하원 처리(안심톡)** 와 **주차 할인권 등록(Nicepark)** 을 한 화면에서 처리하는 로컬 웹 서버입니다. 브라우저에서 버튼 클릭 한 번으로 등하원 문자 발송과 주차권 등록을 실행하고, 안심톡 웹의 출결 상태를 실시간으로 동기화해 보여줍니다.

## 주요 기능

### 원생 목록 (메인 화면 좌측)
- 원생별 **출결상태 배지** — 미등원(흰색) · 등원(하늘색) · 하원(연노랑) · 결석/공결/캠프(빨강)
- **등원/하원 시간** 컬럼 (안심톡 웹에서 자동 동기화)
- **등하원처리** 버튼 — 안심톡 서버에 출결 등록 (보호자 문자 발송)
- **차량등록** 버튼 — 입차된 원생 차량에 주차 할인권 적용 (기본: 2시간 무료권)
- 원생 차량이 입차 중이면 행이 초록색으로 표시
- 이름 검색 (초성 검색 지원)
- **새로고침 버튼** (`⟳ N초`) — 다음 자동 갱신까지 남은 초 표시, 클릭 시 즉시 갱신
  (등하원처리 완료 후에는 등하원 상태만 자동으로 즉시 갱신, 차량등록은 다음 주기에 반영)

### 로그 (메인 화면 우측)
- 등하원/차량등록/입출차 로그를 실시간 표시, 탭으로 필터링
- 날짜별 `logs/YYYY-MM-DD.jsonl` 저장, 재시작 시 당일 로그 복원

### 자동화
- **등하원 상태 동기화** — 안심톡 웹에서 원생별 출결 상태·시간을 주기적으로 조회해 배지에 반영
- **등하원 변동 로그** — 키패드 등 다른 기기에서 찍힌 등원/하원도 동기화 시 감지해
  일반 등하원처리와 동일한 양식(`[안심톡] 출결번호 이름 - 등원하였습니다.`)으로 로그 기록
- **원생 차량 자동검색** — 등록된 차량번호의 입차 여부를 주기적으로 확인
- **입출차 토스트 알림** — 입차(🚗)/출차(🅿️) 감지 시 브라우저 우측 하단 알림
- **예약 실행** — 지정 시각·요일에 등하원 등록/주차권 등록 자동 실행
- 위 두 자동검색·동기화는 **공통 갱신 주기**(기본 1분, 최소 10초, 설정에서 변경) 사용
- 등하원 등록 성공/실패 시 사운드 알림 (`sound/S1.Wav` / `S2.Wav`)

### 기타
- 헤더 중앙에 초단위 24시간 시계, 라이트/다크 모드 토글
- 설정 페이지: 기능별 on/off 스위치, 갱신 주기, Windows 시작 시 자동 실행,
  원생 관리 / 차량 예약 등록 관리, 안심톡 계정 변경, Nicepark 쿠키 수동 갱신,
  업데이트 확인
- **자동 업데이트** — 앱 시작 시 GitHub Release 최신 버전과 비교해 새 버전이면
  자동 다운로드·적용 후 재시작 (설정의 "업데이트 확인" 버튼으로 수동 실행도 가능,
  `config/`·`logs/` 는 보존)
- 시스템 트레이 아이콘으로 백그라운드 실행 (우클릭 → 종료)

## 설치

### 방법 A — 설치 프로그램 (권장, 파이썬 몰라도 됨)

[Releases](https://github.com/RoofSkY/auto-ansimtalk/releases) 에서 **AnsimTalk-Setup.exe** 를 받아 실행하면 끝:

1. 파이썬이 없으면 Python 3.12 자동 설치
2. 최신 버전 앱 다운로드 및 필요 패키지 자동 설치
3. 시작 메뉴/바탕화면 바로가기 생성, (선택) Windows 시작 시 자동 실행 등록

이후 새 버전이 릴리스되면 앱이 시작할 때 스스로 업데이트한다.

제거는 **Windows 설정 → 앱 → 설치된 앱 → 등하원차량등록 → 제거**.
제거 시 원생 목록·계정 등 데이터(config/logs)를 함께 지울지 선택할 수 있다.

### 방법 B — 수동 설치 (개발용)

요구 사항: **Windows**, **Python 3.12+**

```bat
pip install -r requirements.txt
playwright install chromium
```

> Nicepark 로그인 브라우저는 Playwright 크로미움이 없으면 Windows 기본 Edge 로
> 자동 폴백하므로 `playwright install chromium` 은 생략해도 된다.

## 초기 설정

### 1. 안심톡 계정

서버 실행 후 **설정 → 안심톡 계정** 에서 아이디/비밀번호를 입력하고 저장하면 됩니다. 저장 시 기존 세션이 초기화되고 새 계정으로 즉시 동기화되며, 값은 `config/ansim_config.json` 에 저장됩니다 (파일을 직접 만들어도 됩니다 — `user_id`/`password` 키).

등하원 등록(agent API)과 상태 동기화(웹 포털)에 공용으로 사용되고, 시설 정보(program/customer_id)는 로그인 시 자동 조회됩니다.

> ⚠️ 계정이 설정되지 않으면 등하원 등록이 "안심톡 자격증명 미설정" 으로 실패합니다.

### 2. Nicepark(주차) 로그인 — 최초 1회

**설정 → 나이스파크(주차) 세션 → 쿠키 수동 갱신** 버튼을 누르면 로그인 브라우저 창이 열립니다. Nicepark 웹디스카운트에 로그인하면 완료가 자동 감지되어 쿠키가 `config/cookies.json` 에 저장되고, 이후에는 자동으로 재사용됩니다 (터미널에서 `python auth.py` 로도 가능). 세션 만료 시 자동 재로그인을 시도합니다.

### 3. 원생 등록

서버 실행 후 **설정 → 원생 관리** 에서 이름, 출석번호(안심톡 출결번호 4자리), 차량번호를 등록합니다.

- 차량번호는 콤마로 여러 대 입력 가능: `0000, 0000, 0000`
- 기본은 마지막 4자리만 입력, 같은 4자리 차량이 여럿이면 앞자리 포함 전체 번호판 입력: `00가0000`

## 실행

```
start.bat
```

터미널 창 없이 백그라운드로 실행되며 브라우저가 자동으로 열립니다 (http://localhost:5000). 종료는 트레이 아이콘 우클릭 → 종료, 또는 설정 페이지의 서버 종료 버튼.

## 프로젝트 구조

```
auto-ansimtalk/
├─ server.py           # FastAPI 웹 서버 (메인)
├─ ansim.py            # 안심톡 agent API — 등하원 등록
├─ ansim_web.py        # 안심톡 웹 포털 — 등하원 상태/시간 조회
├─ npdc.py             # Nicepark — 입차 조회·주차 할인권 등록
├─ auth.py             # Nicepark 로그인 세션(쿠키) 관리
├─ version.py          # 앱 버전 (릴리스 시 여기만 수정)
├─ updater.py          # GitHub Release 자동 업데이트
├─ autostart.py        # Windows 시작 시 자동 실행 (HKCU Run) 관리
├─ start.bat           # 실행 스크립트 (백그라운드)
├─ build_css.bat       # 화면 수정 시 static/tailwind.css 재생성 (아래 참고)
├─ build_release.bat   # 배포물 빌드 (릴리스 zip + Setup.exe) — RELEASE.md 참고
├─ installer/          # 설치 프로그램 소스 (setup.py → AnsimTalk-Setup.exe)
├─ tools/              # 빌드 보조 스크립트 (릴리스 zip 생성)
├─ templates/          # 화면 (Jinja2 + Tailwind + Alpine.js)
├─ static/             # 로컬 정적 자원 (tailwind.css, alpine.min.js, app.ico)
├─ sound/              # S1.Wav(성공) / S2.Wav(실패)
├─ config/             # 설정·데이터 (gitignore — 개인정보/자격증명 포함)
│   ├─ ansim_config.json    # 안심톡 계정 (설정 페이지에서 관리)
│   ├─ students.json        # 원생 목록 (UI에서 관리)
│   ├─ schedules.json       # 예약 (UI에서 관리)
│   ├─ app_config.json      # 앱 설정 (UI에서 관리)
│   ├─ cookies.json         # Nicepark 세션 (자동 생성)
│   ├─ ansim_session.json   # 안심톡 세션 (자동 생성)
│   └─ update_state.json    # 자동 업데이트 상태 (자동 생성)
└─ logs/               # 날짜별 작업 로그·진단 로그 (gitignore)
```

새 버전 배포 방법은 [RELEASE.md](RELEASE.md) 참고.

## 화면(CSS) 수정 시

스타일은 빌드된 정적 CSS(`static/tailwind.css`)로 서빙됩니다. `templates/` 에서 Tailwind 클래스를 추가·변경했다면 재빌드가 필요합니다:

```bat
build_css.bat
```

빌드 도구는 Tailwind v3 standalone CLI 로, `_dev/tailwindcss.exe` 에 두면 됩니다 (없으면
https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-windows-x64.exe 다운로드 후 이름 변경).

## 참고 사항

- 등하원 상태는 **메모리로만 관리**됩니다 — 서버 재시작 시 첫 동기화(한 주기 이내)가 다시 채우고, 날짜가 바뀌면 전원 미등원으로 초기화됩니다.
- 등하원처리·차량등록 실행이 끝나면 자동으로 즉시 갱신되어 화면에 바로 반영됩니다.
- `config/` 와 `logs/` 는 자격증명·개인정보가 포함되므로 git 에 커밋되지 않습니다.
- 사운드는 Windows 전용(`winsound`)이며, 다른 OS에서는 무음으로 동작합니다.

## 문제 해결

| 증상 | 확인 |
|---|---|
| 등하원 등록이 "학생정보 없음" 실패 | `config/ansim_config.json` 자격증명 확인. 빈 계정으로 로그인되면 엉뚱한 시설로 붙습니다 |
| 등하원 등록이 "자격증명 미설정" 실패 | `config/ansim_config.json` 파일 생성 여부 확인 |
| 차량 조회가 계속 실패 | Nicepark 세션 만료 — 설정 페이지의 "쿠키 수동 갱신" 버튼 (또는 `python auth.py`) |
| 상태 배지가 갱신되지 않음 | 설정에서 "등하원 상태 동기화" 스위치 확인, 새로고침 버튼으로 즉시 갱신 |
| 세션이 꼬인 것 같을 때 | `config/ansim_session.json`, `config/cookies.json` 삭제 후 재시작/재로그인 |
