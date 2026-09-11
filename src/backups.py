"""입소자·예약 백업 형식과 중단된 복원의 복구 처리."""

import copy
import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

import jsonstore

MAX_BYTES = 2 * 1024 * 1024
FORMAT = "auto-ansimtalk-backup"
SCHEMA_VERSION = 1


class BackupError(ValueError):
    pass


def _text(value, label, limit=100, *, empty=True):
    if (not isinstance(value, str) or len(value) > limit or (not empty and not value.strip())
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise BackupError(f"{label} 항목을 확인해 주세요.")
    return value.strip()


def _fields(value, required, label, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys():
        raise BackupError(f"{label}의 필수 항목이 누락되었습니다.")
    if value.keys() - set(required) - set(optional):
        raise BackupError(f"{label}에 지원하지 않는 항목이 있습니다.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BackupError("파일에 중복된 항목 이름이 있습니다.")
        result[key] = value
    return result


def decode(raw: bytes):
    if len(raw) > MAX_BYTES:
        raise BackupError("백업 파일은 2MB 이하로 선택해 주세요.")
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_number)
    except BackupError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise BackupError("올바른 JSON 백업 파일을 선택해 주세요.") from exc


def _invalid_number(value):
    raise BackupError("잘못된 숫자 형식입니다.")


def validate(data, ticket_limits):
    _fields(data, ("format", "schema_version", "app_version", "created_at", "students", "schedules"), "백업")
    if data["format"] != FORMAT:
        raise BackupError("이 앱에서 저장한 백업 파일이 아닙니다.")
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise BackupError("지원하지 않는 백업 버전입니다. 앱 버전을 확인해 주세요.")
    _text(data["app_version"], "앱 버전", empty=False)
    try:
        stamp = datetime.fromisoformat(_text(data["created_at"], "백업 시각", empty=False))
        if stamp.tzinfo is None:
            raise ValueError()
    except ValueError as exc:
        raise BackupError("백업 시각 형식이 올바르지 않습니다.") from exc
    for key in ("students", "schedules"):
        if not isinstance(data[key], list) or len(data[key]) > 10000:
            raise BackupError("입소자·예약 목록 형식이나 개수를 확인해 주세요.")
    for s in data["students"]:
        _fields(s, ("name", "code", "car_no4s"), "입소자")
        _text(s["name"], "이름", empty=False)
        # 기존 입소자 폼에서 허용한 출석번호와 차량번호도 손실 없이 보관한다.
        _text(s["code"], "출석번호")
        if not isinstance(s["car_no4s"], list) or len(s["car_no4s"]) > 100:
            raise BackupError("차량번호 목록을 확인해 주세요.")
        for car in s["car_no4s"]:
            _text(car, "차량번호", empty=False)
    for s in data["schedules"]:
        _fields(s, ("time", "days", "code", "car_no4", "tickets", "enabled"), "예약")
        clock = _text(s["time"], "예약 시각")
        if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", clock):
            raise BackupError("예약 시각은 HH:MM 형식이어야 합니다.")
        days = s["days"]
        if (not isinstance(days, list) or not 1 <= len(days) <= 5
                or any(type(d) is not int or not 0 <= d <= 4 for d in days)
                or len(set(days)) != len(days)):
            raise BackupError("예약 요일을 확인해 주세요.")
        code = _text(s["code"], "예약 출석번호")
        car = _text(s["car_no4"], "예약 차량번호")
        if not code and not car:
            raise BackupError("예약에 출석번호 또는 차량번호가 필요합니다.")
        if code and not re.fullmatch(r"[0-9]{4}", code):
            raise BackupError("예약 출석번호는 숫자 4자리여야 합니다.")
        if car and (len(car) < 4 or not car[-4:].isdigit()):
            raise BackupError("예약 차량번호의 마지막 4자리를 확인해 주세요.")
        tickets = s["tickets"]
        if not isinstance(tickets, dict) or (car and not tickets):
            raise BackupError("예약 주차권 매수를 확인해 주세요.")
        for kind, count in tickets.items():
            if kind not in ticket_limits or type(count) is not int or not 1 <= count <= ticket_limits[kind]:
                raise BackupError("예약 주차권 종류나 최대 매수를 확인해 주세요.")
        if type(s["enabled"]) is not bool:
            raise BackupError("예약 사용 여부 형식이 올바르지 않습니다.")
    normalized = copy.deepcopy(data)
    for s in normalized["students"]:
        s["name"], s["code"] = s["name"].strip(), s["code"].strip()
        s["car_no4s"] = [car.strip() for car in s["car_no4s"]]
    for s in normalized["schedules"]:
        for key in ("time", "code", "car_no4"):
            s[key] = s[key].strip()
        s["days"] = sorted(s["days"])
    return normalized


def create(students, schedules, version):
    student_defaults = {"name": "", "code": "", "car_no4s": []}
    schedule_defaults = {"time": "", "days": [], "code": "", "car_no4": "", "tickets": {}, "enabled": True}
    return {
        "format": FORMAT, "schema_version": SCHEMA_VERSION, "app_version": version,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "students": [{k: copy.deepcopy(s.get(k, default)) for k, default in student_defaults.items()} for s in students],
        "schedules": [{k: copy.deepcopy(s.get(k, default)) for k, default in schedule_defaults.items()} for s in schedules],
    }


def revision(students, schedules):
    raw = json.dumps([students, schedules], ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def materialize(data):
    students = [{**s, "id": uuid.uuid4().hex} for s in copy.deepcopy(data["students"])]
    schedules = [{**s, "id": str(uuid.uuid4()), "enabled": False, "last_run": ""}
                 for s in copy.deepcopy(data["schedules"])]
    return sorted(students, key=lambda s: s["name"].strip()), schedules


def recover(config_dir: Path):
    journal = config_dir / "restore_pending.json"
    if not journal.exists():
        return
    # 완료 표식을 지우기 전 종료됐다면 다음 실행에서도 두 파일을 함께 되돌린다.
    data = json.loads(journal.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BackupError("중단된 복원의 복구 파일을 확인해 주세요.")
    for name in ("students", "schedules"):
        if not isinstance(data.get(name), list):
            raise BackupError("중단된 복원의 복구 파일을 확인해 주세요.")
    for name in ("students", "schedules"):
        jsonstore.save(config_dir / f"{name}.json", data[name], private=True)
    journal.unlink()


def replace(config_dir: Path, students, schedules, previous_students, previous_schedules):
    journal = config_dir / "restore_pending.json"
    jsonstore.save(journal, {"students": previous_students, "schedules": previous_schedules}, private=True)
    try:
        jsonstore.save(config_dir / "students.json", students, private=True)
        jsonstore.save(config_dir / "schedules.json", schedules, private=True)
        journal.unlink()
    except Exception as exc:
        try:
            recover(config_dir)
        except Exception as rollback_error:
            raise BackupError("복원을 중단했습니다. 자동 복구를 완료하지 못해 데이터 변경을 잠갔습니다. 저장 공간·파일 권한을 확인한 뒤 앱을 다시 시작해 주세요.") from rollback_error
        raise BackupError("복원에 실패하여 기존 데이터로 되돌렸습니다. 저장 공간·파일 권한을 확인해 주세요.") from exc
