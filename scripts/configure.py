#!/usr/bin/env python3
"""Interactive local setup. Never print the token or accept it as a command argument."""
import datetime as dt,getpass,json,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 os.umask(0o077)
 path=ROOT/'.credentials.json'
 if path.exists():raise SystemExit('Credentials already exist. Back them up before changing them.')
 username=input('Baidu account name: ').strip();uid=int(input('Baidu account ID: '));app=input('Application ID: ').strip()
 expiry=input('Expiry shown in Baidu (YYYY-MM-DD): ').strip();dt.date.fromisoformat(expiry)
 token=getpass.getpass('Self-authorisation token (hidden): ').strip()
 if not username or not app or len(token)<30:raise SystemExit('Incomplete credentials')
 path.write_text(json.dumps(dict(user_name=username,user_id=uid,app_id=app,token_expires=expiry,access_token=token),ensure_ascii=False,indent=2));path.chmod(0o600)
 print('Saved local credentials with owner-only permissions. Do not commit this file.')
if __name__=='__main__':main()
