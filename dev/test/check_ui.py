"""예시 데이터만 제공하는 임시 서버와 Edge로 UI를 확인한다."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import urlopen
from urllib.parse import parse_qs, urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())
env.globals.update(static_version="preview", app_version="preview")
names = ["김민준", "이서연", "박지호", "최하윤", "정서준", "한지우"]
students = [{"id": str(i), "name": name, "code": f"{i+1:04d}",
             "car_no4s": [str(1234+i*111)] if i % 2 == 0 else []} for i, name in enumerate(names)]
tickets = {"free": {"label": "1시간 무료권", "max": 2, "entry": "cycle", "color": "#E7F3FF"},
           "paid": {"label": "1시간 유료권", "max": 100, "entry": "number", "color": "#FFF5D8"}}
config = {"vehicle_windows_notify": True, "vehicle_toast": True, "vehicle_toast_duration": 5,
          "auto_search": True, "att_sync": True, "refresh_interval": 60, "vehicle_ticket_count": 1}
context = dict(students=students, config=config, service_status={"health":{"ansim":True,"iparking":True},"last_refresh":"15:42"}, logs=[
    {"time": "15:42:03", "type": "차량등록", "target": "1234 김민준", "message": "1시간 무료권 1매 등록 완료", "ok": True},
    {"time": "15:41:20", "type": "입차", "target": "박지호", "message": "1456 입차", "ok": True},
    {"time": "15:40:12", "type": "안심톡", "target": "0002 이서연", "message": "출석번호를 확인해 주세요", "ok": False},
    {"time": "15:20:10", "type": "안심톡", "target": "0004 최하윤", "message": "하원하였습니다", "ok": True}],
    in_cars=["1234", "1456"], att_status={"0001": "등원", "0003": "등원", "0004": "하원", "0005": "결석"},
    att_times={"0001": {"in": "14:30"}, "0003": {"in": "15:10"}, "0004": {"in": "13:00", "out": "15:20"}},
    att_refresh_remaining=55, tickets=tickets, ticket_order=["free", "paid"], day_labels=list("월화수목금"),
    schedules=[{"id": "r1", "time": "16:00", "code": "0001", "car_no4": "1234", "enabled": True,
                "days": [0, 2, 4], "tickets": {"free": 1}}], vehicle_ticket=tickets["free"],
    version="1.4.1", autostart_enabled=False, ansim_user_id="", iparking_store_id="", iparking_user_id="")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, data, content_type="application/json", status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = self.path.split("?")[0]
        if route.startswith("/static/"):
            path = ROOT / "static" / Path(route).name
            content_type = {'.css': 'text/css', '.svg': 'image/svg+xml'}.get(path.suffix, 'text/javascript')
            return self.reply(path.read_bytes(), content_type)
        if route == "/api/actions":
            return self.reply(b'{"instance":"preview","revision":1,"resources":[]}')
        if route == "/api/logs":
            query = parse_qs(urlparse(self.path).query)
            entries = [{**context['logs'][0], 'id': f'history-{i:04d}', 'time': '14:00:00'} for i in range(1002)]
            if query.get('before'):
                before = json.loads(query['before'][0])
                entries = [entry for entry in entries if [entry['time'], entry['id']] < before]
            return self.reply(json.dumps({'logs': entries[-500:], 'has_more': len(entries) > 500,
                                          'date': '2026-09-11'}).encode())
        if route == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                for _ in range(60):
                    self.wfile.write(b': keepalive\n\n')
                    self.wfile.flush()
                    time.sleep(1)
            except OSError:
                pass
            return
        name = {"/": "index", "/students": "students", "/schedules": "schedules", "/settings": "settings"}.get(route)
        if not name:
            return self.reply(b'{}', status=404)
        content = env.get_template(name + ".html").render(**context, request=SimpleNamespace(url=SimpleNamespace(path=route)))
        self.reply(content.encode(), "text/html; charset=utf-8")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/api/settings":
            if self.headers.get('X-Async-Form') == '1':
                return self.reply(b'{"ok":true,"redirect":"/settings?saved=test"}')
            self.send_response(303)
            self.send_header("Location", "/settings?saved=test")
            self.end_headers()
            return
        # 서버 검증 오류를 주어 입력값이 유지되는지 확인한다.
        self.reply(json.dumps({"error": "검사 예시: 최소 1개 요일 선택"}).encode(), status=400)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(prefix="ansim-ui-") as profile:
        browser = subprocess.Popen([
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--remote-debugging-port=0", "--remote-allow-origins=*", f"--user-data-dir={profile}", "about:blank",
        ], creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            active = Path(profile) / "DevToolsActivePort"
            for _ in range(100):
                if active.exists():
                    break
                time.sleep(.1)
            port = active.read_text().splitlines()[0]
            page = next(p for p in json.load(urlopen(f"http://127.0.0.1:{port}/json/list"))
                        if p["type"] == "page" and p["url"] == "about:blank")
            with connect(page["webSocketDebuggerUrl"], origin="http://localhost") as ws:
                seq, errors = 0, []

                def call(method, params=None):
                    nonlocal seq
                    seq += 1
                    ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
                    while True:
                        result = json.loads(ws.recv(timeout=15))
                        if result.get("method") == "Runtime.exceptionThrown":
                            errors.append(result["params"]["exceptionDetails"])
                        if result.get("id") == seq:
                            if "error" in result:
                                raise RuntimeError(result["error"])
                            return result.get("result", {})

                def evaluate(expression):
                    result = call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
                    if result.get("exceptionDetails"):
                        raise AssertionError(result["exceptionDetails"])
                    return result["result"].get("value")

                call("Runtime.enable")
                call("Page.enable")
                base = f"http://127.0.0.1:{server.server_port}"
                results = []
                for width in (1920, 1440, 390):
                    call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 940 if width == 1920 else 1000, "deviceScaleFactor": 1, "mobile": False})
                    for route in (("/settings",) if '--settings-only' in sys.argv else ("/students",) if '--students-only' in sys.argv else ("/",) if '--main-only' in sys.argv else ("/", "/students", "/schedules", "/settings")):
                        evaluate("window.sseBus?.stop()")
                        call("Page.stopLoading")
                        navigation = call("Page.navigate", {"url": base + route})
                        for _ in range(60):
                            if evaluate("location.pathname === " + json.dumps(route) + " && document.querySelector('.app-nav') && typeof Alpine !== 'undefined'"):
                                break
                            time.sleep(.1)
                        time.sleep(.3)
                        evaluate("Alpine.$data(document.documentElement).dark = false")
                        time.sleep(.05)
                        assert evaluate("document.querySelector('.app-nav [aria-current=page]') !== null"), (navigation, evaluate("JSON.stringify({url:location.href, html:document.documentElement.outerHTML.slice(0,2000)})"))
                        overflow = evaluate("document.documentElement.scrollWidth > innerWidth + 1")
                        assert not overflow, (width, route, "horizontal overflow")
                        for dark in (False, True):
                            evaluate("Alpine.$data(document.documentElement).dark = " + json.dumps(dark))
                            time.sleep(.05)
                            report = evaluate((ROOT / 'dev/test/check_contrast.js').read_text(encoding='utf-8'))
                            assert not report['failures'], (width, route, dark, report)
                            if width == 1440:
                                results.append(f"contrast {route} {'dark' if dark else 'light'}: min text {report['minimumText']}:1")
                            shot = call("Page.captureScreenshot", {"format": "png"})
                            (ROOT / "dev/test" / f"ui-{route.strip('/') or 'main'}-{'dark' if dark else 'light'}-{width}.png").write_bytes(base64.b64decode(shot['data']))
                            evaluate("document.querySelectorAll('.btn').forEach(b => {b.dataset.wasDisabled = String(b.disabled); b.disabled = true})")
                            report = evaluate((ROOT / 'dev/test/check_contrast.js').read_text(encoding='utf-8'))
                            assert not report['failures'], (width, route, dark, 'disabled', report)
                            evaluate("document.querySelectorAll('.btn').forEach(b => {b.disabled = b.dataset.wasDisabled === 'true'; delete b.dataset.wasDisabled})")
                        evaluate("Alpine.$data(document.documentElement).dark = false")
                        time.sleep(.05)
                        screenshot = call("Page.captureScreenshot", {"format": "png"})
                        (ROOT / "dev/test" / f"ui-{route.strip('/') or 'main'}-{width}.png").write_bytes(base64.b64decode(screenshot["data"]))
                        if route == "/":
                            assert evaluate("document.querySelector('.student-toolbar h2, .student-toolbar .count, .dashboard-status')") is None
                            assert evaluate("[...document.querySelector('.toolbar-main').children].map(e=>e.className)") == ['search-field','btn status-end','refresh-time','service-states']
                            if width > 760:
                                assert evaluate("document.querySelector('.app-header').offsetHeight") == 64
                                assert evaluate("(() => {const toolbar=document.querySelector('.toolbar-main').getBoundingClientRect(), status=document.querySelector('.service-states').getBoundingClientRect();return Math.abs(toolbar.right-status.right-16)<1})()")
                            assert evaluate("document.querySelectorAll('.student-group-heading').length") == 0
                            assert evaluate("document.querySelector('.refresh-time').textContent") == "최근 갱신 15:42"
                            evaluate("window.dispatchEvent(new CustomEvent('connection',{detail:true}));window.dispatchEvent(new CustomEvent('sse',{detail:{type:'service_status',data:{health:{ansim:false,iparking:null},last_refresh:'15:43'}}}))")
                            time.sleep(.05)
                            assert evaluate("document.querySelector('.service-state').textContent") == "안심톡 연결 실패"
                            assert evaluate("document.querySelectorAll('.service-state')[1].textContent") == "아이파킹 확인 대기"
                            evaluate("window.dispatchEvent(new CustomEvent('sse',{detail:{type:'service_status',data:{health:{ansim:true,iparking:true},last_refresh:'15:42'}}}))")
                            time.sleep(.05)
                            if width == 1920:
                                assert evaluate("(() => {const toolbar=document.querySelector('.student-toolbar').getBoundingClientRect(), log=document.querySelector('.log-panel').getBoundingClientRect();return Math.abs(toolbar.left-(innerWidth-toolbar.right))<1 && Math.abs(toolbar.right-log.right)<1})()")
                            assert evaluate("document.querySelectorAll('.student-row').length") == 6
                            assert evaluate("document.querySelector('.row-actions button').disabled") is False
                            assert evaluate("document.querySelectorAll('.row-feedback').length") == 0
                            assert evaluate("document.querySelectorAll('.car-cell,.parking-badge').length") == 0
                            assert evaluate("document.querySelectorAll('.student-head > span').length") == 10
                            assert evaluate("document.querySelectorAll('.student-row.is-parked').length") == 2
                            assert evaluate("getComputedStyle(document.querySelector('.student-row.is-parked')).backgroundColor !== getComputedStyle(document.querySelector('.student-row:not(.is-parked)')).backgroundColor")
                            assert evaluate("[...document.querySelectorAll('.log-message')].some(e => e.textContent.startsWith('아이파킹 ·'))")
                            assert evaluate("mainApp().logType({type:'차량등록(예약)'})") == "아이파킹(예약)"
                            icon_cases = [
                                ('입차','1234',True,'입차'), ('출차','1234',True,'출차'),
                                ('안심톡','등원하였습니다.',True,'등원'), ('안심톡','하원하였습니다.',True,'하원'),
                                ('안심톡(예약)','하원 완료',True,'하원'), ('차량등록','주차권 등록 완료',True,'성공'),
                                ('안심톡','등하원 처리 완료',True,'성공'), ('안심톡','하원 실패',False,'실패'),
                            ]
                            for kind,message,ok,icon in icon_cases:
                                assert evaluate('mainApp().logIcon('+json.dumps({'type':kind,'message':message,'ok':ok})+')') == icon
                            assert evaluate("document.querySelectorAll('.log-entry svg').length === document.querySelectorAll('.log-entry').length")
                            if width == 1920:
                                evaluate("window.savedLogFixture = Alpine.$data(document.querySelector('.student-panel')).logs.slice()")
                                evaluate("(() => {const app=Alpine.$data(document.querySelector('.student-panel')); for(let i=0;i<1200;i++) app.enqueueLog({id:'burst-'+String(i).padStart(4,'0'),time:'16:00:00',type:'입차',target:'예시',message:'입차',ok:true})})()")
                                time.sleep(.2)
                                assert evaluate("document.querySelectorAll('.log-entry').length") == 500
                                assert evaluate("document.querySelectorAll('.log-entry svg').length") == 500
                                evaluate("Alpine.$data(document.querySelector('.student-panel')).loadLogs(true)")
                                time.sleep(.2)
                                assert evaluate("document.querySelectorAll('.log-entry').length") == 1000
                                evaluate("(() => {const app=Alpine.$data(document.querySelector('.student-panel')); app.resetLogs(); app.logs=window.savedLogFixture; app.autoScroll=true;})()")
                                time.sleep(.1)
                            if width > 760:
                                assert evaluate("(() => {const center = e => {const r=e.getBoundingClientRect();return r.x+r.width/2}; return [...document.querySelectorAll('.student-table')].every(table => {const headers=[...table.querySelector('.student-head').children]; return [...table.querySelectorAll('.student-row')].every(row => [...row.children].every((cell,i) => Math.abs(center(cell)-center(headers[i]))<1 && getComputedStyle(cell).textAlign==='center'))})})()")
                            evaluate("window.dispatchEvent(new CustomEvent('sse',{detail:{type:'in_cars',data:{cars:[]}}}))")
                            time.sleep(.05)
                            assert evaluate("document.querySelectorAll('.student-row.is-parked').length") == 0
                            evaluate("window.dispatchEvent(new CustomEvent('sse',{detail:{type:'in_cars',data:{cars:['1234','1456']}}}))")
                            time.sleep(.05)
                            assert evaluate("document.querySelectorAll('.student-row.is-parked').length") == 2
                            assert evaluate("new Set([...document.querySelectorAll('.attendance-cell .badge')].map(e => `${e.offsetWidth}x${e.offsetHeight}`)).size") == 1
                            assert evaluate("[...document.querySelectorAll('.log-target')].every(e => !/\\d/.test(e.innerText))")
                            assert evaluate("[...document.querySelectorAll('.log-status')].every(e => e.offsetWidth === 24 && e.offsetHeight === 24 && [...e.querySelectorAll('svg')].filter(s => getComputedStyle(s).display !== 'none').length === 1)")
                            assert evaluate("getComputedStyle(document.querySelector('.log-message.feedback-error')).color !== getComputedStyle(document.querySelector('.log-message:not(.feedback-error)')).color")
                            cases = [
                                ("차량등록", "1628,6913 테스트", "테스트"),
                                ("차량등록", "12가1628, 123나6913 김 민준", "김 민준"),
                                ("안심톡", "0001 김민준", "김민준"),
                                ("차량등록", "김민준", "김민준"),
                                ("안심톡(예약)", "0001 예약 16:00", "김민준"),
                                ("차량등록(예약)", "1234 예약 16:00", "김민준"),
                                ("입차", "김민준", "김민준"),
                                ("시스템", "입출차 알림", "입출차 알림"),
                            ]
                            for kind, target, expected in cases:
                                assert evaluate("mainApp().logTarget(" + json.dumps({"type": kind, "target": target}) + ")") == expected
                            screenshot = call("Page.captureScreenshot", {"format": "png"})
                            (ROOT / "dev/test" / f"ui-main-{width}.png").write_bytes(base64.b64decode(screenshot["data"]))
                            assert evaluate("mainApp().actionLabel({id:'x'},'attendance')") == "연결 확인 중"
                            evaluate("document.querySelector('input[type=search]').value='ㄱㅁㅈ';document.querySelector('input[type=search]').dispatchEvent(new Event('input',{bubbles:true}))")
                            time.sleep(.1)
                            assert evaluate("[...document.querySelectorAll('.student-row')].filter(e=>e.offsetHeight).length") == 1
                            evaluate("(() => {const app = Alpine.$data(document.querySelector('.student-panel')); app.searchQuery = ''; app.students = Array.from({length:30}, (_,i) => ({...app.students[i%6],id:'preview-'+i,name:app.students[i%6].name+' '+String(i+1).padStart(2,'0')}))})()")
                            time.sleep(.1)
                            assert evaluate("document.querySelectorAll('.student-row').length") == 30
                            assert evaluate("[...document.querySelectorAll('.student-table')].map(t=>t.querySelectorAll('.student-row').length)") == [15,15]
                            assert evaluate("[...document.querySelectorAll('.student-table')].every(t => t.scrollHeight<=t.clientHeight+1)")
                            if width == 1920:
                                for height in (1080, 940):
                                    call('Emulation.setDeviceMetricsOverride', {'width':1920,'height':height,'deviceScaleFactor':1,'mobile':False})
                                    assert evaluate("(() => {const rows=[...document.querySelectorAll('.student-row')].map(r=>r.getBoundingClientRect().height);return Math.max(...rows)-Math.min(...rows)<1})()")
                                    assert evaluate("[...document.querySelectorAll('.student-table')].every(t => {const last=[...t.querySelectorAll('.student-row')].at(-1);return Math.abs(t.getBoundingClientRect().bottom-last.getBoundingClientRect().bottom-9)<1})")
                                    assert evaluate("new Set([...document.querySelectorAll('.student-table,.log-panel')].map(e => e.getBoundingClientRect().height)).size") == 1
                                    assert evaluate("document.documentElement.scrollHeight <= innerHeight")
                                assert evaluate("new Set([...document.querySelectorAll('.student-table,.log-panel')].map(e => e.getBoundingClientRect().height)).size") == 1
                                assert evaluate("document.documentElement.scrollHeight <= innerHeight"), evaluate("JSON.stringify({height:innerHeight,scroll:document.documentElement.scrollHeight,panel:document.querySelector('.student-panel').getBoundingClientRect(),log:document.querySelector('.log-panel').getBoundingClientRect()})")
                                assert evaluate("document.querySelector('.log-panel').getBoundingClientRect().left > document.querySelector('.student-panel').getBoundingClientRect().right")
                                shot = call('Page.captureScreenshot', {'format':'png'})
                                (ROOT / 'dev/test/ui-main-30-students.png').write_bytes(base64.b64decode(shot['data']))
                            evaluate("document.querySelectorAll('.student-row')[29].scrollIntoView({block:'center'})")
                            assert evaluate("document.querySelectorAll('.student-row')[29].getBoundingClientRect().bottom <= innerHeight")
                            evaluate("window.scrollTo(0,0)")
                            evaluate("Alpine.$data(document.querySelector('.student-panel')).actionResources = ['attendance:code:0001']")
                            time.sleep(.05)
                            assert evaluate("[...document.querySelectorAll('.student-row')].filter(r => r.querySelector('.row-actions button').disabled).length") == 5
                            evaluate("Alpine.$data(document.querySelector('.student-panel')).actionResources = []")
                            evaluate("Alpine.$data(document.querySelector('.student-panel')).searchQuery = '30'")
                            time.sleep(.05)
                            assert evaluate("document.querySelectorAll('.student-row').length") == 1
                            assert evaluate("document.querySelector('.student-name').textContent.endsWith('30')")
                            evaluate("Alpine.$data(document.querySelector('.student-panel')).searchQuery = 'no-match'")
                            time.sleep(.05)
                            assert evaluate("document.querySelector('.student-columns').offsetHeight") > 0
                            assert evaluate("document.querySelectorAll('.student-table').length") == 2
                            assert evaluate("document.querySelectorAll('.student-row').length") == 0
                            evaluate("Object.assign(Alpine.$data(document.querySelector('.student-panel')), {searchQuery:'',statusFilter:mainApp().statusTabs[0]})")
                            time.sleep(.05)
                            assert evaluate("document.querySelectorAll('.student-row').length") == 30
                            evaluate("Alpine.$data(document.querySelector('.student-panel')).statusFilter = '캠프'")
                            time.sleep(.05)
                            assert evaluate("document.querySelectorAll('.student-row').length") == 0
                            assert evaluate("[...document.querySelectorAll('.student-table')].every(t=>t.offsetHeight>0 && t.querySelector('.empty-state').offsetHeight>0)")
                            if width == 1920:
                                assert evaluate("document.documentElement.scrollHeight <= innerHeight")
                                assert evaluate("new Set([...document.querySelectorAll('.student-table,.log-panel')].map(e=>e.getBoundingClientRect().height)).size") == 1
                            evaluate("Object.assign(Alpine.$data(document.querySelector('.student-panel')), {searchQuery:'',statusFilter:mainApp().statusTabs[0]})")
                            time.sleep(.05)
                        if route == "/students":
                            if width > 760:
                                assert evaluate("(() => {const fields=['new-name','new-code','new-cars'].map(id=>document.getElementById(id).getBoundingClientRect());return fields.every(r=>Math.abs(r.top-fields[0].top)<1 && Math.abs(r.height-fields[0].height)<1)})()")
                            evaluate("document.querySelector('.directory-row button').click()")
                            time.sleep(.1)
                            assert evaluate("document.querySelector('.directory-edit').offsetHeight>0")
                            evaluate("document.querySelector('.directory-edit input').value='Changed'; document.querySelector('.directory-edit button[type=button]').click()")
                            assert evaluate("document.querySelector('.directory-edit input').value") == names[0]
                            assert evaluate("document.querySelector('.directory-edit').offsetHeight") == 0
                            evaluate("window.confirm=()=>true;document.querySelector('.directory-row form button').click()")
                            time.sleep(.2)
                            assert evaluate("document.querySelector('[data-row-status]').dataset.error") == "true"
                        if route == "/schedules":
                            evaluate("document.querySelector('[name=time]').value='16:00'; document.querySelector('[name=code]').value='1234'; document.querySelector('.form-card button').click()")
                            time.sleep(.3)
                            assert evaluate("document.querySelector('[data-form-status]').dataset.error") == "true"
                            assert evaluate("document.querySelector('[name=code]').value") == "1234"
                        if route == "/settings":
                            evaluate("document.querySelector('[name=vehicle_windows_notify]').click()")
                            time.sleep(.1)
                            assert evaluate("document.querySelector('[name=vehicle_toast_duration]').offsetHeight>0")
                            assert evaluate("document.querySelector('#notification-test-button').offsetHeight") > 0
                            assert evaluate("document.querySelector('#notification-test-button').textContent") == '알림 테스트'
                            evaluate("window.originalFetch=window.fetch; window.notificationRequests=0; window.notificationFailure=false; window.fetch=(url, options)=>{if(url==='/api/settings/notifications/test'){window.notificationRequests++;return Promise.resolve(new Response(JSON.stringify(window.notificationFailure?{error:'테스트 요청 실패'}:{ok:true}),{status:window.notificationFailure?503:200,headers:{'Content-Type':'application/json'}}));}return window.originalFetch(url,options)}")
                            evaluate("document.querySelector('[name=vehicle_toast_duration]').value='1'; document.querySelector('#notification-test-button').click()")
                            time.sleep(.1)
                            assert evaluate("document.querySelector('.vehicle-toast strong').textContent") == '웹 알림 테스트'
                            assert evaluate("window.notificationRequests") == 0
                            for _ in range(60):
                                if evaluate("document.querySelectorAll('.vehicle-toast').length") == 0:
                                    break
                                time.sleep(.05)
                            assert evaluate("document.querySelectorAll('.vehicle-toast').length") == 0
                            evaluate("document.querySelector('[name=vehicle_windows_notify]').click()")
                            time.sleep(.05)
                            evaluate("document.querySelector('#notification-test-button').click()")
                            time.sleep(.1)
                            assert evaluate("window.notificationRequests") == 1
                            assert evaluate("document.querySelectorAll('.vehicle-toast').length") == 0
                            assert evaluate("document.querySelector('#notification-test-status').textContent") == ''
                            assert evaluate("document.querySelector('#notification-test-button').disabled") is False
                            evaluate("window.notificationFailure=true; document.querySelector('#notification-test-button').click()")
                            time.sleep(.1)
                            assert evaluate("document.querySelector('#notification-test-status').textContent") == '테스트 실패: 테스트 요청 실패'
                            assert evaluate("document.querySelector('#notification-test-button').disabled") is False
                            evaluate("window.fetch=window.originalFetch; document.querySelector('[name=vehicle_windows_notify]').click()")
                            assert evaluate("document.querySelector('[data-dirty-status]').textContent")
                            evaluate("document.querySelector('[data-dirty-form] button:not([type=button])').click()")
                            for _ in range(50):
                                time.sleep(.1)
                                if evaluate("document.querySelector('#save-toast')?.textContent === 'test 저장 완료'"):
                                    break
                            assert evaluate("document.querySelector('#save-toast').textContent") == "test 저장 완료"
                            assert evaluate("document.querySelector('[data-dirty-status]').textContent") == ""
                        # 같은 화면의 다크 모드도 넘침 없이 표시되어야 한다.
                        evaluate("document.documentElement.classList.add('dark')")
                        assert not evaluate("document.documentElement.scrollWidth > innerWidth + 1")
                        report = evaluate((ROOT / 'dev/test/check_contrast.js').read_text(encoding='utf-8'))
                        assert not report['failures'], (width, route, 'after interaction', report)
                        results.append(f"{width}px {route}: OK")
                assert not errors, errors
                print("\n".join(results))
                print("PASS: two lists of 15, 30 visible without scrolling at 1920x940, shared action locks, search, empty state, light/dark" if '--main-only' in sys.argv else "PASS: main UI, forms, settings, light/dark")
                call("Browser.close")
        finally:
            if browser.poll() is None:
                browser.terminate()
            browser.wait(timeout=10)
            server.shutdown()


if __name__ == "__main__":
    if '--backup-only' in sys.argv:
        from check_backup_ui import main as check_backup
        check_backup()
    else:
        main()
