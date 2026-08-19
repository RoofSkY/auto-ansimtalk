"""아이파킹 STORE 포털(store.iparking.co.kr) 주차 할인권 자동 등록.

기존 npdc.py(나이스파크) 를 대체 — server.py 가 쓰는 인터페이스와 호환:
  TICKETS, find_in_cars(car4), find_in_car(car4, full_plate), apply_discount(in_car, ttype)

나이스파크와 달리 브라우저(Playwright) 없이 순수 HTTP 로 로그인한다.
자격증명+세션: config/iparking.json  { store_id, user_id, password, session:{access,refresh,plid} }

권종:
  free — 1시간 무료권 (discountClassification=FREE)
  paid — 1시간 유료권 (discountClassification=PAID)
  실제 discountTicketId 는 주차장마다 다르므로 로그인 후 조회로 확보한다.

사용법(CLI 테스트):
  python iparking.py <차량번호4자리>            # 무료권 1장
  python iparking.py <차량번호4자리> paid        # 유료권 1장
  python iparking.py <차량번호4자리> paid 2      # 유료권 2장
  python iparking.py --login                    # 로그인/티켓 조회만 확인
"""

import base64
import hashlib
import json
import sys
import threading
import time
from pathlib import Path

import requests

ROOT_URL = "https://store.iparking.co.kr"
BASE_URL = ROOT_URL + "/parking-local-tenant-discount-managements"

HERE = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
CONFIG_DIR = HERE / "config"
CONFIG_DIR.mkdir(exist_ok=True)
CONFIG_PATH = CONFIG_DIR / "iparking.json"  # 자격증명 + 세션 통합

# 구버전 분리 파일(iparking_config.json + iparking_session.json)을 통합 파일로 이관
_legacy_cfg = CONFIG_DIR / "iparking_config.json"
_legacy_sess = CONFIG_DIR / "iparking_session.json"
if (_legacy_cfg.exists() or _legacy_sess.exists()) and not CONFIG_PATH.exists():
    try:
        merged = {}
        if _legacy_cfg.exists():
            with open(_legacy_cfg, encoding="utf-8") as f:
                merged.update(json.load(f))
        if _legacy_sess.exists():
            with open(_legacy_sess, encoding="utf-8") as f:
                merged["session"] = json.load(f)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        _legacy_cfg.unlink(missing_ok=True)
        _legacy_sess.unlink(missing_ok=True)
    except Exception:
        pass

COMMON_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": ROOT_URL,
    "Referer": ROOT_URL + "/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

# server.py 가 참조하는 권종 테이블 — npdc.TICKETS 와 동일한 구조.
# discountId(=discountTicketId) 는 주차장별로 달라 런타임에 classification 으로 매칭한다.
TICKETS = {
    "free": {
        "classification": "FREE", "label": "1시간 무료권",
        "max": 2, "color": "#E7F3FF",
    },
    "paid": {
        "classification": "PAID", "label": "1시간 유료권",
        "max": 2, "color": "#FFF5D8",
    },
}
DEFAULT_TICKET = "free"


class IparkingError(RuntimeError):
    """로그인/설정 등 복구 불가한 오류."""


# ---------- 세션(HTTP) ----------
_session = requests.Session()
_login_lock = threading.Lock()

# Windows 루트 인증서가 오래된 PC 대비 — 기본 검증 실패 시 certifi 로 폴백 (npdc/updater 동일 정책)
try:
    import certifi
    _CA_BUNDLE = certifi.where()
except Exception:
    _CA_BUNDLE = True

_auth = {"access": None, "refresh": None, "plid": None}
_tickets_by_class: dict[str, dict] = {}


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise IparkingError(f"자격증명 파일 없음: {CONFIG_PATH.name}")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    for k in ("store_id", "user_id", "password"):
        if not cfg.get(k):
            raise IparkingError(f"{CONFIG_PATH.name} 에 {k} 누락")
    return cfg


def _load_session() -> dict | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f).get("session")
    except Exception:
        return None


def _save_session() -> None:
    """통합 파일의 session 키만 갱신 — 자격증명은 보존."""
    try:
        data = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        data["session"] = {"access": _auth["access"], "refresh": _auth["refresh"], "plid": _auth["plid"]}
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _encrypt_password(pw: str) -> str:
    """브라우저와 동일: base64(sha256_hex(password))."""
    return base64.b64encode(hashlib.sha256(pw.encode()).hexdigest().encode()).decode()


def _request(method: str, url: str, **kw) -> requests.Response:
    """SSL 검증 폴백 포함 요청."""
    kw.setdefault("timeout", 10)
    try:
        return _session.request(method, url, verify=_CA_BUNDLE, **kw)
    except requests.exceptions.SSLError:
        if _CA_BUNDLE is not True:
            raise
        try:
            import certifi
            return _session.request(method, url, verify=certifi.where(), **kw)
        except Exception:
            raise


def _auth_headers() -> dict:
    h = dict(COMMON_HEADERS)
    if _auth["access"]:
        h["Authorization"] = _auth["access"]
    if _auth["refresh"]:
        h["Refresh-Token"] = _auth["refresh"]
    return h


def _do_login() -> None:
    cfg = _load_config()
    body = {
        "parkingLotId": cfg["store_id"],
        "storeAccountId": cfg["user_id"],
        "storePassword": _encrypt_password(cfg["password"]),
    }
    res = _request(
        "POST", f"{BASE_URL}/api/v1/auth/login-v2",
        headers=COMMON_HEADERS, data=json.dumps(body),
    )
    rc = res.headers.get("result-code")
    if not res.ok or rc not in (None, "0000"):
        msg = {
            "INVALID_STORE_ACCOUNT": "아이디 또는 비밀번호가 일치하지 않습니다",
            "STORE_ACCOUNT_NOT_EXIST": "존재하지 않는 스토어 계정입니다",
            "PASSWORD_MISMATCH": "아이디 또는 비밀번호가 일치하지 않습니다",
        }.get(rc, f"로그인 실패 (result-code={rc}, HTTP {res.status_code})")
        raise IparkingError(msg)
    data = res.json()
    _auth["access"] = data.get("accessToken")
    _auth["refresh"] = data.get("refreshToken")
    _auth["plid"] = cfg["store_id"]
    if not _auth["access"]:
        raise IparkingError("로그인 응답에 accessToken 없음")
    _save_session()
    _tickets_by_class.clear()


def _try_refresh() -> bool:
    if not _auth["refresh"]:
        return False
    try:
        res = _request(
            "POST", f"{BASE_URL}/api/v1/auth/refresh/token",
            headers=COMMON_HEADERS, data=json.dumps({"refreshToken": _auth["refresh"]}),
        )
        if res.ok:
            data = res.json()
            if data.get("accessToken"):
                _auth["access"] = data["accessToken"]
                _auth["refresh"] = data.get("refreshToken") or _auth["refresh"]
                _save_session()
                return True
    except Exception:
        pass
    return False


def ensure_session() -> None:
    """토큰이 없으면 캐시 로드 → 그래도 없으면 로그인."""
    if _auth["access"]:
        return
    with _login_lock:
        if _auth["access"]:
            return
        cached = _load_session()
        if cached and cached.get("access"):
            _auth.update({
                "access": cached.get("access"),
                "refresh": cached.get("refresh"),
                "plid": cached.get("plid"),
            })
            return
        _do_login()


def reset_session() -> None:
    """세션 토큰·티켓 캐시를 버림 (계정 변경 시). 자격증명은 보존, 다음 요청에서 재로그인."""
    with _login_lock:
        _auth["access"] = None
        _auth["refresh"] = None
        _auth["plid"] = None
        _tickets_by_class.clear()
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            data.pop("session", None)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def relogin() -> None:
    """세션을 버리고 자격증명으로 강제 재로그인 (설정 페이지 계정 저장 후 검증용)."""
    with _login_lock:
        _auth["access"] = None
        _auth["refresh"] = None
        _do_login()


def _is_expired(res: requests.Response) -> bool:
    if res.status_code in (401, 403):
        return True
    ct = res.headers.get("Content-Type", "").lower()
    # API 게이트웨이 밖으로 나가면 SPA HTML 이 돌아옴 = 인증/경로 문제
    return "html" in ct and "json" not in ct


def _api(method: str, path: str, *, params=None, json_body=None, _retried=False) -> requests.Response:
    ensure_session()
    res = _request(
        method, BASE_URL + path,
        headers=_auth_headers(),
        params=params,
        data=json.dumps(json_body) if json_body is not None else None,
    )
    if not _retried and _is_expired(res):
        if not _try_refresh():
            with _login_lock:
                _do_login()
        return _api(method, path, params=params, json_body=json_body, _retried=True)
    return res


def _plid() -> str:
    ensure_session()
    return _auth["plid"]


# ---------- 권종(할인권) 조회 ----------
def _load_store_tickets(force: bool = False) -> dict[str, dict]:
    """스토어 보유 할인권을 classification(FREE/PAID) 별로 캐시."""
    if _tickets_by_class and not force:
        return _tickets_by_class
    res = _api("GET", f"/api/v1/stores/{_plid()}/discount-tickets/search")
    res.raise_for_status()
    data = res.json()
    _tickets_by_class.clear()
    for t in (data.get("allocatedTicketList") or []):
        cls = t.get("discountClassification")
        if cls and cls not in _tickets_by_class:
            _tickets_by_class[cls] = t
    return _tickets_by_class


# ---------- 차량 조회 ----------
def find_in_cars(car_no4: str) -> list[dict]:
    """입차 차량 목록. 각 항목에 npdc 호환용 carNo(전체 번호판) 별칭 추가."""
    res = _api("GET", f"/api/v1/stores/completions/{_plid()}/in/{car_no4}")
    res.raise_for_status()
    items = res.json() or []
    if not isinstance(items, list):
        items = items.get("list") or items.get("data") or []
    for it in items:
        if "carNo" not in it:
            it["carNo"] = it.get("carNumber") or ""
    return items


def find_in_car(car_no4: str, full_plate: str | None = None) -> dict | None:
    items = find_in_cars(car_no4)
    if not items:
        return None
    if full_plate:
        for it in items:
            if (it.get("carNo") or it.get("carNumber") or "") == full_plate:
                return it
        return None
    return items[0]


# ---------- 할인권 적용 ----------
def apply_discount(in_car: dict, ticket_type: str = DEFAULT_TICKET) -> tuple[bool, str]:
    spec = TICKETS.get(ticket_type)
    if not spec:
        return False, f"알 수 없는 권종: {ticket_type}"

    try:
        store_tickets = _load_store_tickets()
    except Exception as e:
        return False, f"할인권 목록 조회 실패: {e}"
    ticket = store_tickets.get(spec["classification"])
    if not ticket:
        return False, f"{spec['label']} 이 스토어에 없음"

    discount_id = ticket.get("discountId")
    plid = _plid()
    car_number = in_car.get("carNumber") or in_car.get("carNo") or ""
    phid = in_car.get("parkingHistoryId")
    if not phid:
        return False, "차량 식별자(parkingHistoryId) 없음"

    vbody = {
        "parkingLotId": plid,
        "parkingHistoryId": phid,
        "discountTicketId": discount_id,
        "carNumber": car_number,
    }
    vres = _api(
        "POST",
        f"/api/v1/stores/discounts/{plid}/discount-tickets/tickets/bulk-apply/validate",
        json_body=vbody,
    )
    if not vres.ok:
        return False, _apply_error_message(vres, spec)

    abody = {
        "parkingHistoryId": phid,
        "parkingLotId": plid,
        "carNumber": car_number,
        "discountTicketId": discount_id,
        "applyCount": 1,
        "memo": "",
    }
    ares = _api(
        "POST",
        f"/api/v1/stores/discounts/{plid}/discount-tickets/tickets/bulk-apply",
        json_body=abody,
    )
    if ares.ok:
        return True, f"{spec['label']} 등록 성공"
    return False, _apply_error_message(ares, spec)


def cancel_discount(in_car: dict, ticket_type: str = DEFAULT_TICKET, count: int = 1) -> tuple[bool, str]:
    """적용된 할인권 취소 — 테스트 후 되돌리기용."""
    spec = TICKETS.get(ticket_type)
    if not spec:
        return False, f"알 수 없는 권종: {ticket_type}"
    try:
        ticket = _load_store_tickets().get(spec["classification"])
    except Exception as e:
        return False, f"할인권 목록 조회 실패: {e}"
    if not ticket:
        return False, f"{spec['label']} 이 스토어에 없음"

    discount_id = ticket.get("discountId")
    plid = _plid()
    car_number = in_car.get("carNumber") or in_car.get("carNo") or ""
    phid = in_car.get("parkingHistoryId")
    if not phid:
        return False, "차량 식별자(parkingHistoryId) 없음"

    body = {
        "parkingLotId": plid,
        "parkingHistoryId": phid,
        "discountTicketId": discount_id,
        "carNumber": car_number,
        "applyCancelCount": count,
        "memo": "",
    }
    res = _api(
        "POST",
        f"/api/v1/stores/discounts/{plid}/discount-tickets/tickets/bulk-cancel",
        json_body=body,
    )
    if res.ok:
        return True, f"{spec['label']} {count}장 취소 성공"
    return False, _apply_error_message(res, spec)


def _apply_error_message(res: requests.Response, spec: dict) -> str:
    rc = res.headers.get("result-code")
    table = {
        "70200": "해당 할인권의 잔여 개수가 없습니다",
        "70100": f"{spec['label']} 최대 적용 개수를 초과했습니다",
        "70104": "할인권을 적용할 수 없습니다",
        "1407": "주차요금 결제 진행 중이라 적용 불가",
        "1303": "이미 출차한 차량이라 적용 불가",
        "1416": "출차 대기 중인 차량이라 적용 불가",
    }
    if rc in table:
        return table[rc]
    return f"등록 실패 (result-code={rc}, HTTP {res.status_code})"


# ---------- CLI ----------
def register(car_no4: str, ticket_type: str = DEFAULT_TICKET, count: int = 1) -> None:
    car_no4 = car_no4.strip()
    if not (car_no4.isdigit() and len(car_no4) == 4):
        print("주차번호는 4자리 숫자여야 합니다.")
        return
    if ticket_type not in TICKETS:
        print(f"알 수 없는 권종: {ticket_type!r}. 가능: {', '.join(TICKETS)}")
        return

    spec = TICKETS[ticket_type]
    print(f"[1/2] 차량번호 {car_no4} 입차 정보 조회 중...")
    in_car = find_in_car(car_no4)
    if in_car is None:
        print(f"  ✗ 입차된 차량을 찾을 수 없습니다 (4자리: {car_no4})")
        return
    print(f"  ✓ 찾음: {in_car.get('carNumber')} / 입차 {in_car.get('basicInfo', {}).get('inDateTime', '?')}")

    print(f"[2/2] {spec['label']} × {count} 적용 중...")
    ok_count = 0
    for i in range(count):
        ok, msg = apply_discount(in_car, ticket_type)
        tag = f"  [{i+1}/{count}]" if count > 1 else "  "
        print(f"{tag} {'✓' if ok else '✗'} {msg}")
        if ok:
            ok_count += 1
        else:
            break
    if count > 1:
        print(f"  → 총 {ok_count}/{count} 장 등록됨")


def selftest(car_no4: str, ticket_type: str = DEFAULT_TICKET) -> None:
    """전체 흐름 안전 검증: 조회 → 1장 등록 → 즉시 취소 (재고 소모 없음)."""
    spec = TICKETS[ticket_type]
    print(f"[1/3] 차량번호 {car_no4} 입차 정보 조회...")
    in_car = find_in_car(car_no4)
    if in_car is None:
        print(f"  ✗ 입차된 차량 없음 (4자리: {car_no4})")
        return
    print(f"  ✓ {in_car.get('carNumber')} / 입차 {in_car.get('basicInfo', {}).get('inDateTime', '?')}")

    print(f"[2/3] {spec['label']} 1장 등록...")
    ok, msg = apply_discount(in_car, ticket_type)
    print(f"  {'✓' if ok else '✗'} {msg}")
    if not ok:
        return

    print(f"[3/3] 방금 등록분 취소(되돌리기)...")
    ok, msg = cancel_discount(in_car, ticket_type, 1)
    print(f"  {'✓' if ok else '✗'} {msg}")
    print("→ 전체 흐름 검증 완료" if ok else "→ 등록은 됐으나 취소 실패 — 수동 확인 필요")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in ("--login", "-l"):
        ensure_session()
        tickets = _load_store_tickets(force=True)
        print(f"로그인 OK — 주차장 {_auth['plid']}")
        for cls, t in tickets.items():
            print(f"  [{cls}] {t.get('discountName')} · id={t.get('discountId')} · 잔여 {t.get('remainingQuantity')}")
        return
    if args and args[0] in ("--selftest", "-t"):
        if len(args) < 2:
            print("사용법: python iparking.py --selftest <차량4자리> [free|paid]")
            return
        try:
            selftest(args[1], args[2] if len(args) > 2 else DEFAULT_TICKET)
        except IparkingError as e:
            print(f"  ✗ {e}")
        return
    if not args:
        print("사용법: python iparking.py <차량4자리> [free|paid] [매수]")
        print("        python iparking.py --login              # 로그인/티켓 확인")
        print("        python iparking.py --selftest <차량4자리>  # 등록→즉시취소 안전검증")
        return
    car_no4 = args[0]
    ticket_type = args[1] if len(args) > 1 else DEFAULT_TICKET
    count = int(args[2]) if len(args) > 2 else 1
    try:
        register(car_no4, ticket_type, count)
    except IparkingError as e:
        print(f"  ✗ {e}")
    except requests.RequestException as e:
        print(f"  ✗ 네트워크 오류: {e}")


if __name__ == "__main__":
    main()
