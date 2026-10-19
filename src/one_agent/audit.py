"""Read-only point-in-time reconstruction from committed version history."""
from contextlib import closing
import json
import sqlite3
from pathlib import Path
from .common import iso, ts


def inspect(path, viewer, at=None):
    path=Path(path)
    if not path.is_file():raise ValueError('Database does not exist')
    # SQLite's connection context manager ends transactions but does not close.
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if at is None:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return {table:[json.loads(r[0]) for r in db.execute('SELECT body FROM '+table+' WHERE user_id=?',(viewer,))] if table in tables else []
                    for table in ('viewers','decisions','plans','actions','outcomes')}
        when=ts(at)
        if when.tzinfo is None:raise ValueError('--at requires an explicit timezone')
        at=iso(when)
        try:
            complete=db.execute("SELECT value FROM metadata WHERE key='audit_history_complete'").fetchone()
            if not complete or complete[0]!='true':raise ValueError('This legacy database lacks complete version history. Generate a new replay to use --at.')
            rows=db.execute('SELECT entity,entity_id,known_at,body FROM state_versions WHERE user_id=? AND known_at<=? ORDER BY known_at,seq',(viewer,at))
        except sqlite3.OperationalError as exc:raise ValueError('No audit history; generate a new replay') from exc
        latest={};observations={}
        for table,key,known,body in rows:
            latest[table,key]=json.loads(body) if body else None
            observations[table,key]=known
        result={table:[value for (entity,key),value in latest.items() if entity==table and value is not None]
                for table in ('viewers','decisions','plans','actions','outcomes')}
        result['known_events']=[{'known_at':r[0],'event':json.loads(r[1])} for r in db.execute(
            'SELECT o.known_at,e.body FROM events e JOIN event_observations o ON e.event_id=o.event_id WHERE e.user_id=? AND o.known_at<=? ORDER BY o.known_at,e.event_id',(viewer,at))]
        result.update(viewer_id=viewer,as_of=at,semantics='Latest committed state known by this time. Facts are last resolved at their recorded observation times. Future arrivals and batch simulator outcomes are excluded until observed.')
        return result
