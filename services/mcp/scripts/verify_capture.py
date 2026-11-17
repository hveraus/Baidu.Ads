"""Independent browser-capture/API/MCP acceptance. Emits no private aggregate counts.

Run with the MCP package's Python environment. Input files remain in ignored work/.
A capture ID is deliberately not asserted to be a production release revision.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit
import warnings

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ACCOUNTS = ('tinacms', 'yakshaver', 'ssw')
FIELDS = ('cost', 'click', 'impression')


class VerificationError(Exception):
    pass


class ReportBlock(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active=False;self.blocks=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=='script' and attrs.get('id')=='report-data' and attrs.get('type')=='application/json':
            self.active=True;self.blocks.append('')
    def handle_endtag(self,tag):
        if tag=='script':self.active=False
    def handle_data(self,text):
        if self.active:self.blocks[-1]+=text


def captured(path):
    parser=ReportBlock();parser.feed(path.read_text(encoding='utf-8'))
    if len(parser.blocks)!=1:raise VerificationError('CAPTURE_REPORT_BLOCK_INVALID')
    return json.loads(parser.blocks[0],parse_float=Decimal)


def total(rows, field):
    if not rows or any(r.get(field) is None for r in rows):return None
    return sum((Decimal(str(r[field])) for r in rows),Decimal(0))


def equal(left,right):
    if left is None or right is None:return left is right
    return Decimal(str(left))==Decimal(str(right))


def record(report,name,passed,**context):
    report['checks'].append({'check':name,'passed':bool(passed),**context})


async def stdio_checks(directory,meta,captures,report):
    upstream=json.loads((directory/'upstream.json').read_text(),parse_float=Decimal)
    keyword_api=json.loads((directory/'upstream-keywords.json').read_text(),parse_float=Decimal)
    revision=directory/meta['capture_id']
    params=StdioServerParameters(command=sys.executable,args=['-m','baidu_ads_mcp.server','--release-copy',str(revision)])
    with open(os.devnull,'w') as quiet:
        async with stdio_client(params,errlog=quiet) as streams:
            async with ClientSession(*streams) as client:
                await client.initialize()
                report['registered_tool_count']=len((await client.list_tools()).tools)
                async def call(name,arguments):
                    response=await client.call_tool(name,{'params':arguments})
                    if response.isError or not isinstance(response.structuredContent,dict):
                        raise VerificationError('MCP_TOOL_CALL_FAILED')
                    return response.structuredContent
                for account in ACCOUNTS:
                    reference=upstream[account]
                    start,end=reference['range']
                    if keyword_api[account]['range']!=reference['range']:
                        raise VerificationError('UPSTREAM_RANGES_DIFFER')
                    args={'account':account,'start':start,'end':end}
                    summary=await call('baidu_ads_get_summary',args)
                    observed=captures[account]['reports']
                    selected={dimension:[r for r in observed[dimension] if start<=r['date']<=end] for dimension in ('account','keyword')}
                    for field in FIELDS:
                        record(report,'account_api_browser_capture_mcp',
                               equal(summary['observed_totals'][field],reference['totals'][field])
                               and equal(summary['observed_totals'][field],total(selected['account'],field)),
                               account=account,metric=field,start=start,end=end)
                    keyword=await call('baidu_ads_get_performance',{**args,'dimension':'keyword','limit':5})
                    for field in FIELDS:
                        record(report,'keyword_api_browser_capture_mcp',
                               equal(keyword['observed_totals'][field],keyword_api[account]['totals'][field])
                               and equal(keyword['observed_totals'][field],total(selected['keyword'],field)),
                               account=account,metric=field,start=start,end=end)
                    rec=summary['reconciliation']
                    independent={field:total(selected['account'],field)-total(selected['keyword'],field) for field in FIELDS}
                    record(report,'cross_dimension_difference_explicit',
                           rec['primary_source']=='baidu_ads.account' and rec['dashboard_overview_source']=='baidu_ads.keyword'
                           and all(equal(rec['observed_difference_primary_minus_dashboard'][f],independent[f]) for f in FIELDS),account=account)
                    report['dimension_notes'].append({'account':account,
                        'primary_source':'baidu_ads.account','dashboard_source':'baidu_ads.keyword',
                        'same_dimension_checks_passed':all(equal(summary['observed_totals'][f],reference['totals'][f]) for f in FIELDS),
                        'cross_dimension_totals_differ':any(value!=0 for value in independent.values()),
                        'warning':'Different report dimensions are not interchangeable; inspect MCP reconciliation.'})
                    report['questions'].append({'question':f'{account}指定日期的消费、点击和展现是否与相同维度的百度接口及网页数据一致？',
                        'answer':'PASS' if all(equal(summary['observed_totals'][f],reference['totals'][f]) for f in FIELDS) else 'FAIL',
                        'scope':'account report, not dashboard keyword overview','range':[start,end]})
                    for dimension in ('campaign','search_terms'):
                        response=await call('baidu_ads_get_performance',{**args,'dimension':dimension,'limit':5})
                        selected_rows=[r for r in observed[dimension] if start<=r['date']<=end]
                        record(report,'additional_report_mcp_vs_browser_capture',
                               all(equal(response['observed_totals'][f],total(selected_rows,f)) for f in FIELDS),
                               account=account,dimension=dimension,independent_upstream_api=False)
                    # Stdio has no authenticated private bridge; verify it fails closed.
                    if account in ('tinacms','ssw'):
                        tool='baidu_ads_get_cli_metrics' if account=='tinacms' else 'baidu_ads_get_wecom_metrics'
                        denied=await client.call_tool(tool,{'params':args})
                        record(report,'stdio_private_source_requires_identity_bridge',denied.isError,account=account)
                dimensions_path=directory/'upstream-dimensions.json'
                if dimensions_path.exists():
                    dimensions=json.loads(dimensions_path.read_text(),parse_float=Decimal)
                    for account,reports in dimensions.items():
                        for dimension,reference in reports.items():
                            if 'error_type' in reference:
                                record(report,'additional_dimension_upstream_unavailable',False,account=account,dimension=dimension)
                                continue
                            start,end=reference['range']
                            result=await call('baidu_ads_get_performance',{'account':account,'start':start,'end':end,'dimension':dimension,'limit':1})
                            rows=[r for r in captures[account]['reports'][dimension] if start<=r['date'][:10]<=end]
                            record(report,'additional_dimension_api_browser_capture_mcp',
                                all(equal(result['observed_totals'][f],reference['totals'][f]) and equal(result['observed_totals'][f],total(rows,f)) for f in FIELDS),
                                account=account,dimension=dimension,start=start,end=end)
                for account in ('tinacms','ssw'):
                    web=captures[account]['website_traffic']
                    for range_row in web['ranges']:
                        result=await call('baidu_ads_get_website_metrics',{'account':account,'start':range_row['start'],'end':range_row['end']})
                        record(report,'website_exact_range_mcp_vs_browser_capture',
                            result['aggregation']=='exact_range' and all(equal(result['totals'][f],range_row.get(f)) for f in result['totals']),
                            account=account,start=range_row['start'],end=range_row['end'],independent_upstream_api=False)
                    key='tina_paid_website_conversions' if account=='tinacms' else 'ssw_paid_website_conversions'
                    rows=captures[account].get(key,[])
                    for row in rows:
                        response=await call('baidu_ads_get_website_metrics',{'account':account,'start':row['date'],'end':row['date'],'traffic_scope':'baidu_paid'})
                        record(report,'paid_daily_mcp_preserves_capture_nulls_and_values',
                            all(equal(response['totals'][field],row.get(field)) for field in response['totals']),account=account,date=row['date'],independent_upstream_api=False)


def private_capture_checks(directory,meta,captures,report):
    """Exercise HTTP private tools with explicit test identity and captured values only.

    This validates parsing/calculation through the tool, not real OAuth identity,
    a production reader deployment or an independent PostHog/WeCom API call.
    """
    from mcp.server.auth.provider import AuthorizationParams
    from starlette.testclient import TestClient
    from baidu_ads_mcp.auth import OAuthProvider
    from baidu_ads_mcp.changes import ChangeStore
    from baidu_ads_mcp.query import ReleaseQuery
    from baidu_ads_mcp.server import create_server
    from baidu_ads_mcp.web import BoundaryMiddleware

    base='http://127.0.0.1:8797';subject='capture-test-viewer'
    source_keys={'cli':('tinacms','private_posthog_cli',('started','finished')),
                 'wecom':('ssw','private_wecom_consultations',('customers','messages'))}
    class CaptureBridge:
        async def authenticate(self,name,password):
            if name!=subject:raise VerificationError('TEST_IDENTITY_INVALID')
            return {'subject':subject,'version':'capture-validation-only'}
        async def verify(self,identity):
            if identity!={'subject':subject,'version':'capture-validation-only'}:raise VerificationError('TEST_IDENTITY_INVALID')
            return identity
        async def call(self,action,**arguments):
            if action!='private':raise VerificationError('TEST_BRIDGE_OPERATION_INVALID')
            account,key,_=source_keys[arguments['source']]
            data=captures[account][key]
            return {'release_id':meta['capture_id'],'source':arguments['source'],'through':data['through'],'rows':data['rows']}
    with tempfile.TemporaryDirectory(prefix='mcp-capture-acceptance-') as temporary:
        query=ReleaseQuery(directory/meta['capture_id'])
        store=ChangeStore(Path(temporary)/'state.sqlite3',query)
        clients=[{'client_id':'capture-test-client','redirect_uris':['http://127.0.0.1:9999/callback'],
                  'grant_types':['authorization_code','refresh_token'],'response_types':['code'],
                  'token_endpoint_auth_method':'none','scope':'analytics:read'}]
        provider=OAuthProvider(store,CaptureBridge(),base,clients,{subject:{'role':'viewer','scopes':['analytics:read']}})
        async def grant():
            client=await provider.get_client('capture-test-client')
            challenge=base64.urlsafe_b64encode(hashlib.sha256(secrets.token_bytes(32)).digest()).decode().rstrip('=')
            params=AuthorizationParams(state='capture-only',scopes=['analytics:read'],code_challenge=challenge,
                redirect_uri=clients[0]['redirect_uris'][0],redirect_uri_provided_explicitly=True,resource=provider.resource)
            pending=parse_qs(urlsplit(await provider.authorize(client,params)).query)['request'][0]
            code=parse_qs(urlsplit(await provider.consent(pending,subject,'synthetic-test-only')).query)['code'][0]
            model=await provider.load_authorization_code(client,code)
            return await provider.exchange_authorization_code(client,model)
        token=asyncio.run(grant())
        server=create_server(query,store,provider=provider)
        app=BoundaryMiddleware(server.streamable_http_app(),base)
        with TestClient(app,base_url=base) as client:
            headers={'Authorization':'Bearer '+token.access_token,'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25'}
            for source,(account,key,fields) in source_keys.items():
                snapshot=captures[account].get(key)
                if not snapshot or not snapshot.get('rows'):
                    record(report,'private_browser_capture_present',False,account=account,source=source)
                    continue
                rows=snapshot['rows'];start=min(r['date'] for r in rows);end=max(r['date'] for r in rows)
                for window,first in [('capture_range',start),('latest_day',end)]:
                    args={'account':account,'start':first,'end':end}
                    tool='baidu_ads_get_cli_metrics' if source=='cli' else 'baidu_ads_get_wecom_metrics'
                    response=client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':tool,'arguments':{'params':args}}},headers=headers)
                    body=response.json().get('result',{})
                    if response.status_code!=200 or body.get('isError'):
                        record(report,'private_capture_tool_calculation',False,source=source,window=window);continue
                    result=body['structuredContent'];selected={r['date']:r for r in rows if first<=r['date']<=end}
                    expected_days=(date.fromisoformat(end)-date.fromisoformat(first)).days+1
                    checks=[]
                    for field in fields:
                        complete=len(selected)==expected_days and all(r.get(field) is not None for r in selected.values())
                        expected=sum(r[field] for r in selected.values()) if complete else None
                        checks.append(equal(result['totals'][field],expected))
                    record(report,'private_capture_tool_calculation',all(checks),source=source,window=window,
                        provenance='authenticated_browser_capture_with_test_identity_bridge',independent_upstream_api=False,production_identity_verified=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=Path(__file__).resolve().parents[3]/'work/mcp-verification')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--browser-directory',type=Path,help='Directory containing original authenticated mcp-live-{account}.html captures; defaults to parent of --directory')
    args=parser.parse_args();directory=args.directory.resolve()
    logging.disable(logging.CRITICAL);warnings.filterwarnings('ignore')
    meta=json.loads((directory/'capture.json').read_text())
    output=args.output or directory/'acceptance-summary.json'
    report={'schema_version':'1','verified_at':datetime.now(timezone.utc).isoformat(),'capture_id':meta['capture_id'],
        'source_release_id':meta.get('source_release_id'),'capture_id_is_production_revision':False,
        'provenance':'authenticated_browser_capture_plus_saved_independent_baidu_account_and_keyword_api_responses',
        'private_counts_in_report':False,'production_mcp_deployment_verified':False,
        'production_identity_bridge_verified':False,'independent_private_upstream_api_verified':False,
        'questions':[],'checks':[],'dimension_notes':[]}
    try:
        browser=args.browser_directory.resolve() if args.browser_directory else directory.parent
        captures={a:captured(browser/f'mcp-live-{a}.html') for a in ACCOUNTS}
        asyncio.run(stdio_checks(directory,meta,captures,report))
        private_capture_checks(directory,meta,captures,report)
    except Exception as exc:
        record(report,'acceptance_process_completed',False,error_code=str(exc) if isinstance(exc,VerificationError) else 'UNEXPECTED_ACCEPTANCE_ERROR',error_type=type(exc).__name__)
    report['status']='PASS' if report['checks'] and all(c['passed'] for c in report['checks']) else 'FAIL'
    report['passed_checks']=sum(c['passed'] for c in report['checks']);report['total_checks']=len(report['checks'])
    output.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as target:json.dump(report,target,ensure_ascii=False,indent=2)
    output.chmod(0o600)
    print(f"{report['status']}: {report['passed_checks']}/{report['total_checks']} checks; no private counts emitted.")
    print('Scope: authenticated browser captures and saved independent Baidu API responses; not production MCP/private API acceptance.')
    raise SystemExit(0 if report['status']=='PASS' else 1)


if __name__=='__main__':main()
