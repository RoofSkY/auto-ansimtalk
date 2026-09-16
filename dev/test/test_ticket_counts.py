import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_backup_api import load_isolated_app
import iparking
from parking_state import TicketCounts


class TicketApiTests(unittest.TestCase):
    def response(self, data, code=None):
        response = Mock()
        response.headers = {} if code is None else {'result-code': code}
        response.json.return_value = data
        return response

    def test_counts_all_stores_and_uses_current_history(self):
        data = {'parkingHistoryId': 'visit-1', 'myStoreApplyRequestTicketInfoList': [
            {'applyCount': 2}, {'applyCount': '3'}, {'applyCount': 0}],
            'otherStoreApplyRequestTicketInfoList': [{'applyCount': 20}],
            'enableAllocatedTicketInfoList': [{'applyCount': 25, 'remainingQuantity': 100}],
            'disableAllocatedTicketInfoList': [{'applyCount': 25}]}
        with patch.object(iparking, '_api', return_value=self.response(data)) as api:
            with patch.object(iparking, '_plid', return_value='lot'):
                self.assertEqual(iparking.get_applied_ticket_count({'parkingHistoryId': 'visit-1'}), 25)
        api.assert_called_once_with('GET', '/api/v2/stores/completions/lot/detail/visit-1')

    def test_other_store_only_completed_tickets_are_counted(self):
        data = {'myStoreApplyRequestTicketInfoList': [],
                'otherStoreApplyRequestTicketInfoList': [
                    {'applyCount': 2, 'discountStatus': 'APPLY_COMPLETE'}],
                'enableAllocatedTicketInfoList': [{'applyCount': 2, 'remainingQuantity': 100}]}
        with patch.object(iparking, '_api', return_value=self.response(data)):
            with patch.object(iparking, '_plid', return_value='lot'):
                self.assertEqual(iparking.get_applied_ticket_count({'parkingHistoryId': 1}), 2)

    def test_empty_list_is_zero_but_invalid_response_is_not_zero(self):
        fields = ('myStoreApplyRequestTicketInfoList', 'otherStoreApplyRequestTicketInfoList')
        for value in ([], None):
            with patch.object(iparking, '_api', return_value=self.response(dict.fromkeys(fields, value))):
                with patch.object(iparking, '_plid', return_value='lot'):
                    self.assertEqual(iparking.get_applied_ticket_count({'parkingHistoryId': 1}), 0)
        invalid = [{}, [], *({field: []} for field in fields),
                   {'parkingHistoryId': 2, **dict.fromkeys(fields, [])}]
        for field in fields:
            for value in ({}, *([{'applyCount': count}] for count in (None, True, -1, 1.2, 'bad'))):
                invalid.append({**dict.fromkeys(fields, []), field: value})
        for data in invalid:
            with self.subTest(data=data), patch.object(iparking, '_api', return_value=self.response(data)):
                with patch.object(iparking, '_plid', return_value='lot'):
                    with self.assertRaises(iparking.IparkingError):
                        iparking.get_applied_ticket_count({'parkingHistoryId': 1})

    def test_missing_history_and_server_error_do_not_return_zero(self):
        with patch.object(iparking, '_api') as api:
            with self.assertRaises(iparking.IparkingError):
                iparking.get_applied_ticket_count({})
            api.assert_not_called()
        with patch.object(iparking, '_api', return_value=self.response({}, '1303')):
            with patch.object(iparking, '_plid', return_value='lot'):
                with self.assertRaises(iparking.IparkingError):
                    iparking.get_applied_ticket_count({'parkingHistoryId': 1})


class TicketStateTests(unittest.TestCase):
    def test_read_started_before_mutation_cannot_restore_old_count(self):
        state = TicketCounts()
        state.update({'1234': {'history_id': 'old', 'count': 2}}, state.token())
        old = state.token()
        state.begin({'1234'})
        during = state.token()
        state.update({'1234': {'history_id': 'old', 'count': 2}}, during)
        self.assertIsNone(state.snapshot()['1234']['count'])
        state.finish({'1234'})
        for token in (old, during):
            state.update({'1234': {'history_id': 'old', 'count': 2}}, token)
            self.assertIsNone(state.snapshot()['1234']['count'])
        state.update({'1234': {'history_id': 'old', 'count': 3}}, state.token())
        self.assertEqual(state.snapshot()['1234']['count'], 3)

    def test_restore_or_account_change_discards_inflight_response(self):
        state = TicketCounts()
        token = state.token()
        state.clear()
        self.assertFalse(state.update({'1234': {'history_id': 'old', 'count': 2}}, token))
        self.assertEqual(state.snapshot(), {})

    def test_partial_update_preserves_other_vehicles_and_old_mutation_guard(self):
        state = TicketCounts()
        state.update({'1234': {'history_id': 'a', 'count': 2},
                      '5678': {'history_id': 'b', 'count': 3}}, state.token())
        old = state.token()
        state.begin({'1234'})
        state.finish({'1234'})
        state.update({'1234': {'history_id': 'a', 'count': 1}}, old, partial=True)
        self.assertIsNone(state.snapshot()['1234']['count'])
        self.assertEqual(state.snapshot()['5678']['count'], 3)
        state.update({'1234': {'history_id': 'a', 'count': 1}}, state.token(), partial=True)
        self.assertEqual(state.snapshot()['1234']['count'], 1)


class TicketPollingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m = load_isolated_app(self.tmp.name)
        self.addCleanup(self.m.action_runner.close)
        self.m.state.students = [{'id': 'a', 'name': 'Example', 'code': '',
                                  'car_no4s': ['1234', '11가1234', '5678']}]
        self.cars = {'1234': [{'parkingHistoryId': 'visit-a', 'carNumber': '11가1234'}], '5678': []}
        self.count = 2
        for target, name, value in ((self.m, '_notify_vehicle', Mock()),
                                    (iparking, '_api', Mock(side_effect=AssertionError('Unexpected network'))),
                                    (iparking, 'find_in_cars', Mock(side_effect=lambda number: self.cars[number]))):
            mock = patch.object(target, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.counter = patch.object(iparking, 'get_applied_ticket_count', side_effect=lambda car: self.count)
        self.fetch_count = self.counter.start()
        self.addCleanup(self.counter.stop)

    def test_aliases_share_detail_request_and_initial_snapshot_has_real_count(self):
        self.m._poll_once()
        self.fetch_count.assert_called_once()
        snapshot = self.m._vehicle_snapshot()
        self.assertEqual(snapshot['ticket_counts']['1234']['count'], 2)
        self.assertEqual(snapshot['ticket_counts']['11가1234']['history_id'], 'visit-a')
        self.assertEqual(snapshot['ticket_counts']['5678']['count'], 0)
        self.assertIn('1234', snapshot['cars'])

    def test_failure_hides_count_without_false_exit_and_later_recovers(self):
        self.m._poll_once()
        self.fetch_count.side_effect = RuntimeError('test offline')
        self.m._poll_once()
        self.assertIn('1234', self.m.state.prev_in_cars)
        self.assertIsNone(self.m.state.ticket_counts.snapshot()['1234']['count'])
        self.fetch_count.side_effect = lambda car: 1
        self.m._poll_once()
        self.assertEqual(self.m.state.ticket_counts.snapshot()['1234']['count'], 1)

    def test_exit_and_new_visit_replace_previous_visit_count(self):
        self.m._poll_once()
        self.cars['1234'] = []
        self.m._poll_once()
        self.assertEqual(self.m.state.ticket_counts.snapshot()['1234']['count'], 0)
        self.assertNotIn('1234', self.m.state.prev_in_cars)
        self.cars['1234'] = [{'parkingHistoryId': 'visit-new', 'carNumber': '11가1234'}]
        self.count = 0
        self.m._poll_once()
        self.assertEqual(self.m.state.ticket_counts.snapshot()['1234'], {'history_id': 'visit-new', 'count': 0})

    def test_vehicle_search_failure_does_not_preserve_a_verified_count(self):
        self.m._poll_once()
        with patch.object(iparking, 'find_in_cars', side_effect=RuntimeError('offline')):
            self.m._poll_once()
        self.assertIn('1234', self.m.state.prev_in_cars)
        self.assertIsNone(self.m.state.ticket_counts.snapshot()['1234']['count'])

    def test_registration_logs_use_resolved_full_plate(self):
        student = {**self.m.state.students[0], 'car_no4s': ['1234']}
        for result in ((True, 'ok'), (False, 'rejected'), RuntimeError('offline')):
            for tag in ('차량등록', '차량등록(예약)'):
                with self.subTest(result=result, tag=tag):
                    with patch.object(iparking, 'find_in_car', return_value=self.cars['1234'][0]):
                        with patch.object(iparking, 'apply_discount') as apply:
                            if isinstance(result, Exception):
                                apply.side_effect = result
                            else:
                                apply.return_value = result
                            with patch.object(self.m, 'emit_log') as emit:
                                self.m.do_vehicle(student, tickets={'free': 1}, tag=tag)
                                self.assertEqual(emit.call_args.args[1], '11가1234 Example')

    def test_partial_registration_requests_fresh_server_count(self):
        student = {**self.m.state.students[0], 'car_no4s': ['1234']}
        self.m._poll_once()
        with patch.object(iparking, 'find_in_car', return_value=self.cars['1234'][0]):
            with patch.object(iparking, 'manual_vehicle', return_value={'tickets': [
                    {'key': 'free', 'max': 1, 'ticket': {'discountId': 'free'}}]}), \
                    patch.object(iparking, 'apply_discount', return_value=(True, 'ok')) as apply:
                with patch.object(self.m, '_trigger_refresh') as refresh:
                    self.assertTrue(self.m._start_action(student, 'vehicle', tickets={'free': 2}))
                    self.m.action_runner._queues['vehicle'].join()
                    refresh.assert_called_with('vehicle', vehicle_suffixes={'1234'})
                    apply.assert_called_once()
                    self.assertEqual(apply.call_args.kwargs['count'], 1)
        self.assertIsNone(self.m.state.ticket_counts.snapshot()['1234']['count'])
        self.count = 1
        self.m._poll_once()
        self.assertEqual(self.m.state.ticket_counts.snapshot()['1234']['count'], 1)
        self.m.action_runner.close()

    def test_targeted_refresh_preserves_other_cars_and_updates_all_aliases(self):
        self.cars['5678'] = [{'parkingHistoryId': 'b', 'carNumber': '22나5678'}]
        self.m._poll_once()
        iparking.find_in_cars.reset_mock()
        self.fetch_count.reset_mock()
        self.m._notify_vehicle.reset_mock()
        self.count = 7
        self.m._poll_once({'1234'})
        iparking.find_in_cars.assert_called_once_with('1234')
        self.fetch_count.assert_called_once()
        rows = self.m.state.ticket_counts.snapshot()
        self.assertEqual([rows[k]['count'] for k in ('1234', '11가1234', '5678')], [7, 7, 2])
        self.assertIn('5678', self.m.state.prev_in_cars)
        self.m._notify_vehicle.assert_not_called()
        self.cars['1234'] = []
        self.m._poll_once({'1234'})
        self.assertEqual(self.m.state.prev_in_cars, {'5678'})

    def test_untracked_manual_vehicle_does_not_refresh_resident_list(self):
        self.m._poll_once({'9999'})
        iparking.find_in_cars.assert_not_called()
        self.fetch_count.assert_not_called()
        with patch.object(self.m, '_trigger_refresh') as refresh:
            self.m._manual_parking_finish('22나9999')
            refresh.assert_called_once_with('vehicle', vehicle_suffixes={'9999'})

    def test_thirty_vehicle_refresh_reduces_45_reads_to_two_for_one_target(self):
        self.m.state.students = [{'id': str(i), 'name': 'Example', 'car_no4s': [str(1000 + i)]}
                                 for i in range(30)]
        self.cars = {str(1000 + i): [{'parkingHistoryId': str(i), 'carNumber': '11가' + str(1000 + i)}]
                     if i < 15 else [] for i in range(30)}
        self.m._poll_once()
        self.assertEqual(iparking.find_in_cars.call_count + self.fetch_count.call_count, 45)
        iparking.find_in_cars.reset_mock()
        self.fetch_count.reset_mock()
        self.count = 4
        self.m._poll_once({'1000'})
        self.assertEqual(iparking.find_in_cars.call_count + self.fetch_count.call_count, 2)
        self.assertEqual(len(self.m.state.ticket_counts.snapshot()), 30)
        self.assertEqual(self.m.state.ticket_counts.snapshot()['1001']['count'], 2)

    def test_student_edit_during_query_cannot_restore_removed_vehicle(self):
        def search(number):
            self.m.state.students = []
            return self.cars[number]
        iparking.find_in_cars.side_effect = search
        self.assertFalse(self.m._poll_once({'1234'}))
        self.assertEqual(self.m.state.prev_in_cars, set())
        self.assertEqual(self.m.state.ticket_counts.snapshot(), {})
        self.fetch_count.assert_not_called()

    def test_entry_notification_and_green_row_precede_slow_ticket_query(self):
        self.m._poll_once()
        self.cars['5678'] = [{'parkingHistoryId': 'b', 'carNumber': '22나5678'}]
        entered, release = threading.Event(), threading.Event()
        self.fetch_count.side_effect = lambda car: (entered.set(), release.wait(3), 4)[-1]
        worker = threading.Thread(target=self.m._poll_once, args=({'5678'},))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertIn('5678', self.m.state.prev_in_cars)
            self.m._notify_vehicle.assert_called_once_with('입차', 'Example', '22나5678')
            self.assertIsNone(self.m.state.ticket_counts.snapshot()['5678']['count'])
        finally:
            release.set()
            worker.join(3)
        self.assertEqual(self.m.state.ticket_counts.snapshot()['5678']['count'], 4)
        self.m._poll_once({'5678'})
        self.assertEqual(self.m._notify_vehicle.call_count, 1)

    def test_full_and_partial_reads_are_serialized_and_old_counts_stay_invalid(self):
        self.m._poll_once()
        entered, release, second_done = threading.Event(), threading.Event(), threading.Event()
        self.fetch_count.side_effect = lambda car: (entered.set(), release.wait(3), 2)[-1]
        first = threading.Thread(target=self.m._poll_once)
        first.start()
        second = threading.Thread(target=lambda: (self.m._poll_once({'1234'}), second_done.set()))
        try:
            self.assertTrue(entered.wait(1))
            self.m.state.ticket_counts.begin({'1234'})
            self.m.state.ticket_counts.finish({'1234'})
            second.start()
            self.assertFalse(second_done.wait(.05))
            self.fetch_count.side_effect = lambda car: 5
        finally:
            release.set()
            first.join(3)
            if second.ident:
                second.join(3)
        self.assertTrue(second_done.is_set())
        self.assertEqual(self.m.state.ticket_counts.snapshot()['1234']['count'], 5)

    def test_partial_success_does_not_clear_other_vehicle_failure(self):
        def search(number):
            if number == '5678':
                raise RuntimeError('offline')
            return self.cars[number]
        iparking.find_in_cars.side_effect = search
        self.m._poll_once()
        self.m._poll_once({'1234'})
        self.assertFalse(self.m.state.service_health['iparking'])
        self.assertIn('5678', self.m.state.poll_errors)
        iparking.find_in_cars.side_effect = lambda number: self.cars[number]
        self.m._poll_once({'5678'})
        self.assertTrue(self.m.state.service_health['iparking'])

    def test_account_reset_discards_search_result_before_notification(self):
        self.m._poll_once()
        self.m._notify_vehicle.reset_mock()
        def search(number):
            self.m.state.ticket_counts.clear()
            return []
        iparking.find_in_cars.side_effect = search
        self.assertFalse(self.m._poll_once({'1234'}))
        self.m._notify_vehicle.assert_not_called()
        self.assertEqual(self.m.state.ticket_counts.snapshot(), {})

    def test_bulk_request_uses_available_count_once_and_reports_partial_success(self):
        student = {**self.m.state.students[0], 'car_no4s': ['1234']}
        with patch.object(iparking, 'manual_vehicle', return_value={'tickets': [
                {'key': 'paid', 'max': 42, 'ticket': {'discountId': 'paid'}}]}), \
                patch.object(iparking, 'apply_discount', return_value=(True, 'ok')) as apply, \
                patch.object(self.m, 'emit_log') as log:
            self.m.do_vehicle(student, {'paid': 100})
            apply.assert_called_once_with(self.cars['1234'][0], 'paid', count=42, ticket={'discountId': 'paid'})
            self.assertIn('42/100', log.call_args.args[2])
            self.assertFalse(log.call_args.args[3])

    def test_bulk_rejection_is_not_retried_and_free_limit_keeps_paid_request(self):
        student = {**self.m.state.students[0], 'car_no4s': ['1234']}
        for free_max in (0, 2):
            with self.subTest(free_max=free_max), \
                    patch.object(iparking, 'manual_vehicle', return_value={'tickets': [
                        {'key': 'free', 'max': free_max, 'ticket': {'discountId': 'free'}},
                        {'key': 'paid', 'max': 100, 'ticket': {'discountId': 'paid'}}]}), \
                    patch.object(iparking, 'apply_discount', side_effect=lambda car, kind, **kw: (kind == 'paid', 'result')) as apply:
                self.m.do_vehicle(student, {'free': 2, 'paid': 100})
                self.assertEqual(apply.call_count, 2 if free_max else 1)
                self.assertEqual(apply.call_args.args[1], 'paid')
                self.assertEqual(apply.call_args.kwargs['count'], 100)

    def test_uncertain_registration_stops_without_retry_or_other_ticket_write(self):
        student = {**self.m.state.students[0], 'car_no4s': ['1234']}
        with patch.object(iparking, 'manual_vehicle', return_value={'tickets': [
                {'key': 'free', 'max': 2, 'ticket': {'discountId': 'free'}},
                {'key': 'paid', 'max': 100, 'ticket': {'discountId': 'paid'}}]}), \
                patch.object(iparking, 'apply_discount', side_effect=TimeoutError('lost')) as apply:
            self.m.do_vehicle(student, {'free': 2, 'paid': 100})
            apply.assert_called_once()

    def test_unknown_available_count_prevents_registration(self):
        with patch.object(iparking, 'manual_vehicle', side_effect=iparking.IparkingError('unknown')), \
                patch.object(iparking, 'apply_discount') as apply:
            self.m.do_vehicle(self.m.state.students[0], {'paid': 100})
            apply.assert_not_called()


if __name__ == '__main__':
    unittest.main()
