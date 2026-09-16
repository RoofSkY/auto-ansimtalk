"""아이파킹 STORE 포털(store.iparking.co.kr) 주차 할인권 자동 등록.

app.py 가 쓰는 인터페이스:
  TICKETS, find_in_cars(car4), find_in_car(car4, full_plate),
  get_applied_ticket_count(in_car), apply_discount(in_car, ttype)

브라우저(Playwright) 없이 순수 HTTP 로 로그인한다.
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
import urllib.parse
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

import jsonstore

ROOT_URL = "https://store.iparking.co.kr"
BASE_URL = ROOT_URL + "/parking-local-tenant-discount-managements"

HERE = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent
)
CONFIG_DIR = HERE / "config"
CONFIG_DIR.mkdir(exist_ok=True)
CONFIG_PATH = CONFIG_DIR / "iparking.json"

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

# discountId(=discountTicketId) 는 주차장별로 달라 런타임에 classification 으로 매칭한다.
# entry: 예약 화면의 매수 UI — "cycle"=클릭 순환 버튼, "number"=숫자 입력 칩.
# max(최대 적용 매수)와 분리해 두어야 상한만 조정해도 UI 모양이 바뀌지 않는다.
TICKETS = {
    "free": {
        "classification": "FREE", "label": "1시간 무료권",
        "max": 2, "entry": "cycle", "color": "#E7F3FF",
    },
    "paid": {
        "classification": "PAID", "label": "1시간 유료권",
        "max": 100, "entry": "number", "color": "#FFF5D8",
    },
}
DEFAULT_TICKET = "free"


class IparkingError(RuntimeError):
    """로그인/설정 등 복구 불가한 오류."""


class VehicleDetailUnavailable(IparkingError):
    """출차 또는 결제 중이라 주차권 상세를 조회할 수 없는 상태."""


# keep-alive 연결 재사용 — 차량 검색은 워커 8개로 병렬 실행되고, 그 와중에
# 차량등록 버튼이나 예약이 겹치면 기본 풀(10개)을 넘겨 초과 연결이 버려진다.
# 그러면 요청마다 TLS 핸드셰이크를 새로 해 사이클이 눈에 띄게 느려진다.
_session = requests.Session()
_session.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=16))
_login_lock = threading.Lock()

# Windows 루트 인증서가 오래된 PC 대비 — 기본 검증 실패 시 certifi 로 폴백 (updater 와 동일 정책)
try:
    import certifi
    _CA_BUNDLE = certifi.where()
except Exception:
    _CA_BUNDLE = True

_auth = {"access": None, "refresh": None, "plid": None, "gen": 0}
_tickets_by_class: dict[str, dict] = {}


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise IparkingError(f"자격증명 파일 없음: {CONFIG_PATH.name}")
    cfg = jsonstore.load(CONFIG_PATH)
    for k in ("store_id", "user_id", "password"):
        if not cfg.get(k):
            raise IparkingError(f"{CONFIG_PATH.name} 에 {k} 누락")
    return cfg


def _load_session() -> dict | None:
    return jsonstore.load(CONFIG_PATH).get("session")


def _save_session() -> None:
    """통합 파일의 session 키만 갱신 — 자격증명은 보존(락 안에서 읽고 쓴다)."""
    def _mutate(data: dict) -> None:
        data["session"] = {
            "access": _auth["access"], "refresh": _auth["refresh"], "plid": _auth["plid"],
        }
    try:
        jsonstore.update(CONFIG_PATH, _mutate, private=True)
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
    _auth["gen"] += 1
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
                _auth["gen"] += 1
                _save_session()
                return True
    except Exception:
        pass
    return False


def _renew_session(seen_gen: int) -> None:
    """만료된 세션을 한 번만 갱신한다.

    차량 검색은 8개 스레드로 병렬 실행되므로 토큰이 만료되면 여러 스레드가
    동시에 401 을 받는다. 각자 갱신하면 같은 refresh 토큰으로 중복 요청이 나가고
    (서버가 토큰을 회전시키면 뒷 요청은 실패) 불필요한 재로그인이 반복된다.
    락 안에서 세대(gen)를 확인해, 다른 스레드가 이미 갱신했으면 그대로 쓴다.
    """
    with _login_lock:
        if _auth["gen"] != seen_gen:
            return
        if _try_refresh():
            return
        _do_login()


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
        _auth["gen"] += 1
        _tickets_by_class.clear()
    def _drop_session(data: dict) -> None:
        data.pop("session", None)

    try:
        if CONFIG_PATH.exists():
            jsonstore.update(CONFIG_PATH, _drop_session, private=True)
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
    seen_gen = _auth["gen"]  # 요청에 쓴 토큰의 세대 — 만료 시 중복 갱신 판별용
    res = _request(
        method, BASE_URL + path,
        headers=_auth_headers(),
        params=params,
        data=json.dumps(json_body) if json_body is not None else None,
    )
    mutation = method.upper() == "POST" and path.endswith(("/bulk-apply", "/bulk-cancel"))
    if mutation and (res.status_code >= 500 or res.status_code == 408
                     or (res.status_code not in (401, 403) and _is_expired(res))):
        raise IparkingError("처리 응답을 확정할 수 없습니다. 등록 내역을 확인해 주세요.")
    if not _retried and _is_expired(res):
        _renew_session(seen_gen)
        return _api(method, path, params=params, json_body=json_body, _retried=True)
    return res


def _plid() -> str:
    ensure_session()
    return _auth["plid"]


def _load_store_tickets(force: bool = False) -> dict[str, dict]:
    """스토어 보유 할인권을 classification(FREE/PAID) 별로 캐시."""
    global _tickets_by_class
    if _tickets_by_class and not force:
        return _tickets_by_class
    res = _api("GET", f"/api/v1/stores/{_plid()}/discount-tickets/search")
    res.raise_for_status()
    data = res.json()
    if (res.headers.get("result-code") not in (None, "0000") or not isinstance(data, dict)
            or not isinstance(data.get("allocatedTicketList"), list)):
        raise IparkingError("보유 주차권 조회 응답 형식 오류")
    tickets = {}
    for t in data["allocatedTicketList"]:
        cls = t.get("discountClassification")
        if cls and cls not in tickets:
            tickets[cls] = t
    _tickets_by_class = tickets
    return _tickets_by_class


def find_in_cars(car_no4: str) -> list[dict]:
    """입차 차량 목록 (각 항목의 전체 번호판은 carNumber)."""
    res = _api("GET", f"/api/v1/stores/completions/{_plid()}/in/{car_no4}")
    res.raise_for_status()
    items = res.json() or []
    if not isinstance(items, list):
        items = items.get("list") or items.get("data") or []
    return items


def find_in_car(car_no4: str, full_plate: str | None = None) -> dict | None:
    items = find_in_cars(car_no4)
    if not items:
        return None
    if full_plate:
        for it in items:
            if (it.get("carNumber") or "") == full_plate:
                return it
        return None
    return items[0]


def get_vehicle_detail(in_car: dict) -> dict:
    history_id = in_car.get("parkingHistoryId")
    if history_id is None or history_id == "":
        raise IparkingError("차량 식별자(parkingHistoryId) 없음")
    history_path = urllib.parse.quote(str(history_id), safe="")
    res = _api("GET", f"/api/v2/stores/completions/{_plid()}/detail/{history_path}")
    code = res.headers.get("result-code")
    unavailable = {
        "1303": "이미 출차한 차량은 주차권 내역을 조회할 수 없습니다.",
        "1407": "주차요금 결제 중에는 주차권 내역을 조회할 수 없습니다.",
    }
    if code in unavailable and (res.status_code == 400 or res.ok):
        raise VehicleDetailUnavailable(unavailable[code])
    if not res.ok or code not in (None, "0000"):
        message = _result_message(res) or "서버가 상세 조회를 거절했습니다."
        raise IparkingError(
            f"등록 주차권 조회 실패 (HTTP {res.status_code}, result-code={code}): {message}"
        )
    res.raise_for_status()
    data = res.json()
    fields = ("myStoreApplyRequestTicketInfoList", "otherStoreApplyRequestTicketInfoList")
    if not isinstance(data, dict) or any(field not in data for field in fields):
        raise IparkingError("등록 주차권 조회 응답 형식 오류")
    if data.get("parkingHistoryId") is not None and str(data["parkingHistoryId"]) != str(history_id):
        raise IparkingError("등록 주차권의 입차 이력이 일치하지 않음")
    return data


def get_applied_ticket_count(in_car: dict) -> int:
    """현재 입차 건에 모든 스토어가 실제 적용한 무료·유료 주차권의 합계."""
    data = get_vehicle_detail(in_car)
    fields = ("myStoreApplyRequestTicketInfoList", "otherStoreApplyRequestTicketInfoList")
    total = 0
    for field in fields:
        applied = data[field]
        if applied is None:
            applied = []
        if not isinstance(applied, list):
            raise IparkingError("등록 주차권 목록 형식 오류")
        for ticket in applied:
            count = ticket.get("applyCount") if isinstance(ticket, dict) else None
            if isinstance(count, str) and count.isascii() and count.isdecimal():
                count = int(count)
            if type(count) is not int or count < 0:
                raise IparkingError("등록 주차권 수량 형식 오류")
            total += count
    return total


def _quantity(value):
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    return value if type(value) is int and value >= 0 else None


def manual_inventory() -> list[dict]:
    tickets = dict(_load_store_tickets(force=True))
    return [{"key": key, "label": ticket.get("discountName") or spec["label"],
             "remaining": _quantity(ticket.get("remainingQuantity")) if ticket else 0}
            for key, spec in TICKETS.items()
            for ticket in [tickets.get(spec["classification"], {})]]


def manual_vehicle(in_car: dict) -> dict:
    data = get_vehicle_detail(in_car)
    if data.get("carNumber") != in_car.get("carNumber"):
        raise IparkingError("조회 차량번호가 일치하지 않습니다. 다시 조회해 주세요.")
    applied = []
    for field in ("myStoreApplyRequestTicketInfoList", "otherStoreApplyRequestTicketInfoList"):
        entries = data[field] if data[field] is not None else []
        if not isinstance(entries, list):
            raise IparkingError("등록 주차권 목록 형식 오류")
        for ticket in entries:
            count = _quantity(ticket.get("applyCount")) if isinstance(ticket, dict) else None
            if count is None:
                raise IparkingError("등록 주차권 수량 형식 오류")
            if count:
                other_store = field.startswith("other")
                discount_id = ticket.get("discountId")
                cancellable = not other_store and not data.get("isConsumed") and bool(discount_id)
                applied.append({"label": ticket.get("discountName") or "주차권", "count": count,
                                "other_store": other_store,
                                "key": str(discount_id) if cancellable else None,
                                "cancel_max": min(count, 100) if cancellable else 0,
                                "ticket": {"discountId": discount_id, "discountName": ticket.get("discountName")}})
    enabled = data.get("enableAllocatedTicketInfoList")
    disabled = data.get("disableAllocatedTicketInfoList")
    if not isinstance(enabled, list) or not isinstance(disabled, list):
        raise IparkingError("등록 가능 주차권 조회 형식 오류")
    tickets = []
    for key, spec in TICKETS.items():
        options = [t for t in enabled if t.get("discountClassification") == spec["classification"]]
        ticket = next(iter(options), None)
        stock_ticket = ticket or next((t for t in disabled if t.get("discountClassification") == spec["classification"]), {})
        remaining = _quantity(stock_ticket.get("remainingQuantity")) if stock_ticket else 0
        available = _quantity(ticket.get("availableApplyCount")) if ticket else 0
        maximum = min(spec["max"], remaining or 0, available or 0)
        if data.get("isConsumed") or not ticket or not ticket.get("discountId"):
            maximum = 0
        tickets.append({"key": key, "label": stock_ticket.get("discountName") or spec["label"],
                        "remaining": remaining, "max": maximum, "ticket": ticket})
    return {"plate": data["carNumber"], "history_id": str(in_car["parkingHistoryId"]),
            "entered_at": data.get("inCarDateTime") or in_car.get("inCarDateTime") or "",
            "minutes": _quantity(data.get("totalInParkingTime")), "applied": applied, "tickets": tickets,
            "inventory": _inventory_from_detail(enabled + disabled)}


def _inventory_from_detail(rows):
    """보유 목록에서 확인한 권종 ID의 잔여 수량만 상세 응답에서 재사용한다."""
    catalog = _tickets_by_class
    inventory = []
    for key, spec in TICKETS.items():
        stock = catalog.get(spec['classification'], {})
        discount_id = stock.get('discountId')
        matches = [row for row in rows if discount_id and row.get('discountId') == discount_id
                   and row.get('discountClassification') == spec['classification']]
        if not matches:
            return None
        amounts = {_quantity(row.get('remainingQuantity')) for row in matches}
        if None in amounts or len(amounts) != 1:
            return None
        inventory.append({'key': key, 'label': matches[0].get('discountName') or stock.get('discountName') or spec['label'],
                          'remaining': amounts.pop()})
    return inventory


def _result_message(res: requests.Response) -> str:
    """응답의 서버 메시지. 성공은 본문 resultMessage, 실패는 Result-Message 헤더.

    실패 응답은 본문이 비고 헤더에만 메시지가 오며, percent-encoding
    (공백은 +, 줄바꿈 포함)이라 unquote_plus 로 풀고 한 줄로 만든다.
    """
    if res.ok:
        try:
            msg = (res.json() or {}).get("resultMessage") or ""
            if msg:
                return msg
        except ValueError:
            pass
    raw = res.headers.get("result-message") or ""
    if raw:
        try:
            return " ".join(urllib.parse.unquote_plus(raw).split())
        except Exception:
            pass
    return ""


def apply_discount(in_car: dict, ticket_type: str = DEFAULT_TICKET,
                   count: int = 1, *, ticket: dict | None = None) -> tuple[bool, str]:
    """할인권 적용. count 는 bulk-apply 의 applyCount 로 전달 — 취소(bulk-cancel 의
    applyCancelCount)와 같은 방식이라 여러 장도 한 번의 호출로 등록된다."""
    spec = TICKETS.get(ticket_type)
    if not spec:
        return False, f"알 수 없는 권종: {ticket_type}"

    if ticket is None:
        try:
            store_tickets = _load_store_tickets()
        except Exception as e:
            return False, f"할인권 목록 조회 실패: {e}"
        ticket = store_tickets.get(spec["classification"])
    if not ticket:
        return False, f"{spec['label']} 이 스토어에 없음"

    discount_id = ticket.get("discountId")
    plid = _plid()
    car_number = in_car.get("carNumber") or ""
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
    if not vres.ok or vres.headers.get("result-code") not in (None, "0000"):
        return False, _result_message(vres) or _apply_error_message(vres)

    abody = {
        "parkingHistoryId": phid,
        "parkingLotId": plid,
        "carNumber": car_number,
        "discountTicketId": discount_id,
        "applyCount": count,
        "memo": "",
    }
    ares = _api(
        "POST",
        f"/api/v1/stores/discounts/{plid}/discount-tickets/tickets/bulk-apply",
        json_body=abody,
    )
    if ares.status_code >= 500 or ares.status_code == 408:
        raise IparkingError("등록 응답을 확정할 수 없습니다. 등록 내역을 확인해 주세요.")
    if ares.ok and ares.headers.get("result-code") in (None, "0000"):
        return True, _result_message(ares) or "등록 성공"
    return False, _result_message(ares) or _apply_error_message(ares)


def cancel_discount(in_car: dict, ticket_type: str = DEFAULT_TICKET, count: int = 1,
                    *, ticket: dict | None = None) -> tuple[bool, str]:
    """현재 입차 건에 적용된 할인권을 검증한 후 지정 수량만 취소한다."""
    if type(count) is not int or not 1 <= count <= 100:
        return False, "취소할 수량을 확인해 주세요."
    spec = TICKETS.get(ticket_type)
    if not spec:
        return False, f"알 수 없는 권종: {ticket_type}"
    if ticket is None:
        try:
            ticket = _load_store_tickets().get(spec["classification"])
        except Exception as e:
            return False, f"할인권 목록 조회 실패: {e}"
    if not ticket:
        return False, f"{spec['label']} 이 스토어에 없음"

    discount_id = ticket.get("discountId")
    plid = _plid()
    car_number = in_car.get("carNumber") or ""
    phid = in_car.get("parkingHistoryId")
    if not phid:
        return False, "차량 식별자(parkingHistoryId) 없음"

    if not discount_id:
        return False, "취소할 주차권 식별자가 없습니다."
    body = {"parkingLotId": plid, "parkingHistoryId": phid,
            "discountTicketId": discount_id, "carNumber": car_number}
    validation = _api("POST", f"/api/v1/stores/discounts/{plid}/discount-tickets/tickets/bulk-cancel/validate",
                      json_body=body)
    if not validation.ok or validation.headers.get("result-code") not in (None, "0000"):
        return False, _result_message(validation) or "취소할 수 없는 주차권입니다."
    res = _api(
        "POST",
        f"/api/v1/stores/discounts/{plid}/discount-tickets/tickets/bulk-cancel",
        json_body={**body, "applyCancelCount": count, "memo": ""},
    )
    if res.ok and res.headers.get("result-code") in (None, "0000"):
        return True, "할인권이 취소되었습니다."
    return False, _result_message(res) or f"취소 실패 (result-code={res.headers.get('result-code')}, HTTP {res.status_code})"


def _apply_error_message(res: requests.Response) -> str:
    """서버가 Result-Message 를 주지 않은 실패의 최종 폴백 — 진단 정보만 남긴다."""
    rc = res.headers.get("result-code")
    return f"등록 실패 (result-code={rc}, HTTP {res.status_code})"


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
    ok, msg = apply_discount(in_car, ticket_type, count=count)
    print(f"  {'✓' if ok else '✗'} {msg}")


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
