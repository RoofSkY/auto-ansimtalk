"""당일 로그의 시간순 캐시와 이전 기록 조회."""

import bisect
import json
import threading
import uuid
from datetime import date


def log_key(entry):
    return entry.get("time", ""), entry["id"]


def matches(entry, kind):
    if kind == "failed":
        return not entry.get("ok")
    if kind == "ansim":
        return entry.get("type") in ("안심톡", "안심톡(예약)", "등하원", "등하원(예약)")
    if kind == "park":
        return entry.get("type") in ("차량등록", "차량등록(예약)", "입차", "출차")
    return True


class LogStore:
    def __init__(self, directory):
        self.directory = directory
        self.lock = threading.RLock()
        self.day = None
        self.signature = None
        self.entries = []

    def _load(self):
        day = date.today().isoformat()
        path = self.directory / f"{day}.jsonl"
        stat = path.stat() if path.exists() else None
        signature = (stat.st_size, stat.st_mtime_ns) if stat else None
        if self.day == day and self.signature == signature:
            return path
        entries = []
        if stat:
            with path.open(encoding="utf-8") as stream:
                for i, line in enumerate(stream):
                    try:
                        entry = json.loads(line)
                        if not isinstance(entry, dict):
                            continue
                        entry.setdefault("id", f"{day}:{i}")
                        entry["date"] = day
                        entries.append(entry)
                    except (ValueError, TypeError):
                        continue
        self.day, self.signature = day, signature
        self.entries = sorted(entries, key=log_key)
        return path

    def append(self, entry):
        with self.lock:
            path = self._load()
            entry.setdefault("id", uuid.uuid4().hex)
            entry["date"] = self.day
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
            bisect.insort_right(self.entries, dict(entry), key=log_key)
            stat = path.stat()
            self.signature = (stat.st_size, stat.st_mtime_ns)

    def all(self):
        with self.lock:
            self._load()
            return list(self.entries)

    def page(self, kind="all", before=None, limit=500):
        with self.lock:
            self._load()
            end = bisect.bisect_left(self.entries, tuple(before), key=log_key) if before else len(self.entries)
            selected = []
            for i in range(end - 1, -1, -1):
                if matches(self.entries[i], kind):
                    selected.append(self.entries[i])
                    if len(selected) > limit:
                        break
            return {"logs": list(reversed(selected[:limit])), "has_more": len(selected) > limit,
                    "date": self.day}
