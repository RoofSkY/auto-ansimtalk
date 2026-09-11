"""예약 접수는 묶어서 기록하고, 외부 작업 시작 여부는 작은 저널에 남긴다."""

import copy
import json
import os
import threading

import jsonstore


class ScheduleQueue:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.jobs = {}
        self.error = None
        try:
            if path.exists():
                with path.open(encoding="utf-8") as stream:
                    for line in stream:
                        self._apply(json.loads(line))
        except (OSError, ValueError, KeyError, TypeError):
            # 손상된 시작 기록을 건너뛰면 외부 작업을 재전송할 수 있어 예약만 중단한다.
            self.error = "예약 실행 기록을 읽지 못했습니다. schedule_queue.jsonl을 확인해 주세요."
        self.interrupted = {key for key, job in self.jobs.items() if job["status"] == "running"}

    def _apply(self, record):
        if "jobs" in record:
            for job in record["jobs"]:
                if (not all(key in job for key in ("id", "date", "schedule", "action", "status"))
                        or job["action"] not in ("attendance", "vehicle")
                        or job["status"] != "pending" or job["id"] in self.jobs):
                    raise ValueError("Invalid schedule job")
                self.jobs[job["id"]] = job
        else:
            if record["status"] not in ("running", "done", "interrupted", "expired", "skipped"):
                raise ValueError("Invalid schedule state")
            self.jobs[record["id"]]["status"] = record["status"]

    def _append(self, record):
        if self.error:
            raise RuntimeError(self.error)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        if self.path.stat().st_size == 0:
            jsonstore.restrict_permissions(self.path)
        # 쓰기 실패 시 같은 프로세스에서 뒤에 덧붙여 손상시키지 않는다.
        size = self.path.stat().st_size
        try:
            with self.path.open("ab") as stream:
                stream.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            try:
                with self.path.open("r+b") as stream:
                    stream.truncate(size)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError:
                self.error = "예약 실행 기록 저장에 실패했습니다. 파일을 확인한 후 앱을 다시 시작해 주세요."
            raise
        self._apply(copy.deepcopy(record))

    def enqueue(self, schedules, today):
        with self.lock:
            if self.error:
                raise RuntimeError(self.error)
            jobs = []
            for schedule in schedules:
                for action in ("attendance", "vehicle"):
                    if action == "attendance" and not schedule.get("code"):
                        continue
                    if action == "vehicle" and not (schedule.get("car_no4") and schedule.get("tickets")):
                        continue
                    key = f"{today}:{schedule['id']}:{action}"
                    if key not in self.jobs:
                        jobs.append({"id": key, "date": today, "schedule": copy.deepcopy(schedule),
                                     "action": action, "status": "pending"})
            if jobs:
                self._append({"jobs": jobs})

    def change(self, key, status):
        with self.lock:
            self._append({"id": key, "status": status})

    def snapshot(self, today=None):
        with self.lock:
            if today:
                self.jobs = {key: job for key, job in self.jobs.items()
                             if job["date"] >= today or job["status"] in ("pending", "running")}
            return copy.deepcopy(list(self.jobs.values()))

    def has_pending(self):
        with self.lock:
            return bool(self.error) or any(job["status"] == "pending" for job in self.jobs.values())
