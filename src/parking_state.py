"""주차권 서버 조회 결과와 등록 중인 차량의 세대를 관리한다."""

import threading


class TicketCounts:
    def __init__(self):
        self.lock = threading.RLock()
        self.rows = {}
        self.versions = {}
        self.active = {}
        self.generation = 0

    def snapshot(self):
        with self.lock:
            return {entry: dict(info) for entry, info in self.rows.items()}

    def token(self):
        with self.lock:
            return self.generation, dict(self.versions)

    def begin(self, suffixes):
        with self.lock:
            for suffix in suffixes:
                self.versions[suffix] = self.versions.get(suffix, 0) + 1
                self.active[suffix] = self.active.get(suffix, 0) + 1
            for entry, info in self.rows.items():
                if entry.strip()[-4:] in suffixes:
                    info["count"] = None

    def finish(self, suffixes):
        with self.lock:
            for suffix in suffixes:
                self.versions[suffix] = self.versions.get(suffix, 0) + 1
                self.active[suffix] = max(0, self.active.get(suffix, 0) - 1)

    def update(self, rows, token):
        with self.lock:
            generation, versions = token
            if generation != self.generation:
                return False
            updated = {}
            for entry, info in rows.items():
                suffix = entry.strip()[-4:]
                if self.active.get(suffix) or versions.get(suffix, 0) != self.versions.get(suffix, 0):
                    # 등록 전이나 도중에 시작한 조회를 등록 후 확정 수량으로 사용하지 않는다.
                    updated[entry] = dict(self.rows.get(entry, {"history_id": None, "count": None}))
                else:
                    updated[entry] = dict(info)
            self.rows = updated
            return True

    def clear(self):
        with self.lock:
            self.generation += 1
            self.rows = {}
            self.versions = {}
