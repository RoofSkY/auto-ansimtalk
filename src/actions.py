"""실행 중인 작업의 자원 잠금과 화면 동기화."""

import threading
import uuid
from queue import Queue
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
        self._queues = {"attendance": Queue(maxsize=100), "vehicle": Queue(maxsize=100)}
        self._workers_started = False
        self._closed = False

    def _start_workers(self):
        if self._workers_started:
            return
        for kind, count in (("attendance", 1), ("vehicle", 4)):
            for i in range(count):
                threading.Thread(target=self._worker, args=(kind,),
                                 name=f"action-{kind}-{i}", daemon=True).start()
        self._workers_started = True

    def _worker(self, kind):
        queue = self._queues[kind]
        while True:
            item = queue.get()
            if item is None:
                queue.task_done()
                return
            keys, work = item
            try:
                work()
            except Exception as exc:
                try:
                    self._report_error(exc)
                except Exception:
                    pass
            finally:
                self._release(keys)
                queue.task_done()

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

    def start(self, keys: set[str], work: Callable, *, kind: str = "vehicle") -> bool:
        with self._lock:
            if self._closed or self._active & keys:
                return False
            self._start_workers()
            queue = self._queues[kind]
            if queue.full():
                return False
            self._active.update(keys)
            self._changed()
            queue.put_nowait((set(keys), work))
        return True

    def close(self):
        """새 접수를 중단하고 이미 접수한 작업 뒤에서 작업자를 종료한다."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._workers_started:
            for kind, count in (("attendance", 1), ("vehicle", 4)):
                for _ in range(count):
                    self._queues[kind].put(None)

    def _release(self, keys: set[str]) -> None:
        with self._lock:
            self._active.difference_update(keys)
            self._changed()
