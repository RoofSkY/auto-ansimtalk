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


def _warn(msg: str) -> None:
    """저장 계층의 경고 — 서버 콘솔(stderr)에 남긴다.

    이 모듈의 실패 대부분은 무시해도 앱은 돌지만, 무엇이 실패했는지조차
    안 남기면 배포 PC 에서 원인 추적이 불가능하다(실제로 겪었다).
    pythonw 처럼 stderr 가 없는 환경에서는 print 가 조용히 무시된다.
    """
    print(f"[jsonstore] {msg}", file=sys.stderr, flush=True)


def _replace(tmp: Path, path: Path) -> None:
    """tmp → path 원자적 교체.

    읽기 전용 속성, ACL 의 DELETE 권한 부재, 대상 파일이 다른 프로세스에
    열려 있는 경우가 전부 PermissionError(WinError 5)로 올라온다 —
    MoveFileEx 는 대상이 열려 있으면 공유 모드와 무관하게 5 를 반환한다
    (공유 위반 32 는 원본 쪽 충돌에서만 난다). 속성을 풀고 잠깐 기다렸다
    다시 시도하고, 그래도 안 되면 호출부(save)가 폴백을 탄다.
    """
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            _clear_readonly(path)
            time.sleep(0.2 * (attempt + 1))


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


def _write_tmp(tmp: Path, data: dict | list, *, private: bool) -> None:
    """임시파일 생성·기록. private=True 면 평문을 쓰기 전에 권한부터 좁힌다."""
    # 남이 미리 만들어 둔(권한이 넓은) 파일을 물려받지 않도록 지우고 배타 생성.
    # 이전 실행이 비정상 종료해 남긴 잔재도 여기서 정리된다.
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        if private:
            restrict_permissions(tmp)
        f = os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise
    with f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())


def _write_in_place(path: Path, data: dict | list) -> None:
    """최후 폴백 — 대상에 직접 덮어쓰기(없으면 생성).

    os.replace 는 임시파일 쪽 DELETE 권한과 '대상이 닫혀 있음'을 요구하지만
    직접 쓰기는 둘 다 필요 없다. 원자성은 잃지만 경로별 락 안이고, 교체가
    불가능한 환경(ACL 손상·계정명 불일치·백신의 핸들 점유)에서 저장이
    영영 실패하는 것보다 낫다. 손상 시에는 update() 의 .corrupt 백업이 받친다.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())


def save(path: Path, data: dict | list, *, private: bool = False) -> None:
    """원자적 쓰기 (임시파일 → os.replace). private=True 면 ACL 도 좁힌다."""
    with lock_for(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        retry = path.with_name(path.name + ".tmp2")
        try:
            _write_tmp(tmp, data, private=private)
            try:
                _replace(tmp, path)
            except PermissionError as first:
                # rename 은 임시파일 쪽 DELETE 권한도 요구한다. 사전 ACL 축소가
                # 실행 계정을 잠근 환경이면(계정명 불일치 등) 여기서 막히므로,
                # ACL 을 건드리지 않은 새 임시파일로 원자적 교체를 한 번 더
                # 시도하고, 그래도 안 되면 직접 덮어쓰기로 강등한다.
                _warn(f"{path.name}: 원자적 교체 실패({first}) — 폴백 저장 시도")
                try:
                    _write_tmp(retry, data, private=False)
                    _replace(retry, path)
                except OSError:
                    _write_in_place(path, data)
        finally:
            # 실패했다면 평문이 담긴 임시파일을 남기지 않는다
            for t in (tmp, retry):
                try:
                    t.unlink(missing_ok=True)
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
    영향이 없으므로 진행은 계속하되, stderr 에는 남긴다 — 무음 실패가
    배포 PC 의 저장 불능(WinError 5)을 오래 은폐한 전력이 있다.
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
        _warn(f"{path.name}: USERNAME 이 비어 있어 ACL 축소 생략")
        return
    # bare "icacls" 는 PATH 가 벗겨진 기동 환경(서비스·스케줄러)에서 못 찾는다
    icacls = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "icacls.exe")
    try:
        r = subprocess.run(
            # (M) — (R,W) 는 삭제 권한이 없어 이후 os.replace 가 거부된다
            [icacls, str(path), "/inheritance:r",
             "/grant:r", f"{user}:(M)",
             "/grant:r", "SYSTEM:(F)",
             "/grant:r", "Administrators:(F)"],
            capture_output=True, text=True, errors="replace", timeout=10,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception as e:
        _warn(f"{path.name}: icacls 실행 실패 — {e}")
        return
    if r.returncode != 0:
        _warn(f"{path.name}: icacls 오류(rc={r.returncode}) — "
              f"{(r.stderr or r.stdout).strip()}")
        return
    # %USERNAME% 이 실행 계정과 다른 주체로 해석되는 환경(스케줄러가 물려준
    # 환경변수 등)에서는 방금 좁힌 ACL 이 우리 자신을 잠근다 — 곧바로 접근을
    # 확인하고, 잠겼으면 상속 복원으로 되돌린다. 안 되돌리면 이후 os.replace
    # 가 임시파일 DELETE 권한 부재로 영구히 WinError 5 를 낸다.
    try:
        with open(path, "r+", encoding="utf-8"):
            pass
    except PermissionError:
        try:
            subprocess.run([icacls, str(path), "/reset"],
                           capture_output=True, timeout=10,
                           creationflags=0x08000000)
        except Exception:
            pass
        _warn(f"{path.name}: ACL 축소가 실행 계정을 잠가 상속 복원으로 되돌림 "
              f"(USERNAME={user!r} 이 실행 계정과 다른지 확인 필요)")
    except OSError:
        pass
