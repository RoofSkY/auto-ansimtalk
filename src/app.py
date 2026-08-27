"""안심톡 + 주차할인 자동화 웹 서버.

브라우저로 http://localhost:5000 접속해서 사용.
"""

import asyncio
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
import uuid
import webbrowser
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

import iparking
import ansim
import ansim_web
import autostart
import jsonstore
import updater
from version import __version__

try:
    import winsound
    _SOUND_AVAILABLE = True
except ImportError:
    _SOUND_AVAILABLE = False


# ---------- 경로 ----------
HERE = Path(__file__).resolve().parent.parent  # src/ → 앱 루트
CONFIG_DIR = HERE / "config"
CONFIG_DIR.mkdir(exist_ok=True)
STUDENTS_PATH = CONFIG_DIR / "students.json"
SCHEDULES_PATH = CONFIG_DIR / "schedules.json"
CONFIG_PATH = CONFIG_DIR / "app_config.json"
LOGS_DIR = HERE / "logs"
LOGS_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR = HERE / "templates"
STATIC_DIR = HERE / "static"
STATIC_DIR.mkdir(exist_ok=True)
SOUND_DIR = HERE / "sound"


# ---------- 기본 설정 ----------
DEFAULT_CONFIG = {
    "auto_search": True,
    "vehicle_toast": True,
    "vehicle_toast_duration": 5,
    "att_sync": True,
    "vehicle_ticket_count": 1,  # 차량등록 버튼이 등록할 1시간 무료권 매수
    "refresh_interval": 60,  # 차량 자동검색 + 등하원 상태 동기화 공통 갱신 주기 (초)
}

ATT_STATUSES = ["미등원", "등원", "하원", "결석", "공결", "캠프"]

DAY_LABELS = ["월", "화", "수", "목", "금"]
TICKET_DISPLAY_ORDER = ["free", "paid"]
DEFAULT_VEHICLE_TICKET = "free"  # 차량등록 버튼이 쓰는 권종
PORT = 5000


# ---------- 차량번호 파서 ----------
def _parse_car_entry(s: str) -> tuple[str, str] | None:
    s = (s or "").strip()
    if len(s) < 4:
        return None
    last4 = s[-4:]
    if not last4.isdigit():
        return None
    full = s if len(s) > 4 else ""
    return last4, full


# ---------- 데이터 로드/저장 ----------
def _student_sort_key(s: dict) -> str:
    return (s.get("name") or "").strip()


def _student_key(s: dict) -> str:
    """등하원 상태 저장 키 — 출석번호 우선, 없으면 이름."""
    return (s.get("code") or "").strip() or (s.get("name") or "").strip()


def load_students() -> list[dict]:
    if not STUDENTS_PATH.exists():
        return []
    try:
        with open(STUDENTS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return []
    needs_save = False
    for s in raw:
        if "car_no4s" not in s:
            legacy = s.get("car_no4", "")
            s["car_no4s"] = [legacy] if legacy else []
        s.pop("car_no4", None)
        # 액션 API 가 참조하는 안정 식별자 — 이름순 정렬로 위치가 바뀌어도 불변.
        # 구버전 데이터에는 없으므로 최초 로드 시 부여하고 파일에 반영한다.
        if not s.get("id"):
            s["id"] = uuid.uuid4().hex
            needs_save = True
    if needs_save:
        try:
            return save_students(raw)
        except Exception as e:
            # 여기서 예외가 올라가면 State() 가 임포트 단계에서 터져 앱이 아예 뜨지 않는다.
            # 저장은 실패해도 읽어 둔 데이터로 기동시킨다.
            print(f"students.json 저장 실패 — 메모리 데이터로 계속합니다: {e}",
                  file=sys.stderr)
    return sorted(raw, key=_student_sort_key)


def save_students(students: list[dict]) -> list[dict]:
    """정규화·정렬해 저장하고 그 결과 리스트를 반환.

    호출자는 반환값을 `state.students` 에 재대입할 것 — 제자리 정렬(list.sort)은
    정렬 중 리스트가 일시적으로 비어 보여 폴링 스레드가 원생을 놓칠 수 있다.
    """
    normalized = [
        {
            "id": s.get("id") or uuid.uuid4().hex,
            "name": s.get("name", ""),
            "code": s.get("code", ""),
            "car_no4s": list(s.get("car_no4s", [])),
        }
        for s in sorted(students, key=_student_sort_key)
    ]
    jsonstore.save(STUDENTS_PATH, normalized, private=True)
    return normalized


def load_schedules() -> list[dict]:
    if not SCHEDULES_PATH.exists():
        return []
    try:
        with open(SCHEDULES_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return []
    for s in raw:
        if "tickets" not in s:
            ttype = s.get("ticket_type", "")
            try:
                tcount = int(s.get("ticket_count", 1))
            except Exception:
                tcount = 1
            s["tickets"] = {ttype: tcount} if ttype and tcount > 0 else {}
        s.pop("ticket_type", None)
        s.pop("ticket_count", None)
        if "days" not in s:
            s["days"] = list(range(5))
        else:
            s["days"] = [d for d in s["days"] if isinstance(d, int) and 0 <= d <= 4]
            if not s["days"]:
                s["days"] = list(range(5))
    return raw


def save_schedules(schedules: list[dict]) -> None:
    normalized = [
        {
            "id": s.get("id") or str(uuid.uuid4()),
            "time": s.get("time", ""),
            "days": sorted({int(d) for d in (s.get("days") or []) if 0 <= int(d) <= 4}),
            "code": s.get("code", ""),
            "car_no4": s.get("car_no4", ""),
            "tickets": {k: int(v) for k, v in (s.get("tickets") or {}).items() if int(v) > 0},
            "enabled": bool(s.get("enabled", True)),
            # 마지막 실행 날짜(YYYY-MM-DD) — 메모리에만 두면 같은 분에 서버가 재시작될 때
            # 이미 실행한 예약이 한 번 더 실행된다(등하원 중복 등록·주차권 추가 소모)
            "last_run": s.get("last_run", ""),
        }
        for s in schedules
    ]
    jsonstore.save(SCHEDULES_PATH, normalized, private=True)  # 원자적 쓰기


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return dict(DEFAULT_CONFIG)
    # 구버전 마이그레이션: 나눠져 있던 두 갱신 주기를 하나로 통합
    if "refresh_interval" not in raw:
        legacy = raw.get("att_sync_interval") or raw.get("auto_search_interval")
        if legacy:
            try:
                raw["refresh_interval"] = max(10, int(legacy))
            except Exception:
                pass
    raw.pop("att_sync_interval", None)
    raw.pop("auto_search_interval", None)
    return {**DEFAULT_CONFIG, **raw}


def save_config(cfg: dict) -> None:
    try:
        # github_token 이 들어갈 수 있어 권한도 제한한다
        jsonstore.save(CONFIG_PATH, cfg, private=True)  # 원자적 쓰기 — 중단 시 파일 절단 방지
    except Exception as e:
        print(f"config 저장 실패: {e}", file=sys.stderr)


def append_log(entry: dict) -> None:
    today = date.today().isoformat()
    with open(LOGS_DIR / f"{today}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_today_logs() -> list[dict]:
    path = LOGS_DIR / f"{date.today().isoformat()}.jsonl"
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


# ---------- 전역 상태 ----------
class State:
    def __init__(self):
        self.students = load_students()
        self.schedules = load_schedules()
        self.config = load_config()
        # 등하원 상태는 메모리로만 관리 — 재시작 시 첫 동기화가 다시 채움
        self.att = {"date": date.today().isoformat(), "status": {}, "times": {}}
        self.prev_in_cars: set[str] = set()
        self.last_seen_carNo: dict[str, str] = {}
        # 입출차 판정 기준이 잡힌 차량 — 처음 확인된 차량은 알림 없이 기준만 잡는다
        self.poll_baseline: set[str] = set()
        self._poll_failing = False  # 차량 조회 실패 상태 — 로그를 주기마다 반복하지 않기 위함
        self._att_failing = False   # 등하원 동기화 실패 상태 (동일)
        # 원생별 등원/하원 로그 기록 여부 — None 이면 다음 동기화 때 당일 로그에서 복원
        self.att_logged: dict[str, dict[str, bool]] | None = None

        # 수동/액션 트리거로 갱신 루프를 즉시 깨우는 이벤트.
        # wake_tasks 로 이번 수동 갱신에서 실행할 작업을 지정 ("att" / "vehicle")
        self.refresh_wake = threading.Event()
        self.wake_tasks: set[str] = set()
        self.next_refresh_ts: float = 0.0

        self.sse_subscribers: list[asyncio.Queue] = []
        self.main_loop: asyncio.AbstractEventLoop | None = None


state = State()


# ---------- 이벤트 발행 (스레드 → SSE) ----------
def emit_event(event_type: str, payload: dict) -> None:
    """백그라운드 스레드 → asyncio 루프로 안전하게 메시지 전달."""
    if state.main_loop is None:
        return
    msg = {"type": event_type, "data": payload}
    for q in list(state.sse_subscribers):
        try:
            state.main_loop.call_soon_threadsafe(q.put_nowait, msg)
        except Exception:
            pass


def emit_log(kind: str, target: str, message: str, ok: bool,
             at_time: str | None = None) -> None:
    """at_time("HH:MM") 지정 시 그 시각으로 기록 — 동기화 감지 등하원 로그가
    감지 시점이 아닌 실제 등원/하원 시각을 갖도록."""
    if at_time and re.fullmatch(r"\d{1,2}:\d{2}", at_time):
        h, m = at_time.split(":")
        time_str = f"{int(h):02d}:{m}:00"  # 시간순 문자열 비교가 되도록 2자리 패딩
    else:
        time_str = datetime.now().strftime("%H:%M:%S")
    entry = {
        "time": time_str,
        "type": kind,
        "target": target,
        "message": message,
        "ok": ok,
    }
    try:
        append_log(entry)
    except Exception:
        pass
    emit_event("log", entry)


# ---------- 등하원 상태 ----------
def _reset_att_if_new_day() -> None:
    today = date.today().isoformat()
    if state.att.get("date") != today:
        state.att = {"date": today, "status": {}, "times": {}}
        state.att_logged = None
        emit_event("att_reset", {})


def set_att_status(key: str, status: str,
                   in_time: str | None = None, out_time: str | None = None) -> None:
    """등하원 상태(+선택적으로 등/하원 시각) 갱신 후 SSE 반영.

    in_time/out_time 이 None 이면 기존 시각 유지 (수동 변경 시).
    """
    if not key or status not in ATT_STATUSES:
        return
    _reset_att_if_new_day()
    if status == "미등원":
        state.att["status"].pop(key, None)
    else:
        state.att["status"][key] = status
    if in_time is not None or out_time is not None:
        t = dict(state.att["times"].get(key) or {})
        if in_time is not None:
            t["in"] = in_time
        if out_time is not None:
            t["out"] = out_time
        if t.get("in") or t.get("out"):
            state.att["times"][key] = t
        else:
            state.att["times"].pop(key, None)
    t = state.att["times"].get(key) or {}
    emit_event("att_status", {
        "key": key, "status": status,
        "in_time": t.get("in", ""), "out_time": t.get("out", ""),
    })


# ---------- 사운드 재생 ----------
def _play_sound(filename: str) -> None:
    if not _SOUND_AVAILABLE:
        return
    path = SOUND_DIR / filename
    try:
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass


# ---------- 입출차 토스트 (브라우저 SSE) ----------
def _notify_vehicle(kind: str, name: str, car: str) -> None:
    if not state.config.get("vehicle_toast", True):
        return
    duration_ms = max(1, int(state.config.get("vehicle_toast_duration", 5))) * 1000
    emit_event("vehicle_notify", {
        "kind": kind, "name": name, "car": car,
        "duration_ms": duration_ms,
    })


# ---------- 액션 (백그라운드 스레드에서 실행) ----------
def _trigger_refresh(*tasks: str) -> None:
    """갱신 루프를 즉시 깨움. tasks 미지정 시 전체("att"+"vehicle") 갱신."""
    state.wake_tasks |= set(tasks) or {"att", "vehicle"}
    state.refresh_wake.set()


def _run_action_then_refresh(fn, args, tasks: tuple[str, ...]) -> None:
    """액션 실행 완료 후 해당 범위만 즉시 갱신 (등하원→att, 차량→vehicle)."""
    try:
        fn(*args)
    finally:
        _trigger_refresh(*tasks)


def do_attendance(student: dict, tag: str = "안심톡") -> None:
    name = student.get("name", "")
    code = student.get("code", "")
    target = f"{code} {name}".strip() if code else name
    if not (code.isdigit() and len(code) == 4):
        emit_log(tag, target, f"출석번호 형식 오류: {code!r}", False)
        _play_sound("S2.wav")
        return
    try:
        ok = ansim.register(code)
        msg = getattr(ansim, "LAST_MESSAGE", "") or (
            "등록 완료" if ok else "등록 실패"
        )
        emit_log(tag, target, msg, ok)
        _play_sound("S1.wav" if ok else "S2.wav")
        if ok:
            _update_att_from_message(code, msg)
    except Exception as e:
        emit_log(tag, target, f"오류: {e}", False)
        _play_sound("S2.wav")


def _update_att_from_message(code: str, msg: str) -> None:
    """안심톡 서버 응답 문구(등원/하원하였습니다)로 상태 네모 갱신."""
    if "하원" in msg:
        new_status = "하원"
    elif "등원" in msg:
        new_status = "등원"
    else:
        cur = state.att["status"].get(code, "미등원")
        new_status = "하원" if cur == "등원" else "등원"
    set_att_status(code, new_status)
    # 직접 처리한 건은 액션 로그가 이미 남으므로 동기화 로그 대상에서 제외
    if state.att_logged is not None:
        rec = state.att_logged.setdefault(code, {"in": False, "out": False})
        rec["in" if new_status == "등원" else "out"] = True


def _vehicle_ticket_count() -> int:
    """차량등록 버튼이 등록할 매수 — 설정값을 권종의 최대 매수 안으로 제한."""
    limit = iparking.TICKETS[DEFAULT_VEHICLE_TICKET]["max"]
    try:
        n = int(state.config.get("vehicle_ticket_count", 1))
    except (TypeError, ValueError):
        n = 1
    return min(limit, max(1, n))


def do_vehicle(student: dict, tickets: dict[str, int] | None = None,
               tag: str = "차량등록") -> None:
    if tickets is None:
        # 설정에서 정한 매수만큼 등록 (예약은 자체 매수를 넘겨받으므로 여기 오지 않음)
        tickets = {DEFAULT_VEHICLE_TICKET: _vehicle_ticket_count()}
    name = student.get("name", "")

    cars = list(student.get("car_no4s") or [])
    if not cars:
        single = student.get("car_no4", "")
        if single:
            cars = [single]
    parsed = [(c, p) for c in cars if (p := _parse_car_entry(c))]

    cars_label = ",".join(c for c, _ in parsed) if parsed else (",".join(cars) if cars else "")
    overall_target = f"{cars_label} {name}".strip() if cars_label else name

    tickets = {t: int(c) for t, c in tickets.items() if int(c) > 0}
    if not tickets:
        emit_log(tag, overall_target, "등록할 주차권 없음", False)
        return
    for ttype in tickets:
        if ttype not in iparking.TICKETS:
            emit_log(tag, overall_target, f"알 수 없는 권종: {ttype}", False)
            return

    if not parsed:
        emit_log(tag, overall_target, f"차량번호 없음/오류: {cars}", False)
        return

    parked = []
    for entry, (last4, full) in parsed:
        car_target = f"{entry} {name}".strip()
        try:
            in_car = iparking.find_in_car(last4, full_plate=full or None)
        except Exception as e:
            emit_log(tag, car_target, f"조회 오류: {e}", False)
            continue
        if in_car is not None:
            parked.append((entry, in_car))

    if not parked:
        emit_log(tag, overall_target, "입차된 차량 없음", False)
        return

    for entry, in_car in parked:
        car_target = f"{entry} {name}".strip()
        for ttype, count in tickets.items():
            ticket = iparking.TICKETS[ttype]
            try:
                ok_count = 0
                last_msg = ""
                for _ in range(count):
                    ok, msg = iparking.apply_discount(in_car, ttype)
                    last_msg = msg
                    if ok:
                        ok_count += 1
                    else:
                        break
                if count > 1:
                    summary = f"{ticket['label']} {ok_count}/{count}매 - {last_msg}"
                else:
                    summary = f"{ticket['label']} - {last_msg}"
                emit_log(tag, car_target, summary, ok_count == count)
            except Exception as e:
                emit_log(tag, car_target, f"{ticket['label']} 오류: {e}", False)


# ---------- 백그라운드 루프 ----------
def refresh_loop():
    """공통 갱신 루프 — 한 주기마다 차량 자동검색 + 등하원 상태 동기화를 함께 실행.

    수동 새로고침/등하원처리/차량등록 시 refresh_wake 로 즉시 깨어나
    (설정 스위치가 꺼져 있어도) 한 번 갱신하고 카운트다운을 리셋한다.
    """
    manual_tasks: set[str] | None = None  # None = 주기 도래(스위치 따름), set = 수동 트리거 범위
    while True:
        # 루프 전체를 보호 — 여기서 예외가 새어 나가면 스레드가 끝나고
        # 차량 검색·등하원 동기화가 조용히 영구 정지한다
        try:
            manual_tasks = _refresh_tick(manual_tasks)
        except Exception as e:
            _log_loop_error("갱신", e)
            time.sleep(5)
            manual_tasks = None


_loop_error_seen: dict[str, tuple[str, float]] = {}
_LOOP_ERROR_REPEAT_SEC = 300


def _log_loop_error(what: str, e: Exception) -> None:
    """백그라운드 루프의 예외를 사용자에게 드러낸다 (로그 자체가 실패해도 루프는 계속).

    같은 오류가 계속 나면 재시도 주기마다 로그가 도배되므로,
    오류 종류가 바뀌거나 일정 시간이 지났을 때만 기록한다.
    """
    kind = type(e).__name__
    msg = f"{what} 처리 중 오류: {kind}: {e}"
    print(msg, file=sys.stderr)

    prev_kind, prev_ts = _loop_error_seen.get(what, ("", 0.0))
    now = time.time()
    if kind == prev_kind and (now - prev_ts) < _LOOP_ERROR_REPEAT_SEC:
        return
    _loop_error_seen[what] = (kind, now)
    try:
        emit_log("시스템", what, msg, False)
    except Exception:
        pass


def _refresh_tick(manual_tasks: set[str] | None) -> set[str] | None:
    """한 주기 실행 후, 다음 주기에 쓸 manual_tasks 를 반환."""
    # 등하원 동기화를 먼저 — 빠르게 끝나서 배지가 즉시 갱신됨
    if (manual_tasks is not None and "att" in manual_tasks) or \
            (manual_tasks is None and state.config.get("att_sync", True)):
        try:
            _att_sync_once()
            _report_att_health(True, "")
        except Exception as e:
            print(f"등하원 상태 동기화 오류: {e}", file=sys.stderr)
            _report_att_health(False, f"{type(e).__name__}: {e}")
    if (manual_tasks is not None and "vehicle" in manual_tasks) or \
            (manual_tasks is None and state.config.get("auto_search")):
        try:
            _poll_once()
        except Exception as e:
            print(f"차량 검색 오류: {e}", file=sys.stderr)
    try:
        interval = max(10, int(state.config.get("refresh_interval", 60)))
    except Exception:
        interval = 60
    state.next_refresh_ts = time.time() + interval
    emit_event("refreshed", {"next_in": interval})
    if state.refresh_wake.wait(timeout=interval):
        state.refresh_wake.clear()
        tasks = state.wake_tasks or {"att", "vehicle"}
        state.wake_tasks = set()
        return tasks
    return None


def _report_att_health(ok: bool, reason: str) -> None:
    """등하원 동기화 실패를 상태가 바뀔 때만 1회 알린다.

    실패를 stderr 로만 남기면(pythonw 실행 시 로그파일行) 사용자는 배지가 왜
    멈췄는지 알 수 없다. 비밀번호 변경·포털 개편 때 조용히 정지하는 것을 막는다.
    """
    if not ok and not state._att_failing:
        state._att_failing = True
        emit_log("시스템", "등하원동기화", f"조회 실패 — {reason}"[:200], False)
    elif ok and state._att_failing:
        state._att_failing = False
        emit_log("시스템", "등하원동기화", "조회 정상 복구", True)


def _report_poll_health(failed: int, total: int, errors: dict[str, Exception]) -> None:
    """차량 조회 실패를 상태가 바뀔 때만 1회 알린다.

    실패를 조용히 삼키면(자격증명 만료·서버 점검 등) 자동 검색이 무기한 먹통이어도
    사용자는 알 수 없다. 반대로 매 주기 기록하면 로그가 도배되므로 전환 시점만 남긴다.
    """
    if failed and not state._poll_failing:
        state._poll_failing = True
        reason = ""
        if errors:
            e = next(iter(errors.values()))
            reason = f" — {type(e).__name__}: {e}"[:200]
        emit_log("시스템", "차량검색", f"조회 실패 {failed}/{total}{reason}", False)
    elif not failed and state._poll_failing:
        state._poll_failing = False
        emit_log("시스템", "차량검색", "조회 정상 복구", True)


def _poll_once():
    entry_to_student: dict[str, dict] = {}
    entry_to_full: dict[str, str] = {}
    by_last4: dict[str, list[str]] = {}
    for s in state.students:
        for c in s.get("car_no4s", []):
            p = _parse_car_entry(c)
            if not p:
                continue
            last4, full = p
            entry_to_student[c] = s
            entry_to_full[c] = full
            by_last4.setdefault(last4, []).append(c)
    tracked = set(entry_to_student.keys())

    errors: dict[str, Exception] = {}

    def _query(last4: str):
        """성공하면 리스트(입차 없으면 빈 리스트), 실패하면 None.

        실패와 '입차 차량 없음' 을 반드시 구분해야 한다 — 실패를 미입차로 오인하면
        아래 출차 판정에서 입차 중이던 차량이 전부 출차로 잡힌다.
        """
        try:
            return iparking.find_in_cars(last4)
        except Exception as e:
            errors[last4] = e
            return None

    # last4 별 순차 조회는 원생 수에 비례해 느려짐 (~0.5초×N) — 병렬로 단축
    last4_list = list(by_last4.keys())
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = dict(zip(last4_list, ex.map(_query, last4_list)))

    # 조회에 실패한 차량은 이번 주기 입출차 판정에서 제외하고 직전 상태를 유지한다.
    # (네트워크 순단·서버 점검 때 가짜 출차/입차가 무더기로 발생하는 것을 방지)
    unknown: set[str] = {e for l4, r in results.items() if r is None for e in by_last4[l4]}
    observed = tracked - unknown  # 이번 주기에 상태를 실제로 확인한 차량
    _report_poll_health(len(errors), len(last4_list), errors)

    current: set[str] = set()
    for last4, entries in by_last4.items():
        cars = results.get(last4)
        if not cars:  # None=조회 실패(위에서 제외됨) / []=정상적으로 입차 없음
            continue
        for entry in entries:
            full = entry_to_full[entry]
            if full:
                match = next((c for c in cars if (c.get("carNo") or "") == full), None)
            else:
                match = cars[0]
            if match:
                current.add(entry)
                state.last_seen_carNo[entry] = match.get("carNo") or entry

    # 기준(baseline)은 차량별로 잡는다. 전체가 성공해야 기준을 잡는 방식이면
    # 특정 차량 하나만 계속 실패해도 입출차 알림이 영영 발생하지 않는다.
    # 처음 확인된 차량은 이번 주기에 조용히 기준만 잡고(가짜 입차 방지),
    # 이미 기준이 있는 차량만 입차/출차로 판정한다.
    judged = state.poll_baseline & observed
    entered = (current & judged) - state.prev_in_cars
    exited = (state.prev_in_cars & judged) - current
    for entry in entered:
        s = entry_to_student.get(entry) or {}
        name = s.get("name", "")
        full_name = state.last_seen_carNo.get(entry, entry)
        emit_log("입차", name, full_name, True)
        _notify_vehicle("입차", name, full_name)
    for entry in exited:
        s = entry_to_student.get(entry) or {}
        name = s.get("name", "")
        full_name = state.last_seen_carNo.get(entry, entry)
        emit_log("출차", name, full_name, True)
        _notify_vehicle("출차", name, full_name)

    # 이번에 확인된 차량은 다음 주기부터 판정 대상. 등록이 사라진 차량은 정리.
    state.poll_baseline = (state.poll_baseline | observed) & tracked
    # 조회 실패한 차량은 직전 상태를 그대로 유지 — 판정 보류
    state.prev_in_cars = current | (state.prev_in_cars & unknown)
    emit_event("in_cars", {"cars": list(state.prev_in_cars)})


def _build_att_logged() -> dict[str, dict[str, bool]]:
    """오늘 로그에서 원생별 등원/하원 기록 여부를 복원 — 동기화 로그 중복 방지."""
    logged: dict[str, dict[str, bool]] = {}
    for e in load_today_logs():
        if not str(e.get("type", "")).startswith("안심톡") or not e.get("ok"):
            continue
        parts = str(e.get("target", "")).split()
        code = parts[0] if parts else ""
        if not (code.isdigit() and len(code) == 4):
            continue
        msg = str(e.get("message", ""))
        rec = logged.setdefault(code, {"in": False, "out": False})
        if "등원" in msg:
            rec["in"] = True
        if "하원" in msg:
            rec["out"] = True
    return logged


def _att_sync_once():
    _reset_att_if_new_day()
    status_map = ansim_web.fetch_status_map()
    if not status_map:
        # 포털 응답 구조가 바뀌면 예외 없이 빈 결과가 온다 — 조용한 정지를 막는다
        raise RuntimeError("등하원 상태 조회 결과가 비어 있음 (포털 응답 확인 필요)")
    if state.att_logged is None:
        state.att_logged = _build_att_logged()
    local_codes = {
        (s.get("code") or "").strip()
        for s in state.students
        if (s.get("code") or "").strip()
    }
    for code, info in status_map.items():
        if code not in local_codes:
            continue
        label = info["label"]
        if label not in ATT_STATUSES:
            continue
        cur = state.att["status"].get(code, "미등원")
        cur_t = state.att["times"].get(code) or {}
        if (cur != label
                or cur_t.get("in", "") != info["in_time"]
                or cur_t.get("out", "") != info["out_time"]):
            set_att_status(code, label, info["in_time"], info["out_time"])
        # 로그 보정 — 다른 PC/키패드에서 처리된 등하원도 당일 로그에 없으면
        # 실제 등원/하원 시각(SDATE/EDATE)으로 기록. 일반 등하원처리와 동일한 양식.
        # 이 화면에서 직접 처리한 건은 액션 로그가 이미 있어 중복되지 않음.
        # (결석/공결/캠프는 배지만 반영, 로그 없음)
        if label in ("등원", "하원"):
            rec = state.att_logged.setdefault(code, {"in": False, "out": False})
            target = f"{code} {info.get('name') or ''}".strip()
            if info["in_time"] and not rec["in"]:
                rec["in"] = True
                emit_log("안심톡", target, "등원하였습니다.", True,
                         at_time=info["in_time"])
            if label == "하원" and info["out_time"] and not rec["out"]:
                rec["out"] = True
                emit_log("안심톡", target, "하원하였습니다.", True,
                         at_time=info["out_time"])


def scheduler_loop():
    """예약 실행 루프.

    루프 본문 전체를 예외로부터 보호한다 — 여기서 예외가 한 번이라도 새어 나가면
    데몬 스레드가 끝나고 이후 모든 예약이 조용히 실행되지 않는다(화면에는 아무 표시도
    없어 알아채기 어렵다). 실패는 삼키지 말고 로그로 드러낸다.
    """
    while True:
        try:
            _scheduler_tick()
        except Exception as e:
            _log_loop_error("예약", e)
        time.sleep(30)


def _scheduler_tick() -> None:
    _reset_att_if_new_day()

    now = datetime.now()
    today = now.date().isoformat()
    hhmm = now.strftime("%H:%M")
    weekday = now.weekday()

    for sched in list(state.schedules):
        if not sched.get("enabled", True):
            continue
        if sched.get("time", "") != hhmm:
            continue
        days = sched.get("days") or []
        if not days or weekday not in days:
            continue
        # 하루 1회 보장 — 파일에 남겨 재시작해도 중복 실행되지 않게 한다
        if sched.get("last_run") == today:
            continue
        sched["last_run"] = today
        save_schedules(state.schedules)

        code = sched.get("code", "")
        car = sched.get("car_no4", "")
        label = f"예약 {hhmm}"
        tickets = sched.get("tickets") or {}

        if code:
            threading.Thread(
                target=do_attendance,
                args=({"name": label, "code": code}, "안심톡(예약)"),
                daemon=True,
            ).start()
        if car and tickets:
            threading.Thread(
                target=do_vehicle,
                args=({"name": label, "car_no4": car}, tickets, "차량등록(예약)"),
                daemon=True,
            ).start()


# ---------- 폼 파싱 ----------
def _parse_schedule_form(form) -> dict:
    time_s = (form.get("time") or "").strip()
    code = (form.get("code") or "").strip()
    car = (form.get("car_no4") or "").strip()

    try:
        # "13:0" 도 파싱은 되지만 스케줄러의 "%H:%M" 비교와 문자열이 달라
        # 그 예약이 하루도 실행되지 않는다 - 반드시 정규화한다
        time_s = datetime.strptime(time_s, "%H:%M").strftime("%H:%M")
    except ValueError:
        return {"error": "시각 형식 오류 (HH:MM)"}

    if not code and not car:
        return {"error": "출석번호 또는 차량번호 중 최소 하나 필요"}
    if code and not (code.isdigit() and len(code) == 4):
        return {"error": "출석번호 4자리 숫자"}
    if car and _parse_car_entry(car) is None:
        return {"error": "차량번호는 마지막 4자리가 숫자여야 함"}

    days = []
    for i in range(5):
        if form.get(f"day_{i}"):
            days.append(i)
    if not days:
        return {"error": "최소 1개 요일 선택"}

    tickets = {}
    for ttype in iparking.TICKETS:
        raw = (form.get(f"tk_{ttype}") or "0").strip() or "0"
        try:
            n = int(raw)
        except ValueError:
            return {"error": f"{ttype} 매수는 숫자"}
        if n < 0:
            return {"error": "매수는 0 이상"}
        if n > 0:
            tickets[ttype] = n

    if car and not tickets:
        return {"error": "차량번호 입력 시 매수 최소 1 필요"}

    return {
        "time": time_s,
        "days": days,
        "code": code,
        "car_no4": car,
        "tickets": tickets,
    }


# ---------- FastAPI ----------
def _auto_update_check():
    """서버 시작 시 1회 — 새 릴리스가 있으면 자동 업데이트 후 재시작."""
    time.sleep(3)  # 서버가 뜬 뒤에 실행 (업데이트 로그가 UI에 보이도록)
    try:
        updater.auto_update_on_start(
            log=lambda msg: emit_log("시스템", "업데이트", msg, True)
        )
    except Exception as e:
        emit_log("시스템", "업데이트", f"자동 업데이트 실패: {e}", False)


def _secure_credential_files() -> None:
    """자격증명 파일 권한을 현재 사용자 전용으로 (기존 설치본도 시작 시 1회 정리).

    기본 상속 ACL 에서는 BUILTIN\\Users 에 수정 권한까지 열려 있어,
    PC 를 여러 계정이 공유하면 다른 사용자가 비밀번호를 읽을 수 있다.
    """
    for p in (ansim.ANSIM_CONFIG_PATH, iparking.CONFIG_PATH,
              STUDENTS_PATH, SCHEDULES_PATH, CONFIG_PATH):
        try:
            if p.exists():
                jsonstore.restrict_permissions(p)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.main_loop = asyncio.get_running_loop()
    threading.Thread(target=_secure_credential_files, daemon=True).start()
    threading.Thread(target=refresh_loop, daemon=True).start()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    threading.Thread(target=_auto_update_check, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
def _static_version() -> str:
    """정적 파일 캐시 무효화 키.

    앱 버전만 쓰면 같은 버전에서 CSS 를 재빌드했을 때(개발 중 흔함) 브라우저가
    옛 파일을 계속 써서 새 스타일이 적용되지 않는다. 파일 수정 시각을 함께 넣어
    내용이 바뀌면 주소도 바뀌게 한다.
    """
    try:
        stamp = max(
            p.stat().st_mtime
            for name in ("tailwind.css", "alpine.min.js")
            if (p := STATIC_DIR / name).exists()
        )
        return f"{__version__}-{int(stamp)}"
    except Exception:
        return __version__


templates.env.globals["app_version"] = __version__
templates.env.globals["static_version"] = _static_version()  # 정적 자원 캐시 버스팅용
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------- 보안 (로컬 전용 앱 보호) ----------
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


@app.middleware("http")
async def _local_only_guard(request: Request, call_next):
    """브라우저를 경유한 외부 공격 차단.

    127.0.0.1 바인딩은 '읽기' 만 막을 뿐 '쓰기' 는 막지 못한다. 사용자가 아무 웹페이지나
    열어두면 그 페이지가 자동 제출 폼으로 이 서버의 POST 를 호출할 수 있고
    (허위 등하원 문자 발송, 주차권 소진, 서버 종료), Host 를 위조한 DNS 리바인딩으로는
    원생 개인정보를 읽어갈 수도 있다.

    - Host 검증: 로컬 이름으로 온 요청만 처리 (DNS 리바인딩 차단)
    - Origin 검증: 상태를 바꾸는 요청은 출처 확인 (CSRF 차단).
      크로스오리진 폼 제출은 브라우저가 Origin 을 반드시 붙인다.
      Origin 이 아예 없는 요청(curl 등 비브라우저)은 CSRF 가 성립하지 않아 허용한다.
    """
    # 호스트명은 대소문자를 구분하지 않는다 (LOCALHOST 도 정상 요청)
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
    if host and host not in _ALLOWED_HOSTS:
        return JSONResponse({"error": "invalid host"}, status_code=421)

    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin is not None:
            # 포트는 보지 않는다 — 앱이 다른 포트로 떠도 자기 화면의 폼은 동작해야 함.
            # 같은 PC 의 다른 로컬 앱은 통과하지만, 그 수준이면 이미 설정 파일을
            # 직접 읽을 수 있는 상황이라 이 검증의 방어 대상(외부 웹페이지)이 아니다.
            try:
                origin_host = urllib.parse.urlparse(origin).hostname
            except ValueError:
                origin_host = None
            if origin_host not in _ALLOWED_HOSTS:
                return JSONResponse({"error": "cross-origin request denied"}, status_code=403)

    response = await call_next(request)
    # 클릭재킹 방지 — 악성 페이지가 이 화면을 iframe 으로 덮고 클릭을 유도하는 것을 차단
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    return response


# ---------- 페이지 ----------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    _reset_att_if_new_day()
    return templates.TemplateResponse(request, "index.html", {
        "students": state.students,
        "in_cars": list(state.prev_in_cars),
        "logs": load_today_logs(),
        "config": state.config,
        "att_status": state.att["status"],
        "att_times": state.att["times"],
        "att_refresh_remaining": max(0, round(state.next_refresh_ts - time.time())),
    })


@app.get("/students", response_class=HTMLResponse)
async def students_page(request: Request):
    return templates.TemplateResponse(request, "students.html", {
        "students": state.students,
    })


@app.get("/schedules", response_class=HTMLResponse)
async def schedules_page(request: Request):
    return templates.TemplateResponse(request, "schedules.html", {
        "schedules": state.schedules,
        "tickets": iparking.TICKETS,
        "ticket_order": TICKET_DISPLAY_ORDER,
        "day_labels": DAY_LABELS,
    })


def _load_ansim_account() -> dict:
    return jsonstore.load(ansim.ANSIM_CONFIG_PATH)


def _load_iparking_account() -> dict:
    return jsonstore.load(iparking.CONFIG_PATH)


def _settings_saved(what: str) -> RedirectResponse:
    """저장 후 설정 화면으로 돌아가며 무엇이 저장됐는지 알린다.

    폼 제출은 리다이렉트로 페이지가 다시 그려질 뿐이라, 표시가 없으면
    사용자는 저장이 됐는지 알 수 없다.
    """
    return RedirectResponse(
        "/settings?saved=" + urllib.parse.quote(what), status_code=303)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    ip_acc = _load_iparking_account()
    return templates.TemplateResponse(request, "settings.html", {
        "config": state.config,
        "ansim_user_id": (_load_ansim_account().get("user_id") or "").strip(),
        "iparking_store_id": (ip_acc.get("store_id") or "").strip(),
        "iparking_user_id": (ip_acc.get("user_id") or "").strip(),
        "vehicle_ticket_max": iparking.TICKETS[DEFAULT_VEHICLE_TICKET]["max"],
        "version": __version__,
        "autostart_enabled": autostart.is_enabled(),
    })


# ---------- 액션 API ----------
def _find_student(sid: str) -> dict | None:
    """안정 식별자로 원생 조회.

    배열 인덱스로 지정하면 원생 추가/삭제로 순서가 밀렸을 때 열려 있던 화면이
    엉뚱한 원생을 처리한다(잘못된 보호자에게 문자 발송). 반드시 id 로 찾을 것.
    """
    if not sid:
        return None
    return next((s for s in state.students if s.get("id") == sid), None)


@app.post("/api/students/{sid}/attendance")
async def trigger_attendance(sid: str):
    student = _find_student(sid)
    if student is None:
        return JSONResponse({"error": "student not found"}, status_code=404)
    threading.Thread(
        target=_run_action_then_refresh,
        args=(do_attendance, (student,), ("att",)), daemon=True,
    ).start()
    return {"ok": True}


@app.post("/api/students/{sid}/vehicle")
async def trigger_vehicle(sid: str):
    student = _find_student(sid)
    if student is None:
        return JSONResponse({"error": "student not found"}, status_code=404)
    threading.Thread(target=do_vehicle, args=(student,), daemon=True).start()
    return {"ok": True}


@app.get("/api/students/list")
async def list_students():
    """열려 있는 화면이 원생 목록 변경(students_changed)을 반영할 때 사용."""
    return {"students": state.students}


@app.post("/api/refresh")
async def manual_refresh():
    """수동 새로고침 — 차량 검색 + 등하원 상태 동기화를 즉시 실행."""
    _trigger_refresh()
    return {"ok": True}


@app.post("/api/students/{sid}/status")
async def set_student_status(sid: str, status: str = Form(...)):
    student = _find_student(sid)
    if student is None:
        return JSONResponse({"error": "student not found"}, status_code=404)
    if status not in ATT_STATUSES:
        return JSONResponse({"error": "invalid status"}, status_code=400)
    set_att_status(_student_key(student), status)
    return {"ok": True}


# ---------- 원생 CRUD ----------
def _commit_students(students: list[dict]) -> None:
    """저장 후 state 를 새 리스트로 교체하고, 열려 있는 화면에 변경을 알린다."""
    state.students = save_students(students)
    emit_event("students_changed", {})


@app.post("/api/students")
async def add_student(name: str = Form(""), code: str = Form(""), cars: str = Form("")):
    if not name.strip():
        return RedirectResponse("/students", status_code=303)
    car_list = [c.strip() for c in cars.split(",") if c.strip()]
    _commit_students(state.students + [{
        "id": uuid.uuid4().hex,
        "name": name.strip(),
        "code": code.strip(),
        "car_no4s": car_list,
    }])
    return RedirectResponse("/students", status_code=303)


@app.post("/api/students/{sid}/edit")
async def edit_student(sid: str, name: str = Form(""), code: str = Form(""), cars: str = Form("")):
    student = _find_student(sid)
    if student is None:
        return RedirectResponse("/students", status_code=303)
    car_list = [c.strip() for c in cars.split(",") if c.strip()]
    student["name"] = name.strip()
    student["code"] = code.strip()
    student["car_no4s"] = car_list
    _commit_students(state.students)
    return RedirectResponse("/students", status_code=303)


@app.post("/api/students/{sid}/delete")
async def delete_student(sid: str):
    if _find_student(sid) is not None:
        _commit_students([s for s in state.students if s.get("id") != sid])
    return RedirectResponse("/students", status_code=303)


# ---------- 예약 CRUD ----------
@app.post("/api/schedules")
async def add_schedule(request: Request):
    form = await request.form()
    parsed = _parse_schedule_form(form)
    if "error" in parsed:
        return JSONResponse(parsed, status_code=400)
    parsed["id"] = str(uuid.uuid4())
    parsed["enabled"] = True
    state.schedules.append(parsed)
    save_schedules(state.schedules)
    return RedirectResponse("/schedules", status_code=303)


@app.post("/api/schedules/{sid}/edit")
async def edit_schedule(sid: str, request: Request):
    form = await request.form()
    parsed = _parse_schedule_form(form)
    if "error" in parsed:
        return JSONResponse(parsed, status_code=400)
    for i, s in enumerate(state.schedules):
        if s.get("id") == sid:
            parsed["id"] = sid
            parsed["enabled"] = form.get("enabled") == "on"
            # 승계하지 않으면 같은 분에 편집 시 그 예약이 한 번 더 실행된다
            parsed["last_run"] = s.get("last_run", "")
            state.schedules[i] = parsed
            save_schedules(state.schedules)
            break
    return RedirectResponse("/schedules", status_code=303)


@app.post("/api/schedules/{sid}/delete")
async def delete_schedule(sid: str):
    state.schedules = [s for s in state.schedules if s.get("id") != sid]
    save_schedules(state.schedules)
    return RedirectResponse("/schedules", status_code=303)


# ---------- 시스템 ----------
@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/api/shutdown")
async def shutdown():
    emit_event("server_shutdown", {})  # 다른 탭·페이지도 종료 화면을 그리도록

    def _exit():
        time.sleep(0.5)  # 응답과 SSE 이벤트가 나갈 시간
        os._exit(0)
    threading.Thread(target=_exit, daemon=True).start()
    return {"ok": True}


# ---------- 설정 ----------
@app.post("/api/settings")
async def update_settings(request: Request):
    form = await request.form()
    state.config["auto_search"] = form.get("auto_search") == "on"
    state.config["vehicle_toast"] = form.get("vehicle_toast") == "on"
    state.config["att_sync"] = form.get("att_sync") == "on"
    raw = form.get("vehicle_toast_duration")
    if raw:
        try:
            state.config["vehicle_toast_duration"] = max(1, int(raw))
        except ValueError:
            pass
    raw = form.get("refresh_interval")
    if raw:
        try:
            state.config["refresh_interval"] = max(10, int(raw))
        except ValueError:
            pass
    raw = form.get("vehicle_ticket_count")
    if raw:
        try:
            # 할인권 자체의 최대 적용 매수를 넘지 않도록 제한
            limit = iparking.TICKETS[DEFAULT_VEHICLE_TICKET]["max"]
            state.config["vehicle_ticket_count"] = min(limit, max(1, int(raw)))
        except (ValueError, KeyError):
            pass
    save_config(state.config)
    return _settings_saved("설정")


@app.post("/api/settings/autostart")
async def update_autostart(enabled: str = Form("")):
    """자동 실행 스위치 — 켜고 끌 때마다 즉시 저장 (Windows HKCU Run 레지스트리)."""
    try:
        autostart.set_enabled(enabled == "1")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"ok": True, "enabled": autostart.is_enabled()}


# ---------- 계정 / 세션 ----------
@app.post("/api/settings/ansim")
async def update_ansim_account(user_id: str = Form(""), password: str = Form("")):
    """안심톡 계정 저장. 비밀번호가 비어 있으면 기존 값 유지."""
    uid = user_id.strip()

    def _mutate(cfg: dict) -> None:
        if uid:
            cfg["user_id"] = uid
        if password:
            cfg["password"] = password

    # 저장은 리다이렉트 전에 끝내야 설정 화면에 새 값이 보인다 (파일 IO 라 금방 끝남).
    # 백그라운드 갱신 스레드가 같은 파일의 session 을 쓰고 있을 수 있어 락 안에서 처리.
    await asyncio.to_thread(
        jsonstore.update, ansim.ANSIM_CONFIG_PATH, _mutate, private=True)

    def _reset() -> None:
        # 이전 계정의 세션이 남지 않도록 초기화 후 즉시 동기화로 검증
        ansim.reset_session()
        ansim_web.reset_session()
        _trigger_refresh("att")

    # reset_session 은 갱신 스레드가 HTTP 중에 잡고 있는 락을 기다릴 수 있다
    # (안심톡 웹 조회는 타임아웃 15초 × 재시도). 이벤트 루프에서 직접 호출하면
    # 그동안 모든 페이지·SSE·API 가 멈추므로 백그라운드로 넘긴다.
    threading.Thread(target=_reset, daemon=True).start()
    return _settings_saved("안심톡 계정")


@app.post("/api/update/check")
async def update_check():
    """수동 업데이트 확인 — 현재/최신 버전과 업데이트 가능 여부."""
    info = await asyncio.to_thread(updater.check_update)
    return {k: v for k, v in info.items() if k != "_release"}


@app.post("/api/update/apply")
async def update_apply():
    """수동 업데이트 실행 — 다운로드 후 서버가 재시작됨."""
    info = await asyncio.to_thread(updater.check_update)
    if info.get("error"):
        return JSONResponse({"error": info["error"]}, status_code=502)
    if not info["available"]:
        return JSONResponse({"error": f"이미 최신 버전입니다 (v{info['current']})"},
                            status_code=400)

    def _run():
        try:
            updater.download_and_apply(
                info["_release"],
                log=lambda msg: emit_log("시스템", "업데이트", msg, True),
            )
        except Exception as e:
            emit_log("시스템", "업데이트", f"업데이트 실패: {e}", False)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "latest": info["latest"]}


@app.post("/api/settings/iparking")
async def update_iparking_account(store_id: str = Form(""), user_id: str = Form(""),
                                  password: str = Form("")):
    """아이파킹 계정 저장. 비밀번호가 비어 있으면 기존 값 유지. 저장 후 세션 초기화·재로그인 검증."""
    sid, uid = store_id.strip(), user_id.strip()

    def _mutate(cfg: dict) -> None:
        if sid:
            cfg["store_id"] = sid
        if uid:
            cfg["user_id"] = uid
        if password:
            cfg["password"] = password

    # 저장은 리다이렉트 전에 끝내야 설정 화면에 새 값이 보인다 (파일 IO 라 금방 끝남).
    # 백그라운드 갱신 스레드가 같은 파일의 session 을 쓰고 있을 수 있어 락 안에서 처리.
    await asyncio.to_thread(
        jsonstore.update, iparking.CONFIG_PATH, _mutate, private=True)

    def _verify() -> None:
        # 이전 계정의 세션이 남지 않도록 초기화 후, 새 계정으로 즉시 재로그인해 검증
        iparking.reset_session()
        try:
            iparking.relogin()
            emit_log("시스템", "아이파킹", "계정 저장 — 주차 세션 재로그인 완료", True)
        except Exception as e:
            emit_log("시스템", "아이파킹", f"계정 저장했으나 로그인 실패: {e}", False)

    # reset_session/relogin 은 폴링 스레드가 HTTP 중에 잡고 있는 로그인 락을
    # 기다릴 수 있어 이벤트 루프에서 직접 호출하면 안 된다
    threading.Thread(target=_verify, daemon=True).start()
    return _settings_saved("아이파킹 계정")


# ---------- SSE ----------
@app.get("/stream")
async def event_stream(request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    state.sse_subscribers.append(q)

    # 접속 시점의 현재 상태를 먼저 내려줌 — 특히 refreshed 는 브라우저가
    # 첫 갱신 완료 방송보다 늦게 연결해도 카운트다운을 바로 받도록 필수
    initial_msgs = [{"type": "in_cars", "data": {"cars": list(state.prev_in_cars)}}]
    if state.next_refresh_ts > 0:
        initial_msgs.append({"type": "refreshed", "data": {
            "next_in": max(0, round(state.next_refresh_ts - time.time())),
        }})

    async def gen():
        try:
            for m in initial_msgs:
                yield f"data: {json.dumps(m, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            try:
                state.sse_subscribers.remove(q)
            except ValueError:
                pass

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------- 진입점 ----------
def _server_already_running() -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/health")
        with urllib.request.urlopen(req, timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def _redirect_stdio_if_pythonw():
    """pythonw.exe 로 실행되면 sys.stdout/stderr 가 None — print() 가 죽지 않게 파일로 우회."""
    if sys.stdout is None or sys.stderr is None:
        log_path = LOGS_DIR / "server.log"
        try:
            f = open(log_path, "a", encoding="utf-8", buffering=1)
            sys.stdout = f
            sys.stderr = f
        except Exception:
            class _Null:
                def write(self, *_a, **_k): pass
                def flush(self): pass
            sys.stdout = _Null()
            sys.stderr = _Null()


def _open_browser():
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass


def _make_tray_icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, 62, 62], fill=(59, 130, 246, 255))
    d.ellipse([22, 22, 42, 42], fill=(255, 255, 255, 255))
    return img


def _run_tray():
    import pystray
    from pystray import Menu, MenuItem

    def on_open(_icon, _item):
        _open_browser()

    def on_quit(icon, _item):
        # 브라우저들이 종료 화면을 그리도록 먼저 알리고, 이벤트가 SSE 로
        # 나갈 시간을 준 뒤 정리한다. icon.stop() 을 건너뛰고 죽으면
        # 트레이에 유령 아이콘이 남는다.
        emit_event("server_shutdown", {})
        time.sleep(0.5)
        icon.stop()
        os._exit(0)

    icon = pystray.Icon(
        "auto-ansimtalk",
        _make_tray_icon_image(),
        "auto-ansimtalk 서버 실행 중",
        menu=Menu(
            MenuItem("웹 페이지 열기", on_open, default=True),
            MenuItem("종료", on_quit),
        ),
    )
    icon.run()


def _run_server():
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


def main():
    _redirect_stdio_if_pythonw()
    open_browser = "--no-browser" not in sys.argv  # 부팅 자동 실행/업데이트 재시작용

    if _server_already_running():
        if open_browser:
            _open_browser()
        return

    threading.Thread(target=_run_server, daemon=True).start()

    deadline = time.time() + 10
    while time.time() < deadline:
        if _server_already_running():
            break
        time.sleep(0.2)

    if open_browser:
        _open_browser()

    print(f"=== auto-ansimtalk 웹 서버 ===")
    print(f"브라우저 접속: http://localhost:{PORT}")
    print(f"종료: 트레이 아이콘 우클릭 → 종료\n")

    try:
        _run_tray()
    except Exception as e:
        # 트레이 실패로 프로세스가 끝나면 데몬 스레드인 서버까지 함께 죽어
        # 앱이 "잠깐 떴다 사라짐" 으로 보인다. 서버는 계속 살려 둔다.
        # (pystray/pillow 설치 손상, 트레이가 없는 세션 등)
        msg = f"트레이 아이콘을 띄우지 못했습니다 — 서버는 계속 실행됩니다: {e}"
        print(msg, file=sys.stderr)
        try:
            emit_log("시스템", "트레이", msg, False)
        except Exception:
            pass
        threading.Event().wait()  # 메인 스레드 유지 (설정 > 서버 종료로 끌 수 있음)


if __name__ == "__main__":
    main()
