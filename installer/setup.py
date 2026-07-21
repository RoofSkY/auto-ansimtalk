"""등하원차량등록(auto-ansimtalk) 설치 프로그램.

PyInstaller 로 단독 exe(AnsimTalk-Setup.exe)로 빌드해 배포한다 —
파이썬이 없는 PC에서도 이 exe 하나로 설치가 끝나도록:

  1. 파이썬(3.10+) 확인 — 없으면 python.org 에서 3.12 받아 자동 설치
  2. GitHub Release 최신 버전 zip 다운로드 → 설치 폴더에 압축 해제
     (재설치 시 config/, logs/ 는 보존)
  3. pip 로 requirements.txt 설치
  4. 시작 메뉴/바탕화면 바로가기, (선택) Windows 시작 시 자동 실행 등록

표준 라이브러리만 사용 (requests 등 외부 패키지 금지 — exe 크기/의존성 최소화).

테스트/무인 설치용 CLI:
  setup.py --silent [--dir 경로] [--zip 로컬zip] [--no-desktop] [--autostart]
           [--skip-pip] [--no-launch]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

APP_NAME = "auto-ansimtalk"
APP_TITLE = "등하원차량등록"
GITHUB_REPO = "RoofSkY/auto-ansimtalk"

PY_VERSION = "3.12.10"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-amd64.exe"
MIN_PY = (3, 10)

def _documents_dir() -> Path:
    """실제 문서 폴더 경로 (OneDrive 등으로 이동된 경우 포함)."""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                val, _ = winreg.QueryValueEx(k, "Personal")
            p = Path(os.path.expandvars(val))
            if p.is_dir():
                return p
        except OSError:
            pass
    return Path.home() / "Documents"


DEFAULT_DIR = _documents_dir() / APP_NAME
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
PRESERVE_DIRS = {"config", "logs"}


# ---------- 유틸 ----------
def _run_hidden(cmd, **kw):
    kw.setdefault("creationflags", CREATE_NO_WINDOW)
    kw.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.run(cmd, **kw)


def _github_request(url: str, token: str, accept: str) -> urllib.request.Request:
    req = urllib.request.Request(url, headers={"User-Agent": APP_NAME, "Accept": accept})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def _download(url: str, dest: Path, token: str = "", accept: str = "application/octet-stream",
              progress=None) -> None:
    with urllib.request.urlopen(_github_request(url, token, accept), timeout=30) as r, \
            open(dest, "wb") as f:
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


# ---------- 1. 파이썬 확인 / 설치 ----------
_PROBE = "import sys;print(sys.executable);print('%d.%d' % sys.version_info[:2])"


def _probe_python(cmd: list[str]) -> tuple[str, tuple[int, int]] | None:
    try:
        r = _run_hidden(cmd + ["-c", _PROBE], capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    exe = lines[0]
    try:
        major, minor = (int(x) for x in lines[1].split("."))
    except ValueError:
        return None
    return exe, (major, minor)


def find_python() -> tuple[str, tuple[int, int]] | None:
    """설치된 파이썬(3.10+) 탐색 — py 런처, PATH, 표준 설치 경로 순."""
    candidates: list[list[str]] = [["py", "-3"], ["python"]]
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
    ]
    for root in roots:
        if root.is_dir():
            for d in sorted(root.glob("Python3*"), reverse=True):
                exe = d / "python.exe"
                if exe.exists():
                    candidates.append([str(exe)])
    for cmd in candidates:
        found = _probe_python(cmd)
        if found and found[1] >= MIN_PY:
            return found
    return None


def install_python(log, progress=None) -> tuple[str, tuple[int, int]]:
    log(f"파이썬이 없어 Python {PY_VERSION} 을 설치합니다... (수 분 소요)")
    with tempfile.TemporaryDirectory(prefix="pysetup-") as tmp:
        installer = Path(tmp) / f"python-{PY_VERSION}-amd64.exe"
        log(f"  다운로드: {PY_URL}")
        _download(PY_URL, installer, progress=progress)
        log("  설치 중... (조용히 설치, 창이 뜨지 않습니다)")
        r = _run_hidden(
            [str(installer), "/quiet", "InstallAllUsers=0", "PrependPath=1",
             "Include_launcher=1", "Include_test=0"],
            timeout=1800,
        )
        if r.returncode != 0:
            raise RuntimeError(f"파이썬 설치 실패 (코드 {r.returncode})")
    found = find_python()
    if not found:
        raise RuntimeError("파이썬 설치 후에도 실행 파일을 찾지 못했습니다")
    log(f"  파이썬 설치 완료: {found[0]}")
    return found


# ---------- 2. 릴리스 다운로드 ----------
def get_latest_release(token: str) -> dict:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    try:
        with urllib.request.urlopen(
                _github_request(url, token, "application/vnd.github+json"), timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError(
                "GitHub 릴리스를 찾을 수 없습니다.\n"
                "- 아직 릴리스가 없다면 먼저 릴리스를 만들어야 합니다 (RELEASE.md 참고).\n"
                "- 저장소가 private 이면 GITHUB_TOKEN 환경변수 또는 --token 이 필요합니다."
            ) from e
        raise
    tag = data.get("tag_name") or ""
    asset = None
    for a in data.get("assets") or []:
        name = (a.get("name") or "").lower()
        if name.endswith(".zip") and APP_NAME in name:
            asset = a
            break
    if asset is None:
        zips = [a for a in data.get("assets") or []
                if (a.get("name") or "").lower().endswith(".zip")]
        if len(zips) == 1:
            asset = zips[0]
    return {
        "tag": tag,
        "url": (asset or {}).get("url" if token else "browser_download_url")
               or data.get("zipball_url") or "",
        "name": (asset or {}).get("name") or f"{APP_NAME}-{tag}.zip",
    }


def safe_extract(zip_path: Path, dest: Path) -> Path:
    """zip 을 풀고 실제 앱 루트(server.py 가 있는 폴더)를 반환. 경로 탈출 차단."""
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


def copy_app(root: Path, install_dir: Path) -> None:
    """앱 파일을 설치 폴더로 복사 — 기존 config/, logs/ 는 보존."""
    install_dir.mkdir(parents=True, exist_ok=True)
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


# ---------- 3. 패키지 설치 ----------
def pip_install(python_exe: str, install_dir: Path, log) -> None:
    req = install_dir / "requirements.txt"
    if not req.exists():
        log("requirements.txt 없음 — 패키지 설치 건너뜀")
        return
    log("필요 패키지 설치 중... (pip)")
    proc = subprocess.Popen(
        [python_exe, "-m", "pip", "install", "-r", str(req)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            log("  " + line)
    if proc.wait() != 0:
        raise RuntimeError("pip 패키지 설치 실패 — 로그를 확인하세요")


# ---------- 4. 바로가기 / 자동 실행 ----------
def _pythonw_of(python_exe: str) -> str:
    pw = Path(python_exe).with_name("pythonw.exe")
    return str(pw) if pw.exists() else python_exe


def create_shortcuts(python_exe: str, install_dir: Path, desktop: bool, log) -> None:
    pythonw = _pythonw_of(python_exe)
    server = install_dir / "server.py"
    icon = install_dir / "static" / "app.ico"
    targets = ["[Environment]::GetFolderPath('Programs')"]
    if desktop:
        targets.append("[Environment]::GetFolderPath('Desktop')")
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        + "; ".join(
            f"$d = {t}; $s = $ws.CreateShortcut((Join-Path $d '{APP_TITLE}.lnk')); "
            f"$s.TargetPath = '{pythonw}'; "
            f"$s.Arguments = '\"{server}\"'; "
            f"$s.WorkingDirectory = '{install_dir}'; "
            f"$s.IconLocation = '{icon}'; "
            f"$s.Save()"
            for t in targets
        )
    )
    r = _run_hidden(["powershell", "-NoProfile", "-Command", ps],
                    capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"바로가기 생성 실패: {r.stderr or r.stdout}")
    log("바로가기 생성 완료 (시작 메뉴" + (" + 바탕화면" if desktop else "") + ")")


def set_autostart(python_exe: str, install_dir: Path, on: bool, log) -> None:
    if sys.platform != "win32":
        return
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                        winreg.KEY_SET_VALUE) as key:
        if on:
            cmd = f'"{_pythonw_of(python_exe)}" "{install_dir / "server.py"}" --no-browser'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            log("Windows 시작 시 자동 실행 등록 완료")
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except OSError:
                pass


# ---------- 설치 실행 ----------
class Options:
    def __init__(self):
        self.install_dir = DEFAULT_DIR
        self.desktop = True
        self.shortcuts = True
        self.autostart = False
        self.local_zip: Path | None = None  # 테스트용 — 릴리스 대신 로컬 zip 사용
        self.token = os.environ.get("GITHUB_TOKEN", "").strip()
        self.skip_pip = False
        self.launch = True


def run_install(opts: Options, log, progress=None) -> tuple[str, Path]:
    """설치 본체. (python_exe, install_dir) 반환. 실패 시 예외."""
    def phase(pct):
        if progress:
            progress(pct)

    log(f"설치 폴더: {opts.install_dir}")
    phase(5)

    found = find_python()
    if found:
        log(f"파이썬 확인: {found[0]} (v{found[1][0]}.{found[1][1]})")
    else:
        found = install_python(
            log, progress=lambda d, t: phase(10 + int(25 * d / t)))
    python_exe = found[0]
    phase(35)

    with tempfile.TemporaryDirectory(prefix=f"{APP_NAME}-setup-") as tmp:
        tmp_path = Path(tmp)
        if opts.local_zip:
            log(f"로컬 zip 사용: {opts.local_zip}")
            zip_path = Path(opts.local_zip)
        else:
            log("GitHub 에서 최신 버전 확인 중...")
            rel = get_latest_release(opts.token)
            if not rel["url"]:
                raise RuntimeError("릴리스에 다운로드 가능한 zip 이 없습니다")
            log(f"최신 버전 {rel['tag']} 다운로드 중...")
            zip_path = tmp_path / rel["name"]
            _download(rel["url"], zip_path, token=opts.token,
                      progress=lambda d, t: phase(40 + int(20 * d / t)))
        phase(60)

        log("압축 해제 및 파일 복사 중...")
        root = safe_extract(zip_path, tmp_path / "extracted")
        copy_app(root, opts.install_dir)
        phase(68)

    if opts.skip_pip:
        log("(--skip-pip) 패키지 설치 건너뜀")
    else:
        pip_install(python_exe, opts.install_dir, log)
    phase(90)

    if opts.shortcuts:
        create_shortcuts(python_exe, opts.install_dir, opts.desktop, log)
    set_autostart(python_exe, opts.install_dir, opts.autostart, log)
    phase(100)
    log("설치가 완료되었습니다!")
    return python_exe, opts.install_dir


def launch_app(python_exe: str, install_dir: Path) -> None:
    subprocess.Popen(
        [_pythonw_of(python_exe), str(install_dir / "server.py")],
        cwd=str(install_dir), creationflags=CREATE_NO_WINDOW,
        close_fds=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


# ---------- GUI ----------
def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    root = tk.Tk()
    root.title(f"{APP_TITLE} 설치")
    root.geometry("600x520")
    root.resizable(False, False)

    tk.Label(root, text=f"{APP_TITLE} 설치", font=("Malgun Gothic", 15, "bold")
             ).pack(pady=(16, 2))
    tk.Label(root, text="파이썬과 필요한 패키지를 자동으로 설치합니다.",
             font=("Malgun Gothic", 9), fg="#666").pack()

    frm = tk.Frame(root)
    frm.pack(fill="x", padx=20, pady=(14, 4))
    tk.Label(frm, text="설치 폴더", font=("Malgun Gothic", 9)).pack(anchor="w")
    row = tk.Frame(frm)
    row.pack(fill="x")
    dir_var = tk.StringVar(value=str(DEFAULT_DIR))
    tk.Entry(row, textvariable=dir_var, font=("Malgun Gothic", 9)
             ).pack(side="left", fill="x", expand=True, ipady=3)

    def browse():
        d = filedialog.askdirectory(initialdir=dir_var.get() or str(DEFAULT_DIR))
        if d:
            dir_var.set(str(Path(d)))

    tk.Button(row, text="찾아보기", command=browse, font=("Malgun Gothic", 9)
              ).pack(side="left", padx=(6, 0))

    desktop_var = tk.BooleanVar(value=True)
    autostart_var = tk.BooleanVar(value=False)
    opt = tk.Frame(root)
    opt.pack(fill="x", padx=20, pady=(6, 0))
    tk.Checkbutton(opt, text="바탕화면 바로가기 만들기", variable=desktop_var,
                   font=("Malgun Gothic", 9)).pack(anchor="w")
    tk.Checkbutton(opt, text="Windows 시작 시 자동 실행 (설치 후 설정에서 변경 가능)",
                   variable=autostart_var, font=("Malgun Gothic", 9)).pack(anchor="w")

    bar = ttk.Progressbar(root, maximum=100)
    bar.pack(fill="x", padx=20, pady=(12, 4))

    log_box = scrolledtext.ScrolledText(root, height=12, font=("Consolas", 9),
                                        state="disabled")
    log_box.pack(fill="both", expand=True, padx=20, pady=(4, 8))

    btn = tk.Button(root, text="설치 시작", font=("Malgun Gothic", 11, "bold"),
                    bg="#3b82f6", fg="white", height=1)
    btn.pack(fill="x", padx=20, pady=(0, 16), ipady=4)

    result: dict = {}

    def log(msg):
        def _append():
            log_box.configure(state="normal")
            log_box.insert("end", msg + "\n")
            log_box.see("end")
            log_box.configure(state="disabled")
        root.after(0, _append)

    def progress(pct):
        root.after(0, lambda: bar.configure(value=pct))

    def worker(opts: Options):
        try:
            py, d = run_install(opts, log, progress)
            result["python"] = py
            result["dir"] = d
            root.after(0, on_done)
        except Exception as e:
            log("\n[오류] " + str(e))
            log(traceback.format_exc(limit=3))
            root.after(0, lambda: (
                btn.configure(state="normal", text="다시 시도"),
                messagebox.showerror(f"{APP_TITLE} 설치", f"설치 중 오류가 발생했습니다:\n{e}"),
            ))

    def on_done():
        btn.configure(state="normal", text="지금 실행", command=do_launch)
        messagebox.showinfo(f"{APP_TITLE} 설치", "설치가 완료되었습니다!")

    def do_launch():
        try:
            launch_app(result["python"], result["dir"])
        finally:
            root.destroy()

    def do_install():
        btn.configure(state="disabled", text="설치 중...")
        opts = Options()
        opts.install_dir = Path(dir_var.get().strip() or str(DEFAULT_DIR))
        opts.desktop = desktop_var.get()
        opts.autostart = autostart_var.get()
        threading.Thread(target=worker, args=(opts,), daemon=True).start()

    btn.configure(command=do_install)
    root.mainloop()


# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description=f"{APP_TITLE} 설치 프로그램")
    ap.add_argument("--silent", action="store_true", help="GUI 없이 설치")
    ap.add_argument("--dir", type=Path, default=None, help="설치 폴더")
    ap.add_argument("--zip", type=Path, default=None, help="릴리스 대신 로컬 zip 사용(테스트)")
    ap.add_argument("--no-desktop", action="store_true", help="바탕화면 바로가기 생략")
    ap.add_argument("--no-shortcut", action="store_true", help="바로가기 전부 생략(테스트)")
    ap.add_argument("--autostart", action="store_true", help="시작 시 자동 실행 등록")
    ap.add_argument("--skip-pip", action="store_true", help="pip 설치 생략(테스트)")
    ap.add_argument("--no-launch", action="store_true", help="설치 후 실행 안 함")
    ap.add_argument("--token", default="", help="GitHub 토큰 (private 저장소용)")
    args = ap.parse_args()

    if not args.silent:
        run_gui()
        return

    opts = Options()
    if args.dir:
        opts.install_dir = args.dir
    opts.local_zip = args.zip
    opts.desktop = not args.no_desktop
    opts.shortcuts = not args.no_shortcut
    opts.autostart = args.autostart
    opts.skip_pip = args.skip_pip
    opts.launch = not args.no_launch
    if args.token:
        opts.token = args.token

    log_path = opts.install_dir / "installer.log"

    def log(msg):
        print(msg, flush=True)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    py, d = run_install(opts, log)
    if opts.launch:
        launch_app(py, d)


if __name__ == "__main__":
    main()
