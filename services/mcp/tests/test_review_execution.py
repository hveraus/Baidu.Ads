"""Independent regressions for uncertain creation and destructive recovery.

Every provider interaction uses the synthetic transport fixture. No credentials,
network writes, or production mutation capabilities are involved.
"""
import asyncio
import time
import unittest
from unittest.mock import patch

import httpx

from baidu_ads_mcp.capabilities import Capability
from baidu_ads_mcp.baidu_write import BaiduWriteAdapter
from baidu_ads_mcp.changes import Principal, canonical, digest
from baidu_ads_mcp.executor import Receipt
from baidu_ads_mcp.query import QueryError
from baidu_ads_mcp.worker import tick
import test_creation
from test_analytics_changes import OWNER, OPERATOR


class ExecutionReviewTests(unittest.TestCase):
    def setUp(self):
        self.f = test_creation.CreationTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def approve(self, operation, *, actor=OPERATOR, key='review-execution-key'):
        f = self.f
        plan = f.store.create(actor, 'tinacms', [operation], 'Synthetic review regression')
        f.store.request_approval(actor, plan['id'], 1)
        f.store.approve_from_web(f.identities['kenshi'], plan['id'], 1, plan['hash'])
        return f.queue.enqueue(actor, plan['id'], 1, key)

    def test_arbitrary_manual_receipt_cannot_attribute_existing_object(self):
        f = self.f
        plan = f.store.create(OPERATOR, 'tinacms', [f.op], 'Synthetic candidate')
        change = plan['proposal']['changes'][0]
        f.rows[999] = {**change['after'], 'campaignId': 999}
        observed = f.adapter.reconcile('tinacms', change['operation'], 'local-intent',
                                       Receipt('manual:owner-assertion', '999'),
                                       change['before'], change['after'])
        self.assertFalse(observed.authoritative)
        self.assertEqual(observed.outcome, 'unknown')
        self.assertEqual(f.calls, [])

    def test_owner_matching_candidate_keeps_unknown_and_lock(self):
        f = self.f
        f.timeout = True
        job = f.enqueue()
        tick(f.queue)
        f.rows[999] = {**f.rows[100], 'campaignId': 999}
        result = f.queue.bind_created_entity_from_web(OWNER, job['job_id'], 0, '999',
                                                    'Owner supplied matching pre-existing object')
        self.assertTrue(result['requires_upstream_creation_provenance'])
        tick(f.queue)
        self.assertEqual(f.queue.get(OPERATOR, job['job_id'])['state'], 'unknown')
        with f.store.db() as db:
            self.assertGreater(db.execute('SELECT count(*) FROM execution_locks WHERE job_id=?',
                                          (job['job_id'],)).fetchone()[0], 0)
        self.assertEqual(len([r for r in f.calls if r[0] == 'addCampaign']), 1)

    def test_creation_compensation_refuses_later_manual_changes(self):
        f = self.f
        job = f.enqueue()
        tick(f.queue)
        inventory = f.query.data['tinacms']['extra']['inventory']['campaign']
        for edits in ({'pause': False}, {'budget': '150.00'}, {'campaignName': 'Manual rename'}):
            with self.subTest(edits=edits):
                inventory[:] = [{**f.rows[100], 'campaignId': '100', **edits}]
                with self.assertRaisesRegex(QueryError, 'POST_CREATION_OBJECT_CHANGED'):
                    f.queue.compensation(OPERATOR, job['job_id'], 'Synthetic restore')

    def test_paused_creation_needs_no_compensation_write(self):
        f = self.f
        job = f.enqueue()
        tick(f.queue)
        f.query.data['tinacms']['extra']['inventory']['campaign'][:] = [{**f.rows[100], 'campaignId': '100'}]
        with self.assertRaisesRegex(QueryError, 'NO_COMPENSATION_NEEDED'):
            f.queue.compensation(OPERATOR, job['job_id'], 'Synthetic restore')
        self.assertEqual(len([r for r in f.calls if r[0] == 'addCampaign']), 1)

    def test_noop_plan_never_reaches_provider_or_holds_a_lock(self):
        f = self.f
        for operation in (
            {'op': 'set_campaign_budget', 'entity_id': '1', 'amount': '100.0'},
            {'op': 'set_pause', 'kind': 'campaign', 'entity_id': '1', 'paused': False},
            {'op': 'add_negative_keywords', 'kind': 'campaign', 'entity_id': '1',
             'match': 'phrase', 'words': ['原词']},
        ):
            with self.subTest(operation=operation), self.assertRaisesRegex(QueryError, 'NO_CHANGES'):
                f.store.create(OPERATOR, 'tinacms', [operation], 'Synthetic no-op')
        self.assertEqual(f.calls, [])
        with f.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM execution_locks').fetchone()[0], 0)

    def test_automatic_plan_fails_closed_without_an_installed_rule_guard(self):
        f = self.f
        plan = f.store.create(OPERATOR, 'tinacms', [f.op], 'Synthetic automatic marker')
        body = dict(plan['proposal'])
        body['automatic_rule'] = {'rule_id': 'synthetic-rule', 'version': 1}
        with f.store.db() as db:
            db.execute('UPDATE plans SET payload=?,hash=? WHERE id=?',
                       (canonical(body), digest(body), plan['id']))
        f.store.request_approval(OPERATOR, plan['id'], 1)
        f.store.approve_from_web(OWNER, plan['id'], 1, digest(body))
        with self.assertRaisesRegex(QueryError, 'AUTOMATIC_RULE_GUARD_REQUIRED'):
            f.queue.enqueue(OPERATOR, plan['id'], 1, 'review-automatic-key')
        self.assertEqual(f.calls, [])
        with f.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM execution_jobs').fetchone()[0], 0)

    def test_unknown_child_creation_blocks_parent_changes(self):
        f = self.f
        f.rows[1] = dict(f.query.data['tinacms']['extra']['inventory']['campaign'][0])
        original_submit = f.adapter.submit_authorized
        def uncertain_submit(account, operation, intent_id, authorize):
            original_submit(account, operation, intent_id, authorize)
            raise TimeoutError('Synthetic lost creation acknowledgement')
        f.adapter.submit_authorized = uncertain_submit
        f.registry.record(OWNER, Capability('tinacms', 'create_adgroup', 'adgroup',
            f.adapter.adapter_id, 'fixture', 'synthetic', 10000,
            ('parentId', 'parentPause'), '100', '100', '100'), enabled=True)
        f.registry.record(OWNER, Capability('tinacms', 'set_pause', 'campaign',
            f.adapter.adapter_id, 'fixture', 'synthetic', 10000, ('pause',)), enabled=True)
        child = self.approve({'op': 'create_adgroup', 'request_id': 'b' * 32,
            'campaign_id': '1', 'name': 'Synthetic child', 'max_price': '1.00'})
        tick(f.queue)
        self.assertEqual(f.queue.get(OPERATOR, child['job_id'])['state'], 'unknown')
        parent = self.approve({'op': 'set_pause', 'kind': 'campaign', 'entity_id': '1',
                              'paused': True}, key='review-parent-pause-key')
        tick(f.queue)
        self.assertEqual(f.queue.get(OPERATOR, parent['job_id'])['state'], 'queued')
        self.assertEqual(len([r for r in f.calls if r[0] == 'updateCampaign']), 0)

    def test_owner_cannot_replace_another_authors_version(self):
        f = self.f
        plan = f.store.create(OPERATOR, 'tinacms', [f.op], 'Original author')
        with self.assertRaisesRegex(QueryError, 'PLAN_AUTHOR_REQUIRED'):
            f.store.revise(OWNER, plan['id'], 1, [{**f.op, 'name': 'Owner edit'}], 'Changed')
        current = f.store.get(OPERATOR, plan['id'])
        self.assertEqual(current['version'], 1)
        self.assertEqual(current['author'], 'alice')

    def test_owner_permission_removal_before_creation_stops_send(self):
        f = self.f
        job = f.enqueue()
        # Even a malformed resolver that keeps the same version cannot preserve
        # a permission that has disappeared from the live principal.
        f.identities['kenshi'] = Principal('kenshi', OWNER.version, 'owner',
                                         OWNER.scopes - {'changes:execute'})
        tick(f.queue)
        self.assertEqual(f.queue.get(OPERATOR, job['job_id'])['state'], 'stopped')
        self.assertEqual(len([r for r in f.calls if r[0] == 'addCampaign']), 0)

    def test_approval_cancelled_during_adapter_reread_never_sends(self):
        f = self.f
        job = f.enqueue()
        read = f.adapter.read
        reads = []
        def cancellation_during_read(account, operation):
            current = read(account, operation)
            reads.append(True)
            if len(reads) == 2:
                f.store.cancel(OPERATOR, job['plan_id'], 1)
            return current
        f.adapter.read = cancellation_during_read
        tick(f.queue)
        self.assertEqual(len(reads), 2)
        self.assertEqual(len([r for r in f.calls if r[0] == 'addCampaign']), 0)
        self.assertNotEqual(f.queue.get(OPERATOR, job['job_id'])['state'], 'unknown')

    def test_delete_approval_requires_separate_delete_permission(self):
        f = self.f
        plan = f.store.create(OWNER, 'tinacms', [{'op': 'delete_entity', 'kind': 'creative',
            'entity_id': '3', 'acknowledge_irreversible': True}], 'Synthetic delete review')
        f.store.request_approval(OWNER, plan['id'], 1)
        with self.assertRaisesRegex(QueryError, 'INSUFFICIENT_SCOPE'):
            f.store.approve_from_web(OWNER, plan['id'], 1, plan['hash'])
        self.assertEqual(f.store.get(OWNER, plan['id'])['state'], 'awaiting_approval')

    def test_leaf_delete_checks_all_approved_object_fields(self):
        f = self.f
        owner = Principal('kenshi', OWNER.version, 'owner', OWNER.scopes | {'changes:delete'})
        f.identities['kenshi'] = owner
        f.registry.record(owner, Capability('tinacms', 'delete_entity', 'creative',
            f.adapter.adapter_id, 'fixture', 'synthetic', 10000, ('deleted',)), enabled=True)
        job = self.approve({'op': 'delete_entity', 'kind': 'creative', 'entity_id': '3',
                           'acknowledge_irreversible': True}, actor=owner)
        approved = dict(f.query.data['tinacms']['extra']['inventory']['creative'][0])
        # The capability only requires the tombstone field. Destructive execution
        # must additionally protect the approved title/URL/content from stale delete.
        f.adapter.read = lambda *args: {**approved, 'title': 'Edited after approval', 'deleted': False}
        sends = []
        f.adapter.submit = lambda *args: sends.append(args) or Receipt('synthetic-delete')
        tick(f.queue)
        self.assertEqual(sends, [])
        result = f.queue.get(owner, job['job_id'])
        self.assertEqual(result['state'], 'stopped')
        self.assertEqual(result['items'][0]['result']['code'], 'STALE_PRECONDITION')

    def test_parent_delete_cannot_bypass_unverified_subtree_contract(self):
        f = self.f
        owner = Principal('kenshi', OWNER.version, 'owner', OWNER.scopes | {'changes:delete'})
        for kind in ('campaign', 'adgroup'):
            with self.subTest(kind=kind), self.assertRaisesRegex(QueryError, 'CASCADE_CONTRACT_UNVERIFIED'):
                f.registry.record(owner, Capability('tinacms', 'delete_entity', kind,
                    f.adapter.adapter_id, 'fixture', 'synthetic', 10000, ('deleted',)), enabled=True)

    def test_slow_provider_stream_has_absolute_deadline_and_is_closed(self):
        closed = []
        requests = []
        class Trickle(httpx.AsyncByteStream):
            async def __aiter__(self):
                while True:
                    yield b' '
                    await asyncio.sleep(0.005)
            async def aclose(self):
                closed.append(True)
        async def handler(request):
            requests.append(request.url.path)
            return httpx.Response(200, stream=Trickle())
        adapter = BaiduWriteAdapter(lambda _: {'user_name': 'synthetic', 'access_token': 'SYNTHETIC'},
                                   transport=httpx.MockTransport(handler))
        start = time.monotonic()
        with patch('baidu_ads_mcp.baidu_write.REQUEST_TIMEOUT_SECONDS', 0.03):
            with self.assertRaises(TimeoutError):
                adapter.call('tinacms', 'campaign', 'getCampaign', {'campaignIds': [1]})
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertEqual(len(requests), 1)
        self.assertEqual(closed, [True])


if __name__ == '__main__':
    unittest.main()
