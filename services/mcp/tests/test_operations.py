import asyncio
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from baidu_ads_mcp.changes import Principal
from baidu_ads_mcp.operations import Operations, BackgroundWorker
from baidu_ads_mcp.policies import PolicyStore
from baidu_ads_mcp.query import QueryError
from test_analytics_changes import Base, OPERATOR

SCOPES = OPERATOR.scopes | {'exports:create', 'subscriptions:manage', 'jobs:refresh', 'policies:manage'}
ACTOR = Principal('alice', 'v1', 'operator', frozenset(SCOPES))
OTHER = Principal('bob', 'v1', 'operator', frozenset(SCOPES))


class OperationsTests(Base):
    def setUp(self):
        super().setUp()
        self.policies = PolicyStore(self.store)
        self.operations = Operations(self.store, self.policies)

    def export(self, **kwargs):
        return self.operations.create_export(ACTOR, account='tinacms', start='2026-11-14', end='2026-11-16', **kwargs)

    def test_json_export_fixed_release_missing_data_and_access(self):
        result = self.export()
        content = self.operations.download(ACTOR, result['export_id'])
        data = json.loads(content['content'])
        self.assertEqual(data['release_id'], self.query.release_id)
        self.assertEqual(data['missing_dates'], ['2026-11-15'])
        self.assertEqual(data['observed_totals']['cost'], '0.300000')
        with self.assertRaisesRegex(QueryError, 'EXPORT_NOT_FOUND'):
            self.operations.download(OTHER, result['export_id'])
        revoked = Principal('alice', 'v2', 'operator', ACTOR.scopes)
        with self.assertRaisesRegex(QueryError, 'EXPORT_NOT_FOUND'):
            self.operations.download(revoked, result['export_id'])

    def test_csv_formula_protection_and_exact_json_text(self):
        self.query.data['tinacms']['reports']['campaign'][0]['campaignName'] = '=1+1'
        value = self.export(format='csv', dimension='campaign')
        self.assertIn("'=1+1", self.operations.download(ACTOR, value['export_id'])['content'].decode('utf-8-sig'))
        exact = self.export(format='json', dimension='campaign')
        self.assertIn('=1+1', self.operations.download(ACTOR, exact['export_id'])['content'].decode())

    def test_limits_expiry_and_cleanup(self):
        with self.assertRaisesRegex(QueryError, 'EXPORT_ROW_LIMIT'):
            self.export(dimension='campaign', max_rows=1)
        with patch('baidu_ads_mcp.operations.MAX_EXPORT_BYTES', 10):
            with self.assertRaisesRegex(QueryError, 'EXPORT_SIZE_LIMIT'):
                self.export()
        value = self.export()
        self.now += 3600
        with self.assertRaisesRegex(QueryError, 'EXPORT_EXPIRED'):
            self.operations.download(ACTOR, value['export_id'])
        self.operations.due()
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM private_exports').fetchone()[0], 0)

    def test_export_requires_scope(self):
        with self.assertRaisesRegex(QueryError, 'INSUFFICIENT_SCOPE'):
            self.operations.create_export(OPERATOR, account='tinacms', start='2026-11-14', end='2026-11-16')

    def test_manual_refresh_idempotency(self):
        first = self.operations.enqueue_refresh(ACTOR, 'refresh-key-0123456789')
        second = self.operations.enqueue_refresh(ACTOR, 'refresh-key-0123456789')
        self.assertEqual(first['job_id'], second['job_id'])
        with self.assertRaisesRegex(QueryError, 'JOB_NOT_FOUND'):
            self.operations.job(OTHER, first['job_id'])

    def test_claim_recovery_and_stale_worker_fencing(self):
        value = self.operations.enqueue_refresh(ACTOR, 'refresh-key-0123456789')
        first = self.operations.claim()
        self.assertIsNone(self.operations.claim())
        self.now += 61
        self.operations.due()
        second = self.operations.claim()
        self.assertEqual(second['attempts'], 2)
        self.assertFalse(self.operations.complete(first, {'old': True}))
        self.assertTrue(self.operations.complete(second, {'new': True}))
        self.assertEqual(self.operations.job(ACTOR, value['job_id'])['result'], {'new': True})

    def test_retries_are_bounded_and_error_is_safe(self):
        value = self.operations.enqueue_refresh(ACTOR, 'refresh-key-0123456789')
        for count in range(3):
            job = self.operations.claim()
            self.operations.complete(job, None, 'UPSTREAM_UNAVAILABLE')
            self.now += 300
        self.assertEqual(self.operations.job(ACTOR, value['job_id'])['state'], 'failed')
        self.assertEqual(self.operations.inbox(ACTOR)['items'][0]['body'], {'error':'UPSTREAM_UNAVAILABLE'})

    def test_daily_schedule_beijing_and_pause(self):
        self.now = datetime(2026,11,15,1,0,tzinfo=timezone.utc).timestamp()
        subscription = self.operations.create_subscription(ACTOR, kind='daily_summary', account='tinacms', at_beijing='09:15')
        self.assertEqual(datetime.fromtimestamp(subscription['next_due'], timezone.utc).hour, 1)
        self.now = subscription['next_due']
        self.operations.due()
        first = self.operations.claim()
        self.assertEqual(first['body']['start'], '2026-11-14')
        self.assertEqual(first['body']['end'], '2026-11-14')
        self.operations.pause_subscription(ACTOR, subscription['subscription_id'])
        self.operations.complete(first, {'summary': True})
        self.assertEqual(self.operations.job(ACTOR, first['id'])['state'], 'cancelled')
        self.assertEqual(self.operations.inbox(ACTOR)['items'], [])

    def test_policy_subscription_and_account_mismatch(self):
        policy = self.policies.create(ACTOR, account='tinacms', name='观察', cost_threshold='1', minimum_clicks=1)
        value = self.operations.create_subscription(ACTOR, kind='policy_observation', account='tinacms', policy_id=policy['policy_id'])
        self.assertEqual(value['configuration']['policy_id'], policy['policy_id'])
        with self.assertRaisesRegex(QueryError, 'INVALID_SUBSCRIPTION'):
            self.operations.create_subscription(ACTOR, kind='policy_observation', account='ssw', policy_id=policy['policy_id'])
        with self.assertRaisesRegex(QueryError, 'INVALID_SUBSCRIPTION'):
            self.operations.create_subscription(ACTOR, kind='daily_summary', account='tinacms', refresh_interval_seconds=300)

    def test_notification_owner_and_mark_read(self):
        self.operations.enqueue_refresh(ACTOR, 'refresh-key-0123456789')
        job = self.operations.claim()
        self.operations.complete(job, None, 'UNAUTHORIZED')
        notification = self.operations.inbox(ACTOR)['items'][0]
        self.assertEqual(self.operations.inbox(OTHER)['items'], [])
        with self.assertRaisesRegex(QueryError, 'NOTIFICATION_NOT_FOUND'):
            self.operations.mark_read(OTHER, notification['notification_id'])
        self.operations.mark_read(ACTOR, notification['notification_id'])
        self.assertIsNotNone(self.operations.inbox(ACTOR)['items'][0]['read_at'])

    def test_worker_checks_identity_before_and_after_refresh(self):
        calls=[]
        async def resolve(subject): return ACTOR
        async def refresh(actor): calls.append(actor.subject); return {'release_id': self.query.release_id}
        first = self.operations.enqueue_refresh(ACTOR, 'refresh-key-0123456789')
        asyncio.run(BackgroundWorker(self.operations, resolve, refresh).run_once())
        self.assertEqual(calls, ['alice'])
        self.assertEqual(self.operations.job(ACTOR, first['job_id'])['state'], 'completed')
        second = self.operations.enqueue_refresh(ACTOR, 'refresh-key-0123456790')
        async def revoked(subject): return Principal('alice','revoked','operator',ACTOR.scopes)
        asyncio.run(BackgroundWorker(self.operations, revoked, refresh).run_once())
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.operations.job(ACTOR, second['job_id'])['result'], {'error':'IDENTITY_CHANGED'})

    def test_async_export_idempotency_and_background_result(self):
        values = dict(account='tinacms', start='2026-11-14', end='2026-11-16')
        first = self.operations.enqueue_export(ACTOR, 'export-key-0123456789', **values)
        repeated = self.operations.enqueue_export(ACTOR, 'export-key-0123456789', **values)
        self.assertEqual(first['job_id'], repeated['job_id'])
        with self.assertRaisesRegex(QueryError, 'IDEMPOTENCY_CONFLICT'):
            self.operations.enqueue_export(ACTOR, 'export-key-0123456789', **{**values,'end':'2026-11-15'})
        async def resolve(subject): return ACTOR
        asyncio.run(BackgroundWorker(self.operations, resolve).run_once())
        result = self.operations.job(ACTOR, first['job_id'])
        self.assertEqual(result['state'], 'completed')
        self.assertIn('export_id', result['result'])
        self.assertEqual(self.operations.inbox(ACTOR)['items'], [])

    def test_worker_suppresses_raw_exception_and_never_advertises(self):
        async def resolve(subject): return ACTOR
        async def broken(actor): raise RuntimeError('sensitive-token-SENTINEL')
        value = self.operations.enqueue_refresh(ACTOR, 'refresh-key-0123456789')
        asyncio.run(BackgroundWorker(self.operations, resolve, broken).run_once())
        result = self.operations.job(ACTOR, value['job_id'])
        self.assertEqual(result['result'], {'error':'JOB_FAILED'})
        self.assertNotIn('SENTINEL', json.dumps(self.operations.inbox(ACTOR)))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM plans').fetchone()[0], 0)
