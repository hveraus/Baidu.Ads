"""Standalone optional core-side read/identity adapter; core never imports it.

Bind loopback only. Production requires separately reviewed private ingress and OS
resource limits. Install in its own environment; no MCP SDK or core imports.
"""
import base64
from collections import deque
from contextlib import closing
from datetime import date
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import threading
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class Rejected(ValueError):
    pass


class CoreReader:
    def __init__(self, storage, users_db, owner_hash, version_key, private_keys=None):
        self.storage, self.users_db = Path(storage).resolve(), Path(users_db).resolve()
        self.owner_hash, self.version_key = owner_hash, version_key
        self.private_keys = private_keys or {}
        if len(version_key)<32 or not re.fullmatch('[0-9a-f]{32}:[0-9a-f]{64}',owner_hash):
            raise Rejected('INVALID_CONFIGURATION')
        self.slots = threading.BoundedSemaphore(2)
        self.lock, self.attempts = threading.Lock(), deque()

    def identity(self, subject):
        if not isinstance(subject,str) or not re.fullmatch('[A-Za-z0-9_.@-]{1,64}',subject):
            raise Rejected('UNAUTHORIZED')
        if subject.casefold()=='kenshi':
            subject='kenshi'
            saved = self.owner_hash
        else:
            with closing(sqlite3.connect(self.users_db.as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
                row = db.execute('SELECT username,password_hash FROM users WHERE username=?',(subject,)).fetchone()
            if row is None:
                raise Rejected('UNAUTHORIZED')
            subject,saved = row
        if not re.fullmatch('[0-9a-f]{32}:[0-9a-f]{64}',saved):
            raise Rejected('UNAUTHORIZED')
        version = hmac.new(self.version_key,(subject+':'+saved).encode(),hashlib.sha256).hexdigest()
        return {'subject':subject,'version':version},saved

    def authenticate(self, subject, password):
        with self.lock:
            now=time.monotonic()
            while self.attempts and self.attempts[0]<now-60:
                self.attempts.popleft()
            if len(self.attempts)>=5:
                raise Rejected('RATE_LIMITED')
            self.attempts.append(now)
        if not isinstance(password,str) or not 1<=len(password.encode())<=1024:
            raise Rejected('UNAUTHORIZED')
        if not self.slots.acquire(blocking=False):
            raise Rejected('RATE_LIMITED')
        try:
            try:
                identity,saved = self.identity(subject)
                exists=True
            except Rejected:
                identity,saved,exists = {},self.owner_hash,False
            salt,expected = saved.split(':')
            actual = hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=2**17,r=8,p=1,dklen=32,maxmem=256*1024*1024)
            if not hmac.compare_digest(actual,bytes.fromhex(expected)) or not exists:
                raise Rejected('UNAUTHORIZED')
            current,_=self.identity(identity['subject'])
            if current!=identity:
                raise Rejected('IDENTITY_CHANGED')
            return identity
        finally:
            self.slots.release()

    def current(self):
        with (self.storage/'current').open('rb') as source:
            revision=source.read(128).decode('ascii').strip()
        if not isinstance(revision,str) or not re.fullmatch('[0-9a-f]{32}',revision):
            raise Rejected('INVALID_RELEASE')
        return revision

    def file(self, revision, relative, limit):
        if not isinstance(revision,str) or not re.fullmatch('[0-9a-f]{32}',revision):
            raise Rejected('INVALID_RELEASE')
        root=(self.storage/'releases'/revision).resolve()
        if not root.is_relative_to(self.storage/'releases'):
            raise Rejected('INVALID_RELEASE')
        path=(root/relative).resolve(strict=True)
        if not path.is_relative_to(root):
            raise Rejected('INVALID_RELEASE')
        with path.open('rb') as source:
            raw=source.read(limit+1)
        if len(raw)>limit:
            raise Rejected('SNAPSHOT_TOO_LARGE')
        return raw

    def call(self, action, payload):
        if action=='authenticate':
            return self.authenticate(payload.get('subject'),payload.get('password'))
        identity,_=self.identity(payload.get('subject'))
        if identity['version']!=payload.get('version'):
            raise Rejected('IDENTITY_CHANGED')
        if action=='identity':
            return identity
        if action=='current':
            return {'release_id':self.current()}
        revision=payload.get('release_id','')
        if action=='report':
            account=payload.get('account')
            if account not in ('tinacms','yakshaver','ssw'):
                raise Rejected('UNKNOWN_ACCOUNT')
            path='report/index.html' if account=='tinacms' else f'report/{account}/index.html'
            raw=self.file(revision,path,32*1024*1024)
            return {'release_id':revision,'sha256':hashlib.sha256(raw).hexdigest(),'html':raw.decode('utf-8')}
        if action=='private':
            source=payload.get('source')
            specs={'cli':('data/posthog-cli.enc',b'BAIDU-ADS-POSTHOG-v1\n',('started','finished')),
                   'wecom':('accounts/ssw/data/wecom-consultations.enc',b'BAIDU-ADS-WECOM-v1\n',('customers','messages'))}
            if source not in specs or source not in self.private_keys:
                raise Rejected('UNSUPPORTED_SOURCE')
            path,header,fields=specs[source]
            raw=self.file(revision,path,16384)
            if not raw.startswith(header):
                raise Rejected('INVALID_SNAPSHOT')
            offset=len(header)
            value=json.loads(AESGCM(self.private_keys[source]).decrypt(raw[offset:offset+12],raw[offset+12:],header))
            if not isinstance(value,dict):
                raise Rejected('INVALID_SNAPSHOT')
            try:
                through=date.fromisoformat(value['through'])
                if through.isoformat()!=value['through']:
                    raise ValueError()
            except (TypeError,ValueError,KeyError):
                raise Rejected('INVALID_SNAPSHOT') from None
            rows=value.get('rows')
            if not isinstance(rows,list) or not 1<=len(rows)<=90:
                raise Rejected('INVALID_SNAPSHOT')
            clean=[]
            previous=None
            for row in rows:
                if not isinstance(row,dict) or not isinstance(row.get('date'),str):
                    raise Rejected('INVALID_SNAPSHOT')
                try:
                    day=date.fromisoformat(row['date'])
                    if day.isoformat()!=row['date'] or not 0<=(through-day).days<90 or (previous is not None and day<=previous):
                        raise ValueError()
                except ValueError:
                    raise Rejected('INVALID_SNAPSHOT') from None
                previous=day
                if (source=='cli' and any(field not in row for field in fields)) or not any(field in row for field in fields):
                    raise Rejected('INVALID_SNAPSHOT')
                item={'date':row['date']}
                for field in fields:
                    if field in row:
                        if type(row[field]) is not int or not 0<=row[field]<2**53:
                            raise Rejected('INVALID_SNAPSHOT')
                        item[field]=row[field]
                clean.append(item)
            return {'release_id':revision,'source':source,'through':value.get('through'),'rows':clean}
        raise Rejected('UNSUPPORTED_OPERATION')


def serve(reader,service_token,port=8798):
    if len(service_token)<43:
        raise Rejected('INVALID_CONFIGURATION')
    slots=threading.BoundedSemaphore(4)
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(5)
            def expire():
                try:self.request.shutdown(socket.SHUT_RDWR)
                except OSError:pass
            self.deadline=threading.Timer(15,expire)
            self.deadline.daemon=True
            self.deadline.start()
            super().setup()
        def finish(self):
            try:super().finish()
            finally:self.deadline.cancel()
        def log_message(self,*args):
            pass  # Request bodies, passwords, tokens, usernames and counts never logged.
        def do_POST(self):
            if not hmac.compare_digest(self.headers.get('Authorization','').encode('utf-8'),('Bearer '+service_token).encode('utf-8')):
                self.send_error(401);return
            if not slots.acquire(blocking=False):
                self.send_error(429);return
            try:
                self.connection.settimeout(5)
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8192:
                    raise Rejected('INVALID_REQUEST')
                payload=json.loads(self.rfile.read(length))
                if not isinstance(payload,dict):
                    raise Rejected('INVALID_REQUEST')
                result=reader.call(self.path.removeprefix('/'),payload)
                raw=json.dumps(result,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)))
                self.send_header('Cache-Control','no-store')
                self.end_headers();self.wfile.write(raw)
            except Exception:
                self.send_error(403,'Request rejected')
            finally:
                slots.release()
    class BoundedServer(ThreadingHTTPServer):
        daemon_threads=True
        request_queue_size=8
        workers=threading.BoundedSemaphore(4)
        def process_request(self,request,address):
            if not self.workers.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request,address)
            except BaseException:
                self.workers.release()
                raise
        def process_request_thread(self,request,address):
            try:
                super().process_request_thread(request,address)
            finally:
                self.workers.release()
    BoundedServer(('127.0.0.1',port),Handler).serve_forever()


if __name__=='__main__':
    # Independent protected configuration, supplied at process startup. Never core imports.
    keys={name:bytes.fromhex(os.environ['MCP_READER_'+name.upper()+'_KEY']) for name in ('cli','wecom') if 'MCP_READER_'+name.upper()+'_KEY' in os.environ}
    reader=CoreReader(os.environ['MCP_READER_STORAGE'],os.environ['MCP_READER_USERS_DB'],
                      os.environ['MCP_READER_OWNER_HASH'],bytes.fromhex(os.environ['MCP_READER_VERSION_KEY']),keys)
    serve(reader,os.environ['MCP_READER_SERVICE_TOKEN'])
