import datetime as dt
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sync_wecom_consultations import HEADER, encrypt_snapshot, fetch_rows, normalize_rows, timestamp


class WeComConsultationTests(unittest.TestCase):
    def test_missing_days_and_empty_statistics_are_distinct_from_zero(self):
        day = dt.date(2026, 11, 12)
        first = day - dt.timedelta(days=1)
        rows = normalize_rows([
            {'stat_time': timestamp(first), 'statistic': {}},
            {'stat_time': timestamp(day), 'statistic': {'customer_cnt': 0, 'session_cnt': 2}},
        ], first, day)
        self.assertEqual(rows, [{'date': day.isoformat(), 'customers': 0}])
        key = bytes(range(32))
        snapshot = encrypt_snapshot(rows, day, key.hex())
        offset = len(HEADER)
        self.assertTrue(snapshot.startswith(HEADER))
        data = AESGCM(key).decrypt(snapshot[offset:offset + 12], snapshot[offset + 12:], HEADER)
        self.assertEqual(json.loads(data), {'through': day.isoformat(), 'rows': rows})

    @patch('sync_wecom_consultations.wecom', return_value={'statistic_list': []})
    def test_90_days_are_requested_in_disjoint_inclusive_windows_for_one_account(self, api):
        through = dt.date(2026, 11, 12)
        first = through - dt.timedelta(days=89)
        self.assertEqual(fetch_rows('example-token', 'example-account', through), [])
        requests = api.call_args_list
        self.assertEqual(len(requests), 3)
        for index, call in enumerate(requests):
            start = first + dt.timedelta(days=index * 31)
            end = min(start + dt.timedelta(days=30), through)
            self.assertEqual(call.args, ('kf/get_corp_statistic',))
            self.assertEqual(call.kwargs['method'], 'POST')
            self.assertEqual(call.kwargs['payload'], {
                'open_kfid': 'example-account', 'start_time': timestamp(start), 'end_time': timestamp(end),
            })
