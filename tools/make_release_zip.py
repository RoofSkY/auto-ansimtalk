"""릴리스 zip 생성 — dist/auto-ansimtalk-v{버전}.zip

런타임에 필요한 파일만 담는다 (config/, logs/, _dev/ 등 제외).
GitHub Release 에 이 zip 을 첨부하면 설치 프로그램/자동 업데이트가 사용한다.
"""

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from version import __version__, APP_NAME  # noqa: E402

FILES = [
    "server.py",
    "ansim.py",
    "ansim_web.py",
    "npdc.py",
    "auth.py",
    "updater.py",
    "autostart.py",
    "version.py",
    "requirements.txt",
    "start.bat",
    "README.md",
]
GLOBS = [
    "templates/*.html",
    "static/tailwind.css",
    "static/alpine.min.js",
    "static/app.ico",
    "sound/*",
]


def main() -> Path:
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    out = dist / f"{APP_NAME}-v{__version__}.zip"

    paths: list[Path] = []
    missing: list[str] = []
    for f in FILES:
        p = ROOT / f
        (paths if p.exists() else missing).append(p if p.exists() else f)
    for g in GLOBS:
        found = sorted(ROOT.glob(g))
        if not found:
            missing.append(g)
        paths.extend(p for p in found if p.is_file())

    if missing:
        print(f"[오류] 릴리스에 필요한 파일 없음: {missing}", file=sys.stderr)
        sys.exit(1)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            z.write(p, p.relative_to(ROOT).as_posix())

    print(f"생성 완료: {out} ({out.stat().st_size / 1024:.0f} KB, 파일 {len(paths)}개)")
    return out


if __name__ == "__main__":
    main()
