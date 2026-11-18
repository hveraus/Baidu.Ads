"""Authenticated MCP-owned login/consent and human proposal approval pages."""
import asyncio
import hmac
import html
import json
import secrets
from urllib.parse import urlsplit, parse_qs

from starlette.responses import HTMLResponse, RedirectResponse, PlainTextResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .query import QueryError
from .auth import token_hash


def escape(value):
    return html.escape(str(value),quote=True)


def page(title,body):
    return HTMLResponse('<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>'+escape(title)+'</title><body><main><h1>'+escape(title)+'</h1>'+body+'</main></body></html>',headers={
        'Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        'Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'})


def install_routes(server,provider,store,operations=None,execution=None,automatic_rules=None):
    secure=urlsplit(provider.base).scheme=='https'
    cookie='__Host-mcp_session' if secure else 'mcp_dev_session'
    challenge_cookie='__Host-mcp_challenge' if secure else 'mcp_dev_challenge'

    async def form(request):
        if request.headers.get('origin')!=provider.base:
            raise QueryError('INVALID_ORIGIN')
        if len(await request.body())>8192:
            raise QueryError('REQUEST_TOO_LARGE')
        return dict(await request.form())

    async def entry(request):
        pending=request.query_params.get('request') if request.url.path=='/consent' else None
        try:
            if request.method=='GET':
                text='使用看板身份登录 MCP 管理站。'
                if pending:
                    with store.db() as db:
                        row=provider.load(db,pending,'pending')
                    if not row:
                        raise QueryError('AUTHORIZATION_EXPIRED')
                    text='授权客户端 '+escape(row['data']['client_id'])+' 访问：'+escape(', '.join(row['data']['params']['scopes']))
                    text+='<br>授权结果将返回：'+escape(row['data']['params']['redirect_uri'])
                    text+='<br>仅在你刚刚主动连接该客户端时继续。'
                elif request.url.path=='/consent':
                    raise QueryError('AUTHORIZATION_EXPIRED')
                csrf=secrets.token_urlsafe(32)
                with store.db() as db:
                    challenge=provider.put(db,'browser',{'csrf':csrf,'pending':pending},provider.clock()+300)
                response=page('MCP 登录与授权', '<p>'+text+'</p><form method="post">'
                    '<input type="hidden" name="csrf" value="'+csrf+'">'
                    '<label>账号 <input name="subject" required maxlength="64" autocomplete="username"></label>'
                    '<label>密码 <input type="password" name="password" required maxlength="1024" autocomplete="current-password"></label>'
                    '<button type="submit">'+('登录并授权' if pending else '登录')+'</button></form>')
                response.set_cookie(challenge_cookie,challenge,secure=secure,httponly=True,samesite='lax',max_age=300,path='/')
                return response
            data=await form(request)
            with store.db() as db:
                row=provider.load(db,request.cookies.get(challenge_cookie,''),'browser')
                if not row or row['data']['pending']!=pending or not hmac.compare_digest(row['data']['csrf'],data.get('csrf','')):
                    raise QueryError('INVALID_CSRF')
                db.execute('UPDATE tokens SET used=1 WHERE hash=?',(row['hash'],))
            if pending:
                redirect=await provider.consent(pending,data.get('subject'),data.get('password'))
                response=RedirectResponse(redirect,status_code=303)
            else:
                session=await provider.login_web(data.get('subject'),data.get('password'))
                response=RedirectResponse('/plans',status_code=303)
                response.set_cookie(cookie,session,secure=secure,httponly=True,samesite='lax',max_age=3600,path='/')
            response.delete_cookie(challenge_cookie,path='/')
            response.headers['Cache-Control']='no-store'
            response.headers['Referrer-Policy']='no-referrer'
            return response
        except (QueryError,ValueError,TypeError):
            return PlainTextResponse('登录或授权失败，请重新打开登录页面。',status_code=403,headers={'Cache-Control':'no-store'})

    server.custom_route('/login',methods=['GET','POST'])(entry)
    server.custom_route('/consent',methods=['GET','POST'])(entry)

    @server.custom_route('/automatic-rules/{rule_id}',methods=['GET','POST'])
    async def automatic_rule(request):
        try:
            if automatic_rules is None:raise QueryError('POLICY_STORE_NOT_CONFIGURED')
            actor,csrf=await provider.web_session(request.cookies.get(cookie,''))
            rule_id=request.path_params['rule_id']
            if request.method=='POST':
                data=await form(request)
                if not hmac.compare_digest(csrf,data.get('csrf','')):raise QueryError('INVALID_CSRF')
                # Every approval is bound to exactly the version/hash displayed.
                if data.get('action')=='approve':
                    automatic_rules.approve_from_web(actor,rule_id,int(data.get('version','')),data.get('hash',''))
                elif data.get('action')=='pause':
                    automatic_rules.pause(actor,rule_id)
                else:raise QueryError('INVALID_ACTION')
                return RedirectResponse('/automatic-rules/'+rule_id,status_code=303)
            rule=automatic_rules.get(actor,rule_id)
            body='<p>此授权允许在规则固定范围内自动执行。暂停、恢复、预算和出价均以以下明确目标为准；它不会授予其他操作。修改规则后必须重新审批。</p>'
            body+='<p>全局自动开关：'+escape(rule['automatic_switch_enabled'])+'；执行开关：'+escape(rule['execution_switch_enabled'])+'。任一关闭或能力未验证都会拒绝执行。缺数、身份变化、未知结果或触发限额后停止。</p>'
            body+='<pre>'+escape(json.dumps(rule,ensure_ascii=False,indent=2))+'</pre>'
            body+='<form method="post"><input type="hidden" name="csrf" value="'+escape(csrf)+'"><input type="hidden" name="version" value="'+str(rule['version'])+'"><input type="hidden" name="hash" value="'+escape(rule['hash'])+'">'
            if rule['state']=='pending_approval':body+='<button name="action" value="approve">批准此版本的固定规则及限额</button>'
            body+='<button name="action" value="pause">停止规则并撤销授权</button></form><p><a href="/plans">返回变更方案</a></p>'
            return page('受限自动规则审批',body)
        except (QueryError,ValueError,TypeError):
            return PlainTextResponse('无法审批或读取规则。请核对权限、版本和登录状态。',status_code=403,headers={'Cache-Control':'no-store'})

    @server.custom_route('/plans',methods=['GET'])
    async def plans(request):
        try:
            actor,csrf=await provider.web_session(request.cookies.get(cookie,''))
            with store.db() as db:
                if actor.role=='owner':
                    rows=db.execute('SELECT id,account,state,version FROM plans ORDER BY rowid DESC LIMIT 100').fetchall()
                else:
                    rows=db.execute('SELECT id,account,state,version FROM plans WHERE author=? ORDER BY rowid DESC LIMIT 100',(actor.subject,)).fetchall()
            links=''.join('<li><a href="/plans/'+escape(r['id'])+'">'+escape(r['account'])+' · '+escape(r['state'])+' · v'+str(r['version'])+'</a></li>' for r in rows)
            status='已开启，仍须验证操作能力、审批及实时条件' if execution and execution.enabled() else '已关闭'
            return page('投流变更方案','<p>广告执行总开关：'+status+'。</p><p><a href="/notifications">站内通知</a> · <a href="/subscriptions">订阅管理</a></p><ul>'+links+'</ul>'
                        '<form method="post" action="/logout"><input type="hidden" name="csrf" value="'+csrf+'"><button>退出登录</button></form>')
        except QueryError:
            return RedirectResponse('/login',status_code=303)

    @server.custom_route('/plans/{plan_id}',methods=['GET','POST'])
    async def plan(request):
        try:
            actor,csrf=await provider.web_session(request.cookies.get(cookie,''))
            plan_id=request.path_params['plan_id']
            if request.method=='POST':
                data=await form(request)
                if not hmac.compare_digest(csrf,data.get('csrf','')):
                    raise QueryError('INVALID_CSRF')
                version=int(data.get('version',''))
                if data.get('action')=='approve':
                    store.approve_from_web(actor,plan_id,version,data.get('hash'))
                elif data.get('action')=='cancel':
                    store.cancel(actor,plan_id,version)
                else:
                    raise QueryError('INVALID_ACTION')
                return RedirectResponse('/plans/'+plan_id,status_code=303)
            value=store.get(actor,plan_id)
            body='<p>账号：'+escape(value['account'])+'；作者：'+escape(value['author'])+'；版本：'+str(value['version'])+'；状态：'+escape(value['state'])+'</p>'
            status='已开启，执行前仍须通过能力和实时条件验证' if execution and execution.enabled() else '已关闭'
            body+='<p>依据为历史快照。批准后可由具备权限的客户端提交本版本执行任务。当前执行总开关：'+status+'。预算变化不代表实际花费预测。</p>'
            body+='<p>理由：'+escape(value['proposal']['reason'])+'</p>'
            for change in value['proposal']['changes']:
                body+='<h2>'+escape(change['operation']['op'])+' · '+escape(change['operation'].get('entity_id',change['operation'].get('request_id','')))+'</h2><table><tr><th>字段</th><th>修改前</th><th>修改后</th></tr>'
                for field,diff in change['changed_fields'].items():
                    body+='<tr><td>'+escape(field)+'</td><td><pre>'+escape(json.dumps(diff['before'],ensure_ascii=False))+'</pre></td><td><pre>'+escape(json.dumps(diff['after'],ensure_ascii=False))+'</pre></td></tr>'
                body+='</table>'
                if change.get('creation'):
                    body+='<p>创建操作必须保存并核对返回的新对象 ID；结果未知时禁止按名称匹配或重发。</p>'
                if change.get('irreversible'):
                    body+='<p>不可逆删除：不能恢复已经消耗的金额或历史曝光，需独立删除权限。</p>'
            body+='<form method="post"><input type="hidden" name="csrf" value="'+csrf+'"><input type="hidden" name="version" value="'+str(value['version'])+'"><input type="hidden" name="hash" value="'+value['hash']+'">'
            if actor.role=='owner' and actor.subject=='kenshi' and value['state']=='awaiting_approval':
                body+='<button name="action" value="approve">批准此版本，允许后续提交执行</button>'
            body+='<button name="action" value="cancel">取消方案</button></form><p><a href="/plans">返回列表</a></p>'
            return page('审核投流变更',body)
        except (QueryError,ValueError):
            return PlainTextResponse('身份、权限或方案版本校验失败。',status_code=403,headers={'Cache-Control':'no-store'})

    @server.custom_route('/exports/{export_id}',methods=['GET'])
    async def download_export(request):
        try:
            actor,_=await provider.web_session(request.cookies.get(cookie,''))
            if operations is None:
                raise QueryError('EXPORT_NOT_FOUND')
            value=operations.download(actor,request.path_params['export_id'])
            return Response(value['content'],headers={'Content-Type':value['mime'],
                'Content-Disposition':'attachment; filename="'+value['filename']+'"',
                'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
                'Content-Security-Policy':"default-src 'none'; sandbox"})
        except QueryError:
            return PlainTextResponse('导出不存在、已过期或无权下载。',status_code=403,headers={'Cache-Control':'no-store'})

    @server.custom_route('/notifications',methods=['GET','POST'])
    async def notifications(request):
        try:
            actor,csrf=await provider.web_session(request.cookies.get(cookie,''))
            if operations is None:
                raise QueryError('BACKGROUND_SERVICE_NOT_CONFIGURED')
            if request.method=='POST':
                data=await form(request)
                if not hmac.compare_digest(csrf,data.get('csrf','')):
                    raise QueryError('INVALID_CSRF')
                operations.mark_read(actor,data.get('notification_id',''))
                return RedirectResponse('/notifications',status_code=303)
            values=operations.inbox(actor)
            body='<p>仅站内通知，不向外部发送消息。</p>'
            for value in values['items']:
                body+='<article><h2>'+escape(value['title'])+'</h2><pre>'+escape(json.dumps(value['body'],ensure_ascii=False,indent=2))+'</pre>'
                if value['read_at'] is None:
                    body+='<form method="post"><input type="hidden" name="csrf" value="'+csrf+'"><input type="hidden" name="notification_id" value="'+value['notification_id']+'"><button>标记已读</button></form>'
                body+='</article>'
            return page('站内通知',body)
        except QueryError:
            return PlainTextResponse('请登录具有订阅权限的账号。',status_code=403,headers={'Cache-Control':'no-store'})

    @server.custom_route('/subscriptions',methods=['GET','POST'])
    async def subscriptions(request):
        try:
            actor,csrf=await provider.web_session(request.cookies.get(cookie,''))
            actor.require('subscriptions:manage')
            if operations is None:
                raise QueryError('BACKGROUND_SERVICE_NOT_CONFIGURED')
            if request.method=='POST':
                data=await form(request)
                if not hmac.compare_digest(csrf,data.get('csrf','')):
                    raise QueryError('INVALID_CSRF')
                operations.pause_subscription(actor,data.get('subscription_id',''))
                return RedirectResponse('/subscriptions',status_code=303)
            with store.db() as db:
                ids=[r[0] for r in db.execute('SELECT id FROM subscriptions WHERE owner=? ORDER BY next_due LIMIT 100',(actor.subject,))]
            body='<p>按北京时间运行；只刷新 MCP 缓存、生成摘要或观察规则，不修改广告。</p>'
            for identifier in ids:
                value=operations.get_subscription(actor,identifier)
                body+='<article><pre>'+escape(json.dumps(value,ensure_ascii=False,indent=2))+'</pre>'
                if not value['paused']:
                    body+='<form method="post"><input type="hidden" name="csrf" value="'+csrf+'"><input type="hidden" name="subscription_id" value="'+identifier+'"><button>暂停订阅</button></form>'
                body+='</article>'
            return page('订阅管理',body)
        except QueryError:
            return PlainTextResponse('请登录具有订阅权限的账号。',status_code=403,headers={'Cache-Control':'no-store'})

    @server.custom_route('/execution/{job_id}',methods=['GET','POST'])
    async def execution_job(request):
        import asyncio
        try:
            actor,csrf=await provider.web_session(request.cookies.get(cookie,''))
            if execution is None:
                raise QueryError('EXECUTION_SERVICE_NOT_CONFIGURED')
            identifier=request.path_params['job_id']
            if request.method=='POST':
                data=await form(request)
                if not hmac.compare_digest(csrf,data.get('csrf','')):
                    raise QueryError('INVALID_CSRF')
                ordinal=int(data.get('ordinal',''))
                current=await asyncio.to_thread(execution.get,actor,identifier)
                item=next((item for item in current['items'] if item['ordinal']==ordinal),None)
                if item is None or not hmac.compare_digest(str(item['intent_id']),data.get('intent_id','')):
                    raise QueryError('STALE_EXECUTION_INTENT')
                await asyncio.to_thread(execution.bind_created_entity_from_web,actor,identifier,
                    ordinal,data.get('entity_id',''),data.get('evidence_reference',''))
                return RedirectResponse('/execution/'+identifier,status_code=303)
            result=await asyncio.to_thread(execution.get,actor,identifier)
            body='<pre>'+escape(json.dumps(result,ensure_ascii=False,indent=2))+'</pre>'
            if actor.subject=='kenshi' and actor.role=='owner':
                body+='<p>仅在已通过上游证据核对创建结果后填写新对象 ID。此操作不重新发送创建请求。</p>'
                for item in result['items']:
                    if item['state']!='unknown':
                        continue
                    body+='<form method="post"><input type="hidden" name="csrf" value="'+csrf+'"><input type="hidden" name="ordinal" value="'+str(item['ordinal'])+'"><input type="hidden" name="intent_id" value="'+escape(item['intent_id'])+'"><p>项目 '+str(item['ordinal'])+' · 执行意图 '+escape(item['intent_id'])+'</p><label>已核实的新对象 ID <input name="entity_id" required></label><label>核对证据说明 <input name="evidence_reference" required maxlength="1000"></label><button>登记此项目核对结果</button></form>'
            return page('执行结果核对',body)
        except (QueryError,ValueError):
            return PlainTextResponse('执行记录或核对权限校验失败。',status_code=403,headers={'Cache-Control':'no-store'})

    @server.custom_route('/logout',methods=['POST'])
    async def logout(request):
        try:
            _,csrf=await provider.web_session(request.cookies.get(cookie,''))
            data=await form(request)
            if not hmac.compare_digest(csrf,data.get('csrf','')):
                raise QueryError('INVALID_CSRF')
            with store.db() as db:
                db.execute('UPDATE tokens SET used=1 WHERE hash=? AND kind=\'web\'',(token_hash(request.cookies[cookie]),))
            response=RedirectResponse('/login',status_code=303)
            response.delete_cookie(cookie,path='/')
            return response
        except QueryError:
            return PlainTextResponse('拒绝请求',status_code=403)


class BoundaryMiddleware:
    """Bound request bodies before SDK/form parsers, enforce Origin and no-store."""
    def __init__(self,app,origin):
        self.app,self.origin=app,origin

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        headers=dict(scope['headers'])
        if b'origin' in headers and headers[b'origin']!=self.origin.encode():
            return await PlainTextResponse('Invalid origin',status_code=403)(scope,receive,send)
        parts=[];total=0
        deadline=asyncio.get_running_loop().time()+10
        while True:
            try:
                async with asyncio.timeout_at(deadline):
                    message=await receive()
            except TimeoutError:
                return await PlainTextResponse('Request timeout',status_code=408)(scope,receive,send)
            if message['type']=='http.disconnect':
                return
            total+=len(message.get('body',b''))
            if total>65536:
                return await PlainTextResponse('Request too large',status_code=413)(scope,receive,send)
            parts.append(message)
            if not message.get('more_body',False):
                break
        if scope.get('path')=='/token' and scope.get('method')=='POST':
            from starlette.responses import JSONResponse
            try:
                values=parse_qs(b''.join(p.get('body',b'') for p in parts).decode(),strict_parsing=True)
                valid=values.get('resource')==[self.origin+'/mcp'] and all(len(v)==1 for v in values.values())
            except (ValueError,UnicodeError):
                valid=False
            if not valid:
                return await JSONResponse({'error':'invalid_target'},status_code=400,headers={'Cache-Control':'no-store'})(scope,receive,send)
        async def replay():
            return parts.pop(0) if parts else await receive()
        async def guarded_send(message):
            if message['type']=='http.response.start':
                message['headers']=list(message.get('headers',[]))+[(b'cache-control',b'no-store')]
            await send(message)
        await self.app(scope,replay,guarded_send)
