"""Independent automatic-rule boundaries using only a disposable fake provider."""
from datetime import datetime, timedelta
from copy import copy
import json

from baidu_ads_mcp.automatic_rules import AutomaticRules
from baidu_ads_mcp.capabilities import Capability, CapabilityRegistry
from baidu_ads_mcp.changes import Principal
from baidu_ads_mcp.executor import ExecutionQueue, Observation, Receipt
from baidu_ads_mcp.operations import BEIJING
from baidu_ads_mcp.query import QueryError
from baidu_ads_mcp.worker import tick
from test_analytics_changes import Base


class AutomaticReviewTests(Base):
    def setUp(self):
        super().setUp()
        self.now = datetime(2026, 11, 15, 9, tzinfo=BEIJING).timestamp()
        self.actor = Principal('kenshi', 'synthetic-version', 'owner', frozenset({
            'policies:automate', 'analytics:read', 'settings:read', 'changes:propose', 'changes:execute'}))
        self.identity = self.actor
        self.automatic_on = True
        self.execution_on = True
        self.calls = []
        self.uncertain = False
        review = self
        class Provider:
            adapter_id = 'review-synthetic'
            def read(self, account, operation):
                return dict(review.query.data[account]['extra']['inventory']['campaign'][0])
            def submit(self, account, operation, intent):
                review.calls.append((account, operation))
                if review.uncertain:
                    raise TimeoutError('Synthetic uncertain send')
                return Receipt('synthetic-pause-ack')
            def reconcile(self, account, operation, intent, receipt, before, after):
                if review.uncertain:
                    return Observation('unknown', {}, 'synthetic-unknown', False)
                return Observation('applied', dict(after), 'synthetic-readback', True)
        self.registry = CapabilityRegistry(self.store)
        self.queue = ExecutionQueue(self.store, self.registry, Provider(), lambda _: self.identity,
                                    mutations_enabled=lambda: self.execution_on)
        self.rules = AutomaticRules(self.store, self.queue, enabled=lambda: self.automatic_on)
        self.queue.pre_execution_guard = self.rules.guard
        self.registry.record(self.actor, Capability('tinacms', 'set_pause', 'campaign',
            'review-synthetic', 'fixture', 'synthetic-only', self.now + 30 * 86400, ('pause',)), enabled=True)
        self.definition = dict(account='tinacms', name='Synthetic reviewed rule', kind='campaign',
            entity_ids=['1'], action='pause', cost_threshold='0.05', minimum_clicks=1,
            maximum_observed_cost='10.00', lookback_days=1, interval_seconds=300,
            cooldown_seconds=300, maximum_actions_per_run=1, maximum_actions_per_day=1,
            maximum_actions_total=2, expires_in_days=7)

    def approved(self, **changes):
        rule = self.rules.create(self.actor, **{**self.definition, **changes})
        return self.rules.approve_from_web(self.actor, rule['rule_id'], rule['version'], rule['hash'])

    def plan_record(self, plan_id):
        with self.store.db() as db:
            return dict(db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone())

    def next_day(self):
        self.now += 86400
        rows = self.query.data['tinacms']['reports']['campaign']
        yesterday = (datetime.fromtimestamp(self.now, BEIJING).date() - timedelta(days=1)).isoformat()
        rows.append({**rows[0], 'date': yesterday})

    def test_both_switches_are_required_and_default_automatic_switch_is_off(self):
        rule = self.approved()
        default_rules = AutomaticRules(self.store, self.queue)
        with self.assertRaisesRegex(QueryError, 'AUTOMATIC_MUTATIONS_DISABLED'):
            default_rules.trigger(self.actor, rule['rule_id'])
        for automatic, execution in ((False, True), (True, False), (False, False)):
            self.automatic_on, self.execution_on = automatic, execution
            with self.subTest(automatic=automatic, execution=execution), self.assertRaisesRegex(QueryError, 'AUTOMATIC_MUTATIONS_DISABLED'):
                self.rules.trigger(self.actor, rule['rule_id'])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM plans').fetchone()[0], 0)
        self.assertEqual(self.calls, [])

    def test_token_scope_reduction_after_enqueue_stops_send_without_version_change(self):
        rule = self.approved()
        result = self.rules.trigger(self.actor, rule['rule_id'])
        self.identity = Principal(self.actor.subject, self.actor.version, self.actor.role,
                                  self.actor.scopes - {'policies:automate'})
        tick(self.queue)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.queue.get(self.identity, result['run']['job_id'])['state'], 'stopped')

    def test_rule_edit_invalidates_already_queued_template_approval(self):
        rule = self.approved()
        result = self.rules.trigger(self.actor, rule['rule_id'])
        updated = self.rules.update(self.actor, rule['rule_id'], rule['version'],
                                    **{**self.definition, 'name': 'Changed definition'})
        self.assertEqual(updated['state'], 'pending_approval')
        self.assertIsNone(updated['approved_version'])
        tick(self.queue)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.queue.get(self.actor, result['run']['job_id'])['state'], 'stopped')

    def test_rule_paused_while_identity_is_checked_cannot_pass_final_guard(self):
        rule = self.approved()
        result = self.rules.trigger(self.actor, rule['rule_id'])
        plan = self.plan_record(result['run']['plan_id'])
        def pause_during_identity(subject):
            self.rules.pause(self.actor, rule['rule_id'])
            return self.actor
        self.queue.identity_resolver = pause_during_identity
        with self.assertRaisesRegex(QueryError, 'AUTOMATIC_APPROVAL_REQUIRED|STALE_RULE_VERSION'):
            self.rules.guard(plan, json.loads(plan['payload']))
        self.assertEqual(self.calls, [])

    def test_automatic_switch_disabled_during_identity_check_stops_final_guard(self):
        rule = self.approved()
        result = self.rules.trigger(self.actor, rule['rule_id'])
        plan = self.plan_record(result['run']['plan_id'])
        def disable_during_identity(subject):
            self.automatic_on = False
            return self.actor
        self.queue.identity_resolver = disable_during_identity
        with self.assertRaisesRegex(QueryError, 'AUTOMATIC_MUTATIONS_DISABLED'):
            self.rules.guard(plan, json.loads(plan['payload']))

    def test_cache_swap_cannot_mix_trigger_evidence_and_plan_inventory(self):
        rule = self.approved()
        original_facts = self.rules.facts
        replacement = copy(self.query)
        replacement.release_id = 'b' * 32
        def swap_after_facts(*args):
            result = original_facts(*args)
            self.store.query = replacement
            return result
        self.rules.facts = swap_after_facts
        with self.assertRaisesRegex(QueryError, 'SNAPSHOT_CHANGED'):
            self.rules.trigger(self.actor, rule['rule_id'])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM plans').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM execution_jobs').fetchone()[0], 0)
        self.assertEqual(self.calls, [])

    def test_generated_plan_cannot_expand_entity_or_action_scope(self):
        rule = self.approved()
        result = self.rules.trigger(self.actor, rule['rule_id'])
        plan = self.plan_record(result['run']['plan_id'])
        for changes in ({'entity_id': '999'}, {'paused': False}):
            payload = json.loads(plan['payload'])
            payload['changes'][0]['operation'].update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(QueryError, 'AUTOMATIC_OPERATION_OUT_OF_SCOPE'):
                self.rules.guard(plan, payload)
        self.assertEqual(self.calls, [])

    def test_midnight_expiry_prevents_previous_day_reservation_from_using_today_limit(self):
        self.now = datetime(2026, 11, 15, 23, 59, tzinfo=BEIJING).timestamp()
        rule = self.approved()
        result = self.rules.trigger(self.actor, rule['rule_id'])
        midnight = datetime(2026, 11, 16, tzinfo=BEIJING).timestamp()
        plan = self.plan_record(result['run']['plan_id'])
        self.assertLessEqual(plan['approval_expires'], midnight)
        self.now = midnight + 1
        with self.assertRaises(QueryError):
            self.rules.guard(plan, json.loads(plan['payload']))
        tick(self.queue)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.queue.get(self.actor, result['run']['job_id'])['state'], 'stopped')

    def test_unknown_prior_version_blocks_newly_approved_version(self):
        rule = self.approved()
        result = self.rules.trigger(self.actor, rule['rule_id'])
        self.uncertain = True
        tick(self.queue)
        self.assertEqual(self.queue.get(self.actor, result['run']['job_id'])['state'], 'unknown')
        updated = self.rules.update(self.actor, rule['rule_id'], rule['version'],
                                    **{**self.definition, 'name': 'Reapproved but old outcome unknown'})
        self.rules.approve_from_web(self.actor, rule['rule_id'], updated['version'], updated['hash'])
        with self.assertRaisesRegex(QueryError, 'AUTOMATIC_UNKNOWN_STOP'):
            self.rules.trigger(self.actor, rule['rule_id'])
        self.assertEqual(len(self.calls), 1)

    def test_total_and_cooldown_limits_survive_new_data_days(self):
        for definition, expected in (({'maximum_actions_total': 1}, 'limit'),
                                     ({'cooldown_seconds': 172800}, 'cooldown')):
            with self.subTest(expected=expected):
                # Separate approved rules isolate reservations between scenarios.
                rule = self.approved(**definition)
                self.rules.trigger(self.actor, rule['rule_id'])
                tick(self.queue)
                before = len(self.calls)
                self.next_day()
                if expected == 'limit':
                    with self.assertRaisesRegex(QueryError, 'AUTOMATIC_ACTION_LIMIT'):
                        self.rules.trigger(self.actor, rule['rule_id'])
                else:
                    result = self.rules.trigger(self.actor, rule['rule_id'])
                    self.assertIsNone(result['run']['plan_id'])
                tick(self.queue)
                self.assertEqual(len(self.calls), before)
