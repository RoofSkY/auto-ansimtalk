"""자격증명·설정 JSON 을 안전하게 읽고 쓰는 공용 유틸.

한 파일에 자격증명과 세션이 함께 들어 있으므로(ansimtalk.json, iparking.json)
다음 두 가지를 보장해야 한다:

  1. 원자적 쓰기 — 임시 파일에 쓴 뒤 os.replace 로 교체.
     쓰기 도중 프로세스가 죽어도 파일이 잘리지 않는다(자격증명 유실 방지).
  2. read-modify-write 직렬화 — 웹 요청 스레드(계정 저장)와 백그라운드
     갱신 스레드(세션 저장)가 같은 파일을 동시에 갱신하면 나중 쓰기가
     앞선 변경을 덮어써 비밀번호가 날아갈 수 있다. 경로별 락으로 막는다.

파일 권한: 자격증명 파일은 현재 사용자만 읽도록 ACL 을 좁힌다(Windows).
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def lock_for(path: Path) -> threading.RLock:
    """경로별 재진입 락 — update() 안에서 다시 load() 를 불러도 안전."""
    key = str(path.resolve()).lower()  # Windows 는 대소문자 구분 없음
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = threading.RLock()
            _locks[key] = lk
        return lk


def _clear_readonly(path: Path) -> None:
    """읽기 전용 속성 해제 — 복사해 온 파일에 붙어 있으면 os.replace 가 거부된다."""
    try:
        if path.exists():
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _replace(tmp: Path, path: Path) -> None:
    """tmp → path 원자적 교체.

    읽기 전용 속성(WinError 5)과 백신·클라우드 동기화의 일시적 잠금(WinError 32)이
    둘 다 PermissionError 로 올라온다 — 속성을 풀고 잠깐 기다렸다 다시 시도한다.
    """
    for attempt in range(4):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 3:
                raise
            _clear_readonly(path)
            time.sleep(0.15 * (attempt + 1))


def load(path: Path) -> dict:
    """JSON 을 읽어 dict 반환. 없거나 깨졌으면 빈 dict."""
    with lock_for(path):
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def save(path: Path, data: dict | list, *, private: bool = False) -> None:
    """원자적 쓰기 (임시파일 → os.replace). private=True 면 ACL 도 좁힌다."""
    with lock_for(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        try:
            # 남이 미리 만들어 둔(권한이 넓은) 파일을 물려받지 않도록 지우고 배타 생성.
            # 이전 실행이 비정상 종료해 남긴 잔재도 여기서 정리된다.
            tmp.unlink(missing_ok=True)
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                if private:
                    restrict_permissions(tmp)  # 평문을 쓰기 전에 권한부터 좁힌다
                f = os.fdopen(fd, "w", encoding="utf-8")
            except BaseException:
                os.close(fd)
                raise
            with f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            _replace(tmp, path)
        finally:
            # 실패했다면 평문이 담긴 임시파일을 남기지 않는다
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        if private:
            restrict_permissions(path)


def _load_strict(path: Path) -> dict:
    """파일이 있는데 읽지 못하면 예외를 올린다.

    관대한 load() 를 갱신 경로에 쓰면 안 된다 — 파일이 잠깐 잠기거나(백신·동기화)
    손상돼 있을 때 빈 dict 로 간주해 저장하면, 그 파일에 함께 들어 있는
    아이디·비밀번호가 통째로 지워진다. 못 읽으면 쓰지 않는 편이 옳다.
    """
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: 최상위가 객체가 아님")
    return data


def update(path: Path, mutate, *, private: bool = False) -> dict:
    """읽기→수정→쓰기를 락 안에서 한 번에 수행.

    mutate(data) 가 dict 를 반환하면 그것을, None 이면 제자리 수정된 data 를 저장.
    기존 내용을 읽지 못하면 덮어쓰지 않고 예외를 올린다(자격증명 보호).
    """
    with lock_for(path):
        try:
            data = _load_strict(path)
        except Exception:
            # 손상본을 남겨 두면 원인 파악과 수동 복구가 가능하다
            try:
                backup = path.with_name(path.name + ".corrupt")
                if path.exists() and not backup.exists():
                    shutil.copy2(path, backup)
            except OSError:
                pass
            raise
        result = mutate(data)
        if result is not None:
            data = result
        save(path, data, private=private)
        return data


def restrict_permissions(path: Path) -> None:
    """자격증명 파일을 현재 사용자 전용으로 (Windows ACL 상속 제거).

    기본 상태에서는 BUILTIN\\Users 에 수정 권한까지 상속돼, PC 를 여러 계정이
    공유하면 다른 사용자가 비밀번호를 읽을 수 있다. 실패해도 앱 동작에는
    영향이 없으므로 조용히 무시한다(권한 부족·비 Windows 환경 등).
    """
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return
    _clear_readonly(path)
    user = os.environ.get("USERNAME") or ""
    if not user:
        return
    try:
        subprocess.run(
            # (M) — (R,W) 는 삭제 권한이 없어 이후 os.replace 가 거부된다
            ["icacls", str(path), "/inheritance:r",
             "/grant:r", f"{user}:(M)",
             "/grant:r", "SYSTEM:(F)",
             "/grant:r", "Administrators:(F)"],
            capture_output=True, timeout=10,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception:
        pass
