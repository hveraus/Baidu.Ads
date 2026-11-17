import json
import httpx
from baidu_ads_mcp.changes import Principal
from baidu_ads_mcp.capabilities import Capability,CapabilityRegistry
from baidu_ads_mcp.executor import ExecutionQueue
from baidu_ads_mcp.baidu_write import BaiduWriteAdapter
from baidu_ads_mcp.worker import tick,simulate
from baidu_ads_mcp.query import QueryError
from test_analytics_changes import Base,OWNER,OPERATOR


class CreationTests(Base):
    def setUp(self):
        super().setUp()
        self.query.data['tinacms']['extra']['inventory']['adgroup']=[{'adgroupId':'2','campaignId':'1','adgroupName':'Synthetic','pause':False,'status':31}]
        self.query.data['tinacms']['extra']['inventory']['creative']=[{'creativeId':'3','adgroupId':'2','title':'old','description1':'desc','description2':'','pcDestinationUrl':'https://example.test/','mobileDestinationUrl':'https://example.test/','pause':True,'status':1}]
        self.calls=[];self.rows={};self.timeout=False
        def handler(request):
            payload=json.loads(request.content)['body'];method=request.url.path.rsplit('/',1)[-1];self.calls.append((method,payload))
            if method=='getAdgroup':rows=[{'adgroupId':2,'campaignId':1,'pause':False,'status':31}]
            elif method=='getCampaign':rows=[self.rows[i] for i in payload['campaignIds'] if i in self.rows]
            elif method=='addCampaign':
                self.rows[100]={**payload['campaignTypes'][0],'campaignId':100};rows=[self.rows[100]]
                if self.timeout:raise httpx.ReadTimeout('synthetic')
            elif method=='addAdgroup':rows=[{**payload['adgroupTypes'][0],'adgroupId':200}]
            elif method=='addWord':rows=[{**payload['keywordTypes'][0],'keywordId':300}]
            elif method=='addCreative':rows=[{**payload['creativeTypes'][0],'creativeId':400}]
            elif method=='deleteCampaign':
                for identifier in payload['campaignIds']:self.rows.pop(identifier,None)
                rows=[]
            else:rows=[]
            return httpx.Response(200,json={'header':{'status':0,'failures':[]},'body':{'data':rows}})
        self.adapter=BaiduWriteAdapter(lambda _: {'user_name':'synthetic','access_token':'SYNTHETIC'},enabled=lambda:True,transport=httpx.MockTransport(handler))
        self.registry=CapabilityRegistry(self.store)
        self.identities={'kenshi':OWNER,'alice':OPERATOR}
        self.queue=ExecutionQueue(self.store,self.registry,self.adapter,self.identities.get,mutations_enabled=lambda:True)
        self.op={'op':'create_campaign','request_id':'a'*32,'name':'Synthetic new campaign','budget':'120.00'}
        self.registry.record(OWNER,Capability('tinacms','create_campaign','campaign',self.adapter.adapter_id,'official-sdk','offline-contract',10000,('account',),'500','200','500'),enabled=True)

    def enqueue(self):
        plan=self.store.create(OPERATOR,'tinacms',[self.op],'Synthetic creation')
        self.store.request_approval(OPERATOR,plan['id'],1);self.store.approve_from_web(OWNER,plan['id'],1,plan['hash'])
        return self.queue.enqueue(OPERATOR,plan['id'],1,'synthetic-create-key')

    def test_create_returns_new_id_and_readback_without_name_search(self):
        job=self.enqueue();tick(self.queue)
        result=self.queue.get(OPERATOR,job['job_id'])
        self.assertEqual(result['state'],'succeeded')
        self.assertEqual(result['items'][0]['result']['created_entity_id'],'100')
        self.assertEqual(self.calls[0],('addCampaign',{'campaignTypes':[{'campaignName':'Synthetic new campaign','budget':120.0,'pause':True}]}))
        self.assertEqual(self.calls[1][1]['campaignIds'],[100])

    def test_unknown_creation_requires_manual_id_never_resends(self):
        self.timeout=True;job=self.enqueue();tick(self.queue);tick(self.queue)
        result=self.queue.get(OPERATOR,job['job_id'])
        self.assertEqual(result['state'],'unknown')
        self.assertEqual(result['items'][0]['result']['code'],'MANUAL_CREATED_ID_RECONCILIATION_REQUIRED')
        self.assertEqual(len([r for r in self.calls if r[0]=='addCampaign']),1)
        with self.assertRaisesRegex(QueryError,'INSUFFICIENT_ROLE'):
            self.queue.bind_created_entity_from_web(OPERATOR,job['job_id'],0,'100','synthetic owner inspection')
        binding=self.queue.bind_created_entity_from_web(OWNER,job['job_id'],0,'100','synthetic owner inspection')
        self.assertTrue(binding['requires_upstream_creation_provenance'])
        tick(self.queue)
        self.assertEqual(self.queue.get(OPERATOR,job['job_id'])['state'],'unknown')
        self.assertEqual(len([r for r in self.calls if r[0]=='addCampaign']),1)

    def test_creation_defaults_paused_and_validates_parents(self):
        for operation in (
            {'op':'create_adgroup','request_id':'b'*32,'campaign_id':'1','name':'new','max_price':'1.00'},
            {'op':'create_keyword','request_id':'c'*32,'adgroup_id':'2','keyword':'原词','price':'1.00','match_type':1},
            {'op':'create_creative','request_id':'d'*32,'adgroup_id':'2','title':'new','description1':'desc','pc_destination_url':'https://example.test/','mobile_destination_url':'https://example.test/'},
        ):
            plan=self.store.create(OPERATOR,'tinacms',[operation],'synthetic')
            self.assertTrue(plan['proposal']['changes'][0]['after']['pause'])
            self.assertTrue(plan['proposal']['changes'][0]['creation'])
        with self.assertRaisesRegex(QueryError,'INVALID_OPERATION'):
            self.store.create(OPERATOR,'tinacms',[{**self.op,'pause':False}],'synthetic')

    def test_creative_update_and_delete_are_explicit(self):
        op={'op':'update_creative','entity_id':'3','title':'new','description1':'desc','pc_destination_url':'https://example.test/','mobile_destination_url':'https://example.test/'}
        result=self.store.create(OPERATOR,'tinacms',[op],'synthetic')['proposal']['changes'][0]
        self.assertEqual(result['after']['title'],'new')
        result=self.store.create(OWNER,'tinacms',[{'op':'delete_entity','kind':'campaign','entity_id':'1','acknowledge_irreversible':True}],'synthetic')['proposal']['changes'][0]
        self.assertTrue(result['irreversible'])
        with self.assertRaisesRegex(QueryError,'INSUFFICIENT_SCOPE'):
            self.registry.record(OWNER,Capability('tinacms','delete_entity','campaign',self.adapter.adapter_id,'v1','synthetic',10000,('deleted',)),enabled=True)

    def test_worker_cli_simulation(self):
        self.assertEqual(simulate(),{'mode':'simulation','state':'succeeded','provider_calls':1,'production_calls':0})
