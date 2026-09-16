import copy
import threading
import unittest
from unittest.mock import Mock, patch

from test_backup_api import load_isolated_app
from actions import ActionRunner
from manual_parking import ManualParking, ParkingError
import iparking


CAR = {'carNumber': '123가6595', 'parkingHistoryId': 'visit-1', 'inCarDateTime': '2026-09-16 13:20'}
DETAIL = {'carNumber': CAR['carNumber'], 'inCarDateTime': CAR['inCarDateTime'],
          'totalInParkingTime': 142, 'isConsumed': False,
          'myStoreApplyRequestTicketInfoList': [],
          'otherStoreApplyRequestTicketInfoList': [{'discountName': '무료권', 'applyCount': 2}],
          'enableAllocatedTicketInfoList': [
              {'discountId': 'free-id', 'discountName': '무료권', 'discountClassification': 'FREE',
               'remainingQuantity': 128, 'availableApplyCount': 2},
              {'discountId': 'paid-id', 'discountName': '유료권', 'discountClassification': 'PAID',
               'remainingQuantity': 42, 'availableApplyCount': 100}],
          'disableAllocatedTicketInfoList': []}


class ManualParkingTests(unittest.TestCase):
    def setUp(self):
        self.runner = ActionRunner(Mock(), Mock())
        self.addCleanup(self.runner.close)
        self.log, self.begin, self.finish = Mock(), Mock(), Mock()
        self.desk = ManualParking(self.runner, self.log, self.begin, self.finish)
        for name, mock in [('_api', Mock(side_effect=AssertionError('Unexpected external API'))),
                           ('find_in_cars', Mock(return_value=[CAR])),
                           ('get_vehicle_detail', Mock(return_value=copy.deepcopy(DETAIL))),
                           ('apply_discount', Mock(return_value=(True, '완료'))),
                           ('cancel_discount', Mock(return_value=(True, '취소 완료')))]:
            patcher = patch.object(iparking, name, mock)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.apply = iparking.apply_discount

    def select(self):
        return self.desk.detail(CAR['carNumber'], CAR['parkingHistoryId'])

    def wait(self, token):
        self.runner._queues['vehicle'].join()
        return self.desk.status(token)

    def test_search_lists_collisions_and_full_plate_selects_exact_vehicle(self):
        iparking.find_in_cars.return_value = [CAR, {**CAR, 'carNumber': '55나6595', 'parkingHistoryId': 'visit-2'}]
        self.assertEqual(len(self.desk.search('6595')), 2)
        self.assertEqual(self.desk.search('123가 6595')[0]['history_id'], 'visit-1')
        self.assertEqual(len(self.desk.search('123가 6595')), 1)
        self.assertEqual(self.desk.search('66가6595'), [])
        for number in ('', '12', '../6595', '１２３４'):
            with self.assertRaises(ParkingError):
                self.desk.search(number)

    def test_detail_reports_stock_applied_time_and_redacts_raw_ticket(self):
        result = self.select()
        self.assertEqual(result['minutes'], 142)
        self.assertEqual(result['applied'][0]['count'], 2)
        self.assertEqual(result['tickets'][0]['remaining'], 128)
        self.assertEqual(result['tickets'][1]['max'], 42)
        self.assertNotIn('ticket', result['tickets'][0])

    def test_repeated_submit_is_idempotent_and_logs_full_plate(self):
        token = self.select()['token']
        self.desk.submit(token, {'free': 1})
        self.assertEqual(self.wait(token)['state'], 'success')
        self.desk.submit(token, {'free': 1})
        self.assertEqual(self.apply.call_count, 1)
        self.assertEqual(self.log.call_args.args[1], '123가6595 직접 등록')
        self.assertEqual(self.apply.call_args.kwargs['ticket']['discountId'], 'free-id')
        with self.assertRaises(ParkingError):
            self.desk.submit(token, {'paid': 1})

    def test_zero_invalid_and_excessive_counts_are_rejected(self):
        token = self.select()['token']
        for counts in ({}, {'free': 0}, {'free': True}, {'free': -1}, {'free': 1.5},
                       {'free': '1'}, {'paid': 43}, {'other': 1}, {'free': 3}, None):
            with self.subTest(counts=counts), self.assertRaises(ParkingError):
                self.desk.submit(token, counts)
        self.apply.assert_not_called()

    def test_numeric_history_id_keeps_server_type_for_registration(self):
        iparking.find_in_cars.return_value = [{**CAR, 'parkingHistoryId': 12345}]
        token = self.desk.detail(CAR['carNumber'], '12345')['token']
        self.desk.submit(token, {'free': 1})
        self.assertEqual(self.wait(token)['state'], 'success')
        self.assertEqual(self.apply.call_args.args[0]['parkingHistoryId'], 12345)

    def test_registered_car_and_manual_request_share_resource_lock(self):
        token = self.select()['token']
        release = threading.Event()
        self.runner.start({'vehicle:car:6595'}, lambda: release.wait(3))
        try:
            with self.assertRaises(ParkingError):
                self.desk.submit(token, {'free': 1})
            self.assertEqual(self.desk.status(token)['state'], 'ready')
            self.apply.assert_not_called()
        finally:
            release.set()
            self.wait(token)

    def test_departure_or_new_visit_prevents_registration(self):
        token = self.select()['token']
        iparking.find_in_cars.return_value = [{**CAR, 'parkingHistoryId': 'new-visit'}]
        self.desk.submit(token, {'free': 1})
        self.assertEqual(self.wait(token)['state'], 'failed')
        self.apply.assert_not_called()
        self.finish.assert_called_once()

    def test_changed_stock_is_revalidated_before_any_write(self):
        token = self.select()['token']
        iparking.get_vehicle_detail.return_value['enableAllocatedTicketInfoList'][0]['remainingQuantity'] = 0
        self.desk.submit(token, {'free': 1})
        self.assertEqual(self.wait(token)['state'], 'failed')
        self.apply.assert_not_called()

    def test_partial_success_is_reported_without_automatic_retry(self):
        token = self.select()['token']
        self.apply.side_effect = [(True, '완료'), (False, '제한')]
        self.desk.submit(token, {'free': 1, 'paid': 1})
        result = self.wait(token)
        self.assertEqual(result['state'], 'partial')
        self.assertEqual([r['ok'] for r in result['results']], [True, False])
        self.assertEqual(self.apply.call_count, 2)

    def test_lost_response_is_unknown_and_stops_further_writes(self):
        token = self.select()['token']
        self.apply.side_effect = TimeoutError('lost')
        self.desk.submit(token, {'free': 1, 'paid': 1})
        self.assertEqual(self.wait(token)['state'], 'unknown')
        self.desk.submit(token, {'free': 1, 'paid': 1})
        self.assertEqual(self.apply.call_count, 1)

    def test_free_limit_rejection_does_not_block_requested_paid_ticket(self):
        token = self.select()['token']
        self.apply.side_effect = [(False, '무료권 한도 초과'), (True, '유료권 등록 완료')]
        self.desk.submit(token, {'free': 1, 'paid': 1})
        result = self.wait(token)
        self.assertEqual(result['state'], 'partial')
        self.assertEqual([r['ok'] for r in result['results']], [False, True])
        self.assertEqual([call.args[1] for call in self.apply.call_args_list], ['free', 'paid'])
        self.desk.submit(token, {'free': 1, 'paid': 1})
        self.assertEqual(self.apply.call_count, 2)

    def test_changed_free_limit_only_skips_free_and_still_applies_paid(self):
        token = self.select()['token']
        iparking.get_vehicle_detail.return_value['enableAllocatedTicketInfoList'][0]['availableApplyCount'] = 0
        self.desk.submit(token, {'free': 1, 'paid': 1})
        result = self.wait(token)
        self.assertEqual(result['state'], 'partial')
        self.assertEqual([r['ok'] for r in result['results']], [False, True])
        self.apply.assert_called_once()
        self.assertEqual(self.apply.call_args.args[1], 'paid')

    def test_paid_only_request_works_with_free_limit_already_full(self):
        iparking.get_vehicle_detail.return_value['enableAllocatedTicketInfoList'][0]['availableApplyCount'] = 0
        token = self.select()['token']
        self.desk.submit(token, {'free': 0, 'paid': 1})
        self.assertEqual(self.wait(token)['state'], 'success')
        self.apply.assert_called_once()
        self.assertEqual(self.apply.call_args.args[1], 'paid')

    def test_free_failure_does_not_add_unrequested_paid_ticket(self):
        token = self.select()['token']
        self.apply.return_value = (False, '무료권 한도 초과')
        self.desk.submit(token, {'free': 1})
        self.assertEqual(self.wait(token)['state'], 'failed')
        self.apply.assert_called_once()
        self.assertEqual(self.apply.call_args.args[1], 'free')

    def test_account_change_and_expiry_invalidate_selection(self):
        token = self.select()['token']
        self.desk.entries[token]['created'] -= 601
        with self.assertRaises(ParkingError):
            self.desk.submit(token, {'free': 1})
        self.desk.clear()
        with self.assertRaises(ParkingError):
            self.desk.submit(token, {'free': 1})
        self.apply.assert_not_called()

    def test_unknown_quantity_and_consumed_visit_disable_registration(self):
        for key, value in [('remainingQuantity', None), ('availableApplyCount', None)]:
            data = copy.deepcopy(DETAIL)
            data['enableAllocatedTicketInfoList'][0][key] = value
            iparking.get_vehicle_detail.return_value = data
            self.assertEqual(self.select()['tickets'][0]['max'], 0)
        data['isConsumed'] = True
        self.assertTrue(all(t['max'] == 0 for t in self.select()['tickets']))

    def test_rejected_runner_does_not_leave_request_pending(self):
        token = self.select()['token']
        with patch.object(self.runner, 'start', side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError):
                self.desk.submit(token, {'free': 1})
        self.assertEqual(self.desk.status(token)['state'], 'ready')

    def select_cancellable(self):
        iparking.get_vehicle_detail.return_value['myStoreApplyRequestTicketInfoList'] = [
            {'discountId': 'applied-old-id', 'discountName': '기존 무료권', 'applyCount': 2}]
        return self.select()

    def test_cancel_exact_applied_ticket_once_and_refresh_badge(self):
        view = self.select_cancellable()
        self.assertNotIn('ticket', view['applied'][0])
        self.assertEqual(view['applied'][1]['cancel_max'], 0)
        token = view['token']
        self.desk.submit_cancel(token, 'applied-old-id', 1)
        self.assertEqual(self.wait(token)['state'], 'success')
        self.desk.submit_cancel(token, 'applied-old-id', 1)
        iparking.cancel_discount.assert_called_once()
        self.assertEqual(iparking.cancel_discount.call_args.kwargs['ticket']['discountId'], 'applied-old-id')
        self.assertEqual(iparking.cancel_discount.call_args.kwargs['count'], 1)
        self.begin.assert_called_once_with(CAR['carNumber'])
        self.finish.assert_called_once_with(CAR['carNumber'])
        self.assertEqual(self.log.call_args.args[:2], ('차량취소', '123가6595 직접 취소'))
        self.apply.assert_not_called()
        with self.assertRaises(ParkingError):
            self.desk.submit(token, {'free': 1})
        with self.assertRaises(ParkingError):
            self.desk.submit_cancel(token, 'applied-old-id', 2)

    def test_cancel_rejects_other_store_invalid_count_and_consumed_visit(self):
        view = self.select_cancellable()
        token = view['token']
        for key, count in [('other-id', 1), ('applied-old-id', True), ('applied-old-id', 0),
                           ('applied-old-id', -1), ('applied-old-id', '1'), ('applied-old-id', 3),
                           ('applied-old-id', 101), (None, 1)]:
            with self.subTest(key=key, count=count), self.assertRaises(ParkingError):
                self.desk.submit_cancel(token, key, count)
        iparking.get_vehicle_detail.return_value['isConsumed'] = True
        view = self.select()
        self.assertEqual(view['applied'][0]['cancel_max'], 0)
        with self.assertRaises(ParkingError):
            self.desk.submit_cancel(view['token'], 'applied-old-id', 1)
        iparking.cancel_discount.assert_not_called()

    def test_cancel_rechecks_ownership_quantity_and_visit_before_execution(self):
        for change in ('quantity', 'owner', 'visit', 'consumed'):
            with self.subTest(change=change):
                iparking.get_vehicle_detail.return_value = copy.deepcopy(DETAIL)
                iparking.find_in_cars.return_value = [CAR]
                token = self.select_cancellable()['token']
                data = iparking.get_vehicle_detail.return_value
                if change == 'quantity':
                    data['myStoreApplyRequestTicketInfoList'][0]['applyCount'] = 0
                elif change == 'owner':
                    data['otherStoreApplyRequestTicketInfoList'] = data['myStoreApplyRequestTicketInfoList']
                    data['myStoreApplyRequestTicketInfoList'] = []
                elif change == 'consumed':
                    data['isConsumed'] = True
                else:
                    iparking.find_in_cars.return_value = [{**CAR, 'parkingHistoryId': 'new-visit'}]
                self.desk.submit_cancel(token, 'applied-old-id', 1)
                self.assertEqual(self.wait(token)['state'], 'failed')
        iparking.cancel_discount.assert_not_called()

    def test_cancel_response_loss_is_unknown_and_never_retried(self):
        token = self.select_cancellable()['token']
        iparking.cancel_discount.side_effect = TimeoutError('response lost')
        self.desk.submit_cancel(token, 'applied-old-id', 1)
        self.assertEqual(self.wait(token)['state'], 'unknown')
        self.desk.submit_cancel(token, 'applied-old-id', 1)
        iparking.cancel_discount.assert_called_once()

    def test_cancel_rejects_shared_vehicle_lock_expired_token_and_register_token(self):
        token = self.select_cancellable()['token']
        release = threading.Event()
        self.runner.start({'vehicle:car:6595'}, lambda: release.wait(3))
        try:
            with self.assertRaises(ParkingError):
                self.desk.submit_cancel(token, 'applied-old-id', 1)
            self.assertEqual(self.desk.status(token)['state'], 'ready')
        finally:
            release.set()
            self.runner._queues['vehicle'].join()
        self.desk.entries[token]['created'] -= 601
        with self.assertRaises(ParkingError):
            self.desk.submit_cancel(token, 'applied-old-id', 1)
        token = self.select()['token']
        self.desk.submit(token, {'free': 1})
        self.wait(token)
        with self.assertRaises(ParkingError):
            self.desk.submit_cancel(token, 'applied-old-id', 1)
        iparking.cancel_discount.assert_not_called()


class CancelProtocolTests(unittest.TestCase):
    def response(self, code='0000', ok=True):
        return Mock(ok=ok, status_code=200 if ok else 400, headers={'result-code': code},
                    json=Mock(return_value={}))

    def test_cancel_validates_and_uses_exact_id_and_count(self):
        with patch.object(iparking, '_plid', return_value='lot'), \
                patch.object(iparking, '_api', return_value=self.response()) as api:
            ok, _ = iparking.cancel_discount(CAR, count=2, ticket={'discountId': 'old-id'})
            self.assertTrue(ok)
            self.assertEqual(api.call_count, 2)
            self.assertTrue(api.call_args_list[0].args[1].endswith('/bulk-cancel/validate'))
            self.assertEqual(api.call_args_list[0].kwargs['json_body']['discountTicketId'], 'old-id')
            body = api.call_args_list[1].kwargs['json_body']
            self.assertEqual(body['applyCancelCount'], 2)
            self.assertEqual(body['parkingHistoryId'], CAR['parkingHistoryId'])

    def test_http_success_with_failure_code_is_rejected_at_both_steps(self):
        for responses in ([self.response('1414')], [self.response(), self.response('1414')]):
            with self.subTest(steps=len(responses)), patch.object(iparking, '_plid', return_value='lot'), \
                    patch.object(iparking, '_api', side_effect=responses) as api:
                ok, _ = iparking.cancel_discount(CAR, ticket={'discountId': 'id'})
                self.assertFalse(ok)
                self.assertEqual(api.call_count, len(responses))


if __name__ == '__main__':
    unittest.main()
