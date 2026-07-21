"""GitHub Release 기반 자동 업데이트.

- 최신 릴리스 조회: https://api.github.com/repos/{repo}/releases/latest
- 릴리스 태그(v1.2.3)와 version.py 의 __version__ 비교
- 새 버전이면 릴리스 zip 을 받아 앱 폴더에 덮어쓰고 서버를 재시작
  (config/, logs/ 는 보존, 데이터·자격증명은 건드리지 않음)

repo 가 private 이면 토큰 없이는 조회가 안 됨 —
config/app_config.json 의 "github_token" 또는 환경변수 GITHUB_TOKEN 사용.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from version import __version__, APP_NAME, GITHUB_REPO

HERE = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
CONFIG_DIR = HERE / "config"
LOGS_DIR = HERE / "logs"
UPDATE_STATE_PATH = CONFIG_DIR / "update_state.json"

# 업데이트 시 덮어쓰지 않는 폴더 (사용자 데이터·자격증명·로그)
PRESERVE_DIRS = {"config", "logs"}

_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_TIMEOUT = 10


# ---------- 토큰 / HTTP ----------
def _get_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        return tok
    try:
        with open(CONFIG_DIR / "app_config.json", encoding="utf-8") as f:
            return (json.load(f).get("github_token") or "").strip()
    except Exception:
        return ""


def _request(url: str, accept: str = "application/vnd.github+json") -> urllib.request.Request:
    req = urllib.request.Request(url, headers={
        "User-Agent": APP_NAME,
        "Accept": accept,
    })
    tok = _get_token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    return req


# ---------- 버전 비교 ----------
def parse_version(s: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2' 형식을 비교 가능한 튜플로. 파싱 불가 시 (0,)."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", (s or "").strip())
    if not m:
        return (0,)
    return tuple(int(p) for p in m.group(1).split("."))


def is_newer(latest_tag: str, current: str = __version__) -> bool:
    a, b = parse_version(latest_tag), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


# ---------- 릴리스 조회 ----------
def get_latest_release() -> dict:
    """최신 릴리스 정보. {'tag','name','notes','zip_url','zip_api_url','zip_name'}"""
    with urllib.request.urlopen(_request(_API_LATEST), timeout=_TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))

    tag = data.get("tag_name") or ""
    asset = None
    for a in data.get("assets") or []:
        name = a.get("name") or ""
        if name.lower().endswith(".zip") and APP_NAME in name.lower():
            asset = a
            break
    if asset is None:  # 이름 규칙에 안 맞아도 zip 이 하나뿐이면 그걸 사용
        zips = [a for a in data.get("assets") or []
                if (a.get("name") or "").lower().endswith(".zip")]
        if len(zips) == 1:
            asset = zips[0]

    return {
        "tag": tag,
        "name": data.get("name") or tag,
        "notes": data.get("body") or "",
        # 다운로드 경로: 첨부 zip 이 있으면 그걸, 없으면 소스 zipball
        "zip_url": (asset or {}).get("browser_download_url") or data.get("zipball_url") or "",
        "zip_api_url": (asset or {}).get("url") or data.get("zipball_url") or "",
        "zip_name": (asset or {}).get("name") or f"{APP_NAME}-{tag}.zip",
    }


def check_update() -> dict:
    """업데이트 확인 결과 요약. 실패 시 error 에 사유."""
    try:
        rel = get_latest_release()
    except Exception as e:
        return {"current": __version__, "latest": "", "available": False,
                "error": _friendly_error(e)}
    return {
        "current": __version__,
        "latest": rel["tag"],
        "available": is_newer(rel["tag"]),
        "notes": rel["notes"],
        "error": None,
        "_release": rel,
    }


def _friendly_error(e: Exception) -> str:
    s = str(e)
    if "404" in s:
        return ("릴리스를 찾을 수 없습니다 — 아직 GitHub Release 가 없거나 "
                "저장소에 접근할 수 없습니다 (RELEASE.md 참고)")
    if "403" in s:
        return "GitHub API 요청 제한/권한 오류 (잠시 후 재시도)"
    return f"업데이트 확인 실패: {s}"


# ---------- 다운로드 / 적용 ----------
def _download_zip(release: dict, dest: Path, progress=None) -> Path:
    """릴리스 zip 다운로드. 토큰이 있으면 API asset URL(사설 저장소용) 사용."""
    if _get_token():
        url, accept = release["zip_api_url"], "application/octet-stream"
    else:
        url, accept = release["zip_url"], "application/octet-stream"
    if not url:
        raise RuntimeError("릴리스에 다운로드 가능한 zip 이 없습니다")

    path = dest / release["zip_name"]
    with urllib.request.urlopen(_request(url, accept), timeout=30) as r, \
            open(path, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done, total)
    return path


def safe_extract(zip_path: Path, dest: Path) -> Path:
    """zip 을 dest 에 안전하게 풀고, 실제 앱 루트 폴더를 반환.

    - 경로 탈출(zip slip) 차단
    - zipball 처럼 최상위 폴더 하나로 감싸져 있으면 그 안쪽을 루트로 판단
    """
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest)):
                raise RuntimeError(f"zip 경로 이상: {info.filename}")
        z.extractall(dest)

    if (dest / "server.py").exists():
        return dest
    subdirs = [p for p in dest.iterdir() if p.is_dir()]
    if len(subdirs) == 1 and (subdirs[0] / "server.py").exists():
        return subdirs[0]
    raise RuntimeError("zip 안에서 server.py 를 찾지 못했습니다")


def apply_release(root: Path, install_dir: Path | None = None) -> None:
    """압축 해제된 릴리스(root)를 설치 폴더에 덮어쓰기. config/, logs/ 는 보존."""
    install_dir = (install_dir or HERE).resolve()
    for src in root.rglob("*"):
        rel = src.relative_to(root)
        if rel.parts and rel.parts[0] in PRESERVE_DIRS:
            continue
        dst = install_dir / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _pip_install_requirements(install_dir: Path, log_file) -> None:
    req = install_dir / "requirements.txt"
    if not req.exists():
        return
    creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req)],
        stdout=log_file, stderr=subprocess.STDOUT,
        creationflags=creationflags, timeout=600, check=False,
    )


def restart_app() -> None:
    """분리된 헬퍼 배치로 서버를 재시작하고 현재 프로세스를 종료."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    bat = Path(tempfile.gettempdir()) / f"{APP_NAME}-restart.bat"
    bat.write_text(
        "@echo off\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        f'cd /d "{HERE}"\r\n'
        f'start "" "{exe}" "{HERE / "server.py"}" --no-browser\r\n',
        encoding="utf-8",
    )
    creationflags = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=creationflags,
                     close_fds=True, cwd=str(tempfile.gettempdir()))
    os._exit(0)


def download_and_apply(release: dict, log=print) -> None:
    """다운로드 → 덮어쓰기 → pip → 재시작. 성공 시 프로세스가 종료됨(반환 안 함)."""
    if (HERE / ".git").exists():
        raise RuntimeError("git 작업 폴더에서는 자동 업데이트를 사용할 수 없습니다 "
                           "(git pull 을 사용하세요)")
    with tempfile.TemporaryDirectory(prefix=f"{APP_NAME}-update-") as tmp:
        tmp_path = Path(tmp)
        log(f"{release['tag']} 다운로드 중...")
        zip_path = _download_zip(release, tmp_path)
        log("압축 해제 중...")
        root = safe_extract(zip_path, tmp_path / "extracted")
        log("파일 적용 중... (config/logs 는 보존)")
        apply_release(root)
        LOGS_DIR.mkdir(exist_ok=True)
        log("패키지 확인 중... (requirements.txt)")
        with open(LOGS_DIR / "update.log", "a", encoding="utf-8") as f:
            f.write(f"\n===== update to {release['tag']} =====\n")
            _pip_install_requirements(HERE, f)
    _save_state({"attempted_tag": release["tag"]})
    log(f"{release['tag']} 적용 완료 — 서버를 재시작합니다.")
    restart_app()


# ---------- 시작 시 자동 업데이트 ----------
def _load_state() -> dict:
    try:
        with open(UPDATE_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        CONFIG_DIR.mkdir(exist_ok=True)
        with open(UPDATE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def auto_update_on_start(log=print) -> None:
    """서버 시작 시 1회 호출 — 새 릴리스가 있으면 자동 업데이트.

    같은 버전으로의 업데이트를 이미 시도했는데 여전히 구버전이면
    (재시작 루프 방지) 자동 적용은 건너뛰고 안내만 남긴다.
    """
    if (HERE / ".git").exists():
        return  # 개발 폴더에서는 자동 업데이트 안 함
    info = check_update()
    if info.get("error"):
        print(f"업데이트 확인 실패: {info['error']}", file=sys.stderr)
        return

    state = _load_state()
    attempted = state.get("attempted_tag", "")
    if not info["available"]:
        if attempted:  # 업데이트가 완료됐거나 더 이상 필요 없음 — 마커 정리
            _save_state({})
        return

    if attempted == info["latest"]:
        log(f"새 버전 {info['latest']} 자동 업데이트가 이전에 실패했습니다 — "
            f"설정에서 수동 업데이트를 실행해 주세요.")
        return

    log(f"새 버전 {info['latest']} 발견 (현재 v{__version__}) — 자동 업데이트를 시작합니다.")
    download_and_apply(info["_release"], log=log)


if __name__ == "__main__":
    info = check_update()
    print(json.dumps({k: v for k, v in info.items() if k != "_release"},
                     ensure_ascii=False, indent=2))
    if info["available"] and "--apply" in sys.argv:
        download_and_apply(info["_release"])
