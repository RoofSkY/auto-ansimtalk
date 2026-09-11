"""실행 중인 작업의 자원 잠금과 화면 동기화."""

import threading
import uuid
from collections.abc import Callable


def resource_keys(student: dict, action: str) -> set[str]:
    keys = {f"{action}:student:{student['id']}"} if student.get("id") else set()
    if action == "attendance":
        code = (student.get("code") or "").strip()
        if code:
            keys.add(f"attendance:code:{code}")
    else:
        # 끝 네 자리와 전체 번호로 등록한 동일 차량도 중복 요청을 차단한다.
        for car in student.get("car_no4s") or []:
            last4 = car.strip()[-4:]
            if len(last4) == 4 and last4.isdigit():
                keys.add(f"vehicle:car:{last4}")
    return keys


class ActionRunner:
    def __init__(self, publish: Callable, report_error: Callable):
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._revision = 0
        self._instance = uuid.uuid4().hex
        self._publish = publish
        self._report_error = report_error

    def _snapshot(self) -> dict:
        return {"instance": self._instance, "revision": self._revision,
                "resources": sorted(self._active)}

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot()

    def _changed(self) -> None:
        self._revision += 1
        # 알림 실패가 잠금 해제나 작업 실행을 막지 않도록 한다.
        try:
            self._publish(self._snapshot())
        except Exception:
            pass

    def start(self, keys: set[str], work: Callable) -> bool:
        with self._lock:
            if self._active & keys:
                return False
            self._active.update(keys)
            self._changed()

        def run():
            try:
                work()
            except Exception as exc:
                self._report_error(exc)
            finally:
                self._release(keys)

        try:
            threading.Thread(target=run, daemon=True).start()
        except Exception:
            self._release(keys)
            raise
        return True

    def _release(self, keys: set[str]) -> None:
        with self._lock:
            self._active.difference_update(keys)
            self._changed()
