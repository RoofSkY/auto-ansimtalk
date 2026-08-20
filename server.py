"""앱 진입점 — 실제 구현은 src/app.py 에 있다.

이 런처를 앱 루트에 남겨두는 이유:
  - 기존 설치본의 바로가기·자동 실행 등록이 `server.py` 를 직접 실행한다
  - updater 가 재시작할 때, installer 가 zip 의 앱 루트를 판정할 때도 이 파일을 찾는다
따라서 파일명/위치를 바꾸면 이미 배포된 PC 들이 실행되지 않는다.

pythonw.exe 는 콘솔이 없어 임포트 단계에서 예외가 나면 창도 로그도 없이 사라진다.
그래서 stdio 우회를 임포트보다 먼저 하고, 실패하면 로그 + 대화상자로 알린다.
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def _is_headless() -> bool:
    """콘솔 없이 실행됐는지 (pythonw.exe).

    설치 프로그램은 stdout 을 DEVNULL 로 넘겨 앱을 띄우므로, 그때는 None 이 아니라
    NUL 이 들어 있다 — 인터프리터 이름까지 봐야 그 경로도 잡힌다.
    """
    if sys.stdout is None or sys.stderr is None:
        return True
    return Path(sys.executable).name.lower().startswith("pythonw")


_HEADLESS = _is_headless()


def _log_path() -> Path:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    return logs / "server.log"


def _redirect_stdio() -> None:
    """콘솔이 없으면 stdout/stderr 을 로그 파일로 — 임포트 단계 예외까지 남긴다."""
    if not _HEADLESS:
        return
    try:
        f = open(_log_path(), "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stdout = f
    sys.stderr = f


def _fatal(exc: BaseException) -> None:
    """기동 실패를 남기고 알린다 — 콘솔이 없으면 대화상자로."""
    try:
        print(f"\n=== 기동 실패 {datetime.now():%Y-%m-%d %H:%M:%S} ===", file=sys.stderr)
        traceback.print_exception(exc, file=sys.stderr)
    except Exception:
        pass
    if not _HEADLESS:
        return
    tail = "".join(traceback.format_exception(exc))[-700:]
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            "프로그램을 시작하지 못했습니다.\n\n"
            f"{tail}\n\n자세한 내용: {_log_path()}",
            "등하원차량등록 — 실행 실패",
            0x10,  # MB_ICONERROR
        )
    except Exception:
        pass


_redirect_stdio()

try:
    from app import main  # noqa: E402
except BaseException as e:
    _fatal(e)
    raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as e:
        _fatal(e)
        raise SystemExit(1)
