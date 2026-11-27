import datetime as dt
import errno
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import tracker
from scripts import collect_local as collector


class ResilienceTests(unittest.TestCase):
    @unittest.skipUnless(tracker.fcntl, 'POSIX locks')
    def test_conflict_is_distinct_from_permission_failure(self):
        with tempfile.TemporaryFile() as lock:
            with patch.object(tracker.fcntl, 'flock', side_effect=BlockingIOError(errno.EAGAIN, 'busy')):
                self.assertFalse(tracker.acquire_lock(lock))
            with patch.object(tracker.fcntl, 'flock', side_effect=PermissionError(errno.EACCES, 'denied')):
                with self.assertRaises(PermissionError):
                    tracker.acquire_lock(lock)

    def test_unspecified_expiry_is_not_expired(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'config.json').write_text('{"timezone":"Asia/Shanghai","refresh_days":7,"reports":{}}')
            with patch.object(tracker, 'ROOT', root), patch.object(sys, 'argv', ['tracker.py']), patch.dict(
                    os.environ, BAIDU_CREDENTIALS_JSON='{"user_name":"example"}'):
                self.assertEqual(tracker.main(), 1)
            status = json.loads((root / 'status.json').read_text())
            self.assertIsNone(status['token_expires'])
            self.assertIn('缺少', status['errors'][0])

    def test_partial_revision_uses_published_fallbacks(self):
        day = (dt.datetime.now(collector.BEIJING).date() - dt.timedelta(days=1)).isoformat()
        older = '2025-01-01'
        with tempfile.TemporaryDirectory() as folder:
            storage = Path(folder)
            revision = 'a' * 32
            (storage / 'current').write_text(revision)
            previous = storage / 'releases' / revision
            for name in collector.REPORTS:
                data = {'status': {'end': older}, 'website_conversions': [{'date': older}]}
                path = previous / name
                path.parent.mkdir(parents=True)
                path.write_text('<script id="report-data" type="application/json">' + json.dumps(data) + '</script>')
            for path in collector.SNAPSHOT_FILES:
                path = previous / path
                path.parent.mkdir(parents=True)
                path.write_bytes(b'previous ciphertext')

            def ads(slug, *args):
                if slug == 'tinacms':
                    raise ValueError('upstream failed')
                return {'status': {'end': day}}

            def website(slug, field):
                if field == 'website_traffic':
                    return {field: {'ranges': [{'end': day}]}}
                return {field: [{'date': older}]}  # successful command with stale data

            with patch.object(collector, 'collect_ads', side_effect=ads), patch.object(
                    collector, 'collect_website', side_effect=website), patch.object(
                    collector, 'require_settings', side_effect=ValueError), patch.object(
                    collector, 'snapshot_date', return_value=older):
                with self.assertRaisesRegex(SystemExit, 'Partial collection'):
                    collector.publish(storage, previous)
            active = storage / 'releases' / (storage / 'current').read_text().strip()
            tina = collector.read_report(active / collector.REPORTS[0])[2]
            self.assertEqual(tina['collection_issues']['ads'], older)
            self.assertEqual(tina['collection_issues']['website_conversions'], older)
            self.assertEqual(tina['website_traffic']['ranges'][0]['end'], day)
            self.assertEqual(collector.read_report(active / collector.REPORTS[1])[2]['status']['end'], day)
            ssw = collector.read_report(active / collector.REPORTS[2])[2]
            self.assertNotIn('ssw_paid_website_conversions', ssw['collection_issues'])
            self.assertIn('ssw_contact_attribution', ssw['collection_issues'])
            for name in collector.SNAPSHOT_FILES:
                self.assertEqual((active / name).read_bytes(), (previous / name).read_bytes())
