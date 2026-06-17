"""Nicepark 세션(쿠키) 자동 관리.

처음 사용 또는 만료 시 Playwright 로 브라우저를 띄워 로그인하고 cookies.json 에 저장.
이후엔 cookies.json 을 로드해 재사용.

config.json (선택 — 있으면 헤드리스 자동 로그인):
    {
        "user_id": "...",
        "password": "...",
        "id_selector": "input[name=\\"userId\\"]",
        "pw_selector": "input[name=\\"password\\"]",
        "submit_selector": "button[type=\\"submit\\"]",
        "logged_in_marker": ".main-content"
    }
"""

import json
import sys
import threading
import time
from pathlib import Path

# 여러 백그라운드 스레드의 동시 로그인 방지
_login_lock = threading.Lock()

BASE_URL = "https://npdc-i.nicepark.co.kr"
LOGIN_URL = BASE_URL + "/"

HERE = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
COOKIES_PATH = HERE / "cookies.json"
CONFIG_PATH = HERE / "config.json"
AUTH_LOG_PATH = HERE / "auth.log"


def _log(msg: str) -> None:
    """pythonw 백그라운드 실행 시 콘솔 출력이 없으므로 파일에 진단 기록."""
    try:
        from datetime import datetime
        with open(AUTH_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _load_config():
    if not CONFIG_PATH.exists():
        return None
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"config.json 읽기 실패: {e}")
        return None


def load_cookies() -> dict | None:
    if not COOKIES_PATH.exists():
        return None
    try:
        with open(COOKIES_PATH, encoding="utf-8") as f:
            return json.load(f).get("cookies")
    except Exception:
        return None


def _save_cookies(cookies_list: list) -> dict:
    cookie_dict = {c["name"]: c["value"] for c in cookies_list}
    with open(COOKIES_PATH, "w", encoding="utf-8") as f:
        json.dump({"cookies": cookie_dict}, f, ensure_ascii=False, indent=2)
    return cookie_dict


def cookies_to_header(cookies: dict | None) -> str:
    if not cookies:
        return ""
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def login(interactive: bool | None = None) -> dict:
    """브라우저로 로그인. 쿠키 dict 반환 후 cookies.json 저장.

    interactive=None: config.json 자격증명 있으면 헤드리스 자동, 없으면 보여서 수동
    interactive=True: 항상 보여서 사람이 직접 로그인
    interactive=False: 무조건 헤드리스 자동 (config.json 필수)
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright 미설치. 다음을 실행하세요:")
        print("  pip install playwright")
        print("  playwright install chromium")
        sys.exit(1)

    config = _load_config()
    has_auto = bool(
        config
        and config.get("user_id")
        and config.get("password")
        and config.get("id_selector")
        and config.get("pw_selector")
        and config.get("submit_selector")
    )

    if interactive is None:
        interactive = not has_auto
    if not interactive and not has_auto:
        raise RuntimeError("자동 로그인 불가 — config.json 자격증명/셀렉터 미설정")

    headless = not interactive

    _login_lock.acquire()
    try:
        return _do_login(headless, interactive, config)
    finally:
        _login_lock.release()


# Nicepark 로그인 직후에만 발급되는 쿠키들 (AJAX 로그인이라 URL 변화로는 감지 불가)
_LOGIN_COOKIE_INDICATORS = {"npdc_uidKey", "npdc_disShpToken", "npdc_usessionid"}


def _wait_for_login(page, timeout: int = 300) -> None:
    """URL 변화 또는 로그인 쿠키 발급으로 사용자의 로그인 완료를 감지."""
    initial_url = page.url
    try:
        initial_cookie_names = {c["name"] for c in page.context.cookies()}
    except Exception:
        initial_cookie_names = set()

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            current_url = page.url
            current_cookie_names = {c["name"] for c in page.context.cookies()}
        except Exception:
            raise RuntimeError("브라우저 창이 닫혔습니다")

        url_changed = (current_url != initial_url)
        new_login_cookies = (current_cookie_names - initial_cookie_names) & _LOGIN_COOKIE_INDICATORS

        if url_changed or new_login_cookies:
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                time.sleep(2)
            return
        time.sleep(0.5)
    raise RuntimeError(f"로그인 시간 초과 ({timeout}초)")


def _do_login(headless: bool, interactive: bool, config) -> dict:
    from playwright.sync_api import sync_playwright

    _log(f"login 시작 (headless={headless}, interactive={interactive})")
    _log(f"COOKIES_PATH={COOKIES_PATH}")

    cookies_list: list = []
    detection_error: Exception | None = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            try:
                context = browser.new_context()
                page = context.new_page()
                _log(f"goto {LOGIN_URL}")
                page.goto(LOGIN_URL, wait_until="domcontentloaded")

                if interactive:
                    print("브라우저에서 로그인하세요. 로그인 완료 시 자동 감지됩니다.")
                    try:
                        _wait_for_login(page)
                        _log("로그인 자동 감지 성공")
                    except Exception as e:
                        detection_error = e
                        _log(f"감지 실패: {e}")
                else:
                    page.fill(config["id_selector"], config["user_id"])
                    page.fill(config["pw_selector"], config["password"])
                    page.click(config["submit_selector"])
                    marker = config.get("logged_in_marker")
                    if marker:
                        page.wait_for_selector(marker, timeout=15000)
                    else:
                        page.wait_for_load_state("networkidle", timeout=15000)

                try:
                    cookies_list = context.cookies()
                    _log(f"쿠키 캡쳐: {len(cookies_list)}개 — {[c['name'] for c in cookies_list]}")
                except Exception as e:
                    _log(f"쿠키 캡쳐 실패: {e}")
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as e:
        _log(f"playwright 오류: {e}")
        raise

    has_login_cookie = any(c["name"] in _LOGIN_COOKIE_INDICATORS for c in cookies_list)
    if not has_login_cookie:
        if detection_error:
            raise detection_error
        raise RuntimeError("로그인 후 인증 쿠키를 받지 못했습니다 (auth.log 참고)")

    cookies = _save_cookies(cookies_list)
    _log(f"쿠키 {len(cookies)}개 → {COOKIES_PATH}")
    print(f"쿠키 {len(cookies)}개 저장됨 → {COOKIES_PATH.name}")
    return cookies


def ensure_session() -> dict:
    """저장된 쿠키 반환. 없으면 로그인부터."""
    cookies = load_cookies()
    if cookies:
        return cookies
    print("저장된 쿠키 없음 — 로그인 진행")
    return login()


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--interactive" in args or "-i" in args:
        cookies = login(interactive=True)
    elif "--auto" in args:
        cookies = login(interactive=False)
    else:
        cookies = login()

    for key in ("JSESSIONID", "npdc_uid", "npdc_disShpToken", "npdc_usessionid"):
        if key in cookies:
            v = cookies[key]
            print(f"  {key} = {v[:40]}{'...' if len(v) > 40 else ''}")
