# 릴리스(배포) 가이드

새 버전을 사용자 PC에 배포하는 전체 과정. 배포물은 GitHub Release 에 올리는 두 파일이다:

| 파일 | 용도 |
|---|---|
| `auto-ansimtalk-v{버전}.zip` | 앱 본체 — **설치 프로그램과 자동 업데이트가 이 zip 을 다운로드** |
| `AnsimTalk-Setup.exe` | 설치 프로그램 — 신규 PC 에서 이것 하나만 실행하면 끝 (파이썬 자동 설치 포함) |

## 1. 버전 올리기

`version.py` 의 `__version__` 만 수정한다:

```python
__version__ = "1.0.1"
```

릴리스 태그는 항상 `v` + 이 값 (예: `v1.0.1`). **태그와 version.py 가 일치해야**
업데이트 판정이 올바르게 동작한다 (zip 안의 version.py 로 현재 버전을 인식).

## 2. 빌드

```bat
build_release.bat
```

`dist\` 에 `auto-ansimtalk-v1.0.1.zip` 과 `AnsimTalk-Setup.exe` 가 생성된다.
(사전 준비: `pip install pyinstaller`)

## 3. GitHub Release 만들기 (웹 UI)

1. https://github.com/RoofSkY/auto-ansimtalk → 우측 **Releases** → **Draft a new release**
2. **Choose a tag** → `v1.0.1` 입력 → **Create new tag: v1.0.1 on publish** 클릭
   (Target 은 배포할 브랜치 — 보통 `main`)
3. 제목: `v1.0.1`, 본문에 변경사항 작성
4. 하단 **Attach binaries** 영역에 `dist\auto-ansimtalk-v1.0.1.zip` 과
   `dist\AnsimTalk-Setup.exe` 를 끌어다 놓기
5. **Publish release**

> zip 파일명은 `auto-ansimtalk-….zip` 형식을 유지할 것 — 설치/업데이트가 이 이름 규칙으로 자산을 찾는다.
> `AnsimTalk-Setup.exe` 는 설치 로직이 바뀌었을 때만 새로 첨부하면 되지만, 항상 같이 올려두는 것이 간단하다.

### gh CLI 를 쓰는 경우 (선택)

```bat
winget install GitHub.cli   (최초 1회)
gh auth login               (최초 1회)

gh release create v1.0.1 dist\auto-ansimtalk-v1.0.1.zip dist\AnsimTalk-Setup.exe ^
    --title "v1.0.1" --notes "변경사항 요약"
```

## 4. 배포 후 동작

- **신규 PC**: Release 페이지에서 `AnsimTalk-Setup.exe` 만 받아 실행
  → 파이썬(없으면 3.12 자동 설치) → 최신 릴리스 zip 다운로드 → 패키지 설치
  → 시작 메뉴/바탕화면 바로가기 → (선택) Windows 시작 시 자동 실행
- **기존 설치 PC**: 앱 시작 시 자동으로 최신 릴리스와 버전 비교 → 새 버전이면
  자동 다운로드·적용 후 재시작. 설정 페이지의 **업데이트 확인** 버튼으로 수동 실행도 가능.
- 업데이트 시 `config/`(계정·원생 데이터)와 `logs/` 는 **보존**된다.
- 같은 버전 자동 업데이트가 실패했던 경우 재시작 루프 방지를 위해 자동 적용은
  건너뛰고 로그로 안내한다 (수동 업데이트는 언제나 가능).

## 참고

- 설치 프로그램과 자동 업데이트는 GitHub API 를 **인증 없이** 호출한다 — 저장소가
  public 인 현재 구성에서는 추가 설정이 필요 없다.
- 저장소를 다시 private 으로 바꾸면 릴리스 조회가 404 로 실패한다. 이 경우
  PC 마다 fine-grained PAT(Contents: Read)를 발급해 설치 시 `--token`(또는
  `GITHUB_TOKEN` 환경변수), 업데이트용으로 `config/app_config.json` 에
  `"github_token"` 을 넣어야 한다.
