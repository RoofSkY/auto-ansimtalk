"""Windows 시작 시 자동 실행 (HKCU Run 레지스트리) 관리.

작업관리자 '시작 앱' 탭은 Run 키 외에 StartupApproved 키(사용/사용 안 함 상태)도
참조하므로, 켜고 끌 때 두 키를 함께 정리한다. 구버전이 쓰던 등록 이름
(AutoAnsimTalk)도 발견 시 제거해 잔재가 남지 않게 한다.
"""

import sys
from pathlib import Path

from version import APP_NAME

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
_LEGACY_NAMES = ("AutoAnsimTalk",)  # 구버전 등록 이름

HERE = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent  # src/ → 앱 루트
)


def _command() -> str:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    # 부팅 시에는 브라우저 자동 열기 없이 조용히 시작
    return f'"{exe}" "{HERE / "server.py"}" --no-browser'


def _delete_value(key_path: str, name: str) -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        pass


def _purge_legacy() -> None:
    for name in _LEGACY_NAMES:
        _delete_value(_RUN_KEY, name)
        _delete_value(_APPROVED_KEY, name)


def is_enabled() -> bool:
    """Run 등록이 있고, 작업관리자에서 '사용 안 함' 처리되지 않았을 때만 True."""
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
    except OSError:
        return False
    # StartupApproved 첫 바이트: 0x02=사용, 0x03=사용 안 함 (값이 없으면 사용)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _APPROVED_KEY) as key:
            data, _ = winreg.QueryValueEx(key, APP_NAME)
        if data and data[0] == 3:
            return False
    except OSError:
        pass
    return True


def enable() -> None:
    if sys.platform != "win32":
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _command())
    # 작업관리자에서 '사용 안 함' 으로 꺼 두었던 상태 초기화
    _delete_value(_APPROVED_KEY, APP_NAME)
    _purge_legacy()


def disable() -> None:
    if sys.platform != "win32":
        return
    _delete_value(_RUN_KEY, APP_NAME)
    _delete_value(_APPROVED_KEY, APP_NAME)
    _purge_legacy()


def set_enabled(on: bool) -> None:
    if on:
        enable()
    else:
        disable()
