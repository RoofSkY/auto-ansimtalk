import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from actions import ActionRunner
from logstore import LogStore
from refresh import RefreshCoordinator
from schedule_queue import ScheduleQueue


class RefreshTests(unittest.TestCase):
    def test_slow_attendance_does_not_block_vehicle_and_requests_coalesce(self):
        entered, release, vehicle, rerun = (threading.Event() for _ in range(4))
        calls = []

        def attendance():
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                release.wait(3)
            else:
                rerun.set()

        manager = RefreshCoordinator({'att': attendance, 'vehicle': vehicle.set},
                                     lambda: 60, lambda kind: False, lambda seconds: None,
                                     lambda *args: None)
        manager.start()
        try:
            manager.request({'att'})
            self.assertTrue(entered.wait(1))
            original_deadline = manager.deadline
            for _ in range(30):
                manager.request({'att'}, if_stale=True)
            manager.request({'vehicle'})
            self.assertTrue(vehicle.wait(1))
            self.assertEqual(manager.deadline, original_deadline)
            self.assertNotIn('att', manager.pending)
            for _ in range(30):
                manager.request({'att'})
            release.set()
            self.assertTrue(rerun.wait(1))
            with manager.condition:
                self.assertTrue(manager.condition.wait_for(lambda: 'att' not in manager.running, 1))
            manager.request({'att'}, if_stale=True)
            self.assertEqual(calls, [1, 1])
            self.assertNotIn('att', manager.pending)
        finally:
            release.set()
            manager.stop()

    def test_timer_skips_running_service_and_keeps_other_service_on_schedule(self):
        entered, release, vehicle = (threading.Event() for _ in range(3))
        manager = RefreshCoordinator(
            {'att': lambda: (entered.set(), release.wait(3)), 'vehicle': vehicle.set},
            lambda: 60, lambda kind: True, lambda seconds: None, lambda *args: None)
        manager.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(vehicle.wait(1))
            with manager.condition:
                self.assertTrue(manager.condition.wait_for(lambda: 'vehicle' not in manager.running, 1))
                vehicle.clear()
                manager.deadline = 0
                manager.condition.notify_all()
            self.assertTrue(vehicle.wait(1))
            self.assertNotIn('att', manager.pending)
        finally:
            release.set()
            manager.stop()


class ActionTests(unittest.TestCase):
    def test_queued_attendance_holds_resources_and_releases_on_failure(self):
        entered, release, second = (threading.Event() for _ in range(3))
        errors = []
        runner = ActionRunner(lambda snapshot: None, errors.append)

        def first():
            entered.set()
            release.wait(3)
            raise ValueError('expected')

        try:
            self.assertTrue(runner.start({'a'}, first, kind='attendance'))
            self.assertTrue(entered.wait(1))
            self.assertTrue(runner.start({'b'}, second.set, kind='attendance'))
            self.assertFalse(second.wait(.03))
            self.assertEqual(runner.snapshot()['resources'], ['a', 'b'])
            self.assertFalse(runner.start({'b'}, lambda: None, kind='attendance'))
            release.set()
            self.assertTrue(second.wait(1))
            runner._queues['attendance'].join()
            self.assertEqual(runner.snapshot()['resources'], [])
            self.assertEqual(len(errors), 1)
        finally:
            release.set()
            runner.close()

    def test_vehicle_concurrency_is_bounded(self):
        release = threading.Event()
        lock = threading.Lock()
        counts = [0, 0]
        four = threading.Event()

        def work():
            with lock:
                counts[0] += 1
                counts[1] = max(counts)
                if counts[0] == 4:
                    four.set()
            release.wait(3)
            with lock:
                counts[0] -= 1

        runner = ActionRunner(lambda value: None, lambda exc: None)
        try:
            for i in range(12):
                self.assertTrue(runner.start({str(i)}, work))
            self.assertTrue(four.wait(1))
            self.assertEqual(counts[1], 4)
        finally:
            release.set()
            runner._queues['vehicle'].join()
            runner.close()


class StorageTests(unittest.TestCase):
    def test_logs_page_by_time_with_stable_ids_and_old_failure_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LogStore(Path(directory))
            for i in range(1002):
                store.append({'id': f'{i:04d}', 'time': '15:00:00', 'ok': i != 0})
            latest = store.page()
            self.assertEqual(len(latest['logs']), 500)
            self.assertTrue(latest['has_more'])
            first = latest['logs'][0]
            older = store.page(before=[first['time'], first['id']])
            self.assertFalse({v['id'] for v in latest['logs']} & {v['id'] for v in older['logs']})
            store.append({'id': 'late', 'time': '14:00:00', 'ok': True})
            self.assertEqual(store.page('failed')['logs'][0]['id'], '0000')
            self.assertEqual(LogStore(Path(directory)).page()['logs'], latest['logs'])

    def test_pending_jobs_survive_restart_without_replaying_started_jobs(self):
        with tempfile.TemporaryDirectory() as directory, patch('jsonstore.restrict_permissions'):
            path = Path(directory) / 'schedule_queue.jsonl'
            queue = ScheduleQueue(path)
            schedules = [{'id': 'a', 'code': '0001'}, {'id': 'b', 'car_no4': '1234', 'tickets': {'free': 1}}]
            queue.enqueue(schedules, '2026-09-11')
            first, second = queue.snapshot()
            queue.change(first['id'], 'running')
            restored = ScheduleQueue(path)
            restored.enqueue(schedules, '2026-09-11')
            self.assertEqual(len(restored.snapshot()), 2)
            self.assertEqual(restored.interrupted, {first['id']})
            self.assertEqual(restored.jobs[second['id']]['status'], 'pending')

    def test_damaged_journal_blocks_dispatch_without_preventing_app_import(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'schedule_queue.jsonl'
            path.write_bytes(b'{"id":')
            queue = ScheduleQueue(path)
            self.assertTrue(queue.has_pending())
            with self.assertRaises(RuntimeError):
                queue.enqueue([], '2026-09-11')

    def test_journal_write_failure_keeps_pending_state(self):
        with tempfile.TemporaryDirectory() as directory, patch('jsonstore.restrict_permissions'):
            path = Path(directory) / 'schedule_queue.jsonl'
            queue = ScheduleQueue(path)
            queue.enqueue([{'id': 'a', 'code': '0001'}], '2026-09-11')
            key = queue.snapshot()[0]['id']
            original = path.read_bytes()
            with patch('schedule_queue.os.fsync', side_effect=[OSError('full'), None]):
                with self.assertRaises(OSError):
                    queue.change(key, 'running')
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(queue.jobs[key]['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
