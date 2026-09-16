import asyncio
import copy
import importlib.util
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from test_backups import ROOT, STUDENTS, SCHEDULES
import backups
import jsonstore
import uvicorn


def load_isolated_app(directory):
    source_path = ROOT / "src/app.py"
    source = source_path.read_text(encoding="utf-8")
    source = source.replace('HERE = Path(__file__).resolve().parent.parent', f'HERE = Path({str(directory)!r})')
    source = source.replace('TEMPLATES_DIR = HERE / "templates"', f'TEMPLATES_DIR = Path({str(ROOT / "templates")!r})')
    source = source.replace('STATIC_DIR = HERE / "static"', f'STATIC_DIR = Path({str(ROOT / "static")!r})')
    module = importlib.util.module_from_spec(importlib.util.spec_from_file_location("backup_test_app", source_path))
    sys.modules[module.__name__] = module
    exec(compile(source, str(source_path), "exec"), module.__dict__)
    return module


class BackupApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.permissions = patch.object(jsonstore, "restrict_permissions")
        cls.permissions.start()
        cls.module = load_isolated_app(cls.tmp.name)
        cls.socket = socket.socket()
        cls.socket.bind(("127.0.0.1", 0))
        cls.base = f"http://127.0.0.1:{cls.socket.getsockname()[1]}"
        cls.server = uvicorn.Server(uvicorn.Config(cls.module.app, lifespan="off", log_level="error"))
        cls.thread = threading.Thread(target=lambda: cls.server.run(sockets=[cls.socket]), daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(.02)
        assert cls.server.started

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(5)
        cls.socket.close()
        cls.permissions.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.m = self.module
        self.m.state.students = copy.deepcopy(STUDENTS)
        self.m.state.schedules = copy.deepcopy(SCHEDULES)
        self.m.state.config = {**self.m.DEFAULT_CONFIG, "private_token": "never-export"}
        self.m._backup_previews.clear()
        self.m._background_reads = 0
        jsonstore.save(self.m.STUDENTS_PATH, STUDENTS)
        jsonstore.save(self.m.SCHEDULES_PATH, SCHEDULES)
        self.external = patch.object(self.m, "_start_action", side_effect=AssertionError("External action attempted"))
        self.external.start()
        self.addCleanup(self.external.stop)

    def request(self, path, data=None, method=None, headers=None):
        raw = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None
        request = Request(self.base + path, data=raw, method=method,
                          headers={"Content-Type": "application/json", **(headers or {})})
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            return response.status, json.loads(body) if response.headers.get_content_type() == "application/json" else body

    def preview(self, students=None, schedules=None):
        data = backups.create(STUDENTS if students is None else students, SCHEDULES if schedules is None else schedules, "1.0")
        code, result = self.request("/api/backup/preview", data)
        self.assertEqual(code, 200, result)
        return result

    def test_export_and_restore(self):
        code, exported = self.request("/api/backup/export", method="POST")
        self.assertEqual(code, 200)
        self.assertNotIn("never-export", json.dumps(exported))
        self.assertNotIn("last_run", json.dumps(exported))
        preview = self.preview()
        self.assertEqual(self.m.state.students, STUDENTS)
        code, result = self.request("/api/backup/restore", {"token": preview["token"]})
        self.assertEqual(code, 200, result)
        self.assertFalse(self.m.state.schedules[0]["enabled"])
        self.assertNotEqual(self.m.state.students[0]["id"], STUDENTS[0]["id"])
        self.assertEqual(self.m.state.config["private_token"], "never-export")
        code, automatic = self.request("/api/backup/automatic/" + result["automatic_backup"])
        self.assertEqual(code, 200)
        self.assertTrue(automatic["schedules"][0]["enabled"])
        self.assertEqual(self.request("/api/backup/summary")[0], 200)
        self.assertEqual(self.request("/api/backup/restore", {"token": preview["token"]})[0], 409)
        self.assertEqual(json.loads(self.m.STUDENTS_PATH.read_text(encoding="utf-8")), self.m.state.students)
        with patch.object(self.m, "datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 14, 16, 30)
            self.m._scheduler_tick()

    def test_partial_restore_keeps_memory_and_disk_in_sync(self):
        preview = self.preview([], [])
        original = jsonstore.save
        failed = False

        def fail_once(path, data, **kwargs):
            nonlocal failed
            if path == self.m.SCHEDULES_PATH and not failed:
                failed = True
                raise OSError("write failed")
            return original(path, data, **kwargs)

        with patch.object(jsonstore, "save", side_effect=fail_once):
            self.assertEqual(self.request("/api/backup/restore", {"token": preview["token"]})[0], 500)
        self.assertEqual(self.m.state.students, STUDENTS)
        self.assertEqual(self.m.state.schedules, SCHEDULES)
        self.assertEqual(json.loads(self.m.STUDENTS_PATH.read_text(encoding="utf-8")), STUDENTS)
        self.assertFalse((self.m.CONFIG_DIR / "restore_pending.json").exists())

    def test_restore_and_student_write_are_serialized(self):
        preview = self.preview([], [])
        entered, release, edited = threading.Event(), threading.Event(), threading.Event()
        original = backups.replace
        outcomes = []

        def slow_replace(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test release timeout")
            return original(*args)

        def edit():
            self.m.add_student(name="복원 후 추가", code="", cars="")
            edited.set()

        with patch.object(backups, "replace", side_effect=slow_replace):
            restore_thread = threading.Thread(target=lambda: outcomes.append(self.m._restore_backup(preview["token"])))
            restore_thread.start()
            self.assertTrue(entered.wait(2))
            edit_thread = threading.Thread(target=edit)
            edit_thread.start()
            try:
                self.assertFalse(edited.wait(.05))
            finally:
                release.set()
                restore_thread.join(5)
                edit_thread.join(5)
        self.assertTrue(outcomes[0]["ok"])
        self.assertTrue(edited.is_set())
        self.assertEqual([s["name"] for s in self.m.state.students], ["복원 후 추가"])
        self.assertEqual(json.loads(self.m.STUDENTS_PATH.read_text(encoding="utf-8")), self.m.state.students)

    def test_preview_rejects_bad_files_and_expired_token(self):
        self.assertEqual(self.request("/api/backup/preview", b"not json")[0], 400)
        self.assertEqual(self.request("/api/backup/preview", b" " * (backups.MAX_BYTES + 1))[0], 400)
        preview = self.preview()
        self.m._backup_previews[preview["token"]]["expires"] = 0
        code, result = self.request("/api/backup/restore", {"token": preview["token"]})
        self.assertEqual(code, 409)
        self.assertTrue(result["reselect"])
        self.assertEqual(self.m.state.students, STUDENTS)

    def test_changed_data_requires_new_preview(self):
        preview = self.preview()
        self.m.state.students.append({"id": "new", "name": "추가", "code": "", "car_no4s": []})
        code, result = self.request("/api/backup/restore", {"token": preview["token"]})
        self.assertEqual(code, 409)
        self.assertTrue(result["reselect"])

    def test_running_jobs_and_polling_block_restore(self):
        preview = self.preview()
        with patch.object(self.m.action_runner, "snapshot", return_value={"resources": ["attendance:code:0001"]}):
            self.assertEqual(self.request("/api/backup/restore", {"token": preview["token"]})[0], 409)
        self.m._background_reads = 1
        self.assertEqual(self.request("/api/backup/restore", {"token": preview["token"]})[0], 409)
        self.m._background_reads = 0
        self.assertEqual(self.request("/api/backup/restore", {"token": preview["token"]})[0], 200)

    def test_backup_failure_leaves_data_untouched(self):
        preview = self.preview()
        with patch.object(jsonstore, "save", side_effect=OSError("Disk full")):
            self.assertEqual(self.request("/api/backup/restore", {"token": preview["token"]})[0], 500)
        self.assertEqual(self.m.state.students, STUDENTS)
        self.assertEqual(json.loads(self.m.STUDENTS_PATH.read_text(encoding="utf-8")), STUDENTS)

    def test_empty_restore_and_disabled_scheduler(self):
        preview = self.preview([], [])
        self.assertEqual(preview["incoming"], {"students": 0, "schedules": 0})
        self.assertEqual(self.request("/api/backup/restore", {"token": preview["token"]})[0], 200)
        self.assertEqual(self.m.state.students, [])
        self.assertEqual(self.m.state.schedules, [])
        self.m._scheduler_tick()

    def test_page_and_origin_guard(self):
        code, html = self.request("/settings/backup")
        self.assertEqual(code, 200)
        self.assertIn('복원 내용 확인'.encode(), html)
        self.assertEqual(self.request("/api/backup/export", method="POST", headers={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("/api/backup/automatic/invalid.json")[0], 404)

    def test_slow_settings_save_does_not_block_health_and_failed_save_keeps_state(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        original = copy.deepcopy(self.m.state.config)

        def slow_save(config):
            entered.set()
            release.wait(3)
            raise OSError('expected test failure')

        with patch.object(self.m, 'save_config', side_effect=slow_save):
            thread = threading.Thread(target=lambda: results.append(self.request(
                '/api/settings', b'refresh_interval=30', headers={'Content-Type': 'application/x-www-form-urlencoded'})))
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                start = time.monotonic()
                self.assertEqual(self.request('/health')[0], 200)
                self.assertLess(time.monotonic() - start, .5)
                self.assertEqual(self.m.state.config, original)
            finally:
                release.set()
                thread.join(5)
        self.assertEqual(results[0][0], 500)
        self.assertEqual(self.m.state.config, original)

    def test_async_forms_return_destination_without_following_redirect(self):
        status, result = self.request('/api/students', b'name=Example&code=0001', headers={
            'Content-Type': 'application/x-www-form-urlencoded', 'X-Async-Form': '1'})
        self.assertEqual(status, 200)
        self.assertEqual(result, {'ok': True, 'redirect': '/students'})
        # A normal form still follows the original 303 to an HTML page.
        status, result = self.request('/api/students', b'name=Second', headers={
            'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(status, 200)
        self.assertIsInstance(result, bytes)

    def test_schedule_batch_single_save_and_recovery_after_save_failure(self):
        journal = self.m.CONFIG_DIR / 'batch-test.jsonl'
        self.m.state.schedules = [dict(copy.deepcopy(SCHEDULES[0]), id=str(i), code=f'{i:04d}',
                                       time='16:30', days=[0], car_no4='', tickets={}, enabled=True, last_run='')
                                  for i in range(5)]
        queue = self.m.ScheduleQueue(journal)
        original = copy.deepcopy(self.m.state.schedules)
        with patch.object(self.m, 'schedule_queue', queue), patch.object(self.m, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 9, 14, 16, 30)
            with patch.object(self.m, 'save_schedules', side_effect=OSError('expected')):
                with self.assertRaises(OSError):
                    self.m._scheduler_tick()
            self.assertEqual(self.m.state.schedules, original)
            self.assertEqual(len(queue.snapshot()), 5)
            # Recreate the queue to simulate a restart after batch persistence.
            self.m.schedule_queue = self.m.ScheduleQueue(journal)
            with patch.object(self.m, 'save_schedules', wraps=self.m.save_schedules) as save:
                with patch.object(self.m, '_start_action', return_value=True) as start:
                    self.m._scheduler_tick()
                    self.assertEqual(save.call_count, 1)
                    self.assertEqual(start.call_count, 5)
                    self.m._scheduler_tick()
                    self.assertEqual(start.call_count, 5)
        self.m._scheduled_inflight.clear()

    def test_log_api_validates_cursor_and_returns_bounded_history(self):
        status, result = self.request('/api/logs?before=invalid')
        self.assertEqual(status, 400)
        for i in range(510):
            self.m.append_log({'id': f'api-{i:04d}', 'time': '12:00:00', 'ok': True})
        status, result = self.request('/api/logs')
        self.assertEqual(status, 200)
        self.assertEqual(len(result['logs']), 500)
        self.assertTrue(result['has_more'])

    def test_scheduler_records_start_before_external_work(self):
        self.external.stop()
        queue = self.m.ScheduleQueue(self.m.CONFIG_DIR / 'dispatch-test.jsonl')
        self.m.state.schedules = [dict(copy.deepcopy(SCHEDULES[0]), id='dispatch', code='0001',
                                       time='16:30', days=[0], car_no4='1234', tickets={'free': 1},
                                       enabled=True, last_run='')]
        states_at_call = []

        def observe(action):
            job = next(job for job in queue.snapshot() if job['action'] == action)
            states_at_call.append((action, job['status']))

        with patch.object(self.m, 'schedule_queue', queue), patch.object(self.m, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 9, 14, 16, 30)
            with patch.object(self.m, 'do_attendance', side_effect=lambda *args: observe('attendance')):
                with patch.object(self.m, 'do_vehicle', side_effect=lambda *args: observe('vehicle')):
                    self.m._scheduler_tick()
                    self.m.action_runner._queues['attendance'].join()
                    self.m.action_runner._queues['vehicle'].join()
        self.assertEqual(sorted(states_at_call), [('attendance', 'running'), ('vehicle', 'running')])
        self.assertTrue(all(job['status'] == 'done' for job in queue.snapshot()))
        self.assertEqual(self.m.action_runner.snapshot()['resources'], [])
        self.assertEqual(self.m._scheduled_inflight, set())


    def test_manual_parking_api_registration_and_duplicate_request(self):
        from test_manual_parking import CAR, DETAIL
        from urllib.parse import urlencode
        import iparking
        with patch.object(iparking, 'find_in_cars', return_value=[CAR]), \
                patch.object(iparking, 'get_vehicle_detail', return_value=copy.deepcopy(DETAIL)), \
                patch.object(iparking, 'apply_discount', return_value=(True, 'done')) as apply:
            status, cars = self.request('/api/parking/search?number=6595')
            self.assertEqual(status, 200)
            self.assertEqual(cars[0]['plate'], CAR['carNumber'])
            status, view = self.request('/api/parking/detail?' + urlencode({'plate': CAR['carNumber'], 'history': CAR['parkingHistoryId']}))
            self.assertEqual(status, 200)
            token = view['token']
            self.assertEqual(self.request('/api/parking/register', {'token': token, 'counts': {'free': True}})[0], 400)
            payload = {'token': token, 'counts': {'free': 1}}
            self.assertEqual(self.request('/api/parking/register', payload)[0], 200)
            self.m.action_runner._queues['vehicle'].join()
            self.assertEqual(self.request('/api/parking/requests/' + token)[1]['state'], 'success')
            self.assertEqual(self.request('/api/parking/register', payload)[0], 200)
            apply.assert_called_once()
            self.assertEqual(self.request('/api/parking/requests/missing')[0], 404)


    def test_manual_parking_cancel_api_and_duplicate_request(self):
        from test_manual_parking import CAR, DETAIL
        from urllib.parse import urlencode
        import iparking
        detail = copy.deepcopy(DETAIL)
        detail['myStoreApplyRequestTicketInfoList'] = [
            {'discountId': 'applied-id', 'discountName': '무료권', 'applyCount': 2}]
        with patch.object(iparking, 'find_in_cars', return_value=[CAR]), \
                patch.object(iparking, 'get_vehicle_detail', return_value=detail), \
                patch.object(iparking, 'cancel_discount', return_value=(True, 'done')) as cancel:
            status, view = self.request('/api/parking/detail?' + urlencode({'plate': CAR['carNumber'], 'history': CAR['parkingHistoryId']}))
            self.assertEqual(status, 200)
            payload = {'token': view['token'], 'key': 'applied-id', 'count': 1}
            self.assertEqual(self.request('/api/parking/cancel', {**payload, 'count': True})[0], 400)
            self.assertEqual(self.request('/api/parking/cancel', {**payload, 'key': 'other-store'})[0], 409)
            self.assertEqual(self.request('/api/parking/cancel', payload)[0], 200)
            self.m.action_runner._queues['vehicle'].join()
            self.assertEqual(self.request('/api/parking/requests/' + view['token'])[1]['state'], 'success')
            self.assertEqual(self.request('/api/parking/cancel', payload)[0], 200)
            cancel.assert_called_once()
            self.assertEqual(self.request('/api/parking/cancel', {**payload, 'token': ''})[0], 400)


if __name__ == "__main__":
    unittest.main()
