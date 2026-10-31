#!/usr/bin/env python3
"""Upload existing local credentials and backup key using stdin, never CLI arguments."""
import argparse,os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--repo',default='hveraus/Baidu.Ads');args=parser.parse_args()
 sources={'BAIDU_CREDENTIALS_JSON':ROOT/'.credentials.json','DATA_BACKUP_KEY':ROOT/'.backup-key'}
 for name,path in sources.items():
  if not path.is_file():raise SystemExit('Missing local file: '+path.name)
  result=subprocess.run(['gh','secret','set',name,'--repo',args.repo],input=path.read_bytes(),capture_output=True)
  if result.returncode:raise SystemExit('GitHub secret update failed for '+name+'; check gh authentication and repository access')
  print('Configured repository secret:',name)
if __name__=='__main__':main()
