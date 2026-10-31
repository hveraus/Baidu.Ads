#!/usr/bin/env python3
"""Read-only Baidu Marketing collector. Python standard library only."""
import argparse, csv, datetime as dt, hashlib, json, os, sqlite3, sys, time
try:
 import fcntl
except ImportError:
 fcntl=None
import urllib.error, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parent
BASE='https://api.baidu.com/json/sms/service/'
ALLOWED={'AccountService/getAccountInfo','OpenApiReportService/getReportData','CampaignService/getCampaign','AdgroupService/getAdgroup','KeywordService/getWord'}
METRICS={'impression','click','cost','ctr','cpc','conversion','bridgeConversion','avgRank','bidNew','qualityEnum','mixWmatchEnum','estimatedClickRate','businessRelationship','landPageExperience','ocpcConversionsDetail1','ocpcConversionsDetail5','ocpcConversionsDetail2','ocpcConversionsDetail35'}
LABELS={'date':'日期','userId':'账户ID','userName':'账户名','campaignId':'计划ID','campaignName':'计划名称','campaignNameStatus':'计划名称含删除标记','adGroupId':'单元ID','adgroupId':'单元ID','adGroupName':'单元名称','adgroupName':'单元名称','adGroupNameStatus':'单元名称含删除标记','wInfoId':'关键词ID','keywordId':'关键词ID','wInfoNameStatus':'关键词含删除标记','keyword':'关键词','queryWord':'实际搜索词','queryStatusName':'搜索词添加状态','wMatchId':'触发模式','mixWmatchEnum':'匹配模式','bidNew':'报告出价','price':'当前出价','qualityEnum':'质量度','quality':'当前质量度','estimatedClickRate':'预估点击率评级','businessRelationship':'创意相关性评级','landPageExperience':'落地页体验评级','avgRank':'上方位平均排名','impression':'展现','click':'点击','cost':'消费元','ctr':'点击率','cpc':'平均点击价格元','conversion':'转化conversion','bridgeConversion':'商桥转化bridgeConversion','device':'设备','provinceName':'省份','provinceCityName':'城市','ideaId':'创意ID','ideaInfo':'创意详情','landingPageUrl':'落地页地址','product':'渠道','pause':'暂停','status':'状态代码','offlineReasons':'下线原因','createTime':'创建时间','matchType':'匹配类型','phraseType':'细分匹配类型','pcDestinationUrl':'计算机访问网址','mobileDestinationUrl':'移动访问网址','report_records':'报告记录数','coverage':'数据说明','snapshot_date':'清单快照日期','hour':'小时','ctOCPCConversionsDetail3':'表单提交成功量按转化时间','ctOCPCConversionsDetail18':'留线索量按转化时间','ctOCPCConversionsDetail30':'电话拨通量按转化时间','topPageViews':'上方位展现','topPClicks':'上方位点击','topPvWinA':'上方展现胜出率','topPvWinP':'上方展现胜出率精确','formatIP':'IP地址','sumClicks':'过滤前点击量','sumCost':'过滤前点击金额原始值','freeClicks':'无效点击量','freeCost':'无效点击金额原始值','validClicks':'计费点击量','validCost':'计费点击金额原始值','wordName':'包含关键词','getTemp':'影子物料','ocpcConversionsDetail1':'咨询按钮点击量','ocpcConversionsDetail5':'表单按钮点击量','ocpcConversionsDetail2':'电话按钮点击量','ocpcConversionsDetail35':'微信复制按钮点击量'}
class ApiError(RuntimeError): pass

def dump(path,obj):
 path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_suffix(path.suffix+'.tmp')
 tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)); tmp.replace(path)

def call(endpoint,body,credentials):
 if endpoint not in ALLOWED: raise ValueError('Endpoint is not in the read-only allowlist')
 payload={'header':{'userName':credentials['user_name'],'accessToken':credentials['access_token']},'body':body}
 for attempt in range(4):
  try:
   req=urllib.request.Request(BASE+endpoint,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json;charset=utf-8'})
   with urllib.request.urlopen(req,timeout=90) as r: result=json.load(r)
   h=result.get('header',{})
   if h.get('status')!=0 or h.get('failures'):
    raise ApiError(json.dumps(h,ensure_ascii=False))
   return result.get('body',{})
  except urllib.error.HTTPError as e:
   if e.code not in (429,500,502,503,504) or attempt==3: raise ApiError('HTTP '+str(e.code)) from None
  except (urllib.error.URLError,TimeoutError):
   if attempt==3: raise ApiError('网络连接失败，请检查网络后重试') from None
  time.sleep(2**attempt)
 raise ApiError('Request failed')

def fetch_report(spec,start,end,credentials,size):
 rows=[]; offset=0; seen=set()
 while True:
  body={'reportType':spec['report_type'],'startDate':start,'endDate':end,'timeUnit':spec['time_unit'],'columns':spec['columns'],'startRow':offset,'rowCount':size,'needSum':False,'sorts':[],'filters':[]}
  data=call('OpenApiReportService/getReportData',body,credentials).get('data',[])
  if not isinstance(data,list) or len(data)!=1 or not isinstance(data[0],dict) or 'rows' not in data[0]:
   raise ApiError('报告响应结构异常，保留已有数据')
  report=data[0]
  page=report['rows']; total=report.get('totalRowCount')
  if not isinstance(page,list): raise ApiError('报告rows格式异常')
  if not page:
   if total is not None and offset<int(total): raise ApiError('分页未完成但接口返回空页')
   break
  signature=hashlib.sha256(json.dumps(page,sort_keys=True).encode()).hexdigest()
  if signature in seen: raise ApiError('检测到重复分页，保留已有数据')
  seen.add(signature); rows.extend(page); offset+=len(page)
  if total is not None and offset>=int(total): break
  if total is None and len(page)<size: break
  if offset>2000000: raise ApiError('数据量超出同步保护限制，需分段获取')
  time.sleep(.15)
 return rows

def fetch_inventory(c):
 fields=['campaignId','campaignName','budget','pause','status','negativeWords','exactNegativeWords','createTime','campaignTransTypes','campaignDeepTransTypes','regionTarget','schedule','equipmentType','campaignBidType','campaignBid','campaignOcpcBidType','campaignOcpcBid','campaignCvSources']
 campaigns=call('CampaignService/getCampaign',{'campaignFields':fields,'campaignIds':[]},c).get('data',[])
 ids=[x['campaignId'] for x in campaigns]
 # Some versions return IDs only on an empty-ID query.
 if ids and any('campaignName' not in x for x in campaigns):
  campaigns=[]
  for i in range(0,len(ids),100): campaigns+=call('CampaignService/getCampaign',{'campaignFields':fields,'campaignIds':ids[i:i+100]},c).get('data',[])
 groups=[]
 for shadow in (0,1):
  for i in range(0,len(ids),100):
   batch=call('AdgroupService/getAdgroup',{'ids':ids[i:i+100],'idType':3,'getTemp':shadow,'adgroupFields':['adgroupId','campaignId','adgroupName','pause','status','maxPrice','negativeWords','exactNegativeWords','offlineReasons','pcFinalUrl','mobileFinalUrl','pcTrackParam','mobileTrackParam','adgroupAutoTargetingStatus']},c).get('data',[])
   groups += [dict(x,getTemp=shadow) for x in batch]
 gids=sorted({x['adgroupId'] for x in groups}); words=[]
 wordfields=['keywordId','campaignId','adgroupId','keyword','price','pause','matchType','phraseType','status','offlineReasons','pcDestinationUrl','mobileDestinationUrl','createTime','quality','estimatedClickRate','businessRelationship','landPageExperience']
 for shadow in (0,1):
  for i in range(0,len(gids),50):
   offset=0
   while True:
    batch=call('KeywordService/getWord',{'ids':gids[i:i+50],'idType':5,'getTemp':shadow,'wordFields':wordfields,'limit':[offset,1000]},c).get('data',[])
    words += [dict(x,getTemp=shadow) for x in batch]
    if len(batch)<1000: break
    offset+=len(batch)
    if offset>1000000: raise ApiError('关键词清单分页超限')
 return {'campaign_inventory':campaigns,'adgroup_inventory':groups,'keyword_inventory':words}

def flatten(row,prefix=''):
 out={}
 for k,v in row.items():
  key=prefix+k
  if isinstance(v,dict): out.update(flatten(v,key+'.'))
  elif isinstance(v,list): out[key]=json.dumps(v,ensure_ascii=False)
  else: out[key]=v
 return out

def write_csv(path,rows,columns=None):
 rows=[flatten(x) for x in rows]
 keys=list(dict.fromkeys((columns or [])+[k for r in rows for k in r]))
 path.parent.mkdir(parents=True,exist_ok=True); temp=path.with_suffix('.tmp')
 with temp.open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.writer(f); w.writerow([LABELS.get(k,k) for k in keys])
  for row in rows:
   vals=[]
   for k in keys:
    v=row.get(k,'')
    if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')): v="'"+v
    vals.append(v)
   w.writerow(vals)
 temp.replace(path)

def put_report(db,name,rows,start,end):
 # Atomic replacement of a fully fetched date interval also removes stale rows.
 with db:
  db.execute('DELETE FROM report_rows WHERE report=? AND substr(date,1,10) BETWEEN ? AND ?',(name,start,end))
  for row in rows:
   dims={k:v for k,v in row.items() if k not in METRICS and k not in {'topPageViews','topPClicks','topPvWinA','topPvWinP','sumClicks','sumCost','freeClicks','freeCost','validClicks','validCost'} and not k.startswith(('ocpcConversions','ctOCPCConversions'))}
   key=hashlib.sha256(json.dumps(dims,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
   db.execute('INSERT OR REPLACE INTO report_rows VALUES(?,?,?,?,?)',(name,key,str(row.get('date','')),json.dumps(row,ensure_ascii=False),dt.datetime.now(dt.timezone.utc).isoformat()))

def export_reports(db,config):
 for name,spec in config['reports'].items():
  rows=[json.loads(x[0]) for x in db.execute('SELECT payload FROM report_rows WHERE report=? ORDER BY date,row_key',(name,))]
  write_csv(ROOT/'csv'/f'{name}.csv',rows,spec['columns'])
  view_keys=list(dict.fromkeys(spec['columns']+[k for r in rows for k in r]))
  expressions=["json_extract(payload, '$."+k+"') AS \""+k+'\"' for k in view_keys]
  db.execute(f'DROP VIEW IF EXISTS "{name}"')
  db.execute(f'CREATE VIEW "{name}" AS SELECT '+','.join(expressions)+" FROM report_rows WHERE report='"+name+"'")
 db.commit()

def summary(db,inventory,start,end,output_dir=None):
 rows=[json.loads(x[0]) for x in db.execute("SELECT payload FROM report_rows WHERE report='keyword' AND substr(date,1,10) BETWEEN ? AND ?",(start,end))]
 groups={}
 for row in rows:
  key=str(row.get('wInfoId'))
  g=groups.setdefault(key,{'keywordId':row.get('wInfoId'),'keyword':row.get('wInfoNameStatus'),'campaignId':row.get('campaignId'),'campaignName':row.get('campaignName'),'adgroupId':row.get('adGroupId'),'adgroupName':row.get('adGroupName'),'impression':0,'click':0,'cost':0,'conversion':0,'bridgeConversion':0,'report_records':0,'coverage':'有表现报告'})
  for k in ('impression','click','cost','conversion','bridgeConversion'): g[k]+=float(row.get(k) or 0)
  g['report_records']+=1
 names={x['campaignId']:x.get('campaignName','') for x in inventory.get('campaign_inventory',[])}
 units={x['adgroupId']:x.get('adgroupName','') for x in inventory.get('adgroup_inventory',[]) if x.get('getTemp')==0}
 for word in inventory.get('keyword_inventory',[]):
  if word.get('getTemp')!=0: continue
  key=str(word['keywordId'])
  g=groups.setdefault(key,{'keywordId':word['keywordId'],'keyword':word.get('keyword'),'campaignId':word.get('campaignId'),'adgroupId':word.get('adgroupId'),'report_records':0,'coverage':'当前清单存在，区间内无返回报告；未填成零'})
  for k in ('price','pause','status','matchType','phraseType','quality','offlineReasons'): g[k]=word.get(k)
  g.setdefault('campaignName',names.get(g.get('campaignId'),'')); g.setdefault('adgroupName',units.get(g.get('adgroupId'),''))
 for g in groups.values():
  if g.get('report_records'):
   g['ctr']=g['click']/g['impression'] if g['impression'] else None
   g['cpc']=g['cost']/g['click'] if g['click'] else None
   g['cost']=round(g['cost'],2)
 ordered=sorted(groups.values(),key=lambda x:x.get('cost',-1),reverse=True)
 write_csv((output_dir or ROOT/'csv')/'keyword_summary.csv',ordered)
 return {'date_start':start,'date_end':end,'keywords_in_summary':len(ordered),'keywords_with_report':sum(x.get('report_records',0)>0 for x in ordered),'impression':sum(x.get('impression',0) for x in ordered),'click':sum(x.get('click',0) for x in ordered),'cost':round(sum(x.get('cost',0) for x in ordered),2)}

def main():
 os.umask(0o077)
 parser=argparse.ArgumentParser(); parser.add_argument('--start'); parser.add_argument('--end'); parser.add_argument('--backfill',action='store_true'); parser.add_argument('--export-only',action='store_true'); parser.add_argument('--reports',help='Comma-separated report names; default all'); args=parser.parse_args()
 config=json.loads((ROOT/'config.json').read_text())
 credential_text=os.environ.get('BAIDU_CREDENTIALS_JSON')
 credential_path=ROOT/'.credentials.json'
 c=json.loads(credential_text) if credential_text else (json.loads(credential_path.read_text()) if credential_path.exists() else {})
 if not args.export_only and not c: parser.error('Provide BAIDU_CREDENTIALS_JSON or run scripts/configure.py locally')
 selected=set(args.reports.split(',')) if args.reports else set(config['reports'])
 if not selected.issubset(config['reports']): parser.error('Unknown report name')
 today=dt.datetime.now(ZoneInfo(config['timezone'])).date()
 end=dt.date.fromisoformat(args.end) if args.end else today-dt.timedelta(days=1)
 start=dt.date.fromisoformat(args.start) if args.start else end-dt.timedelta(days=(config['backfill_days'] if args.backfill else config['refresh_days'])-1)
 if start>end: parser.error('start must be <= end')
 expiry_text=c.get('token_expires')
 if not expiry_text and (ROOT/'status.json').exists(): expiry_text=json.loads((ROOT/'status.json').read_text()).get('token_expires')
 expiry=dt.date.fromisoformat(expiry_text or str(today)); remaining=(expiry-today).days
 status={'run_at':dt.datetime.now(ZoneInfo(config['timezone'])).isoformat(),'start':str(start),'end':str(end),'token_expires':str(expiry),'token_days_remaining':remaining,'reports':{},'errors':[]}
 if args.reports and (ROOT/'status.json').exists(): status['reports']=json.loads((ROOT/'status.json').read_text()).get('reports',{})
 if args.export_only and (ROOT/'status.json').exists(): status=json.loads((ROOT/'status.json').read_text())
 if remaining<=14: status['warning']='自授权令牌将在14天内到期；请在百度应用详情续期并更新本机凭据'
 lock=(ROOT/'.sync.lock').open('a')
 try:
  if fcntl: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  else:
   import msvcrt
   lock.seek(0); lock.write('0'); lock.flush(); lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
 except (BlockingIOError,OSError): print('另一个采集任务正在运行'); return 0
 db=sqlite3.connect(ROOT/'baidu.sqlite3')
 db.execute('CREATE TABLE IF NOT EXISTS report_rows(report TEXT,row_key TEXT,date TEXT,payload TEXT,fetched_at TEXT,PRIMARY KEY(report,row_key))')
 db.execute('CREATE INDEX IF NOT EXISTS report_date ON report_rows(report,date)')
 db.execute('CREATE TABLE IF NOT EXISTS report_coverage(report TEXT,start_date TEXT,end_date TEXT,PRIMARY KEY(report,start_date,end_date))')
 db.execute('CREATE TABLE IF NOT EXISTS inventory_snapshots(kind TEXT,snapshot_date TEXT,payload TEXT,PRIMARY KEY(kind,snapshot_date))')
 inventory={}
 if args.export_only:
  for kind,payload in db.execute('SELECT kind,payload FROM inventory_snapshots WHERE snapshot_date=(SELECT MAX(snapshot_date) FROM inventory_snapshots)'): inventory[kind]=json.loads(payload)
 else:
  if remaining<=0: status['errors'].append('自授权令牌已到期'); dump(ROOT/'status.json',status); return 1
  try:
   inventory=fetch_inventory(c)
   with db:
    for kind,rows in inventory.items():
     db.execute('INSERT OR REPLACE INTO inventory_snapshots VALUES(?,?,?)',(kind,str(today),json.dumps(rows,ensure_ascii=False)))
     write_csv(ROOT/'csv'/f'{kind}.csv',[dict(x,snapshot_date=str(today)) for x in rows])
   print('Inventory:',{k:len(v) for k,v in inventory.items()},flush=True)
  except Exception as e: status['errors'].append('inventory: '+str(e)); print('Inventory failed:',e,flush=True)
  for name,spec in config['reports'].items():
   if name not in selected: continue
   try:
    # Query in <= 30 day chunks; search terms have a maximum 91-day interval.
    previous=db.execute('SELECT MAX(end_date) FROM report_coverage WHERE report=?',(name,)).fetchone()[0]
    effective_start=start
    if previous and not args.start and not args.backfill:
     effective_start=min(start,dt.date.fromisoformat(previous)-dt.timedelta(days=config['refresh_days']-1))
    cursor=effective_start; count=0
    while cursor<=end:
     stop=min(end,cursor+dt.timedelta(days=29))
     rows=fetch_report(spec,str(cursor),str(stop),c,config['page_size'])
     put_report(db,name,rows,str(cursor),str(stop)); count+=len(rows)
     cursor=stop+dt.timedelta(days=1)
    with db: db.execute('INSERT OR REPLACE INTO report_coverage VALUES(?,?,?)',(name,str(effective_start),str(end)))
    status['reports'][name]={'ok':True,'fetched_rows':count,'updated_at':status['run_at'],'start':str(effective_start),'end':str(end)}
    print(name,count,flush=True)
   except Exception as e:
    status['reports'][name]={'ok':False,'error':str(e)}; status['errors'].append(name+': '+str(e)); print(name,'FAILED',e,flush=True)
 export_reports(db,config)
 bounds=db.execute("SELECT MIN(start_date),MAX(end_date) FROM report_coverage WHERE report='keyword'").fetchone()
 if bounds[0]: status['keyword_summary']=summary(db,inventory,*bounds)
 status['inventory']={k:len(v) for k,v in inventory.items()}
 status['inventory_current']={k:sum(x.get('getTemp',0)==0 for x in v) for k,v in inventory.items()}
 status['ok']=not status['errors']; dump(ROOT/'status.json',status)
 if not args.export_only:
  with (ROOT/'runs.jsonl').open('a') as f: f.write(json.dumps(status,ensure_ascii=False)+'\n')
 for kind,rows in inventory.items():
  columns=list(dict.fromkeys(k for row in rows for k in row))
  view='current_'+kind
  db.execute(f'DROP VIEW IF EXISTS "{view}"')
  expressions=["json_extract(item.value, '$."+k+"') AS \""+k+'\"' for k in columns]
  if expressions:
   db.execute(f'CREATE VIEW "{view}" AS SELECT '+','.join(expressions)+" FROM inventory_snapshots s,json_each(s.payload) item WHERE s.kind='"+kind+"' AND s.snapshot_date=(SELECT MAX(snapshot_date) FROM inventory_snapshots WHERE kind=s.kind)")
 db.commit()
 db.close()
 from dashboard import build_dashboard
 try:
  build_dashboard()
 except Exception as e:
  status['ok']=False; status['errors'].append('dashboard: '+str(e)); dump(ROOT/'status.json',status)
 print('Completed:',status['ok'],flush=True)
 return 0 if status['ok'] else 1
if __name__=='__main__':
 try: sys.exit(main())
 except Exception as e: print(type(e).__name__+': '+str(e),file=sys.stderr); sys.exit(1)
