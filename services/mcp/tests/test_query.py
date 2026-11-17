import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from baidu_ads_mcp.query import QueryError, ReleaseQuery
from fixtures import make_release


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.release = make_release(self.tmp.name)
        self.now = 1000
        self.query = ReleaseQuery(self.release, clock=lambda: self.now)

    def query_range(self, **kwargs):
        return self.query.performance('tinacms', '2026-11-14', '2026-11-16', **kwargs)

    def test_gap_and_decimal_ratios(self):
        result = self.query_range()
        self.assertEqual(result['observed_totals']['cost'], '0.300000')
        self.assertEqual(result['observed_totals']['cpc'], '0.075000')
        self.assertEqual(result['observed_totals']['ctr'], '0.100000')
        self.assertEqual(result['missing_dates'], ['2026-11-15'])
        self.assertTrue(result['is_partial'])

    def test_empty_is_unknown(self):
        result = self.query.performance('ssw', '2026-01-01', '2026-01-01')
        self.assertIsNone(result['observed_totals']['cost'])
        self.assertIsNone(result['covered_range'])
        self.assertEqual(result['items'], [])

    def test_explicit_zero_and_original_keyword(self):
        result = self.query.performance('ssw', '2026-11-14', '2026-11-14', dimension='keyword')
        self.assertEqual(result['observed_totals']['cost'], '0.000000')
        self.assertIsNone(result['observed_totals']['cpc'])
        self.assertFalse(result['is_partial'])
        self.assertEqual(result['items'][0]['entity']['wInfoNameStatus'], '原始关键词')
        self.assertEqual(result['items'][0]['entity']['wInfoId'], 3)

    def test_distinct_ids_same_names_and_pagination(self):
        first = self.query_range(dimension='campaign', limit=1)
        second = self.query_range(dimension='campaign', limit=1, cursor=first['next_cursor'])
        self.assertEqual(first['items'][0]['entity']['campaignId'], 2)
        self.assertEqual(second['items'][0]['entity']['campaignId'], 1)
        self.assertIsNone(second['next_cursor'])
        self.assertEqual(first['release_id'], second['release_id'])

    def test_cursor_tampering_and_query_binding(self):
        cursor = self.query_range(dimension='campaign', limit=1)['next_cursor']
        for altered in ('invalid', cursor[:-8] + 'AAAAAAAA'):
            with self.assertRaisesRegex(QueryError, 'INVALID_CURSOR'):
                self.query_range(dimension='campaign', limit=1, cursor=altered)
        with self.assertRaisesRegex(QueryError, 'INVALID_CURSOR'):
            self.query_range(dimension='campaign', limit=2, cursor=cursor)
        with self.assertRaisesRegex(QueryError, 'INVALID_CURSOR'):
            self.query.performance('ssw', '2026-11-14', '2026-11-16', 'campaign', 1, cursor)

    def test_renamed_entity_keeps_same_identity(self):
        rows = self.query.data['tinacms']['reports']['campaign']
        rows.append({**rows[0], 'date': '2026-11-16', 'campaignName': '新名称'})
        result = self.query_range(dimension='campaign')
        self.assertEqual(result['total_count'], 2)
        entity = next(r for r in result['items'] if r['entity']['campaignId'] == 1)
        self.assertEqual(entity['entity']['campaignName'], '新名称')
        self.assertEqual(entity['metrics']['cost'], '0.200000')

    def test_missing_identity_rejects_schema_drift(self):
        path = self.release / 'report/index.html'
        path.write_text(path.read_text().replace('"campaignId": 1', '"renamedId": 1'))
        with self.assertRaisesRegex(QueryError, 'INVALID_SNAPSHOT'):
            ReleaseQuery(self.release)

    def test_expiry(self):
        self.now += 3600
        with self.assertRaisesRegex(QueryError, 'SNAPSHOT_EXPIRED'):
            self.query_range()
        with self.assertRaisesRegex(QueryError, 'SNAPSHOT_EXPIRED'):
            self.query.status()

    def test_validation(self):
        for start, end in [('bad', '2026-01-01'), ('20260101', '2026-01-01'),
                           ('2026-02-30', '2026-03-01'), ('2026-01-02', '2026-01-01'),
                           ('2020-01-01', '2026-01-01')]:
            with self.assertRaises(QueryError):
                self.query.performance('ssw', start, end)
        for limit in (0, 201, True):
            with self.assertRaisesRegex(QueryError, 'INVALID_LIMIT'):
                self.query_range(limit=limit)
        with self.assertRaisesRegex(QueryError, 'UNSUPPORTED_DIMENSION'):
            self.query_range(dimension='invalid_clicks')
        with self.assertRaisesRegex(QueryError, 'UNKNOWN_ACCOUNT'):
            self.query.performance('../credentials', '2026-01-01', '2026-01-01')

    def test_allowlist_discards_private_and_unknown_fields(self):
        value = repr(self.query.data) + json.dumps(self.query.status())
        self.assertNotIn('SENTINEL', value)
        self.assertNotIn('encrypted_posthog', value)
        self.assertFalse(self.query.accounts()['accounts'][0]['write_enabled'])

    def test_missing_field_does_not_become_zero(self):
        self.query.data['tinacms']['reports']['account'][0]['click'] = None
        result = self.query_range()
        self.assertIsNone(result['observed_totals']['click'])
        self.assertIsNone(result['observed_totals']['ctr'])
        self.assertTrue(result['is_partial'])

    def test_in_memory_release_survives_source_removal(self):
        path = self.release / 'report/index.html'
        path.unlink()
        self.assertEqual(self.query_range()['observed_totals']['cost'], '0.300000')
        with self.assertRaisesRegex(QueryError, 'INVALID_SNAPSHOT'):
            ReleaseQuery(self.release)

    def test_reads_do_not_modify_source(self):
        paths = list(self.release.rglob('*.html'))
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        self.query_range(); self.query.status(); self.query.accounts()
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
        self.assertEqual(set(paths), set(self.release.rglob('*.*')))

    def test_invalid_data_fail_closed(self):
        path = self.release / 'report/index.html'
        original = path.read_text()
        for bad in ('NaN', '-1', 'Infinity', 'true', 'null'):
            path.write_text(original.replace('"cost": "0.10"', '"cost": ' + bad, 1))
            if bad == 'null':
                self.assertIsNone(ReleaseQuery(self.release).performance('tinacms', '2026-11-14', '2026-11-14')['observed_totals']['cost'])
            else:
                with self.assertRaisesRegex(QueryError, 'INVALID_SNAPSHOT'):
                    ReleaseQuery(self.release)
        path.write_text('broken')
        with self.assertRaisesRegex(QueryError, 'INVALID_SNAPSHOT'):
            ReleaseQuery(self.release)

    def test_oversize_bounded(self):
        with patch('baidu_ads_mcp.query.MAX_BYTES', 10):
            with self.assertRaisesRegex(QueryError, 'SNAPSHOT_TOO_LARGE'):
                ReleaseQuery(self.release)

    def test_symlink_escape_rejected(self):
        path = self.release / 'report/index.html'
        outside = Path(self.tmp.name) / 'outside.html'
        outside.write_bytes(path.read_bytes())
        path.unlink(); path.symlink_to(outside)
        with self.assertRaisesRegex(QueryError, 'INVALID_RELEASE'):
            ReleaseQuery(self.release)

    def test_invalid_revision(self):
        with self.assertRaisesRegex(QueryError, 'INVALID_RELEASE'):
            ReleaseQuery(Path(self.tmp.name))


if __name__ == '__main__':
    unittest.main()
