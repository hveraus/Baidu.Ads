"""Account-specific UI applied before source data is inserted into the page."""
import html,json,re

def account_template(template,profile,lang):
 en=lang=='en';name=profile['name'];slug=profile['slug']
 page='en.html' if en else 'index.html'
 base='../' if slug!='tinacms' else './'
 links=f'<div class="account-switch" style="display:flex;gap:16px;font-size:14px"><a href="{base}{page}">TinaCMS</a><a href="{base}yakshaver/{page}">YakShaver</a></div>'
 # Review and filter state are scoped to an account, while language preference is shared.
 template=template.replace('baidu-ads-switch-state',f'baidu-ads-switch-state-{slug}')
 if slug=='tinacms':return template.replace('<div class="header-right">',links+'<div class="header-right">',1)
 template=template.replace('TinaCMS',html.escape(name)).replace('tina-baidu-review-v1',slug+'-baidu-review-v1')
 template=template.replace('智鹏瑞尔',html.escape(profile['user_name']))
 template=template.replace('<div class="header-right">',links+'<div class="header-right">',1)
 intro=('Recommendations are draft review steps. Website analytics, qualified leads and sales are not connected; API conversions alone do not establish lead quality.' if en else '以下为人工复核与下一期测试建议。网站会话、有效线索和成交尚未接入，不能只根据API转化判断线索质量。')
 actions=[('Verify tracking end to end','Test a real registration or enquiry, then reconcile website events, Baidu conversions and the lead record.'),('Review the highest-spend queries','Compare each query with the product and landing page. Confirm business relevance before adding an exact negative keyword.'),('Separate brand and problem intent','Test brand queries and product use cases separately; use an ad and landing page that match each intent.'),('Measure qualified outcomes','Record qualified leads, demos or activated trials. Set a test budget manually after reviewing current account limits; no automatic bid or budget changes.')] if en else [('验证转化追踪全链路','人工完成一次注册或询盘，核对网站事件、百度转化与线索记录是否一致。'),('优先复核高花费搜索词','逐条对照产品与落地页，确认是否属于目标需求，再决定是否精确否定；不套用其他网站的否定词。'),('品牌词与需求词分组测试','品牌词与产品使用场景分别使用对应广告和落地页；先核对实际搜索词和现有广告，再确定词方向。'),('以有效业务结果复盘','记录有效线索、演示或有效试用。预算需根据当前账户上限人工制定，不自动调整出价或预算。')]
 body=f'<section class="view" id="advice" hidden><div class="section"><h2>{"Review and next campaign" if en else "投流复核与下一期"}</h2><p>{intro}</p><div id="account-evidence"></div></div><div class="section"><h2>{"Next steps" if en else "下一步行动"}</h2>'+''.join(f'<h3>{html.escape(t)}</h3><p>{html.escape(d)}</p>' for t,d in actions)+'</div></section>\n'
 template,n=re.subn(r'<section class="view" id="advice" hidden>.*?</section>\s*(?=<section class="view" id="health")',lambda _:body,template,flags=re.S)
 if n!=1:raise ValueError('Advice section structure changed')
 labels=['Total spend','Clicks','API conversion records','Review highest-spend keywords','Keyword','Spend','Clicks','Review','Verify search intent, tracking and actual lead quality. This list does not automatically classify traffic as invalid.'] if en else ['消费','点击','API转化记录','优先复核高花费关键词','关键词','消费','点击','复核','先核对搜索意图、追踪与实际线索质量，不直接认定流量无效。']
 script='const REVIEW_LABELS='+json.dumps(labels,ensure_ascii=False)+';\n'+'''function advice(){const a=REVIEW_LABELS,r=filtered('keyword');const top=[...state.words].filter(x=>x.cost>0).sort((x,y)=>y.cost-x.cost).slice(0,15);$('account-evidence').innerHTML='<div class="metrics">'+metric(a[0],money(sum(r,'cost')),'')+metric(a[1],fmt(sum(r,'click')),'')+metric(a[2],fmt(sum(r,'conversion')),'')+'</div><h3>'+a[3]+'</h3><p>'+a[8]+'</p><div class="table-wrap"><table><thead><tr><th>'+a[4]+'</th><th>'+a[5]+'</th><th>'+a[6]+'</th></tr></thead><tbody>'+top.map(x=>'<tr><td>'+esc(x.name)+'</td><td>'+money(x.cost)+'</td><td>'+fmt(x.click)+'</td></tr>').join('')+'</tbody></table></div>';}\n'''
 template,n=re.subn(r'const ACTIONS=.*?(?=// A language change)',lambda _:script+'\n',template,flags=re.S)
 if n!=1:raise ValueError('Advice script structure changed')
 return template
