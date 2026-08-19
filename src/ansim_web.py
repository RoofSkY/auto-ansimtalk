"""안심톡 웹 포털(ansimtalk.gg.go.kr) 클라이언트 — 원생별 등하원 상태 조회.

agent API(ansim.py)와 달리 웹 포털 로그인 세션이 필요하다.
로그인 정보는 agent API 와 동일하게 config/ansimtalk.json 을 사용.

- 원생 구분: 출결번호 (STDINFO-DATA-KEYPAD_NUM, 4자리)
- 등하원 상태: STDINFO-DATA-ATTENDANCE_STATE
    ""=미등원, "1"=등원, "2"=하원, "3"=결석, "4"=공결, "5"=캠프
"""

import json
import re
import threading
from datetime import date
from pathlib import Path

import requests

BASE_URL = "https://ansimtalk.gg.go.kr"

HERE = Path(__file__).resolve().parent.parent  # src/ → 앱 루트
ANSIM_CONFIG_PATH = HERE / "config" / "ansimtalk.json"

STATE_LABELS = {
    "": "미등원",
    "1": "등원",
    "2": "하원",
    "3": "결석",
    "4": "공결",
    "5": "캠프",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ko-KR,ko;q=0.9",
    # loginok.asp 는 Referer 없으면 로그인을 거부함
    "Referer": BASE_URL + "/center_login.asp",
}

_session: requests.Session | None = None
_lock = threading.Lock()


class SessionExpired(RuntimeError):
    pass


def _load_credentials() -> tuple[str, str]:
    if not ANSIM_CONFIG_PATH.exists():
        raise RuntimeError(f"설정 파일 없음: {ANSIM_CONFIG_PATH}")
    with open(ANSIM_CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    uid = (cfg.get("user_id") or "").strip()
    pw = cfg.get("password") or ""
    if not uid or not pw:
        raise RuntimeError("config/ansimtalk.json 에 user_id/password 를 설정하세요")
    return uid, pw


def _login() -> requests.Session:
    """웹 포털 로그인 후 인증된 세션 반환."""
    uid, pw = _load_credentials()
    s = requests.Session()
    s.headers.update(_HEADERS)

    s.get(f"{BASE_URL}/center_login.asp", timeout=15)
    res = s.post(
        f"{BASE_URL}/login/loginok.asp",
        data={
            "GoUrl": "", "Phone": "", "PopupYN": "N", "pass_rule_err": "",
            "user_id": uid, "user_pass": pw,
        },
        timeout=15,
    )
    res.raise_for_status()
    # 성공 시 attendance.asp 로 보내는 스크립트가 응답됨
    if "attendance.asp" not in res.text:
        raise RuntimeError(f"웹 포털 로그인 실패 (id/pw 확인): {res.text[:200]!r}")
    return s


def _fetch(session: requests.Session, day: str) -> list[dict]:
    res = session.post(
        f"{BASE_URL}/content/attendance/attendance_count_ajax_list.asp",
        params={
            "search_att": "", "search_day": day, "searchDayView": "",
            "orderDirection": "student_name|^|asc", "search_value": "",
            "allClass": "", "search_class": "", "selectStudentName_hidden": "",
        },
        data=" ",
        timeout=15,
    )
    res.raise_for_status()
    res.encoding = "utf-8"
    body = res.text
    if "center_login.asp" in body and "stdinfo_" not in body:
        raise SessionExpired("세션 만료 — 재로그인 필요")

    out = []
    for attrs in re.findall(r'<div id="stdinfo_\d+"([^>]*)>', body, re.I):
        d = dict(re.findall(r'STDINFO-DATA-([A-Z_0-9]+)="([^"]*)"', attrs, re.I))
        state = (d.get("ATTENDANCE_STATE") or "").strip()
        in_time = (d.get("SDATE") or "").strip()
        out_time = (d.get("EDATE") or "").strip()
        if out_time == "30:00":
            out_time = ""
        label = STATE_LABELS.get(state, f"미상({state})")
        # 서버는 하원해도 ATTENDANCE_STATE 를 "1" 로 유지하고 EDATE 만 채움 —
        # 하원 여부는 하원시간 존재로 판정 (2026-07-20 실데이터로 확인)
        if state == "1" and out_time:
            label = "하원"
        out.append({
            "keypad": (d.get("KEYPAD_NUM") or "").strip(),
            "name": (d.get("STUDENT_NAME") or "").strip(),
            "class_name": (d.get("CLASS_NAME") or "").strip(),
            "state": state,
            "label": label,
            "in_time": "" if in_time == "30:00" else in_time,  # 30:00 = 시각 없음 표기
            "out_time": out_time,
        })
    return out


def fetch_students(day: str | None = None) -> list[dict]:
    """해당 날짜의 원생별 출결 정보 목록. 세션은 캐시하고 만료 시 재로그인."""
    global _session
    day = day or date.today().isoformat()
    with _lock:
        if _session is None:
            _session = _login()
        try:
            return _fetch(_session, day)
        except SessionExpired:
            _session = _login()
            return _fetch(_session, day)


def reset_session() -> None:
    """자격증명 변경 시 캐시된 세션 초기화 — 다음 호출에서 새로 로그인."""
    global _session
    with _lock:
        _session = None


def fetch_status_map(day: str | None = None) -> dict[str, dict]:
    """출결번호(keypad) → 출결 정보 dict."""
    return {s["keypad"]: s for s in fetch_students(day) if s["keypad"]}
