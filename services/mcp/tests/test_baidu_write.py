import json
import httpx
import unittest
from baidu_ads_mcp.baidu_write import BaiduWriteAdapter,BASE
from baidu_ads_mcp.executor import RejectedBeforeSend
from baidu_ads_mcp.query import QueryError


class BaiduAdapterTests(unittest.TestCase):
    def setUp(self):
        self.requests=[];self.current={'campaignId':1,'budget':100.0,'pause':False,'campaignBidType':0,'campaignOcpcBidType':0,'negativeWords':['原词'],'exactNegativeWords':[]}
        self.fail=False
        def response(request):
            body=json.loads(request.content);self.requests.append((str(request.url),body))
            if str(request.url).endswith('updateCampaign'):
                if self.fail:raise httpx.ReadTimeout('synthetic timeout')
                self.current.update(body['body']['campaignTypes'][0])
            return httpx.Response(200,json={'header':{'status':0,'failures':[]},'body':{'data':[self.current]}})
        self.transport=httpx.MockTransport(response)
        self.adapter=BaiduWriteAdapter(lambda account:{'user_name':'synthetic','access_token':'SYNTHETIC_SECRET'},enabled=lambda:True,transport=self.transport)
        self.operation={'op':'set_campaign_budget','entity_id':'1','amount':'120.00'}
    def test_budget_exact_official_wire_contract(self):
        before=self.adapter.read('tinacms',self.operation)
        receipt=self.adapter.submit('tinacms',self.operation,'a'*32)
        self.assertEqual(self.requests[0][0],BASE+'CampaignService/getCampaign')
        url,payload=self.requests[-1]
        self.assertEqual(url,BASE+'CampaignService/updateCampaign')
        self.assertEqual(payload['body'],{'campaignTypes':[{'campaignId':1,'budget':120.0}]})
        result=self.adapter.reconcile('tinacms',self.operation,'a'*32,receipt,before,{**before,'budget':'120.00'})
        self.assertEqual(result.outcome,'applied');self.assertTrue(result.authoritative)
    def test_disabled_adapter_never_sends(self):
        adapter=BaiduWriteAdapter(lambda account:{},transport=self.transport)
        with self.assertRaises(RejectedBeforeSend):adapter.submit('tinacms',self.operation,'a'*32)
        self.assertEqual(self.requests,[])
    def test_timeout_is_not_retried(self):
        self.adapter.read('tinacms',self.operation);self.fail=True
        with self.assertRaises(httpx.ReadTimeout):self.adapter.submit('tinacms',self.operation,'a'*32)
        self.assertEqual(sum(url.endswith('updateCampaign') for url,_ in self.requests),1)
    def test_negative_replacement_preserves_existing_words(self):
        op={'op':'add_negative_keywords','kind':'campaign','entity_id':'1','match':'phrase','words':['新增','原词']}
        self.adapter.read('tinacms',op);self.adapter.submit('tinacms',op,'a'*32)
        self.assertEqual(self.requests[-1][1]['body'],{'campaignTypes':[{'campaignId':1,'negativeWords':['原词','新增']}]})
    def test_unknown_operation_no_forwarding(self):
        with self.assertRaises(QueryError):self.adapter.read('tinacms',{'op':'raw','url':'https://evil.test'})
        self.assertEqual(self.requests,[])
    def test_upstream_error_does_not_echo_secret(self):
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'header':{'status':1,'desc':'SYNTHETIC_SECRET'}}))
        adapter=BaiduWriteAdapter(lambda account:{'user_name':'test','access_token':'SYNTHETIC_SECRET'},transport=transport)
        with self.assertRaisesRegex(QueryError,'UPSTREAM_OPERATION_REJECTED') as error:adapter.read('tinacms',self.operation)
        self.assertNotIn('SYNTHETIC_SECRET',str(error.exception))
    def test_schedule_and_regions_exact_payload(self):
        for operation,field,value in [
            ({'op':'set_campaign_regions','entity_id':'1','region_ids':[2000]},'regionTarget',[2000]),
            ({'op':'set_campaign_schedule','entity_id':'1','paused_intervals':[]},'schedule',[]),
            ({'op':'set_campaign_devices','entity_id':'1','equipment_type':2},'equipmentType',2)]:
            self.adapter.read('tinacms',operation);self.adapter.submit('tinacms',operation,'a'*32)
            self.assertEqual(self.requests[-1][1]['body'],{'campaignTypes':[{'campaignId':1,field:value}]})

    def test_keyword_match_and_pause_wire_and_id_filter(self):
        records=[]
        keyword={'keywordId':9,'campaignId':1,'adgroupId':2,'keyword':'原词','matchType':1,'phraseType':1,'pause':False,'price':1.00}
        def handler(request):
            payload=json.loads(request.content);records.append((str(request.url),payload['body']))
            if str(request.url).endswith('updateWord'):keyword.update(payload['body']['keywordTypes'][0])
            return httpx.Response(200,json={'header':{'status':0},'body':{'data':[keyword]}})
        adapter=BaiduWriteAdapter(lambda account:{'user_name':'test','access_token':'SYNTHETIC'},enabled=lambda:True,transport=httpx.MockTransport(handler))
        op={'op':'set_keyword_match','entity_id':'9','match_type':2,'phrase_type':2}
        adapter.read('tinacms',op);adapter.submit('tinacms',op,'b'*32)
        self.assertEqual(records[0][1]['ids'],[9]);self.assertEqual(records[0][1]['idType'],11)
        self.assertEqual(records[-1],(BASE+'KeywordService/updateWord',{'keywordTypes':[{'keywordId':9,'matchType':2,'phraseType':2}]}))

    def test_absolute_deadline_bounds_streaming_response(self):
        import asyncio
        from unittest.mock import patch
        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{'
                await asyncio.sleep(1)
                yield b'}'
        adapter=BaiduWriteAdapter(lambda _: {'user_name':'test','access_token':'SYNTHETIC'},
            transport=httpx.MockTransport(lambda _:httpx.Response(200,stream=SlowBody())))
        with patch('baidu_ads_mcp.baidu_write.REQUEST_TIMEOUT_SECONDS',0.01):
            with self.assertRaises(TimeoutError):adapter.read('tinacms',self.operation)

    def test_creations_creative_update_and_leaf_delete_wire_contracts(self):
        records=[]
        rows={'Campaign':{'campaignId':1,'pause':False,'status':1},
              'Adgroup':{'adgroupId':2,'campaignId':1,'pause':False,'status':31},
              'Word':{'keywordId':3,'adgroupId':2,'keyword':'原词','price':1,'pause':True},
              'Creative':{'creativeId':4,'adgroupId':2,'title':'old','description1':'old','pause':True}}
        def handler(request):
            method=request.url.path.rsplit('/',1)[-1];body=json.loads(request.content)['body'];records.append((method,body))
            kind=next(name for name in rows if method.endswith(name))
            if method.startswith('add'):
                key={'Campaign':'campaign','Adgroup':'adgroup','Word':'keyword','Creative':'creative'}[kind]
                data=[{**body[key+'Types'][0],key+'Id':100}]
            elif method.startswith('delete'):data=[]
            else:data=[rows[kind]]
            return httpx.Response(200,json={'header':{'status':0},'body':{'data':data}})
        adapter=BaiduWriteAdapter(lambda _: {'user_name':'test','access_token':'SYNTHETIC'},enabled=lambda:True,transport=httpx.MockTransport(handler))
        cases=[
            ({'op':'create_adgroup','request_id':'a'*32,'campaign_id':'1','name':'Synthetic','max_price':'1.00'},'addAdgroup','adgroupTypes',{'campaignId':1,'adgroupName':'Synthetic','maxPrice':1.0,'pause':True}),
            ({'op':'create_keyword','request_id':'b'*32,'adgroup_id':'2','keyword':'原词','price':'1.00','match_type':1},'addWord','keywordTypes',{'adgroupId':2,'keyword':'原词','price':1.0,'matchType':1,'pause':True}),
            ({'op':'create_creative','request_id':'c'*32,'adgroup_id':'2','title':'new','description1':'desc','pc_destination_url':'https://example.test/','mobile_destination_url':'https://m.example.test/'},'addCreative','creativeTypes',{'adgroupId':2,'title':'new','description1':'desc','description2':'','pcDestinationUrl':'https://example.test/','mobileDestinationUrl':'https://m.example.test/','pause':True}),
            ({'op':'update_creative','entity_id':'4','title':'new','description1':'desc','pc_destination_url':'https://example.test/','mobile_destination_url':'https://m.example.test/'},'updateCreative','creativeTypes',{'creativeId':4,'title':'new','description1':'desc','description2':'','pcDestinationUrl':'https://example.test/','mobileDestinationUrl':'https://m.example.test/'})]
        for operation,method,key,expected in cases:
            with self.subTest(op=operation['op']):
                adapter.read('tinacms',operation);receipt=adapter.submit('tinacms',operation,'f'*32)
                self.assertEqual(records[-1],(method,{key:[expected]}))
                if method.startswith('add'):self.assertEqual(receipt.created_entity_id,'100')
        operation={'op':'delete_entity','kind':'keyword','entity_id':'3','acknowledge_irreversible':True}
        adapter.read('tinacms',operation);adapter.submit('tinacms',operation,'f'*32)
        self.assertEqual(records[-1],('deleteWord',{'keywordIds':[3]}))

    def test_bid_strategy_preserves_official_numeric_fields(self):
        self.current.update(campaignBid=1.0,campaignOcpcBid=2.0)
        operation={'op':'set_campaign_bid_strategy','entity_id':'1','campaign_bid_type':2,'campaign_ocpc_bid_type':1,'campaign_bid':'1.50','campaign_ocpc_bid':'2.50'}
        before=self.adapter.read('tinacms',operation);receipt=self.adapter.submit('tinacms',operation,'f'*32)
        self.assertEqual(self.requests[-1][1]['body'],{'campaignTypes':[{'campaignId':1,'campaignBidType':2,'campaignOcpcBidType':1,'campaignBid':1.5,'campaignOcpcBid':2.5}]})
        after={**before,'campaignBidType':2,'campaignOcpcBidType':1,'campaignBid':'1.50','campaignOcpcBid':'2.50'}
        self.assertEqual(self.adapter.reconcile('tinacms',operation,'f'*32,receipt,before,after).outcome,'applied')
