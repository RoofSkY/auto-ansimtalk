"""실제 백업 API를 임시 데이터로 실행하는 브라우저 검사."""
import base64
import copy
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dev/test"))
from test_backup_api import BackupApiTests, STUDENTS, SCHEDULES
import backups


def main():
    BackupApiTests.setUpClass()
    case = BackupApiTests()
    case.setUp()
    try:
        with tempfile.TemporaryDirectory(prefix="backup-ui-") as profile:
            folder = Path(profile)
            incoming_students = [{**copy.deepcopy(STUDENTS[0]), "id": f"s{i}", "name": f"테스트{i:02}"} for i in range(28)]
            incoming_schedules = [{**copy.deepcopy(SCHEDULES[0]), "id": f"r{i}"} for i in range(4)]
            backup_file = folder / "ansimtalk-backup-test.json"
            backup_file.write_text(json.dumps(backups.create(incoming_students, incoming_schedules, "1.4.1"), ensure_ascii=False), encoding="utf-8")
            invalid = folder / "invalid.json"
            invalid.write_text("not json", encoding="utf-8")
            browser = subprocess.Popen([
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "--headless=new", "--disable-gpu",
                "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0", "--remote-allow-origins=*",
                f"--user-data-dir={profile}", "about:blank"
            ], creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                active = folder / "DevToolsActivePort"
                for _ in range(100):
                    if active.exists():
                        break
                    time.sleep(.1)
                port = active.read_text().splitlines()[0]
                page = next(p for p in json.load(urlopen(f"http://127.0.0.1:{port}/json/list")) if p["type"] == "page")
                with connect(page["webSocketDebuggerUrl"], origin="http://localhost") as ws:
                    seq, errors = 0, []

                    def call(method, params=None):
                        nonlocal seq
                        seq += 1
                        ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
                        while True:
                            result = json.loads(ws.recv(timeout=15))
                            if result.get("method") == "Runtime.exceptionThrown":
                                errors.append(result["params"])
                            if result.get("id") == seq:
                                if "error" in result:
                                    raise AssertionError(result)
                                return result.get("result", {})

                    def evaluate(script):
                        result = call("Runtime.evaluate", {"expression": script, "returnByValue": True, "awaitPromise": True})
                        if result.get("exceptionDetails"):
                            raise AssertionError(result)
                        return result["result"].get("value")

                    def until(script):
                        for _ in range(100):
                            if evaluate(script):
                                return
                            time.sleep(.05)
                        raise AssertionError((script, evaluate("document.body.innerText")))

                    def upload(file):
                        evaluate("document.getElementById('backup-file').value = ''")
                        doc = call("DOM.getDocument")
                        node = call("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "#backup-file"})
                        call("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [str(file)]})

                    call("Runtime.enable")
                    call("Page.enable")
                    call("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(folder / "downloads")})
                    for width in (1920, 390):
                        with case.m._data_lock:
                            case.m.state.students = incoming_students + [{**STUDENTS[0], "id": "extra1"}, {**STUDENTS[0], "id": "extra2"}]
                            case.m.state.schedules = incoming_schedules + [{**SCHEDULES[0], "id": "extra3"}]
                        call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 940 if width == 1920 else 1000, "deviceScaleFactor": 1, "mobile": False})
                        call("Page.navigate", {"url": case.base + "/settings/backup"})
                        until("typeof Alpine !== 'undefined' && document.getElementById('backup-students')?.textContent === '30'")
                        time.sleep(.3)
                        assert evaluate("document.querySelector('.app-nav [aria-current=page]').getAttribute('href')") == "/settings"
                        assert not evaluate("document.documentElement.scrollWidth > innerWidth + 1")
                        for dark in (False, True):
                            evaluate("Alpine.$data(document.documentElement).dark = " + json.dumps(dark))
                            time.sleep(.1)
                            report = evaluate((ROOT / "dev/test/check_contrast.js").read_text(encoding="utf-8"))
                            assert not report["failures"], (width, dark, report)
                            shot = call("Page.captureScreenshot", {"format": "png"})
                            (ROOT / f"dev/test/backup-live-{width}-{'dark' if dark else 'light'}.png").write_bytes(base64.b64decode(shot["data"]))
                        evaluate("document.getElementById('backup-export').click()")
                        until("document.getElementById('backup-status').textContent.includes('다운로드했습니다')")
                        for _ in range(100):
                            files = list((folder / "downloads").glob("*.json"))
                            if files:
                                break
                            time.sleep(.05)
                        assert files
                        exported = json.loads(files[-1].read_text(encoding="utf-8"))
                        assert len(exported["students"]) == 30
                        upload(invalid)
                        until("document.getElementById('backup-status').dataset.error === 'true'")
                        assert not evaluate("document.getElementById('backup-dialog').open")
                        upload(backup_file)
                        until("document.getElementById('backup-dialog').open")
                        assert evaluate("document.getElementById('restore-new-students').textContent") == "28명"
                        assert evaluate("document.activeElement.id") == "restore-cancel"
                        for dark in (False, True):
                            evaluate("Alpine.$data(document.documentElement).dark = " + json.dumps(dark))
                            time.sleep(.1)
                            report = evaluate((ROOT / "dev/test/check_contrast.js").read_text(encoding="utf-8"))
                            assert not report["failures"], (width, dark, "dialog", report)
                            assert evaluate("document.getElementById('backup-dialog').scrollWidth <= document.getElementById('backup-dialog').clientWidth + 1")
                            shot = call("Page.captureScreenshot", {"format": "png"})
                            (ROOT / f"dev/test/restore-live-{width}-{'dark' if dark else 'light'}.png").write_bytes(base64.b64decode(shot["data"]))
                        evaluate("document.getElementById('restore-cancel').click()")
                        assert len(case.m.state.students) == 30
                        upload(backup_file)
                        until("document.getElementById('backup-dialog').open")
                        case.m._background_reads = 1
                        evaluate("document.getElementById('restore-confirm').click()")
                        until("document.getElementById('restore-status').dataset.error === 'true'")
                        assert len(case.m.state.students) == 30
                        case.m._background_reads = 0
                        evaluate("document.getElementById('restore-confirm').click(); document.getElementById('restore-confirm').click()")
                        until("!document.getElementById('backup-dialog').open && document.getElementById('backup-students').textContent === '28'")
                        assert all(not s["enabled"] for s in case.m.state.schedules)
                        assert evaluate("document.querySelectorAll('#backup-history a').length") > 0
                        print(f"PASS {width}px: light/dark contrast, layout, export, invalid file, preview, cancel, busy retry, restore, auto backup", flush=True)
                    assert not errors, errors
                    call("Browser.close")
            finally:
                if browser.poll() is None:
                    browser.terminate()
                browser.wait(timeout=10)
    finally:
        case.doCleanups()
        BackupApiTests.tearDownClass()


if __name__ == "__main__":
    main()
