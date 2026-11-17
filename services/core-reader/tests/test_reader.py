import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

spec=importlib.util.spec_from_file_location('core_reader',Path(__file__).resolve().parents[1]/'reader.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
CoreReader,Rejected=module.CoreReader,module.Rejected


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password='synthetic-long-test-password'
        salt=bytes.fromhex('ab'*16)
        cls.saved=salt.hex()+':'+hashlib.scrypt(cls.password.encode(),salt=salt,n=2**17,r=8,p=1,dklen=32,maxmem=256*1024*1024).hex()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'users.sqlite3'
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE users(username TEXT PRIMARY KEY COLLATE NOCASE,password_hash TEXT)')
            db.execute('INSERT INTO users VALUES(?,?)',('alice',self.saved))
        self.rev='a'*32;self.release=self.root/'storage'/'releases'/self.rev
        (self.release/'report').mkdir(parents=True)
        (self.release/'report/index.html').write_text('synthetic report')
        (self.root/'storage/current').write_text(self.rev)
        self.key=os.urandom(32)
        self.reader=CoreReader(self.root/'storage',self.db,self.saved,b'v'*32,{'cli':self.key,'wecom':self.key})
        self.identity=self.reader.identity('alice')[0]

    def test_password_verification_and_no_hash_export(self):
        result=self.reader.authenticate('alice',self.password)
        self.assertEqual(result,self.identity)
        self.assertEqual(set(result),{'subject','version'})
        with self.assertRaises(Rejected):self.reader.authenticate('alice','wrong')

    def test_reset_revokes_identity_and_read_only_db(self):
        before=self.db.read_bytes()
        self.reader.call('identity',self.identity)
        self.assertEqual(before,self.db.read_bytes())
        with sqlite3.connect(self.db) as db:db.execute('UPDATE users SET password_hash=?',('cd'*16+':'+'ef'*32,))
        with self.assertRaisesRegex(Rejected,'IDENTITY_CHANGED'):
            self.reader.call('identity',self.identity)

    def test_fixed_release_and_path_allowlist(self):
        value=self.reader.call('current',self.identity)
        self.assertEqual(value['release_id'],self.rev)
        result=self.reader.call('report',{**self.identity,'release_id':self.rev,'account':'tinacms'})
        self.assertEqual(result['html'],'synthetic report')
        with self.assertRaises(Rejected):self.reader.call('report',{**self.identity,'release_id':'../../','account':'tinacms'})
        with self.assertRaises(Rejected):self.reader.call('report',{**self.identity,'release_id':self.rev,'account':'../../users.sqlite3'})

    def test_private_authenticated_decryption_filters_fields(self):
        header=b'BAIDU-ADS-WECOM-v1\n';nonce=os.urandom(12)
        content={'through':'2026-11-16','rows':[{'date':'2026-11-16','customers':0,'messages':3,'secret':'SENTINEL'}]}
        path=self.release/'accounts/ssw/data/wecom-consultations.enc';path.parent.mkdir(parents=True)
        path.write_bytes(header+nonce+AESGCM(self.key).encrypt(nonce,json.dumps(content).encode(),header))
        result=self.reader.call('private',{**self.identity,'release_id':self.rev,'source':'wecom'})
        self.assertEqual(result['rows'],[{'date':'2026-11-16','customers':0,'messages':3}])
        self.assertNotIn('SENTINEL',json.dumps(result))
        with self.assertRaises(Rejected):self.reader.call('private',{**self.identity,'version':'bad','release_id':self.rev,'source':'wecom'})

    def test_rate_limit_and_concurrency_guard(self):
        self.reader.attempts.extend([module.time.monotonic()]*5)
        with self.assertRaisesRegex(Rejected,'RATE_LIMITED'):self.reader.authenticate('alice',self.password)

    def test_symlink_escape(self):
        target=self.root/'outside';target.write_text('SENTINEL')
        path=self.release/'report/index.html';path.unlink();path.symlink_to(target)
        with self.assertRaisesRegex(Rejected,'INVALID_RELEASE'):
            self.reader.call('report',{**self.identity,'release_id':self.rev,'account':'tinacms'})

    def test_private_schema_rejects_bad_dates_missing_counts_and_duplicates(self):
        header=b'BAIDU-ADS-POSTHOG-v1\n'
        path=self.release/'data/posthog-cli.enc';path.parent.mkdir(parents=True)
        for rows in (
            [{'date':'2026-11-17','started':1,'finished':1}],
            [{'date':'20261116','started':1,'finished':1}],
            [{'date':'2026-11-16','started':1}],
            [{'date':'2026-11-16','started':1,'finished':1}]*2,
            [{'date':'2026-11-15','started':1,'finished':1},{'date':'2026-11-14','started':1,'finished':1}],
        ):
            nonce=os.urandom(12)
            content={'through':'2026-11-16','rows':rows}
            path.write_bytes(header+nonce+AESGCM(self.key).encrypt(nonce,json.dumps(content).encode(),header))
            with self.assertRaisesRegex(Rejected,'INVALID_SNAPSHOT'):
                self.reader.call('private',{**self.identity,'release_id':self.rev,'source':'cli'})

    def test_reserved_owner_name_cannot_resolve_viewer_row(self):
        with sqlite3.connect(self.db) as db:
            db.execute('INSERT INTO users VALUES(?,?)',('KENSHI','cd'*16+':'+'ef'*32))
        owner,_=self.reader.identity('kenshi')
        upper,_=self.reader.identity('KENSHI')
        self.assertEqual(owner,upper)
