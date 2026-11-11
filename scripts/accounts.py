#!/usr/bin/env python3
"""Run an additional account in an isolated private runtime, using shared collector code."""
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from publish_data import export
from vault import encrypt,restore,key_bytes
from verify_data import verify
PROFILES={'yakshaver':{'slug':'yakshaver','name':'YakShaver','user_name':'智鹏瑞尔-1','user_id':91119162,'app_id':'94f6d7c3dc7ed96fc1984fd505d99666','website':'http://yakshaver.com.cn/','token_expires':'2027-02-01','secret':'BAIDU_YAKSHAVER_CREDENTIALS_JSON'}}
PROFILES['ssw']={'slug':'ssw','name':'SSW','user_name':'智鹏瑞尔-2','user_id':91119163,'app_id':'301ac6829851c4cccb2d8363d17bd623','website':'https://ssw.cn','token_expires':'2027-02-01','secret':'BAIDU_SSW_CREDENTIALS_JSON'}
FILES=['tracker.py','dashboard.py','dashboard.template.html','i18n.py','i18n.en.json','publication.py','account_ui.py','config.json','website_conversions.py']
def prepare(slug):
 profile=PROFILES[slug];runtime=ROOT/'.private'/slug;runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
 for name in FILES:shutil.copy2(ROOT/name,runtime/name)
 (runtime/'profile.json').write_text(json.dumps(profile,ensure_ascii=False))
 return profile,runtime

def collect(slug,backfill=False):
 profile,runtime=prepare(slug);archive=ROOT/'accounts'/slug/'backups/private-data.enc'
 # Never let TinaCMS credentials leak into a second account's backup or API calls.
 secret=os.environ.get(profile['secret']);local=runtime/'.credentials.json'
 if not secret and local.exists():secret=local.read_text()
 if not secret:raise ValueError('Missing account secret: '+profile['secret'])
 credentials=json.loads(secret)
 if credentials['user_name']!=profile['user_name'] or credentials.get('app_id')!=profile['app_id']:raise ValueError('Account credentials do not match configured profile')
 if not (runtime/'baidu.sqlite3').exists() and archive.exists():restore(archive,runtime,key_bytes(),force=True)
 local.write_text(secret);local.chmod(0o600)
 env=dict(os.environ,BAIDU_CREDENTIALS_JSON=secret)
 subprocess.run([sys.executable,str(runtime/'tracker.py')]+(['--backfill'] if backfill else []),env=env,check=True)
 publish(slug)
 old=os.environ.get('BAIDU_CREDENTIALS_JSON')
 try:
  os.environ['BAIDU_CREDENTIALS_JSON']=secret;encrypt(runtime,archive,key_bytes())
 finally:
  if old is None:os.environ.pop('BAIDU_CREDENTIALS_JSON',None)
  else:os.environ['BAIDU_CREDENTIALS_JSON']=old
 print('Collected and encrypted:',slug)

def publish(slug):
 profile,runtime=prepare(slug);verify(runtime/'baidu.sqlite3');export(runtime)
 out=ROOT/'accounts'/slug/'data';out.parent.mkdir(parents=True,exist_ok=True)
 shutil.copytree(runtime/'data',out,dirs_exist_ok=True)
 build_one(slug)

def build_one(slug):
 profile,runtime=prepare(slug);source=ROOT/'accounts'/slug/'data'
 if not (source/'public.sqlite3').exists():raise ValueError('No public history for '+slug)
 shutil.copytree(source,runtime/'data',dirs_exist_ok=True)
 subprocess.run([sys.executable,str(runtime/'dashboard.py'),'--public'],check=True)
 shutil.copytree(runtime/'report',ROOT/'report'/slug,dirs_exist_ok=True)

def build():
 subprocess.run([sys.executable,str(ROOT/'dashboard.py'),'--public'],check=True)
 for slug in PROFILES:build_one(slug)

def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=['collect','publish','build','configure-github','restore']);p.add_argument('--account',choices=list(PROFILES),default='yakshaver');p.add_argument('--backfill',action='store_true');a=p.parse_args()
 if a.command=='collect':collect(a.account,a.backfill)
 elif a.command=='publish':publish(a.account)
 elif a.command=='build':build()
 elif a.command=='restore':
  _,runtime=prepare(a.account);restore(ROOT/'accounts'/a.account/'backups/private-data.enc',runtime,key_bytes())
 else:
  profile,runtime=prepare(a.account);credentials=(runtime/'.credentials.json').read_bytes()
  subprocess.run(['gh','secret','set',profile['secret'],'--repo','hveraus/Baidu.Ads'],input=credentials,check=True)
  print('Configured secret:',profile['secret'])
if __name__=='__main__':main()
