"""Restart-safe reads of only the MCP-owned immutable report cache."""
from datetime import datetime
import json
from pathlib import Path
import re
import time

from .query import ACCOUNTS,QueryError,ReleaseQuery


def load_runtime_query(config, fallback_release=None, *, clock=time.time, ttl=3600, allow_expired=False):
    """Prefer the complete verified MCP pointer; never mask a corrupt/stale cache.

    An absent or genuinely empty cache can use an explicitly supplied standalone
    release copy during initial setup. Once any cache state exists, failures are
    surfaced to the caller. HTTP startup may explicitly allow an expired but
    otherwise valid cache so authentication/refresh can recover it; the returned
    query retains its original age and rejects every data read until replaced.
    No core pointer or private source database is opened.
    """
    location=config.get('cache_directory')
    fallback=fallback_release or config.get('release_copy')
    if not location:
        if not fallback:raise QueryError('RELEASE_COPY_REQUIRED')
        return ReleaseQuery(Path(fallback),clock=clock,ttl=ttl)
    cache=Path(location)
    if cache.is_symlink():raise QueryError('FOREIGN_CACHE_DIRECTORY')
    if not cache.exists() or (cache.is_dir() and not any(cache.iterdir())):
        if not fallback:raise QueryError('RELEASE_COPY_REQUIRED')
        return ReleaseQuery(Path(fallback),clock=clock,ttl=ttl)
    try:
        marker=cache/'.mcp-cache';pointer=cache/'current.json'
        if not cache.is_dir() or marker.is_symlink() or not marker.is_file() or marker.stat().st_size>64 or marker.read_text()!='baidu-ads-mcp-cache-v1':
            raise QueryError('FOREIGN_CACHE_DIRECTORY')
        if pointer.is_symlink() or not pointer.is_file() or pointer.stat().st_size>4096:
            raise QueryError('INVALID_CACHE_POINTER')
        raw=pointer.read_bytes();manifest=json.loads(raw)
        if not isinstance(manifest,dict) or set(manifest)!={'release_id','hashes','fetched_at'}:
            raise QueryError('INVALID_CACHE_POINTER')
        revision=manifest['release_id'];hashes=manifest['hashes']
        if not isinstance(revision,str) or not re.fullmatch('[0-9a-f]{32}',revision):raise QueryError('INVALID_CACHE_POINTER')
        if not isinstance(hashes,dict) or set(hashes)!=set(ACCOUNTS) or any(not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h) for h in hashes.values()):
            raise QueryError('INVALID_CACHE_POINTER')
        fetched=datetime.fromisoformat(manifest['fetched_at'])
        if fetched.tzinfo is None:raise QueryError('INVALID_CACHE_POINTER')
        fetched_at=fetched.timestamp();now=clock()
        if fetched_at>now+5:raise QueryError('INVALID_CACHE_POINTER')
        if now-fetched_at>=ttl and not allow_expired:raise QueryError('SNAPSHOT_EXPIRED')
        release=cache/'releases'/revision
        for account in ACCOUNTS:
            path=release/'report'/('index.html' if account=='tinacms' else account+'/index.html')
            for node in (path,*path.parents):
                if node==cache:break
                if node.is_symlink():raise QueryError('FOREIGN_CACHE_DIRECTORY')
        query=ReleaseQuery(release,clock=clock,ttl=ttl)
        if query.hashes!=hashes:raise QueryError('CACHE_HASH_MISMATCH')
        if pointer.read_bytes()!=raw:raise QueryError('CACHE_POINTER_CHANGED')
        # Restarts do not extend the age of an imported source snapshot.
        query.loaded_at=fetched_at
        if not allow_expired:query.check()
        return query
    except QueryError:raise
    except (OSError,ValueError,TypeError,KeyError,OverflowError):
        raise QueryError('INVALID_CACHE_POINTER') from None
