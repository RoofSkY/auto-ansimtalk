"""안심톡 출결 등록 API 클라이언트.

agent-ansimtalk.gg.go.kr 서버와 직접 HTTP 통신해서 등하원을 등록.

데이터 파일:
    config/ansim_config.json   - 로그인 정보
    config/ansim_session.json  - 세션 쿠키 캐시

CLI:
    python ansim.py 1234       # 등하원 등록
    python ansim.py --setup    # 설정 파일 안내
"""

import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 패키지가 필요합니다: pip install requests")
    sys.exit(1)


BASE_URL = "http://agent-ansimtalk.gg.go.kr"
USER_AGENT = "Microsoft URL Control - 6.01.9782"

_HEADERS = {
    "Accept": "image/gif, image/x-xbitmap, image/jpeg, image/pjpeg, */*",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": USER_AGENT,
    "Cache-Control": "no-cache",
}

# 캡처한 패킷의 고정 값 그대로 — 의미는 _dev/ansim-packets/ 참고
_FIXED_PARAMS = {
    "msg_gubun": "1",
    "destphone": "",
    "mode": "insert_m",
    "Hw_Gubun": "2",
    "re_time": "20",
}

# 자격증명은 ansim_config.json 에서 읽음.
# program / customer_id 는 로그인 직후 member_list.asp 로 자동 조회.
DEFAULT_CONFIG = {
    "user_id": "",
    "password": "",
}

_BASE = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
_CONFIG_DIR = _BASE / "config"
_CONFIG_DIR.mkdir(exist_ok=True)
ANSIM_CONFIG_PATH = _CONFIG_DIR / "ansim_config.json"
ANSIM_SESSION_PATH = _CONFIG_DIR / "ansim_session.json"

# 구버전 루트 위치의 세션 캐시를 새 위치로 이동
_legacy_session = _BASE / "ansim_session.json"
try:
    if _legacy_session.exists() and not ANSIM_SESSION_PATH.exists():
        _legacy_session.replace(ANSIM_SESSION_PATH)
except OSError:
    pass

# 마지막 register() 호출의 상세 메시지 (외부에서 읽기용)
LAST_MESSAGE: str = ""

_session: requests.Session | None = None
_config: dict | None = None


def _load_config() -> dict:
    if ANSIM_CONFIG_PATH.exists():
        try:
            with open(ANSIM_CONFIG_PATH, encoding="utf-8") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def _save_config() -> None:
    try:
        with open(ANSIM_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(_config, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _restore_cookies() -> None:
    if not ANSIM_SESSION_PATH.exists() or _session is None:
        return
    try:
        with open(ANSIM_SESSION_PATH, encoding="utf-8") as f:
            cookies = json.load(f)
        for name, value in cookies.items():
            _session.cookies.set(name, value)
    except Exception:
        pass


def _save_cookies() -> None:
    if _session is None:
        return
    try:
        cookies = dict(_session.cookies)
        with open(ANSIM_SESSION_PATH, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _has_aspsession() -> bool:
    if _session is None:
        return False
    return any(name.upper().startswith("ASPSESSIONID") for name in _session.cookies.keys())


def _ensure_init() -> None:
    global _session, _config
    if _session is None:
        _config = _load_config()
        _session = requests.Session()
        _session.headers.update(_HEADERS)
        _restore_cookies()
    if not _has_aspsession():
        try:
            _login_flow()
        except Exception:
            pass


def _connect() -> bool:
    """connect.asp — 세션 발급 (Set-Cookie ASPSESSIONID)."""
    res = _session.post(f"{BASE_URL}/connect.asp", data="", timeout=10)
    res.raise_for_status()
    _save_cookies()
    return res.text.strip() == "Y"


def _login() -> dict:
    """loginChk.asp — 세션 인증. SHOP_MEM_CODE 반환."""
    uid = (_config.get("user_id") or "").strip()
    pw = _config.get("password") or ""
    # 서버는 빈/잘못된 자격증명에도 RESULT:"Y" 를 주면서 테스트 시설(1001)로 붙여버린다.
    # 자격증명이 비어 있으면 조용히 엉뚱한 시설로 등록되지 않도록 여기서 명확히 차단.
    if not uid or not pw:
        raise RuntimeError(
            "안심톡 자격증명 미설정 — config/ansim_config.json 에 user_id/password 를 입력하세요"
        )
    params = {
        "sMemId": uid,
        "sMemPw": pw,
    }
    res = _session.post(
        f"{BASE_URL}/loginChk.asp",
        params=params, data="", timeout=10,
    )
    res.raise_for_status()
    try:
        data = res.json()
    except ValueError:
        raise RuntimeError(f"로그인 응답 파싱 실패: {res.text[:200]!r}")
    if data.get("RESULT") != "Y":
        raise RuntimeError(f"로그인 거부: {data}")
    _save_cookies()
    return data


def _fetch_facility(shop_mem_code: str) -> tuple[str, str]:
    """member_list.asp 에서 첫 멤버의 (PROGRAM, PROGRAMID) 추출."""
    res = _session.post(
        f"{BASE_URL}/member_list.asp",
        params={"ssMemCode": shop_mem_code}, data="", timeout=10,
    )
    res.raise_for_status()
    try:
        data = res.json()
    except ValueError:
        raise RuntimeError(f"member_list 파싱 실패: {res.text[:200]!r}")
    members = data.get("MEMBER_LIST") or []
    if not members:
        raise RuntimeError(f"member_list 가 비었음 (ssMemCode={shop_mem_code})")
    m = members[0]
    program = (m.get("PROGRAM") or "").strip()
    pid = (m.get("PROGRAMID") or "").strip()
    if not program or not pid:
        raise RuntimeError(f"PROGRAM/PROGRAMID 누락: {m}")
    return program, pid


def _login_flow() -> None:
    _connect()
    login_data = _login()
    shop_mem = (login_data.get("SHOP_MEM_CODE") or "").strip()
    if not shop_mem:
        raise RuntimeError("로그인 응답에 SHOP_MEM_CODE 없음")
    program, customer_id = _fetch_facility(shop_mem)
    _config["program"] = program
    _config["customer_id"] = customer_id


def _try_register(code: str) -> tuple[bool, str, bool]:
    """Returns: (성공 여부, 메시지, 세션 만료로 재시도해야 하는지)."""
    customer_id = (_config.get("customer_id") or "").strip()
    program = (_config.get("program") or "").strip()
    if not customer_id or not program:
        return False, "시설 정보 미설정 — 로그인 필요", True
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    params = {
        "program": program,
        "customer_id": customer_id,
        "Check_Value": code,
        "attendance_dt": now,
        **_FIXED_PARAMS,
    }
    try:
        res = _session.post(
            f"{BASE_URL}/attendance_sms_act.asp",
            params=params, data="", timeout=10,
        )
    except requests.RequestException as e:
        return False, f"네트워크 오류: {e}", False

    if res.status_code != 200:
        return False, f"HTTP {res.status_code}", False

    body = res.text.strip()
    if not body:
        return False, "빈 응답 (세션 만료 의심)", True

    try:
        data = res.json()
    except ValueError:
        return False, f"응답 파싱 실패: {body[:100]}", True

    result = (data.get("RESULT") or "").strip()
    msg1 = (data.get("MSG1") or "").strip()
    msg3 = (data.get("MSG3") or "").strip()
    msg4 = (data.get("MSG4") or "").strip()

    if result == "I":
        return False, msg1 or "학생정보 없음", False
    if result == "R":
        reason = (msg3 + " " + msg4).strip()
        return False, reason or "유예시간 미경과", False
    if result in ("Y", "S", "U", "M"):
        return True, msg4 or msg1 or "등록 완료", False
    return False, msg1 or f"미상 응답: RESULT={result!r}", False


def register(code: str) -> bool:
    """출석번호 4자리로 등하원 등록. 상세 메시지는 LAST_MESSAGE 참조."""
    global LAST_MESSAGE

    code = (code or "").strip()
    if not (code.isdigit() and len(code) == 4):
        LAST_MESSAGE = f"출석번호 형식 오류: {code!r}"
        return False

    try:
        _ensure_init()
        ok, msg, should_retry = _try_register(code)
        if not ok and should_retry:
            try:
                _login_flow()
            except Exception as e:
                LAST_MESSAGE = f"재로그인 실패: {e}"
                return False
            ok, msg, _ = _try_register(code)
        LAST_MESSAGE = msg
        return ok
    except Exception as e:
        LAST_MESSAGE = f"오류: {type(e).__name__}: {e}"
        return False


def setup_guide() -> None:
    print("=== 안심톡 API 클라이언트 ===\n")
    print(f"설정 파일: {ANSIM_CONFIG_PATH}")
    print(f"세션 캐시: {ANSIM_SESSION_PATH}\n")
    print("ansim_config.json 형식:\n")
    print("  {")
    print('    "user_id": "안심톡 로그인 ID",')
    print('    "password": "비밀번호"')
    print("  }\n")
    print("주의: 비밀번호가 평문 저장됨.")
    print("program / customer_id 는 로그인 시 자동 조회 (member_list.asp).")


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--setup":
        setup_guide()
        return
    ok = register(args[0])
    print(f"{'✓' if ok else '✗'} {LAST_MESSAGE}")


if __name__ == "__main__":
    main()
