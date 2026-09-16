"""서비스별 조회를 직렬화하고 두 서비스의 대기는 분리한다."""

import threading
import time


class RefreshCoordinator:
    def __init__(self, tasks, interval, enabled, publish, report_error):
        self.tasks = tasks
        self.interval = interval
        self.enabled = enabled
        self.publish = publish
        self.report_error = report_error
        self.condition = threading.Condition()
        self.pending = set()
        self.vehicle_suffixes = None
        self.running_vehicle_full = False
        self.running = set()
        self.completed = {}
        self.deadline = 0.0
        self.stopped = False
        self.started = False
        self.threads = []

    def _reset_deadline(self):
        seconds = max(10, int(self.interval()))
        self.deadline = time.monotonic() + seconds
        self.publish(seconds)

    def _queue(self, kind, suffixes=None):
        if kind == "vehicle":
            if kind not in self.pending:
                self.vehicle_suffixes = None if suffixes is None else set(suffixes)
            elif self.vehicle_suffixes is not None:
                if suffixes is None:
                    self.vehicle_suffixes = None
                else:
                    self.vehicle_suffixes.update(suffixes)
        self.pending.add(kind)

    def request(self, tasks=None, *, if_stale=False, vehicle_suffixes=None):
        selected = set(tasks or self.tasks)
        with self.condition:
            if self.stopped:
                return
            for kind in selected & self.tasks.keys():
                if kind == "vehicle" and vehicle_suffixes is not None and not vehicle_suffixes:
                    continue
                if if_stale and (kind in self.running or kind in self.pending
                                 or time.monotonic() - self.completed.get(kind, float('-inf')) < 2):
                    continue
                # 실행 중 강제 요청은 완료 후 한 번 더 조회하여 처리 후 상태를 확인한다.
                self._queue(kind, vehicle_suffixes)
            if selected == set(self.tasks):
                self._reset_deadline()
            self.condition.notify_all()

    def start(self):
        with self.condition:
            if self.started:
                return
            self.started = True
            self.threads = [threading.Thread(target=self._worker, args=(kind,),
                                            name=f"refresh-{kind}", daemon=True)
                            for kind in self.tasks]
            self.threads.append(threading.Thread(target=self._timer, name="refresh-timer", daemon=True))
            for thread in self.threads:
                thread.start()

    def _timer(self):
        with self.condition:
            while not self.stopped:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    for kind in self.tasks:
                        if self.enabled(kind) and (kind not in self.running
                                or (kind == "vehicle" and not self.running_vehicle_full)):
                            self._queue(kind)
                    self._reset_deadline()
                    self.condition.notify_all()
                    remaining = self.deadline - time.monotonic()
                self.condition.wait(remaining)

    def _worker(self, kind):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.stopped or kind in self.pending)
                if self.stopped:
                    return
                self.pending.discard(kind)
                suffixes = self.vehicle_suffixes if kind == "vehicle" else None
                if kind == "vehicle":
                    self.vehicle_suffixes = None
                    self.running_vehicle_full = suffixes is None
                self.running.add(kind)
            succeeded = False
            try:
                result = self.tasks[kind]() if suffixes is None else self.tasks[kind](suffixes=suffixes)
                succeeded = result is not False
            except Exception as exc:
                try:
                    self.report_error(kind, exc)
                except Exception:
                    pass
            finally:
                with self.condition:
                    self.running.discard(kind)
                    if kind == "vehicle":
                        self.running_vehicle_full = False
                    if succeeded and suffixes is None:
                        self.completed[kind] = time.monotonic()
                    self.condition.notify_all()

    def stop(self):
        with self.condition:
            self.stopped = True
            self.condition.notify_all()
        for thread in self.threads:
            thread.join(timeout=1)
