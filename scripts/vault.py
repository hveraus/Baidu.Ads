#!/usr/bin/env python3
"""Authenticated private-data backup. No plaintext is written to public directories."""
import argparse,hashlib,io,json,os,sqlite3,tarfile
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
ROOT=Path(__file__).resolve().parents[1]
MAGIC=b'BAIDU-ADS-VAULT-v1\n'
RAW_FILES=['.credentials.json','baidu.sqlite3','status.json','runs.jsonl','website-audit.json']
RAW_DIRS=['csv','work']

def key_bytes(key_file=None):
 value=os.environ.get('DATA_BACKUP_KEY','').strip()
 if not value:value=Path(key_file or ROOT/'.backup-key').read_text().strip()
 key=bytes.fromhex(value)
 if len(key)!=32:raise ValueError('Backup key must contain 64 hexadecimal characters')
 return key

def pack(root):
 files={}
 for name in RAW_FILES:
  p=root/name
  if p.exists():files[name]=p.read_bytes()
 if 'BAIDU_CREDENTIALS_JSON' in os.environ:
  files['.credentials.json']=json.dumps(json.loads(os.environ['BAIDU_CREDENTIALS_JSON']),ensure_ascii=False).encode()
 for folder in RAW_DIRS:
  if (root/folder).exists():
   for p in (root/folder).rglob('*'):
    if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts:files[str(p.relative_to(root))]=p.read_bytes()
 if (root/'baidu.sqlite3').exists():
  # Capture a coherent database even if another reader is connected.
  source=sqlite3.connect(root/'baidu.sqlite3');target=sqlite3.connect(':memory:');source.backup(target)
  files['baidu.sqlite3']=target.serialize();target.close();source.close()
 manifest={k:hashlib.sha256(v).hexdigest() for k,v in files.items()}
 files['vault-manifest.json']=json.dumps(manifest,sort_keys=True).encode()
 stream=io.BytesIO()
 with tarfile.open(fileobj=stream,mode='w:gz') as archive:
  for name,content in sorted(files.items()):
   info=tarfile.TarInfo(name);info.size=len(content);info.mode=0o600;info.mtime=0
   archive.addfile(info,io.BytesIO(content))
 return stream.getvalue(),manifest

def encrypt(root,output,key):
 plain,manifest=pack(root);nonce=os.urandom(12)
 payload=MAGIC+nonce+AESGCM(key).encrypt(nonce,plain,MAGIC)
 output.parent.mkdir(parents=True,exist_ok=True);tmp=output.with_suffix('.tmp');tmp.write_bytes(payload);tmp.replace(output)
 return manifest

def unpack(payload,key):
 if not payload.startswith(MAGIC):raise ValueError('Unknown backup format')
 pos=len(MAGIC);plain=AESGCM(key).decrypt(payload[pos:pos+12],payload[pos+12:],MAGIC)
 files={}
 with tarfile.open(fileobj=io.BytesIO(plain),mode='r:gz') as archive:
  for entry in archive:
   rel=Path(entry.name)
   if not entry.isfile() or rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe archive entry')
   if entry.size>200_000_000:raise ValueError('Archive entry exceeds the restore limit')
   files[entry.name]=archive.extractfile(entry).read()
 manifest=json.loads(files.pop('vault-manifest.json'))
 if set(files)!=set(manifest):raise ValueError('Manifest entry mismatch')
 for name,content in files.items():
  if hashlib.sha256(content).hexdigest()!=manifest[name]:raise ValueError('Backup checksum mismatch')
 return files

def restore(archive,root,key,force=False):
 files=unpack(archive.read_bytes(),key)
 # Validate the complete destination before changing any files.
 for name,content in files.items():
  p=root/name
  if p.exists() and p.read_bytes()!=content and not force:raise FileExistsError(f'{name} already differs; back it up or explicitly use --force')
 root.mkdir(parents=True,exist_ok=True)
 for name,content in files.items():
  p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content);p.chmod(0o600)
 return len(files)

def main():
 os.umask(0o077)
 parser=argparse.ArgumentParser();parser.add_argument('command',choices=['backup','restore','verify']);parser.add_argument('--key-file');parser.add_argument('--root',type=Path,default=ROOT);parser.add_argument('--archive',type=Path,default=ROOT/'backups/private-data.enc');parser.add_argument('--force',action='store_true');args=parser.parse_args();key=key_bytes(args.key_file)
 if args.command=='backup':print('Encrypted backup:',len(encrypt(args.root,args.archive,key)),'files')
 elif args.command=='restore':print('Restored:',restore(args.archive,args.root,key,args.force),'files')
 else:print('Backup verified:',len(unpack(args.archive.read_bytes(),key)),'files')
if __name__=='__main__':main()
