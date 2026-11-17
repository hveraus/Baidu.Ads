import unittest
from baidu_ads_mcp.policies import PolicyStore
from baidu_ads_mcp.changes import Principal
from baidu_ads_mcp.query import QueryError
from test_analytics_changes import Base,OPERATOR


class PolicyTests(Base):
    def setUp(self):
        super().setUp()
        self.policies=PolicyStore(self.store)
        self.actor=Principal('alice','v1','operator',OPERATOR.scopes|{'policies:manage'})

    def create(self):
        return self.policies.create(self.actor,account='tinacms',name='费用观察',cost_threshold='0.10',minimum_clicks=1)

    def test_evaluation_is_idempotent_observation(self):
        policy=self.create()
        first=self.policies.evaluate(self.actor,policy['policy_id'],'2026-11-14','2026-11-14')
        second=self.policies.evaluate(self.actor,policy['policy_id'],'2026-11-14','2026-11-14')
        self.assertEqual(first,second);self.assertTrue(first['matched'])
        self.assertEqual(first['action'],'observation_only')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM evaluations').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM plans').fetchone()[0],0)

    def test_incomplete_data_does_not_evaluate(self):
        policy=self.create()
        with self.assertRaisesRegex(QueryError,'DATA_INCOMPLETE'):
            self.policies.evaluate(self.actor,policy['policy_id'],'2026-11-14','2026-11-16')

    def test_pause_stops_new_evaluation(self):
        policy=self.create();self.policies.pause(self.actor,policy['policy_id'])
        with self.assertRaisesRegex(QueryError,'POLICY_PAUSED'):
            self.policies.evaluate(self.actor,policy['policy_id'],'2026-11-14','2026-11-14')

    def test_auto_execution_mode_rejected(self):
        with self.assertRaisesRegex(QueryError,'INVALID_POLICY'):
            self.policies.create(self.actor,account='tinacms',name='rule',cost_threshold='1',minimum_clicks=1,mode='execute')
