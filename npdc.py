"""주차번호 입력 시 주차할인권을 자동으로 등록하는 프로그램.

권종:
  free  — 2시간 무료권 (기본, 최대 1매)
  paid  — 1시간 유료권 (최대 3매)

사용법:
  python npdc.py <차량번호4자리>              # 2시간 무료 1장
  python npdc.py <차량번호4자리> paid          # 1시간 유료 1장
  python npdc.py <차량번호4자리> paid 3        # 1시간 유료 3장
"""

import sys
import json
import requests

from auth import ensure_session, cookies_to_header, login, load_cookies


BASE_URL = "https://npdc-i.nicepark.co.kr"

COMMON_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": 'application/json; charset="UTF-8"',
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

TRAN = {
    "T_MENU_ID": "500060",
    "T_PGM_ID": "DCWD001M01",
    "T_SYS_CD": "NPDC",
}

TICKETS = {
    "free": {
        "knd": "10000120", "settle": "0", "name": "2시간",
        "label": "2시간 무료권",
    },
    "paid": {
        "knd": "10000061", "settle": "1", "name": "1시간",
        "label": "1시간 유료권",
    },
}
DEFAULT_TICKET = "free"


def _is_session_expired(res: requests.Response) -> bool:
    ct = res.headers.get("Content-Type", "").lower()
    if "html" in ct and "json" not in ct:
        return True
    if res.status_code in (401, 403):
        return True
    return False


def _post(path: str, payload: dict, submission_id: str,
          _retried: bool = False) -> requests.Response:
    cookies = load_cookies() or ensure_session()
    headers = dict(COMMON_HEADERS)
    headers["Cookie"] = cookies_to_header(cookies)
    headers["submissionid"] = submission_id

    res = requests.post(
        BASE_URL + path,
        headers=headers,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=10,
    )

    if not _retried and _is_session_expired(res):
        print("  ! 세션 만료 감지 — 재로그인 후 재시도")
        login()
        return _post(path, payload, submission_id, _retried=True)
    return res


def find_in_cars(car_no4: str) -> list[dict]:
    submission_id = "mf_wfm_body_sbm_selectPtnrNonInCarList"
    payload = {
        "dma_search": {
            "carNo4": car_no4,
            "nRecordCnt": 10,
            "nCurPage": 1,
            "strFirstYn": "Y",
        },
        "dma_tran": {**TRAN, "T_SBM_CD": submission_id},
    }
    res = _post("/wd/dc/selectPtnrNonInCarList.do", payload, submission_id)
    res.raise_for_status()
    data = res.json()
    return data.get("dlt_list") or []


def find_in_car(car_no4: str, full_plate: str | None = None) -> dict | None:
    items = find_in_cars(car_no4)
    if not items:
        return None
    if full_plate:
        for it in items:
            if (it.get("carNo") or "") == full_plate:
                return it
        return None
    return items[0]


def apply_discount(in_car: dict, ticket_type: str = DEFAULT_TICKET) -> tuple[bool, str]:
    ticket = TICKETS[ticket_type]
    submission_id = "mf_wfm_body_sbm_useOrCancelDiscountTk"
    payload = {
        "dma_search": {
            "type": "use",
            "carParkNo": in_car["parkNo"],
            "inCarDt": in_car["inCarDt"],
            "inCarSeqNo": in_car["inCarSeqNo"],
            "discountTkKnd": ticket["knd"],
            "discountsettlementTy": ticket["settle"],
            "niceMacNo": in_car["niceMacNo"],
            "remark": "",
            "discountNm": ticket["name"],
        },
        "dma_tran": {**TRAN, "T_SBM_CD": submission_id},
    }
    res = _post("/wd/dc/useOrCancelDiscountTk.do", payload, submission_id)

    if res.status_code == 200:
        return True, f"{ticket['label']} 등록 성공"

    try:
        err = res.json()
    except ValueError:
        return False, f"HTTP {res.status_code}: {res.text[:200]}"

    code = err.get("errorMsgCode", "")
    contents = err.get("errorMsgContents", [])
    if code == "ERRO_60024":
        return False, f"무료권 사용 한도 초과 (그룹 [{contents[0] if contents else '?'}] 최대 {contents[1] if len(contents) > 1 else '?'}매)"
    if code == "ERRO_60053":
        limit = contents[0] if contents else "?"
        return False, f"이 할인권의 최대 사용매수({limit}매)를 초과했습니다"
    return False, f"등록 실패: {code} {contents}"


def register(car_no4: str, ticket_type: str = DEFAULT_TICKET, count: int = 1) -> None:
    car_no4 = car_no4.strip()
    if not (car_no4.isdigit() and len(car_no4) == 4):
        print("주차번호는 4자리 숫자여야 합니다.")
        return
    if ticket_type not in TICKETS:
        print(f"알 수 없는 권종: {ticket_type!r}. 가능: {', '.join(TICKETS)}")
        return
    if count < 1:
        print("매수는 1 이상이어야 합니다.")
        return

    ticket = TICKETS[ticket_type]

    print(f"[1/2] 차량번호 {car_no4} 입차 정보 조회 중...")
    in_car = find_in_car(car_no4)
    if in_car is None:
        print(f"  ✗ 입차된 차량을 찾을 수 없습니다 (차량번호 4자리: {car_no4})")
        return
    print(f"  ✓ 찾음: {in_car['carNo']} / 입차 {in_car['inCarDtm']} / {in_car['parkNm']}")

    print(f"[2/2] {ticket['label']} × {count} 적용 중...")
    success = 0
    for i in range(count):
        ok, msg = apply_discount(in_car, ticket_type)
        tag = f"  [{i+1}/{count}]" if count > 1 else "  "
        print(f"{tag} {'✓' if ok else '✗'} {msg}")
        if ok:
            success += 1
        else:
            break

    if count > 1:
        print(f"  → 총 {success}/{count} 장 등록됨")


def _parse_input(parts: list[str]) -> tuple[str, str, int]:
    car_no4 = parts[0]
    ticket_type = parts[1] if len(parts) > 1 else DEFAULT_TICKET
    count = int(parts[2]) if len(parts) > 2 else 1
    return car_no4, ticket_type, count


def main() -> None:
    if len(sys.argv) > 1:
        car_no4, ticket_type, count = _parse_input(sys.argv[1:])
        register(car_no4, ticket_type, count)
        return

    print("입력 형식: <차량번호4자리> [free|paid] [매수]")
    print("  예) 1628          → 2시간 무료권")
    print("      1628 paid     → 1시간 유료권 1장")
    print("      1628 paid 3   → 1시간 유료권 3장")
    print("  종료: q")

    while True:
        try:
            line = input("\n주차번호: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.lower() in ("q", "quit", "exit"):
            break
        if not line:
            continue
        try:
            car_no4, ticket_type, count = _parse_input(line.split())
        except (ValueError, IndexError) as e:
            print(f"  ✗ 입력 형식 오류: {e}")
            continue
        try:
            register(car_no4, ticket_type, count)
        except requests.RequestException as e:
            print(f"  ✗ 네트워크 오류: {e}")


if __name__ == "__main__":
    main()
