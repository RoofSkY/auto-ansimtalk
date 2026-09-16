import json
import tempfile
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


class TicketPollingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m = load_isolated_app(self.tmp.name)
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

    def test_partial_registration_requests_fresh_server_count(self):
        student = {**self.m.state.students[0], 'car_no4s': ['1234']}
        self.m._poll_once()
        with patch.object(iparking, 'find_in_car', return_value=self.cars['1234'][0]):
            with patch.object(iparking, 'apply_discount', side_effect=[(False, 'bulk'), (True, 'ok'), (False, 'limit')]):
                with patch.object(self.m, '_trigger_refresh') as refresh:
                    self.assertTrue(self.m._start_action(student, 'vehicle', tickets={'free': 2}))
                    self.m.action_runner._queues['vehicle'].join()
                    refresh.assert_called_with('vehicle')
        self.assertIsNone(self.m.state.ticket_counts.snapshot()['1234']['count'])
        self.count = 1
        self.m._poll_once()
        self.assertEqual(self.m.state.ticket_counts.snapshot()['1234']['count'], 1)
        self.m.action_runner.close()


if __name__ == '__main__':
    unittest.main()
