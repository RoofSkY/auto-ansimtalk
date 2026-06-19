"""안심톡 + 주차할인 자동화 웹 서버.

브라우저로 http://localhost:5000 접속해서 사용.
"""

import asyncio
import json
import os
import sys
import threading
import time
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

import npdc
import ansim

try:
    import winsound
    _SOUND_AVAILABLE = True
except ImportError:
    _SOUND_AVAILABLE = False


# ---------- 경로 ----------
HERE = Path(__file__).resolve().parent
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
    "auto_search_interval": 60,
    "vehicle_toast": True,
    "vehicle_toast_duration": 5,
}

DAY_LABELS = ["월", "화", "수", "목", "금"]
TICKET_DISPLAY_ORDER = ["free", "paid"]
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


def load_students() -> list[dict]:
    if not STUDENTS_PATH.exists():
        return []
    try:
        with open(STUDENTS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return []
    for s in raw:
        if "car_no4s" not in s:
            legacy = s.get("car_no4", "")
            s["car_no4s"] = [legacy] if legacy else []
        s.pop("car_no4", None)
    raw.sort(key=_student_sort_key)
    return raw


def save_students(students: list[dict]) -> None:
    students.sort(key=_student_sort_key)
    normalized = [
        {
            "name": s.get("name", ""),
            "code": s.get("code", ""),
            "car_no4s": list(s.get("car_no4s", [])),
        }
        for s in students
    ]
    with open(STUDENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)


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
        }
        for s in schedules
    ]
    with open(SCHEDULES_PATH, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
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
        self.prev_in_cars: set[str] = set()
        self.last_seen_carNo: dict[str, str] = {}
        self._poll_initialized = False
        self._sched_last_run: dict[str, str] = {}

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


def emit_log(kind: str, target: str, message: str, ok: bool) -> None:
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
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
    except Exception as e:
        emit_log(tag, target, f"오류: {e}", False)
        _play_sound("S2.wav")


def do_vehicle(student: dict, tickets: dict[str, int] | None = None,
               tag: str = "차량등록") -> None:
    if tickets is None:
        tickets = {"free": 1}
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
        if ttype not in npdc.TICKETS:
            emit_log(tag, overall_target, f"알 수 없는 권종: {ttype}", False)
            return

    if not parsed:
        emit_log(tag, overall_target, f"차량번호 없음/오류: {cars}", False)
        return

    parked = []
    for entry, (last4, full) in parsed:
        car_target = f"{entry} {name}".strip()
        try:
            in_car = npdc.find_in_car(last4, full_plate=full or None)
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
            ticket = npdc.TICKETS[ttype]
            try:
                ok_count = 0
                last_msg = ""
                for _ in range(count):
                    ok, msg = npdc.apply_discount(in_car, ttype)
                    last_msg = msg
                    if ok:
                        ok_count += 1
                    else:
                        break
                if count > 1:
                    summary = f"{ticket['label']} {ok_count}/{count}매 — {last_msg}"
                else:
                    summary = f"{ticket['label']} — {last_msg}"
                emit_log(tag, car_target, summary, ok_count == count)
            except Exception as e:
                emit_log(tag, car_target, f"{ticket['label']} 오류: {e}", False)


# ---------- 백그라운드 루프 ----------
def vehicle_poller_loop():
    while True:
        if state.config.get("auto_search"):
            try:
                _poll_once()
            except Exception as e:
                print(f"폴링 오류: {e}", file=sys.stderr)
        try:
            interval = int(state.config.get("auto_search_interval", 60))
        except Exception:
            interval = 60
        time.sleep(max(5, interval))


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

    current: set[str] = set()
    for last4, entries in by_last4.items():
        try:
            cars = npdc.find_in_cars(last4)
        except Exception:
            continue
        if not cars:
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

    if state._poll_initialized:
        prev_tracked = state.prev_in_cars & tracked
        entered = current - prev_tracked
        exited = prev_tracked - current
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
    else:
        state._poll_initialized = True

    state.prev_in_cars = current
    emit_event("in_cars", {"cars": list(current)})


def scheduler_loop():
    while True:
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
            sid = sched.get("id", "")
            key = f"sched_{sid}"
            if state._sched_last_run.get(key) == today:
                continue
            state._sched_last_run[key] = today

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

        time.sleep(30)


# ---------- 폼 파싱 ----------
def _parse_schedule_form(form) -> dict:
    time_s = (form.get("time") or "").strip()
    code = (form.get("code") or "").strip()
    car = (form.get("car_no4") or "").strip()

    try:
        datetime.strptime(time_s, "%H:%M")
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
    for ttype in npdc.TICKETS:
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
@asynccontextmanager
async def lifespan(app: FastAPI):
    state.main_loop = asyncio.get_running_loop()
    threading.Thread(target=vehicle_poller_loop, daemon=True).start()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------- 페이지 ----------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "students": state.students,
        "in_cars": list(state.prev_in_cars),
        "logs": load_today_logs(),
        "config": state.config,
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
        "tickets": npdc.TICKETS,
        "ticket_order": TICKET_DISPLAY_ORDER,
        "day_labels": DAY_LABELS,
    })


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {
        "config": state.config,
    })


# ---------- 액션 API ----------
@app.post("/api/students/{idx}/attendance")
async def trigger_attendance(idx: int):
    if idx < 0 or idx >= len(state.students):
        return JSONResponse({"error": "invalid index"}, status_code=400)
    student = state.students[idx]
    threading.Thread(target=do_attendance, args=(student,), daemon=True).start()
    return {"ok": True}


@app.post("/api/students/{idx}/vehicle")
async def trigger_vehicle(idx: int):
    if idx < 0 or idx >= len(state.students):
        return JSONResponse({"error": "invalid index"}, status_code=400)
    student = state.students[idx]
    threading.Thread(target=do_vehicle, args=(student,), daemon=True).start()
    return {"ok": True}


# ---------- 원생 CRUD ----------
@app.post("/api/students")
async def add_student(name: str = Form(""), code: str = Form(""), cars: str = Form("")):
    if not name.strip():
        return RedirectResponse("/students", status_code=303)
    car_list = [c.strip() for c in cars.split(",") if c.strip()]
    state.students.append({
        "name": name.strip(),
        "code": code.strip(),
        "car_no4s": car_list,
    })
    save_students(state.students)
    return RedirectResponse("/students", status_code=303)


@app.post("/api/students/{idx}/edit")
async def edit_student(idx: int, name: str = Form(""), code: str = Form(""), cars: str = Form("")):
    if idx < 0 or idx >= len(state.students):
        return RedirectResponse("/students", status_code=303)
    car_list = [c.strip() for c in cars.split(",") if c.strip()]
    state.students[idx]["name"] = name.strip()
    state.students[idx]["code"] = code.strip()
    state.students[idx]["car_no4s"] = car_list
    save_students(state.students)
    return RedirectResponse("/students", status_code=303)


@app.post("/api/students/{idx}/delete")
async def delete_student(idx: int):
    if 0 <= idx < len(state.students):
        del state.students[idx]
        save_students(state.students)
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
            state.schedules[i] = parsed
            save_schedules(state.schedules)
            state._sched_last_run.pop(f"sched_{sid}", None)
            break
    return RedirectResponse("/schedules", status_code=303)


@app.post("/api/schedules/{sid}/delete")
async def delete_schedule(sid: str):
    state.schedules = [s for s in state.schedules if s.get("id") != sid]
    save_schedules(state.schedules)
    state._sched_last_run.pop(f"sched_{sid}", None)
    return RedirectResponse("/schedules", status_code=303)


# ---------- 시스템 ----------
@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/api/shutdown")
async def shutdown():
    def _exit():
        time.sleep(0.3)
        os._exit(0)
    threading.Thread(target=_exit, daemon=True).start()
    return {"ok": True}


# ---------- 설정 ----------
@app.post("/api/settings")
async def update_settings(request: Request):
    form = await request.form()
    state.config["auto_search"] = form.get("auto_search") == "on"
    state.config["vehicle_toast"] = form.get("vehicle_toast") == "on"
    raw = form.get("auto_search_interval")
    if raw:
        try:
            state.config["auto_search_interval"] = max(5, int(raw))
        except ValueError:
            pass
    raw = form.get("vehicle_toast_duration")
    if raw:
        try:
            state.config["vehicle_toast_duration"] = max(1, int(raw))
        except ValueError:
            pass
    save_config(state.config)
    return RedirectResponse("/settings", status_code=303)


# ---------- SSE ----------
@app.get("/stream")
async def event_stream(request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    state.sse_subscribers.append(q)

    initial = {"type": "in_cars", "data": {"cars": list(state.prev_in_cars)}}

    async def gen():
        try:
            yield f"data: {json.dumps(initial, ensure_ascii=False)}\n\n"
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

    if _server_already_running():
        _open_browser()
        return

    threading.Thread(target=_run_server, daemon=True).start()

    deadline = time.time() + 10
    while time.time() < deadline:
        if _server_already_running():
            break
        time.sleep(0.2)

    _open_browser()

    print(f"=== auto-ansimtalk 웹 서버 ===")
    print(f"브라우저 접속: http://localhost:{PORT}")
    print(f"종료: 트레이 아이콘 우클릭 → 종료\n")

    _run_tray()


if __name__ == "__main__":
    main()
