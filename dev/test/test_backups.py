import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
import backups
import jsonstore

STUDENTS = [{"id": "old-student", "name": "테스트 입소자", "code": "0001", "car_no4s": ["12가3456", "7890"]}]
SCHEDULES = [{"id": "old-schedule", "time": "16:30", "days": [0, 2, 4], "code": "0001",
              "car_no4": "3456", "tickets": {"free": 2}, "enabled": True, "last_run": "2026-09-11"}]
LIMITS = {"free": 2, "paid": 100}


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.permissions = patch.object(jsonstore, "restrict_permissions")
        self.permissions.start()
        self.addCleanup(self.permissions.stop)
        self.data = backups.create(STUDENTS, SCHEDULES, "1.0.0")

    def test_round_trip_excludes_secrets_and_resets_identity_and_schedules(self):
        students = copy.deepcopy(STUDENTS)
        students[0]["password"] = "secret"
        raw = backups.create(students, SCHEDULES, "1.0.0")
        validated = backups.validate(backups.decode(json.dumps(raw, ensure_ascii=False).encode()), LIMITS)
        self.assertNotIn("secret", json.dumps(validated))
        restored_students, restored_schedules = backups.materialize(validated)
        self.assertEqual(restored_students[0]["car_no4s"], STUDENTS[0]["car_no4s"])
        self.assertEqual(restored_students[0]["code"], "0001")
        self.assertNotEqual(restored_students[0]["id"], STUDENTS[0]["id"])
        self.assertFalse(restored_schedules[0]["enabled"])
        self.assertEqual(restored_schedules[0]["last_run"], "")
        self.assertNotEqual(restored_schedules[0]["id"], SCHEDULES[0]["id"])
        self.assertTrue(SCHEDULES[0]["enabled"])

    def test_empty_backup_is_valid(self):
        backups.validate(backups.create([], [], "1.0"), LIMITS)

    def test_whitespace_normalized_and_unicode_preserved(self):
        self.data["students"][0]["name"] = "  홍길동  "
        self.data["schedules"][0]["time"] = " 16:30 "
        data = backups.validate(self.data, LIMITS)
        students, schedules = backups.materialize(data)
        self.assertEqual(students[0]["name"], "홍길동")
        self.assertEqual(schedules[0]["time"], "16:30")

    def test_rejects_malformed_files(self):
        for raw in (b'{', b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff', b' ' * (backups.MAX_BYTES + 1)):
            with self.subTest(raw=raw[:30]), self.assertRaises(backups.BackupError):
                backups.decode(raw)

    def test_rejects_invalid_schema_fields(self):
        changes = [lambda d: d.update(schema_version=2), lambda d: d.update(schema_version=True),
                   lambda d: d.update(format="another-app"), lambda d: d.update(created_at="invalid"),
                   lambda d: d.update(password="secret"), lambda d: d.update(students={}),
                   lambda d: d["students"][0].update(car_no4s="1234"),
                   lambda d: d["students"][0].update(name="\ud800"),
                   lambda d: d["schedules"][0].update(time="25:00"),
                   lambda d: d["schedules"][0].update(days=[True]),
                   lambda d: d["schedules"][0].update(days=[0, 0]),
                   lambda d: d["schedules"][0].update(days=[]),
                   lambda d: d["schedules"][0].update(tickets={"free": 3}),
                   lambda d: d["schedules"][0].update(tickets={"free": True}),
                   lambda d: d["schedules"][0].update(enabled="yes")]
        for index, change in enumerate(changes):
            data = copy.deepcopy(self.data)
            change(data)
            with self.subTest(index=index), self.assertRaises(backups.BackupError):
                backups.validate(data, LIMITS)

    def seed(self):
        jsonstore.save(self.root / "students.json", STUDENTS)
        jsonstore.save(self.root / "schedules.json", SCHEDULES)

    def read(self, name):
        return json.loads((self.root / f"{name}.json").read_text(encoding="utf-8"))

    def test_partial_write_rolls_back_both_files(self):
        self.seed()
        real_save = jsonstore.save
        failed = False

        def fail_once(path, data, **kwargs):
            nonlocal failed
            if path.name == "schedules.json" and not failed:
                failed = True
                path.write_text("broken", encoding="utf-8")
                raise OSError("disk failure")
            return real_save(path, data, **kwargs)

        with patch.object(jsonstore, "save", side_effect=fail_once), self.assertRaises(backups.BackupError):
            backups.replace(self.root, [], [], STUDENTS, SCHEDULES)
        self.assertEqual(self.read("students"), STUDENTS)
        self.assertEqual(self.read("schedules"), SCHEDULES)
        self.assertFalse((self.root / "restore_pending.json").exists())

    def test_recovery_after_interruption(self):
        self.seed()
        jsonstore.save(self.root / "restore_pending.json", {"students": STUDENTS, "schedules": SCHEDULES})
        jsonstore.save(self.root / "students.json", [])
        backups.recover(self.root)
        self.assertEqual(self.read("students"), STUDENTS)
        self.assertEqual(self.read("schedules"), SCHEDULES)

    def test_failed_rollback_keeps_recovery_journal(self):
        self.seed()
        real_save = jsonstore.save

        def fail(path, data, **kwargs):
            if path.name == "schedules.json":
                raise OSError("disk failure")
            return real_save(path, data, **kwargs)

        with patch.object(jsonstore, "save", side_effect=fail), self.assertRaises(backups.BackupError):
            backups.replace(self.root, [], [], STUDENTS, SCHEDULES)
        self.assertTrue((self.root / "restore_pending.json").exists())
        backups.recover(self.root)
        self.assertEqual(self.read("schedules"), SCHEDULES)


if __name__ == "__main__":
    unittest.main()
