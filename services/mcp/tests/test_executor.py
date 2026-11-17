import json
from baidu_ads_mcp.capabilities import Capability,CapabilityRegistry
from baidu_ads_mcp.executor import ExecutionQueue,Receipt,Observation,RejectedBeforeSend
from baidu_ads_mcp.worker import tick
from baidu_ads_mcp.query import QueryError
from test_analytics_changes import Base,OWNER,OPERATOR,BUDGET


class Adapter:
    adapter_id='synthetic-v1'
    def __init__(self):
        self.actual={'budget':'100.00','pause':False};self.calls=[];self.behavior='ok';self.unknown=False
    def read(self,account,operation):return dict(self.actual)
    def submit(self,account,operation,intent_id):
        self.calls.append(intent_id)
        if self.behavior=='reject':raise RejectedBeforeSend()
        self.actual['budget']=operation['amount']
        if self.behavior=='timeout':raise TimeoutError()
        return Receipt('synthetic-request')
    def reconcile(self,account,operation,intent_id,receipt,before,after):
        if self.unknown:return Observation('unknown',{},'none')
        return Observation('applied',dict(self.actual),'synthetic-readback',True)


class ExecutionTests(Base):
    def setUp(self):
        super().setUp();self.registry=CapabilityRegistry(self.store);self.adapter=Adapter();self.enabled=True
        self.identities={'alice':OPERATOR,'kenshi':OWNER}
        self.queue=ExecutionQueue(self.store,self.registry,self.adapter,self.identities.get,mutations_enabled=lambda:self.enabled,lease_seconds=60)
        self.register()
    def register(self,enabled=True,limit='100.00'):
        cap=Capability('tinacms','set_campaign_budget','campaign','synthetic-v1','test-v1','synthetic-contract',10000,('budget','pause'),'1000.00','100.00',limit)
        self.registry.record(OWNER,cap,enabled=enabled)
    def plan(self,amount='120.00'):
        plan=self.store.create(OPERATOR,'tinacms',[{**BUDGET,'amount':amount}],'synthetic request')
        self.store.request_approval(OPERATOR,plan['id'],1)
        self.store.approve_from_web(OWNER,plan['id'],1,plan['hash'])
        return plan
    def enqueue(self,plan=None,key='synthetic-key-12345'):
        plan=plan or self.plan();return self.queue.enqueue(OPERATOR,plan['id'],1,key)
    def test_default_switch_and_unknown_capability_block(self):
        plan=self.plan();self.enabled=False
        with self.assertRaisesRegex(QueryError,'MUTATIONS_DISABLED'):self.enqueue(plan)
        self.enabled=True;self.register(False)
        with self.assertRaisesRegex(QueryError,'UNVERIFIED_CAPABILITY'):self.enqueue(plan)
        self.assertEqual(self.adapter.calls,[])
    def test_success_idempotency_and_readback(self):
        plan=self.plan();job=self.enqueue(plan)
        self.assertEqual(self.enqueue(plan)['job_id'],job['job_id'])
        self.assertEqual(self.enqueue(plan,'different-key-12345')['job_id'],job['job_id'])
        tick(self.queue)
        self.assertEqual(self.queue.get(OPERATOR,job['job_id'])['state'],'succeeded')
        tick(self.queue);self.assertEqual(len(self.adapter.calls),1)
        events=self.queue.get(OPERATOR,job['job_id'])['events']
        self.assertIn('intent_persisted',[e['event'] for e in events])
    def test_same_key_different_plan_conflicts(self):
        self.enqueue()
        with self.assertRaisesRegex(QueryError,'IDEMPOTENCY_CONFLICT'):self.enqueue(self.plan())
    def test_stale_precondition_never_submits(self):
        job=self.enqueue();self.adapter.actual['budget']='101.00';tick(self.queue)
        result=self.queue.get(OPERATOR,job['job_id'])
        self.assertEqual(result['state'],'stopped');self.assertEqual(self.adapter.calls,[])
        self.assertEqual(result['items'][0]['result']['code'],'STALE_PRECONDITION')
    def test_timeout_unknown_reconciles_without_resending(self):
        job=self.enqueue();self.adapter.behavior='timeout';self.adapter.unknown=True;tick(self.queue)
        self.assertEqual(self.queue.get(OPERATOR,job['job_id'])['state'],'unknown')
        self.enabled=False;self.adapter.unknown=False;tick(self.queue)
        self.assertEqual(self.queue.get(OPERATOR,job['job_id'])['state'],'succeeded')
        self.assertEqual(len(self.adapter.calls),1)
    def test_entity_lock_blocks_second_plan_during_unknown(self):
        one=self.enqueue();two=self.enqueue(self.plan(),key='second-key-1234567')
        self.adapter.behavior='timeout';self.adapter.unknown=True;self.queue.run(one['job_id'])
        self.assertFalse(self.queue.run(two['job_id']));self.assertEqual(len(self.adapter.calls),1)
    def test_expired_lease_changes_sending_to_unknown(self):
        job=self.enqueue();claimed=self.queue.claim(job['job_id']);self.assertIsNotNone(claimed)
        with self.store.db() as db:
            db.execute("UPDATE execution_items SET state='sending' WHERE job_id=?",(job['job_id'],))
        self.adapter.actual['budget']='120.00';self.now+=61
        tick(self.queue)
        self.assertEqual(self.queue.get(OPERATOR,job['job_id'])['state'],'succeeded');self.assertEqual(self.adapter.calls,[])
    def test_daily_limit_is_atomic_and_blocks_split(self):
        self.register(limit='30.00');first=self.enqueue();tick(self.queue)
        self.query.data['tinacms']['extra']['inventory']['campaign'][0]['budget']='120.00'
        second=self.enqueue(self.plan('140.00'),key='second-key-1234567');tick(self.queue)
        result=self.queue.get(OPERATOR,second['job_id'])
        self.assertEqual(result['items'][0]['result']['code'],'DAILY_LIMIT_EXCEEDED');self.assertEqual(len(self.adapter.calls),1)
    def test_approval_reset_before_execution_stops_send(self):
        job=self.enqueue();self.identities['kenshi']=type(OWNER)('kenshi','new','owner',OWNER.scopes)
        tick(self.queue);self.assertEqual(self.adapter.calls,[])
        self.assertEqual(self.queue.get(OPERATOR,job['job_id'])['items'][0]['result']['code'],'APPROVER_IDENTITY_CHANGED')
    def test_cancellation_or_edit_stops_next_send(self):
        plan=self.plan();job=self.enqueue(plan);self.store.cancel(OPERATOR,plan['id'],1);tick(self.queue)
        self.assertEqual(self.adapter.calls,[])
        self.assertEqual(self.queue.get(OPERATOR,job['job_id'])['state'],'stopped')
    def test_compensation_creates_new_unapproved_draft(self):
        job=self.enqueue();tick(self.queue)
        self.query.data['tinacms']['extra']['inventory']['campaign'][0]['budget']='120.00'
        result=self.queue.compensation(OPERATOR,job['job_id'],'restore approved prior value')
        self.assertEqual(result['state'],'draft')
        self.assertEqual(result['proposal']['changes'][0]['operation']['amount'],'100.00')
        self.assertIsNone(result['approval_actor'])
    def test_non_authoritative_readback_cannot_succeed(self):
        job=self.enqueue();self.adapter.reconcile=lambda *args:Observation('applied',{'budget':'120.00'},'no-proof',False)
        tick(self.queue);self.assertEqual(self.queue.get(OPERATOR,job['job_id'])['state'],'unknown')

    def test_late_receipt_cannot_overwrite_new_worker_result(self):
        job=self.enqueue()
        def submit(*args):
            with self.store.db() as db:
                db.execute("UPDATE execution_jobs SET claim='new-worker',state='succeeded' WHERE id=?",(job['job_id'],))
                db.execute("UPDATE execution_items SET state='succeeded' WHERE job_id=?",(job['job_id'],))
            return Receipt('late-receipt')
        self.adapter.submit=submit
        self.queue.run(job['job_id'])
        result=self.queue.get(OPERATOR,job['job_id'])
        self.assertEqual(result['state'],'succeeded')
        self.assertEqual(result['items'][0]['state'],'succeeded')
        self.assertIsNone(result['items'][0]['receipt'])
        self.assertEqual(result['late_receipts'][0]['receipt']['request_id'],'late-receipt')

    def test_busy_worker_does_not_recover_live_worker(self):
        from baidu_ads_mcp.worker import worker_lock
        job=self.enqueue();self.queue.claim(job['job_id']);self.now+=61
        with worker_lock(self.store.path) as acquired:
            self.assertTrue(acquired)
            result=tick(self.queue)
            self.assertTrue(result['busy']);self.assertEqual(result['recovered'],0)

    def test_rule_guard_pause_before_send_stops_persisted_intent(self):
        checks=[]
        def guard(plan,payload):
            checks.append(plan['id'])
            if len(checks)==3:raise QueryError('AUTOMATIC_RULE_PAUSED')
        self.queue.pre_execution_guard=guard
        job=self.enqueue();tick(self.queue)
        self.assertEqual(self.adapter.calls,[])
        self.assertEqual(self.queue.get(OPERATOR,job['job_id'])['items'][0]['result']['code'],'AUTOMATIC_RULE_PAUSED')

    def test_zero_money_never_means_unlimited_or_inherited(self):
        for original,target in [('100.00','0'),('0','50.00')]:
            with self.subTest(original=original,target=target):
                self.adapter.actual['budget']=original
                self.query.data['tinacms']['extra']['inventory']['campaign'][0]['budget']=original
                job=self.enqueue(self.plan(target),key='zero-money-test-'+target)
                tick(self.queue)
                self.assertEqual(self.queue.get(OPERATOR,job['job_id'])['items'][0]['result']['code'],'UNSUPPORTED_MONEY_SEMANTICS')
        self.assertEqual(self.adapter.calls,[])
