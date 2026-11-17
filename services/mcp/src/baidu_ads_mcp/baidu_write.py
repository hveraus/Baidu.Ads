"""Narrow Baidu Search HTTP adapter; disabled by default, no collector imports.

Wire names/endpoints checked against Baidu's official SDK commit
 ea0d5748d530f8b79c1f587f05eb92579be485f4 (2026-11-17).
This proves request construction, not any account's live write entitlement.
"""
from __future__ import annotations
import asyncio
from decimal import Decimal
import hashlib
import json
import httpx
from .changes import OPERATIONS,canonical
from .executor import Receipt,Observation,RejectedBeforeSend,match_fields
from .query import QueryError
from .creation import creation_target,parent_preconditions

SDK_REVISION='ea0d5748d530f8b79c1f587f05eb92579be485f4'
BASE='https://api.baidu.com/json/sms/service/'
REQUEST_TIMEOUT_SECONDS=20
SPECS={
    'creative':('CreativeService','getCreative','updateCreative','creativeId','creativeFields','creativeTypes',('creativeId','campaignId','adgroupId','title','description1','description2','pause','status','pcDestinationUrl','mobileDestinationUrl')),
    'campaign':('CampaignService','getCampaign','updateCampaign','campaignId','campaignFields','campaignTypes',
        ('campaignId','campaignName','budget','pause','status','negativeWords','exactNegativeWords','campaignBidType','campaignOcpcBidType','campaignBid','campaignOcpcBid','sharedBudgetId','regionTarget','schedule','equipmentType')),
    'adgroup':('AdgroupService','getAdgroup','updateAdgroup','adgroupId','adgroupFields','adgroupTypes',
        ('adgroupId','campaignId','adgroupName','maxPrice','pause','status','negativeWords','exactNegativeWords')),
    'keyword':('KeywordService','getWord','updateWord','keywordId','wordFields','keywordTypes',
        ('keywordId','campaignId','adgroupId','keyword','price','pause','status','matchType','phraseType')),
}


class BaiduWriteAdapter:
    adapter_id='baidu-search-'+SDK_REVISION

    def __init__(self,credentials,*,enabled=lambda:False,transport=None,manual_bid_modes=None):
        """credentials(account) resolves target-host private configuration only.

        manual_bid_modes is a verified per-account set; empty defaults deny manual
        bid changes. httpx transport injection is for deterministic offline tests.
        """
        self.credentials,self.enabled=credentials,enabled
        self.transport=transport
        self.manual_bid_modes=manual_bid_modes or {}
        self._before={}

    def operation(self,value):
        try:return OPERATIONS.validate_python([value])[0].model_dump()
        except ValueError:raise QueryError('UNSUPPORTED_OPERATION') from None

    def call(self,account,kind,method,body):
        # A real absolute deadline cancels the active network operation. Per-I/O
        # timeouts alone allow an indefinitely slow response; no abandoned thread
        # may continue a write after its caller has marked the outcome unknown.
        return asyncio.run(self._call(account,kind,method,body))

    async def _call(self,account,kind,method,body):
        service,get,update,*_=SPECS[kind]
        addition={'campaign':'addCampaign','adgroup':'addAdgroup','keyword':'addWord','creative':'addCreative'}[kind]
        deletion={'campaign':'deleteCampaign','adgroup':'deleteAdgroup','keyword':'deleteWord','creative':'deleteCreative'}[kind]
        if method not in (get,update,addition,deletion):raise QueryError('UNSUPPORTED_OPERATION')
        credentials=self.credentials(account)
        if not isinstance(credentials,dict) or not credentials.get('user_name') or not credentials.get('access_token'):
            raise QueryError('PROVIDER_CREDENTIALS_UNAVAILABLE')
        payload={'header':{'userName':credentials['user_name'],'accessToken':credentials['access_token']},'body':body}
        # No retry middleware, proxies, redirects, or caller-provided URL. TLS verifies by default.
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS), httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS,follow_redirects=False,trust_env=False,transport=self.transport) as client:
            async with client.stream('POST',BASE+service+'/'+method,json=payload,headers={'Content-Type':'application/json;charset=UTF-8'}) as response:
                if response.status_code!=200:raise QueryError('UPSTREAM_HTTP_ERROR')
                raw=bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw)>1024*1024:raise QueryError('UPSTREAM_RESPONSE_TOO_LARGE')
        try:result=json.loads(raw)
        except ValueError:raise QueryError('UPSTREAM_INVALID_RESPONSE') from None
        if not isinstance(result,dict) or not isinstance(result.get('header'),dict):raise QueryError('UPSTREAM_INVALID_RESPONSE')
        header=result['header']
        if type(header.get('status')) is not int or header['status']!=0 or header.get('failures'):
            # Never include provider descriptions: they can echo credentials or user input.
            raise QueryError('UPSTREAM_OPERATION_REJECTED')
        if not isinstance(result.get('body',{}),dict):raise QueryError('UPSTREAM_INVALID_RESPONSE')
        return result.get('body',{})

    def entity(self,account,kind,identifier):
        service,get,update,id_field,fields_key,types_key,fields=SPECS[kind]
        if kind=='campaign':body={fields_key:list(fields),'campaignIds':[int(identifier)]}
        else:body={fields_key:list(fields),'ids':[int(identifier)],'idType':{'adgroup':5,'keyword':11,'creative':7}[kind],'getTemp':0}
        rows=self.call(account,kind,get,body).get('data')
        if not isinstance(rows,list):raise QueryError('UPSTREAM_INVALID_RESPONSE')
        rows=[r for r in rows if isinstance(r,dict) and str(r.get(id_field))==str(identifier)]
        if len(rows)!=1:raise QueryError('ENTITY_NOT_FOUND_OR_AMBIGUOUS')
        # Preserve only typed contract fields. No raw response or credentials enter the queue.
        return {key:rows[0][key] for key in fields if key in rows[0]}

    def read(self,account,operation):
        operation=self.operation(operation);kind=operation.get('kind','campaign')
        if operation['op'].startswith('create_'):
            if operation['op']=='create_campaign':current=parent_preconditions(account,operation)
            else:
                parent_kind='campaign' if operation['op']=='create_adgroup' else 'adgroup'
                parent_id=operation['campaign_id'] if parent_kind=='campaign' else operation['adgroup_id']
                parent=self.entity(account,parent_kind,parent_id)
                current=parent_preconditions(account,operation,parent)
        else:
            current=self.entity(account,kind,operation['entity_id'])
            if operation['op']=='delete_entity':current['deleted']=False
        if operation['op']=='set_manual_bid':
            campaign_id=current.get('campaignId')
            if campaign_id is None:raise QueryError('LIVE_PARENT_UNAVAILABLE')
            parent=self.entity(account,'campaign',str(campaign_id))
            allowed=self.manual_bid_modes.get(account,frozenset())
            if parent.get('campaignBidType') not in allowed or parent.get('campaignOcpcBidType') not in (None,0):
                raise QueryError('UNVERIFIED_BID_MODE')
            current['parentCampaignPause']=parent.get('pause')
            current['parentCampaignBidType']=parent.get('campaignBidType')
        self._before[(account,canonical(operation))]=current
        return current

    def submit(self,account,operation,intent_id):
        return self.submit_authorized(account,operation,intent_id,lambda:None)

    def submit_authorized(self,account,operation,intent_id,authorize_before_send):
        if not self.enabled():raise RejectedBeforeSend()
        operation=self.operation(operation);kind=operation.get('kind','campaign')
        spec=SPECS[kind];id_field,types_key=spec[3],spec[5]
        before=self._before.pop((account,canonical(operation)),None)
        if before is None:raise RejectedBeforeSend()
        # Re-read immediately before submit, including mode checks. This narrows,
        # but cannot remove, the race against external account editors.
        current=self.read(account,operation)
        if current!=before:raise RejectedBeforeSend()
        op=operation['op']
        if op.startswith('create_'):
            entity=creation_target(operation)
            for field in ('campaignId','adgroupId'):
                if field in entity:entity[field]=int(entity[field])
            for field in ('budget','maxPrice','price'):
                if field in entity:entity[field]=float(Decimal(entity[field]))
            self.authorize(authorize_before_send)
            method={'campaign':'addCampaign','adgroup':'addAdgroup','keyword':'addWord','creative':'addCreative'}[kind]
            result=self.call(account,kind,method,{types_key:[entity]})
            rows=result.get('data')
            if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict):raise QueryError('CREATED_ENTITY_ID_UNAVAILABLE')
            identifier=rows[0].get(id_field)
            if type(identifier) not in (int,str) or not str(identifier).isdigit() or int(identifier)<=0:raise QueryError('CREATED_ENTITY_ID_UNAVAILABLE')
            return Receipt('intent:'+intent_id,str(identifier))
        entity={id_field:int(operation['entity_id'])}
        if op=='delete_entity':
            if kind in ('campaign','adgroup'):raise RejectedBeforeSend()
            method={'campaign':'deleteCampaign','adgroup':'deleteAdgroup','keyword':'deleteWord','creative':'deleteCreative'}[kind]
            self.authorize(authorize_before_send)
            self.call(account,kind,method,{id_field+'s':[int(operation['entity_id'])]})
            return Receipt('intent:'+intent_id)
        if op=='update_creative':entity.update(creation_target(operation))
        elif op=='set_campaign_budget':
            if current.get('sharedBudgetId') not in (None,0):raise RejectedBeforeSend()
            entity['budget']=float(Decimal(operation['amount']))
        elif op=='set_manual_bid':entity['price' if kind=='keyword' else 'maxPrice']=float(Decimal(operation['amount']))
        elif op=='set_pause':entity['pause']=operation['paused']
        elif op=='set_campaign_bid_strategy':
            entity.update(campaignBidType=operation['campaign_bid_type'],campaignOcpcBidType=operation['campaign_ocpc_bid_type'],campaignBid=float(Decimal(operation['campaign_bid'])),campaignOcpcBid=float(Decimal(operation['campaign_ocpc_bid'])))
        elif op=='set_campaign_regions':entity['regionTarget']=list(dict.fromkeys(operation['region_ids']))
        elif op=='set_campaign_schedule':entity['schedule']=operation['paused_intervals']
        elif op=='set_campaign_devices':entity['equipmentType']=operation['equipment_type']
        elif op=='set_keyword_match':
            entity['matchType']=operation['match_type']
            if operation['phrase_type'] is not None:entity['phraseType']=operation['phrase_type']
        else:
            field='exactNegativeWords' if operation['match']=='exact' else 'negativeWords'
            if not isinstance(current.get(field),list):raise RejectedBeforeSend()
            words=operation['words']
            entity[field]=list(dict.fromkeys(current[field]+words)) if op.startswith('add_') else [v for v in current[field] if v not in words]
        # Last switch check before the single POST. There is no blind retry.
        self.authorize(authorize_before_send)
        self.call(account,kind,spec[2],{types_key:[entity]})
        # Baidu's standard header does not guarantee an upstream idempotency ID.
        # This is only the local intent receipt, not an exactly-once claim.
        return Receipt('intent:'+intent_id)

    def authorize(self,callback):
        # Live rereads above can take time. Revalidate the exact job approval,
        # identity, rule and capability after them, immediately before the POST.
        try:callback()
        except Exception:raise RejectedBeforeSend() from None
        if not self.enabled():raise RejectedBeforeSend()

    def reconcile(self,account,operation,intent_id,receipt,before,after):
        operation=self.operation(operation)
        if operation['op'].startswith('create_'):
            if receipt is None or not receipt.created_entity_id or receipt.request_id!='intent:'+intent_id:
                return Observation('unknown',{},'requires_manual_created_id_reconciliation',False)
            identifier=receipt.created_entity_id
        else:identifier=operation['entity_id']
        try:
            actual=self.entity(account,operation.get('kind','campaign'),identifier)
        except QueryError as exc:
            if str(exc)=='ENTITY_NOT_FOUND_OR_AMBIGUOUS' and operation['op']=='delete_entity' and receipt is not None:
                # Acknowledged deletion plus ID-specific absence. Ambiguous multirows
                # must not count as absent, so repeat a bounded exact-ID query below.
                kind=operation['kind'];spec=SPECS[kind]
                body={spec[4]:[spec[3]],'campaignIds':[int(identifier)]} if kind=='campaign' else {spec[4]:[spec[3]],'ids':[int(identifier)],'idType':{'adgroup':5,'keyword':11,'creative':7}[kind],'getTemp':0}
                if self.call(account,kind,spec[1],body).get('data')==[]:
                    return Observation('applied',{'deleted':True},'acknowledged_delete_and_absence:'+identifier,True)
            raise
        if operation['op']=='delete_entity':return Observation('unknown',{},'delete_not_yet_absent',False)
        changed=[k for k,v in after.items() if before.get(k)!=v]
        if changed and match_fields(actual,after,changed):
            evidence='readback:'+hashlib.sha256(canonical({k:actual.get(k) for k in changed}).encode()).hexdigest()
            return Observation('applied',actual,evidence,True)
        # An old-value read cannot prove an uncertain request will never apply.
        return Observation('unknown',{},'unconfirmed',False)
