"""MCP-side bounded import: verify all files, then publish only MCP's pointer."""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .query import ReleaseQuery,QueryError,ACCOUNTS,MAX_BYTES

MAX_CACHE_BYTES = 384 * 1024 * 1024
RETAIN_RELEASES = 3


@contextmanager
def cache_writer(cache):
    """One importer per cache, including across processes; death releases OS lock."""
    path=cache/'.import.lock'
    if path.is_symlink():
        raise QueryError('FOREIGN_CACHE_DIRECTORY')
    with path.open('a+b') as stream:
        # Windows denies reads of a byte locked by another handle. Inspect
        # file size without touching that byte, then attempt the nonblocking lock.
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b'0');stream.flush()
        stream.seek(0)
        locked=False
        try:
            if os.name=='nt':
                import msvcrt
                try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1);locked=True
                except OSError:raise QueryError('CACHE_REFRESH_BUSY') from None
            else:
                import fcntl
                try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);locked=True
                except BlockingIOError:raise QueryError('CACHE_REFRESH_BUSY') from None
            # Exclusive ownership proves these stages cannot belong to a live
            # importer. Recovery only removes our prefixed temporary directories.
            stages=list(cache.glob('.import-*'))
            if len(stages)>1000:
                raise QueryError('CACHE_QUOTA_EXCEEDED')
            for stage in stages:
                if stage.is_symlink() or not stage.is_dir():
                    raise QueryError('FOREIGN_CACHE_DIRECTORY')
                shutil.rmtree(stage)
            yield
        finally:
            if locked:
                stream.seek(0)
                if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def prune_cache(cache, active=None):
    """Bound MCP-owned cache, preserving its current complete release.

    Never follows links or scans/deletes core storage. Unknown directories fail
    closed instead of being treated as disposable MCP releases.
    """
    releases = cache / 'releases'
    if not releases.exists():
        return 0
    if releases.is_symlink():
        raise QueryError('FOREIGN_CACHE_DIRECTORY')
    entries = list(releases.iterdir())
    if len(entries) > 1000:
        raise QueryError('CACHE_QUOTA_EXCEEDED')
    known = []
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir() or not re.fullmatch('[0-9a-f]{32}', entry.name):
            raise QueryError('FOREIGN_CACHE_DIRECTORY')
        size = 0
        for path in entry.rglob('*'):
            if path.is_symlink():
                raise QueryError('FOREIGN_CACHE_DIRECTORY')
            if path.is_file():
                if path.relative_to(entry).as_posix() not in ('report/index.html', 'report/ssw/index.html', 'report/yakshaver/index.html'):
                    raise QueryError('FOREIGN_CACHE_DIRECTORY')
                size += path.stat().st_size
        known.append((entry.stat().st_mtime, entry, size))
    # In-memory queries already own parsed data. Keep the active disk revision
    # and two most recent predecessors for restart/recovery.
    keep = {active} if active else set()
    for _, entry, _ in sorted(known, reverse=True):
        if len(keep) < RETAIN_RELEASES:
            keep.add(entry.name)
    for _, entry, _ in known:
        if entry.name not in keep:
            shutil.rmtree(entry)
    total = sum(size for _, entry, size in known if entry.name in keep)
    if total + MAX_BYTES * len(ACCOUNTS) > MAX_CACHE_BYTES:
        raise QueryError('CACHE_QUOTA_EXCEEDED')
    return total


async def import_release(bridge,identity,cache):
    cache=Path(cache).resolve()
    cache.mkdir(parents=True,exist_ok=True)
    # Only a dedicated MCP-owned cache, never core storage or an arbitrary existing directory.
    marker=cache/'.mcp-cache'
    if not marker.exists():
        if any(cache.iterdir()):
            raise QueryError('FOREIGN_CACHE_DIRECTORY')
        marker.write_text('baidu-ads-mcp-cache-v1',encoding='ascii');marker.chmod(0o600)
    if marker.is_symlink() or marker.read_text()!='baidu-ads-mcp-cache-v1':
        raise QueryError('FOREIGN_CACHE_DIRECTORY')
    with cache_writer(cache):
        return await _import_locked(bridge, identity, cache)


async def _import_locked(bridge, identity, cache):
    active = None
    current_path = cache/'current.json'
    if current_path.exists():
        if current_path.is_symlink() or current_path.stat().st_size > 4096:
            raise QueryError('FOREIGN_CACHE_DIRECTORY')
        try:
            active = json.loads(current_path.read_text())['release_id']
        except (ValueError, KeyError, TypeError):
            raise QueryError('INVALID_CACHE_POINTER') from None
        if not isinstance(active, str) or not re.fullmatch('[0-9a-f]{32}', active):
            raise QueryError('INVALID_CACHE_POINTER')
    prune_cache(cache, active)
    await bridge.verify(identity)
    current=await bridge.call('current',**identity)
    revision=current.get('release_id','')
    if not re.fullmatch('[0-9a-f]{32}',revision):
        raise QueryError('INVALID_RELEASE')
    temporary=Path(tempfile.mkdtemp(prefix='.import-',dir=cache))
    stage=temporary/revision
    try:
        for account in ACCOUNTS:
            value=await bridge.call('report',**identity,release_id=revision,account=account)
            if value.get('release_id')!=revision or not isinstance(value.get('html'),str):
                raise QueryError('INVALID_BRIDGE_RESPONSE')
            raw=value['html'].encode()
            if len(raw)>MAX_BYTES or hashlib.sha256(raw).hexdigest()!=value.get('sha256'):
                raise QueryError('INVALID_BRIDGE_RESPONSE')
            relative='report/index.html' if account=='tinacms' else f'report/{account}/index.html'
            path=stage/relative;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw);path.chmod(0o600)
        query=ReleaseQuery(stage)
        # Recheck identity before accepting imported data.
        await bridge.verify(identity)
        target=cache/'releases'/revision;target.parent.mkdir(exist_ok=True)
        if target.exists():
            existing=ReleaseQuery(target)
            if existing.hashes!=query.hashes:
                raise QueryError('IMMUTABLE_RELEASE_CHANGED')
        else:
            os.replace(stage,target)
        manifest={'release_id':revision,'hashes':query.hashes,'fetched_at':query.status()['fetched_at']}
        pointer=temporary/'current.json';pointer.write_text(json.dumps(manifest),encoding='utf-8');pointer.chmod(0o600)
        os.replace(pointer,cache/'current.json')
        prune_cache(cache, revision)
        return query
    finally:
        shutil.rmtree(temporary,ignore_errors=True)
