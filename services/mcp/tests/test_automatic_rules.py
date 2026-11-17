import asyncio
import copy
import json
import re
import unittest
from datetime import datetime,timezone

from baidu_ads_mcp.automatic_rules import AutomaticRules
from baidu_ads_mcp.capabilities import Capability,CapabilityRegistry
from baidu_ads_mcp.changes import Principal
from baidu_ads_mcp.executor import ExecutionQueue,Receipt,Observation
from baidu_ads_mcp.query import QueryError
from baidu_ads_mcp.worker import tick
from test_analytics_changes import Base,OWNER,OPERATOR

AUTO=Principal('kenshi','v1','owner',OWNER.scopes|{'policies:automate'})
VALUES=dict(account='tinacms',name='Explicit synthetic guard',kind='campaign',entity_ids=['1'],
            cost_threshold='0.05',minimum_clicks=1,maximum_observed_cost='100',
            maximum_actions_per_run=1,maximum_actions_per_day=1,maximum_actions_total=2)


class Adapter:
    adapter_id='synthetic-auto-v1'
    def __init__(self):self.actual={'pause':False,'budget':'100.00'};self.calls=[];self.unknown=False
    def read(self,*args):return dict(self.actual)
    def submit(self,account,operation,intent_id):
        self.calls.append(operation)
        field='pause' if operation['op']=='set_pause' else ('budget' if operation['op']=='set_campaign_budget' else 'price')
        self.actual[field]=operation.get('paused',operation.get('amount'))
        if self.unknown:raise TimeoutError()
        return Receipt('synthetic-only')
    def reconcile(self,*args):return Observation('unknown' if self.unknown else 'applied',dict(self.actual),'synthetic-current',not self.unknown)


class AutomaticRuleTests(Base):
    def setUp(self):
        super().setUp();self.now=datetime(2026,11,15,1,tzinfo=timezone.utc).timestamp()
        self.registry=CapabilityRegistry(self.store);self.adapter=Adapter();self.current=AUTO
        self.enabled=True;self.automatic=True
        self.queue=ExecutionQueue(self.store,self.registry,self.adapter,lambda subject:self.current,mutations_enabled=lambda:self.enabled)
        self.rules=AutomaticRules(self.store,self.queue,enabled=lambda:self.automatic)
        self.queue.pre_execution_guard=self.rules.guard
        self.queue.automatic_rules=self.rules
        self.register()

    def register(self,operation='set_pause',kind='campaign'):
        self.registry.record(AUTO,Capability('tinacms',operation,kind,self.adapter.adapter_id,'test-only','synthetic-proof',self.now+90*86400,
                             ('pause',) if operation=='set_pause' else ('budget',),'1000','500','1000'),enabled=True)

    def create(self,approve=True,**extra):
        rule=self.rules.create(AUTO,**{**VALUES,**extra})
        if approve:self.rules.approve_from_web(AUTO,rule['rule_id'],rule['version'],rule['hash'])
        return rule

    def test_default_disabled_and_separate_approval(self):
        rule=self.create(False)
        with self.assertRaisesRegex(QueryError,'AUTOMATIC_APPROVAL_REQUIRED'):self.rules.trigger(AUTO,rule['rule_id'])
        self.rules.approve_from_web(AUTO,rule['rule_id'],1,rule['hash'])
        self.automatic=False
        with self.assertRaisesRegex(QueryError,'AUTOMATIC_MUTATIONS_DISABLED'):self.rules.trigger(AUTO,rule['rule_id'])
        self.automatic=True;self.enabled=False
        with self.assertRaisesRegex(QueryError,'AUTOMATIC_MUTATIONS_DISABLED'):self.rules.trigger(AUTO,rule['rule_id'])
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM plans').fetchone()[0],0)

    def test_owner_scope_hash_and_update_invalidation(self):
        with self.assertRaisesRegex(QueryError,'AUTOMATIC_OWNER_REQUIRED'):self.rules.create(OPERATOR,**VALUES)
        with self.assertRaisesRegex(QueryError,'INSUFFICIENT_SCOPE'):self.rules.create(OWNER,**VALUES)
        rule=self.create(False)
        with self.assertRaisesRegex(QueryError,'STALE_RULE_VERSION'):self.rules.approve_from_web(AUTO,rule['rule_id'],1,'wrong')
        self.rules.approve_from_web(AUTO,rule['rule_id'],1,rule['hash'])
        new=self.rules.update(AUTO,rule['rule_id'],1,**{**VALUES,'name':'Updated'})
        self.assertEqual(new['version'],2);self.assertEqual(new['state'],'pending_approval')
        with self.assertRaisesRegex(QueryError,'AUTOMATIC_APPROVAL_REQUIRED'):self.rules.trigger(AUTO,rule['rule_id'])

    def test_real_queue_idempotency_execution_and_readback(self):
        rule=self.create();result=self.rules.trigger(AUTO,rule['rule_id']);again=self.rules.trigger(AUTO,rule['rule_id'])
        self.assertEqual(result['run']['job_id'],again['run']['job_id'])
        tick(self.queue)
        self.assertEqual(self.queue.get(AUTO,result['run']['job_id'])['state'],'succeeded')
        self.assertEqual(len(self.adapter.calls),1)
        self.assertTrue(self.adapter.actual['pause'])

    def test_restart_recovers_reserved_plan_and_no_double_send(self):
        rule=self.create();enqueue=self.queue.enqueue
        self.queue.enqueue=lambda *a:(_ for _ in ()).throw(RuntimeError('simulated crash'))
        with self.assertRaises(RuntimeError):self.rules.trigger(AUTO,rule['rule_id'])
        self.queue.enqueue=enqueue
        self.rules=AutomaticRules(self.store,self.queue,enabled=lambda:True);self.queue.pre_execution_guard=self.rules.guard
        result=self.rules.trigger(AUTO,rule['rule_id']);tick(self.queue)
        self.assertEqual(self.queue.get(AUTO,result['run']['job_id'])['state'],'succeeded')
        self.assertEqual(len(self.adapter.calls),1)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM plans').fetchone()[0],1)

    def test_unknown_result_stops_future_evaluations(self):
        rule=self.create();result=self.rules.trigger(AUTO,rule['rule_id']);self.adapter.unknown=True;tick(self.queue)
        self.assertEqual(self.queue.get(AUTO,result['run']['job_id'])['state'],'unknown')
        self.now+=86400
        rows=self.query.data['tinacms']['reports']['campaign'];rows.append({**rows[0],'date':'2026-11-15'})
        self.rules.run_due()
        self.assertEqual(self.rules.get(AUTO,rule['rule_id'])['stop_reason'],'AUTOMATIC_UNKNOWN_STOP')
        self.assertEqual(len(self.adapter.calls),1)

    def test_missing_data_and_cost_stop_do_not_create_plan(self):
        rule=self.create(lookback_days=2)
        with self.assertRaisesRegex(QueryError,'DATA_INCOMPLETE'):self.rules.trigger(AUTO,rule['rule_id'])
        self.assertEqual(self.rules.get(AUTO,rule['rule_id'])['state'],'stopped')
        other=self.create(maximum_observed_cost='0.05')
        with self.assertRaisesRegex(QueryError,'AUTOMATIC_COST_STOP'):self.rules.trigger(AUTO,other['rule_id'])

    def test_pause_after_enqueue_blocks_send(self):
        rule=self.create();result=self.rules.trigger(AUTO,rule['rule_id']);self.rules.pause(AUTO,rule['rule_id']);tick(self.queue)
        self.assertEqual(self.adapter.calls,[])
        self.assertEqual(self.queue.get(AUTO,result['run']['job_id'])['state'],'stopped')

    def test_budget_explicit_target_money_limits_and_simulation(self):
        self.register('set_campaign_budget')
        values=dict(action='set_campaign_budget',target_amount='120.00',maximum_target_cny='200',maximum_action_change_cny='20',
                    maximum_daily_change_cny='20',maximum_total_change_cny='20',maximum_budget_increase_cny='20')
        rule=self.create(**values)
        result=self.rules.simulate(AUTO,rule['rule_id'],'2026-11-14','2026-11-14')
        self.assertEqual(result['windows'][0]['would_apply'][0]['amount'],'120.00')
        self.assertEqual(result['windows'][0]['amount_change_cny'],'20.00')
        queued=self.rules.trigger(AUTO,rule['rule_id']);tick(self.queue)
        self.assertEqual(self.adapter.actual['budget'],'120.00')
        self.assertEqual(self.queue.get(AUTO,queued['run']['job_id'])['state'],'succeeded')
        blocked=self.create(**{**values,'maximum_budget_increase_cny':'0'})
        with self.assertRaisesRegex(QueryError,'AUTOMATIC_MONEY_LIMIT'):self.rules.trigger(AUTO,blocked['rule_id'])
        self.now+=86400
        rows=self.query.data['tinacms']['reports']['campaign'];rows.append({**rows[0],'date':'2026-11-15'})
        with self.assertRaisesRegex(QueryError,'AUTOMATIC_MONEY_LIMIT'):self.rules.trigger(AUTO,rule['rule_id'])

    def test_manual_bid_has_fixed_target_and_independent_money_contract(self):
        self.query.data['tinacms']['reports']['keyword'][0].update(wInfoId=9,cost='0.10',click=1)
        self.adapter.actual['price']='1.00'
        self.registry.record(AUTO,Capability('tinacms','set_manual_bid','keyword',self.adapter.adapter_id,'synthetic','test-only',self.now+86400,
                                           ('price',),'5','1','10'),enabled=True)
        rule=self.create(kind='keyword',entity_ids=['9'],action='set_manual_bid',target_amount='1.20',maximum_target_cny='2',
                         maximum_action_change_cny='0.20',maximum_daily_change_cny='0.20',maximum_total_change_cny='0.20')
        result=self.rules.trigger(AUTO,rule['rule_id']);tick(self.queue)
        self.assertEqual(self.adapter.actual['price'],'1.20')
        self.assertEqual(self.queue.get(AUTO,result['run']['job_id'])['state'],'succeeded')

    def test_resume_is_explicit_and_noop_does_not_consume_actions(self):
        rule=self.create(action='resume')
        result=self.rules.trigger(AUTO,rule['rule_id'])
        self.assertIsNone(result['run']['plan_id'])
        self.assertEqual(json.loads(result['run']['entity_ids']),[])
        self.query.data['tinacms']['extra']['inventory']['campaign'][0]['pause']=True
        self.adapter.actual['pause']=True
        resume=self.create(action='resume');result=self.rules.trigger(AUTO,resume['rule_id']);tick(self.queue)
        self.assertFalse(self.adapter.actual['pause'])

    def test_total_limit_cooldown_and_frequency_simulation(self):
        rows=self.query.data['tinacms']['reports']['campaign']
        for date in ('2026-11-15','2026-11-16'):rows.append({**rows[0],'date':date})
        rule=self.create(cooldown_seconds=172800,maximum_actions_total=1)
        result=self.rules.simulate(AUTO,rule['rule_id'],'2026-11-14','2026-11-16')
        self.assertEqual([len(w['would_apply']) for w in result['windows']],[1,0,0])
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM plans').fetchone()[0],0)

    def test_unknown_capability_never_enqueues_or_sends(self):
        rule=self.create()
        with self.store.db() as db:db.execute('UPDATE execution_capabilities SET enabled=0')
        with self.assertRaisesRegex(QueryError,'UNVERIFIED_CAPABILITY'):self.rules.trigger(AUTO,rule['rule_id'])
        self.assertEqual(self.adapter.calls,[])

    def test_due_scheduler_uses_same_queue(self):
        rule=self.create();results=self.rules.run_due()
        self.assertEqual(results[0]['rule_id'],rule['rule_id'])
        tick(self.queue)
        self.assertEqual(len(self.adapter.calls),1)


if __name__=='__main__':unittest.main()
