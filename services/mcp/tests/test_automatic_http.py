"""HTTP MCP -> owner web CSRF approval -> queue -> synthetic readback."""
import asyncio
import copy
import re
import unittest
from datetime import datetime,timezone
from starlette.testclient import TestClient
from baidu_ads_mcp.auth import OAuthProvider
from baidu_ads_mcp.automatic_rules import AutomaticRules
from baidu_ads_mcp.capabilities import Capability,CapabilityRegistry
from baidu_ads_mcp.executor import ExecutionQueue
from baidu_ads_mcp.server import create_server
from baidu_ads_mcp.worker import tick
from test_auth_web import Setup,BASE,CLIENTS,GRANTS
from test_automatic_rules import Adapter,VALUES


class AutomaticHTTPTests(Setup,unittest.TestCase):
    def setUp(self):
        self.setup()
        self.now=datetime(2026,11,15,1,tzinfo=timezone.utc).timestamp();self.store.clock=lambda:self.now
        clients=copy.deepcopy(CLIENTS);clients[0]['scope']+=' policies:automate'
        grants=copy.deepcopy(GRANTS);grants['kenshi']['scopes']+=['policies:automate']
        self.provider=OAuthProvider(self.store,self.bridge,BASE,clients,grants)
        self.tokens=asyncio.run(self.grant('kenshi',grants['kenshi']['scopes']))
        self.actor=self.provider.principal({'subject':'kenshi','version':'v1'})
        self.registry=CapabilityRegistry(self.store);self.adapter=Adapter()
        self.registry.record(self.actor,Capability('tinacms','set_pause','campaign',self.adapter.adapter_id,'synthetic','test',self.now+86400,('pause',)),enabled=True)
        self.queue=ExecutionQueue(self.store,self.registry,self.adapter,lambda subject:asyncio.run(self.provider.resolve_actor(subject)),mutations_enabled=lambda:True)
        self.rules=AutomaticRules(self.store,self.queue,enabled=lambda:True)
        self.queue.pre_execution_guard=self.rules.guard;self.queue.automatic_rules=self.rules
        self.server=create_server(self.query,self.store,provider=self.provider,execution=self.queue)
        self.client=TestClient(self.server.streamable_http_app(),base_url=BASE)
        self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)

    def tool(self,name,params,success=True):
        response=self.client.post('/mcp',headers={'Authorization':'Bearer '+self.tokens.access_token,
            'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25'},
            json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':{'params':params}}})
        self.assertEqual(response.status_code,200,response.text)
        result=response.json()['result']
        self.assertEqual(bool(result.get('isError')),not success,str(result))
        return result.get('structuredContent',result)

    def login(self,subject):
        response=self.client.get('/login');csrf=re.search('name="csrf" value="([^"]+)"',response.text)[1]
        response=self.client.post('/login',headers={'Origin':BASE},data={'csrf':csrf,'subject':subject,'password':'test-password'},follow_redirects=False)
        self.assertEqual(response.status_code,303)

    def test_fixed_owner_web_approval_and_mcp_execution_readback(self):
        rule=self.tool('baidu_ads_create_automatic_rule',VALUES);path=rule['approval_path']
        self.tool('baidu_ads_trigger_automatic_rule',{'rule_id':rule['rule_id']},success=False)
        self.assertEqual(self.client.get(path).status_code,403)
        self.login('alice');self.assertEqual(self.client.get(path).status_code,403)
        self.login('kenshi');response=self.client.get(path)
        self.assertEqual(response.status_code,200);self.assertIn('允许在规则固定范围内自动执行',response.text)
        csrf=re.search('name="csrf" value="([^"]+)"',response.text)[1]
        data={'csrf':csrf,'action':'approve','version':1,'hash':rule['hash']}
        self.assertEqual(self.client.post(path,headers={'Origin':BASE},data={**data,'csrf':'bad'}).status_code,403)
        self.assertEqual(self.client.post(path,headers={'Origin':'https://untrusted.invalid'},data=data).status_code,403)
        self.assertEqual(self.client.post(path,headers={'Origin':BASE},data={**data,'hash':'wrong'}).status_code,403)
        self.assertEqual(self.client.post(path,headers={'Origin':BASE},data=data,follow_redirects=False).status_code,303)
        result=self.tool('baidu_ads_trigger_automatic_rule',{'rule_id':rule['rule_id']})
        tick(self.queue)
        job=self.tool('baidu_ads_get_execution_job',{'job_id':result['run']['job_id']})
        self.assertEqual(job['state'],'succeeded');self.assertEqual(len(self.adapter.calls),1)
        updated=self.tool('baidu_ads_update_automatic_rule',{**VALUES,'rule_id':rule['rule_id'],'version':1,'name':'New fixed definition'})
        self.assertEqual(updated['state'],'pending_approval')
        self.assertEqual(self.client.post(path,headers={'Origin':BASE},data=data).status_code,403)
        self.tool('baidu_ads_trigger_automatic_rule',{'rule_id':rule['rule_id']},success=False)
        self.assertEqual(len(self.adapter.calls),1)


if __name__=='__main__':unittest.main()
