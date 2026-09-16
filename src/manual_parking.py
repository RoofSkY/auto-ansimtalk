"""직접 조회한 차량의 주차권 등록과 중복 요청 방지."""

import copy
import re
import threading
import time
import uuid

import iparking
from actions import resource_keys


class ParkingError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class ManualParking:
    def __init__(self, runner, log, begin, finish):
        self.runner, self.log = runner, log
        self.begin, self.finish = begin, finish
        self.lock = threading.RLock()
        self.entries = {}
        self.generation = 0

    def clear(self):
        with self.lock:
            self.generation += 1
            self.entries = {k: v for k, v in self.entries.items() if v['state'] == 'pending'}

    @staticmethod
    def search(number):
        number = re.sub(r'\s+', '', number)
        if not re.fullmatch(r'(?:[0-9가-힣]{1,12})?[0-9]{4}', number):
            raise ParkingError('차량번호 뒤 4자리 또는 전체 번호를 입력해 주세요.')
        cars = iparking.find_in_cars(number[-4:])
        matches = {}
        for car in cars:
            plate, history = car.get('carNumber'), car.get('parkingHistoryId')
            if not plate or history is None or history == '':
                continue
            if len(number) > 4 and re.sub(r'\s+', '', plate) != number:
                continue
            matches[str(history)] = {'plate': plate, 'history_id': str(history),
                                      'entered_at': car.get('inCarDateTime') or ''}
        return list(matches.values())

    @staticmethod
    def current_car(plate, history):
        number = re.sub(r'\s+', '', plate)
        if not re.fullmatch(r'(?:[0-9가-힣]{1,12})?[0-9]{4}', number):
            raise ParkingError('차량번호를 확인해 주세요.')
        for car in iparking.find_in_cars(number[-4:]):
            if car.get('carNumber') == plate and str(car.get('parkingHistoryId')) == str(history):
                return copy.deepcopy(car)
        raise ParkingError('차량이 출차했거나 입차 정보가 변경되었습니다. 다시 조회해 주세요.', 409)

    @staticmethod
    def public_view(view):
        return {**view, **{field: [{k: v for k, v in t.items() if k != 'ticket'} for t in view[field]]
                          for field in ('tickets', 'applied')}}

    def detail(self, plate, history):
        with self.lock:
            generation = self.generation
        car = self.current_car(plate, history)
        view = iparking.manual_vehicle(car)
        with self.lock:
            if generation != self.generation:
                raise ParkingError('계정 정보가 변경되었습니다. 다시 조회해 주세요.', 409)
            now = time.monotonic()
            self.entries = {k: v for k, v in self.entries.items()
                            if v['state'] == 'pending' or now - v['created'] < 3600}
            if len(self.entries) >= 1000:
                raise ParkingError('조회 요청이 많습니다. 잠시 후 다시 시도해 주세요.', 503)
            token = uuid.uuid4().hex
            self.entries[token] = {'created': now, 'car': car, 'state': 'ready', 'results': [],
                                   'view': view, 'message': '', 'generation': generation}
        return {**self.public_view(view), 'token': token}

    def _entry(self, token):
        entry = self.entries.get(token)
        if entry is None:
            raise ParkingError('조회 정보가 만료되었거나 앱이 재시작되었습니다. 처리 기록과 등록 내역을 확인해 주세요.', 404)
        return entry

    def status(self, token):
        with self.lock:
            entry = self._entry(token)
            return copy.deepcopy({k: entry[k] for k in ('state', 'results', 'message')})

    def submit(self, token, counts):
        if (not isinstance(counts, dict) or not counts or set(counts) - set(iparking.TICKETS)
                or any(type(n) is not int or n < 0 or n > iparking.TICKETS[k]['max'] for k, n in counts.items())
                or not any(counts.values())):
            raise ParkingError('추가할 주차권 수량을 확인해 주세요.')
        counts = {k: n for k, n in counts.items() if n}
        with self.lock:
            entry = self._entry(token)
            if entry['state'] != 'ready':
                if entry.get('operation') != 'register' or entry.get('counts') != counts:
                    raise ParkingError('이미 접수한 요청입니다. 결과를 확인해 주세요.', 409)
                return self.status(token)
            if time.monotonic() - entry['created'] > 600 or entry['generation'] != self.generation:
                raise ParkingError('조회 정보가 만료되었습니다. 차량을 다시 조회해 주세요.', 409)
            limits = {t['key']: t['max'] for t in entry['view']['tickets']}
            if any(n > limits.get(k, 0) for k, n in counts.items()):
                raise ParkingError('등록 가능한 수량을 초과했습니다. 차량을 다시 조회해 주세요.', 409)
            car = entry['car']
            keys = resource_keys({'car_no4s': [car['carNumber']]}, 'vehicle')
            entry.update(state='pending', counts=counts, operation='register')
            try:
                started = self.runner.start(keys, lambda: self._run(entry), kind='vehicle')
            except Exception:
                entry['state'] = 'ready'
                raise
            if not started:
                entry['state'] = 'ready'
                raise ParkingError('같은 차량이 처리 중이거나 대기열이 가득 찼습니다. 잠시 후 다시 시도해 주세요.', 409)
            return self.status(token)

    @staticmethod
    def cancel_ticket(view, key, count):
        matches = [t for t in view['applied'] if not t['other_store'] and t['key'] == key]
        if len(matches) != 1 or count > matches[0]['cancel_max']:
            raise ParkingError('취소 가능한 주차권 또는 수량이 변경되었습니다. 차량을 다시 조회해 주세요.', 409)
        return matches[0]

    def submit_cancel(self, token, key, count):
        if not isinstance(key, str) or not key or type(count) is not int or not 1 <= count <= 100:
            raise ParkingError('취소할 주차권과 수량을 확인해 주세요.')
        with self.lock:
            entry = self._entry(token)
            if entry['state'] != 'ready':
                if entry.get('operation') != 'cancel' or entry.get('cancel') != (key, count):
                    raise ParkingError('이미 접수된 요청입니다. 결과를 확인해 주세요.', 409)
                return self.status(token)
            if time.monotonic() - entry['created'] > 600 or entry['generation'] != self.generation:
                raise ParkingError('조회 정보가 만료되었습니다. 차량을 다시 조회해 주세요.', 409)
            self.cancel_ticket(entry['view'], key, count)
            keys = resource_keys({'car_no4s': [entry['car']['carNumber']]}, 'vehicle')
            entry.update(state='pending', operation='cancel', cancel=(key, count))
            try:
                started = self.runner.start(keys, lambda: self._run_cancel(entry), kind='vehicle')
            except Exception:
                entry['state'] = 'ready'
                raise
            if not started:
                entry['state'] = 'ready'
                raise ParkingError('같은 차량을 처리 중이거나 대기열이 가득 찼습니다. 잠시 후 다시 시도해 주세요.', 409)
            return self.status(token)

    def _run_cancel(self, entry):
        results, state, message = [], 'failed', ''
        plate = entry['car']['carNumber']
        try:
            self.begin(plate)
            car = self.current_car(plate, entry['car']['parkingHistoryId'])
            key, count = entry['cancel']
            ticket = self.cancel_ticket(iparking.manual_vehicle(car), key, count)
            try:
                ok, detail = iparking.cancel_discount(car, count=count, ticket=ticket['ticket'])
                state = 'success' if ok else 'failed'
                message = '주차권 취소를 완료했습니다.' if ok else '주차권을 취소하지 못했습니다.'
            except Exception:
                ok, state = False, 'unknown'
                detail = '취소 응답을 확인하지 못했습니다. 등록 내역을 확인해 주세요.'
                message = detail
            results.append({'label': ticket['label'], 'count': count, 'ok': ok, 'message': detail})
            self.log('차량취소', f'{plate} 직접 취소', f"{ticket['label']} {count}매 - {detail}", ok)
        except Exception as exc:
            message = str(exc) if isinstance(exc, (ParkingError, iparking.IparkingError)) else '차량 정보를 확인하지 못했습니다. 다시 조회해 주세요.'
            self.log('차량취소', f'{plate} 직접 취소', message, False)
        finally:
            try:
                self.finish(plate)
            finally:
                with self.lock:
                    entry.update(state=state, results=results, message=message)

    def _run(self, entry):
        results, state, message = [], 'failed', ''
        plate = entry['car']['carNumber']
        try:
            self.begin(plate)
            car = self.current_car(plate, entry['car']['parkingHistoryId'])
            view = iparking.manual_vehicle(car)
            options = {t['key']: t for t in view['tickets']}
            for key, count in entry['counts'].items():
                ticket = options[key]
                if count > ticket['max']:
                    detail = '잔여 수량 또는 등록 가능 수량이 변경되어 이 주차권을 등록하지 못했습니다.'
                    results.append({'label': ticket['label'], 'count': count, 'ok': False, 'message': detail})
                    self.log('차량등록', f'{plate} 직접 등록', f"{ticket['label']} {count}매 - {detail}", False)
                    continue
                try:
                    ok, detail = iparking.apply_discount(car, key, count=count, ticket=ticket['ticket'])
                    results.append({'label': ticket['label'], 'count': count, 'ok': ok, 'message': detail})
                    self.log('차량등록', f'{plate} 직접 등록', f"{ticket['label']} {count}매 - {detail}", ok)
                except Exception:
                    results.append({'label': ticket['label'], 'count': count, 'ok': False,
                                    'message': '응답을 확인하지 못했습니다. 등록 내역을 확인해 주세요.'})
                    state = 'unknown'
                    self.log('차량등록', f'{plate} 직접 등록', results[-1]['message'], False)
                    break
            if state != 'unknown':
                successes = sum(result['ok'] for result in results)
                state = 'success' if successes == len(entry['counts']) else 'partial' if successes else 'failed'
            message = {'success': '주차권 등록을 완료했습니다.', 'partial': '일부 주차권만 등록되었습니다. 등록 내역을 확인해 주세요.',
                       'failed': '주차권을 등록하지 못했습니다.', 'unknown': '등록 결과를 확인하지 못했습니다. 처리 기록과 등록 내역을 확인해 주세요.'}[state]
        except Exception as exc:
            message = str(exc) if isinstance(exc, (ParkingError, iparking.IparkingError)) else '차량 정보를 확인하지 못했습니다. 다시 조회해 주세요.'
            self.log('차량등록', f'{plate} 직접 등록', message, False)
        finally:
            try:
                self.finish(plate)
            finally:
                with self.lock:
                    entry.update(state=state, results=results, message=message)
