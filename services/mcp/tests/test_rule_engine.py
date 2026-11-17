import asyncio
import unittest
from baidu_ads_mcp.changes import Principal
from baidu_ads_mcp.query import QueryError
from baidu_ads_mcp.rule_engine import DecisionRules
from baidu_ads_mcp.operations import Operations,BackgroundWorker
from baidu_ads_mcp.server import create_server
from test_analytics_changes import Base, OPERATOR

ACTOR=Principal('alice','v1','operator',OPERATOR.scopes|{'policies:manage','subscriptions:manage','jobs:refresh'})


class RuleTests(Base):
    def setUp(self):
        super().setUp()
        self.rules=DecisionRules(self.store)
        rows=self.query.data['tinacms']['reports']['campaign']
        for date in ('2026-11-15','2026-11-16'):
            rows.append({**rows[0],'date':date})

    def create(self,**extra):
        values=dict(account='tinacms',name='明确对象费用观察',kind='campaign',entity_ids=['1'],cost_threshold='0.05',minimum_clicks=1,mode='propose_pause',cooldown_seconds=300)
        return self.rules.create(ACTOR,**{**values,**extra})

    def test_recommendation_creates_only_unapproved_draft_and_deduplicates(self):
        rule=self.create()
        first=self.rules.evaluate(ACTOR,rule['rule_id'],'2026-11-14','2026-11-14')
        second=self.rules.evaluate(ACTOR,rule['rule_id'],'2026-11-14','2026-11-14')
        self.assertEqual(first['created_plan_id'],second['created_plan_id'])
        plan=self.store.get(ACTOR,first['created_plan_id'])
        self.assertEqual(plan['state'],'draft');self.assertIsNone(plan['approval_actor'])
        self.assertFalse(plan['execution_enabled'])
        self.assertEqual(plan['proposal']['changes'][0]['operation']['paused'],True)

    def test_entity_daily_coverage_is_required(self):
        rule=self.create()
        with self.assertRaisesRegex(QueryError,'DATA_INCOMPLETE'):
            self.rules.evaluate(ACTOR,rule['rule_id'],'2026-11-13','2026-11-14')
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM plans').fetchone()[0],0)

    def test_cooldown_and_expiry(self):
        rule=self.create()
        self.rules.evaluate(ACTOR,rule['rule_id'],'2026-11-14','2026-11-14')
        later=self.rules.evaluate(ACTOR,rule['rule_id'],'2026-11-15','2026-11-15')
        self.assertIsNone(later['created_plan_id']);self.assertTrue(later['entities'][0]['cooldown_suppressed'])
        self.now+=301
        after=self.rules.evaluate(ACTOR,rule['rule_id'],'2026-11-16','2026-11-16')
        self.assertIsNotNone(after['created_plan_id'])
        self.now+=91*86400
        with self.assertRaisesRegex(QueryError,'POLICY_EXPIRED'):
            self.rules.evaluate(ACTOR,rule['rule_id'],'2026-11-14','2026-11-14')

    def test_historical_simulation_has_virtual_cooldown_no_changes(self):
        rule=self.create(cooldown_seconds=172800)
        result=self.rules.simulate(ACTOR,rule['rule_id'],'2026-11-14','2026-11-16')
        self.assertTrue(result['windows'][0]['entities'][0]['would_recommend'])
        self.assertFalse(result['windows'][1]['entities'][0]['would_recommend'])
        self.assertTrue(result['windows'][2]['entities'][0]['would_recommend'])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM plans').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM decision_cooldowns').fetchone()[0],0)

    def test_pause_identity_and_scope_checks(self):
        rule=self.create()
        changed=Principal('alice','v2','operator',ACTOR.scopes)
        with self.assertRaisesRegex(QueryError,'IDENTITY_CHANGED'):
            self.rules.evaluate(changed,rule['rule_id'],'2026-11-14','2026-11-14')
        reduced=Principal('alice','v1','operator',ACTOR.scopes-{'changes:propose'})
        with self.assertRaisesRegex(QueryError,'INSUFFICIENT_SCOPE'):
            self.rules.evaluate(reduced,rule['rule_id'],'2026-11-14','2026-11-14')
        self.rules.pause(ACTOR,rule['rule_id'])
        with self.assertRaisesRegex(QueryError,'POLICY_PAUSED'):
            self.rules.evaluate(ACTOR,rule['rule_id'],'2026-11-14','2026-11-14')

    def test_automatic_execute_mode_rejected(self):
        with self.assertRaisesRegex(QueryError,'INVALID_RULE'):
            self.create(mode='automatic_execute')
        with self.assertRaisesRegex(QueryError,'INVALID_ENTITY_ID'):
            self.create(entity_ids=['1','1'])

    def test_observe_does_not_create_plan(self):
        rule=self.create(mode='observe')
        result=self.rules.evaluate(ACTOR,rule['rule_id'],'2026-11-14','2026-11-14')
        self.assertIsNone(result['created_plan_id'])

    def test_scheduled_recommendation_uses_normal_draft_path(self):
        from datetime import datetime,timezone
        rule=self.create()
        self.now=datetime(2026,11,15,1,tzinfo=timezone.utc).timestamp()
        # Extend only synthetic test rule's expiry to the scheduled test date.
        with self.store.db() as db:db.execute('UPDATE decision_rules SET expires=?',(self.now+86400,))
        operations=Operations(self.store,decision_rules=self.rules)
        value=operations.create_subscription(ACTOR,kind='decision_rule',account='tinacms',rule_id=rule['rule_id'],at_beijing='09:15')
        self.now=value['next_due']
        async def resolve(subject):return ACTOR
        asyncio.run(BackgroundWorker(operations,resolve).run_once())
        notices=operations.inbox(ACTOR)['items']
        self.assertEqual(len(notices),1)
        self.assertIsNotNone(notices[0]['body']['created_plan_id'])

    def test_configured_cache_poller_is_independent_and_opt_in(self):
        server=create_server(self.query)
        self.assertIsNone(server.auto_refresh)
        server.auto_refresh={'enabled':True,'subject':'alice'}
        class Identity:
            async def resolve_background(self,subject):return ACTOR
        server.identity_provider=Identity()
        async def exercise():
            stop=asyncio.Event();calls=[]
            async def refresh(actor):calls.append(actor.subject);stop.set();return {'release_id':'new'}
            server.auto_refresh_callback=refresh
            await server.poll_cache(stop)
            self.assertEqual(calls,['alice'])
            self.assertEqual(server.auto_refresh_status['state'],'ready')
            self.assertEqual(server.auto_refresh_status['interval_seconds'],300)
        asyncio.run(exercise())
