"""Bounded single-worker tick; no core startup or collection dependency."""
from contextlib import contextmanager
import os
from .executor import ExecutionQueue


@contextmanager
def worker_lock(path):
    """OS lock prevents a paused worker from being taken over solely on lease time.

    All production execution/recovery goes through tick(), never queue.run(). The
    OS releases the lock on process death; only then can recovery inspect intents.
    """
    path=path.with_suffix(path.suffix+'.worker-lock')
    with path.open('a+b') as stream:
        # Windows denies reads of a byte locked by another handle. Inspect
        # file size without touching that byte, then attempt the nonblocking lock.
        if os.fstat(stream.fileno()).st_size == 0:stream.write(b'0');stream.flush()
        stream.seek(0)
        locked=False
        try:
            if os.name=='nt':
                import msvcrt
                try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1);locked=True
                except OSError:pass
            else:
                import fcntl
                try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);locked=True
                except BlockingIOError:pass
            yield locked
        finally:
            if locked:
                stream.seek(0)
                if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def tick(queue: ExecutionQueue,limit=10):
    if type(limit) is not int or not 1<=limit<=100:
        raise ValueError('Invalid worker batch size')
    with worker_lock(queue.store.path) as acquired:
        if not acquired:return {'recovered':0,'processed':0,'busy':True}
        recovered=queue.recover_expired()
        with queue.store.db() as db:
            jobs=[row[0] for row in db.execute("SELECT id FROM execution_jobs WHERE state IN ('queued','recovering','unknown') ORDER BY created_at LIMIT ?",(limit,))]
        processed=sum(bool(queue.run(job)) for job in jobs)
        return {'recovered':recovered,'processed':processed,'busy':False}


def simulate():
    """Disposable full execution demonstration. No credentials or network."""
    import json,tempfile
    from pathlib import Path
    from .query import ReleaseQuery
    from .changes import ChangeStore,Principal
    from .capabilities import Capability,CapabilityRegistry
    from .executor import Receipt,Observation
    class SimulatedProvider:
        adapter_id='simulation-only'
        def __init__(self):self.value={'budget':'100.00','pause':False};self.calls=0
        def read(self,account,operation):return dict(self.value)
        def submit(self,account,operation,intent_id):
            self.calls+=1;self.value['budget']=operation['amount'];return Receipt('synthetic-receipt')
        def reconcile(self,*args):return Observation('applied',dict(self.value),'synthetic-readback',True)
    with tempfile.TemporaryDirectory(prefix='baidu-mcp-worker-simulation-') as directory:
        release=Path(directory)/('a'*32)
        payload={'reports':{'account':[]},'inventory':{'campaign_inventory':[{'campaignId':1,'campaignName':'Synthetic campaign','budget':'100.00','pause':False}]}}
        for relative in ('report/index.html','report/ssw/index.html','report/yakshaver/index.html'):
            path=release/relative;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('<script id="report-data" type="application/json">'+json.dumps(payload)+'</script>')
        store=ChangeStore(Path(directory)/'control.sqlite3',ReleaseQuery(release))
        owner=Principal('kenshi','synthetic-version','owner',frozenset({'changes:propose','changes:execute'}))
        plan=store.create(owner,'tinacms',[{'op':'set_campaign_budget','entity_id':'1','amount':'120.00'}],'Synthetic local simulation')
        store.request_approval(owner,plan['id'],1);store.approve_from_web(owner,plan['id'],1,plan['hash'])
        registry=CapabilityRegistry(store)
        registry.record(owner,Capability('tinacms','set_campaign_budget','campaign','simulation-only','fixture','synthetic',store.clock()+600,('budget','pause'),'200','100','100'),enabled=True)
        adapter=SimulatedProvider();queue=ExecutionQueue(store,registry,adapter,lambda _:owner,mutations_enabled=lambda:True)
        job=queue.enqueue(owner,plan['id'],1,'simulation-idempotency-key')
        tick(queue)
        return {'mode':'simulation','state':queue.get(owner,job['job_id'])['state'],'provider_calls':adapter.calls,'production_calls':0}


def main():
    import argparse,json
    from pathlib import Path
    from .auth import Bridge,OAuthProvider
    from .cache_reader import load_runtime_query
    from .changes import ChangeStore
    from .runtime import build_execution_queue
    parser=argparse.ArgumentParser(description='Independent bounded MCP execution worker; production writes default off')
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--simulate',action='store_true',help='Run disposable synthetic scenario, zero network or production credentials')
    group.add_argument('--config',type=Path,help='Trusted private HTTP configuration plus release_copy; never core config')
    parser.add_argument('--limit',type=int,default=10)
    args=parser.parse_args()
    if args.simulate:
        print(json.dumps(simulate()));return
    config=json.loads(args.config.read_text())
    store=ChangeStore(config['state_database'],load_runtime_query(config))
    bridge=Bridge(config['bridge_url'],os.environ['MCP_BRIDGE_SERVICE_TOKEN'],allow_loopback=config.get('loopback_development') is True)
    provider=OAuthProvider(store,bridge,config['base_url'],config['clients'],config['grants'])
    queue=build_execution_queue(store,provider,args.config)
    print(json.dumps(tick(queue,args.limit)))


if __name__=='__main__':main()
