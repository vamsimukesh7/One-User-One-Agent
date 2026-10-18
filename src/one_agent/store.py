"""Single-writer SQLite journal. A decision, plan and action commit atomically."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .common import iso, ts


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.failure_path = self.path.with_suffix(".failures.jsonl")
        self.db = sqlite3.connect(path, timeout=0.1)
        self.db.row_factory = sqlite3.Row
        self.clock=iso(datetime.now(timezone.utc))
        self.db.create_function("audit_now",0,lambda:self.clock)
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS events (
          event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, received_at TEXT NOT NULL,
          body TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_user_time ON events(user_id,received_at);
        CREATE TABLE IF NOT EXISTS decisions (
          event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS plans (
          plan_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, goal_id TEXT NOT NULL,
          status TEXT NOT NULL, body TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_plan ON plans(user_id) WHERE status='open';
        CREATE INDEX IF NOT EXISTS plans_goal_status ON plans(goal_id,status);
        CREATE TABLE IF NOT EXISTS actions (
          action_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT,
          published_at TEXT NOT NULL, body TEXT NOT NULL,
          UNIQUE(user_id,session_id));
        CREATE TABLE IF NOT EXISTS outcomes(plan_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS viewers (user_id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cohorts (user_id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        if 'dispatch_state' not in {r[1] for r in self.db.execute('PRAGMA table_info(actions)')}:
            self.db.execute("ALTER TABLE actions ADD COLUMN dispatch_state TEXT NOT NULL DEFAULT 'PUBLISHED'")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS state_versions (
          seq INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
          user_id TEXT NOT NULL, known_at TEXT NOT NULL, body TEXT);
        CREATE INDEX IF NOT EXISTS versions_user_time ON state_versions(user_id,known_at,seq);
        CREATE TABLE IF NOT EXISTS applied_controls(control_id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS event_observations(event_id TEXT PRIMARY KEY,known_at TEXT NOT NULL);
        ''')
        if not self.db.execute("SELECT 1 FROM metadata WHERE key='audit_history_complete'").fetchone():
            legacy=self.db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]
            self.db.execute("INSERT INTO metadata VALUES ('audit_history_complete',?)",('false' if legacy else 'true',))
        for table,key in [('viewers','user_id'),('plans','plan_id'),('decisions','event_id'),('actions','action_id'),('outcomes','plan_id')]:
            for op in ('INSERT','UPDATE','DELETE'):
                value='OLD' if op=='DELETE' else 'NEW'
                body='NULL' if op=='DELETE' else 'NEW.body'
                self.db.execute(f"""CREATE TRIGGER IF NOT EXISTS history_{table}_{op} AFTER {op} ON {table}
                BEGIN INSERT INTO state_versions(entity,entity_id,user_id,known_at,body)
                VALUES ('{table}',{value}.{key},{value}.user_id,audit_now(),{body}); END""")
        self.db.commit()

    def set_time(self, at):
        self.clock=iso(ts(at) if isinstance(at,str) else at)

    def ingest(self, event, observed_at=None):
        inserted=self.db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?)',
                               (event['event_id'], event['user_id'], iso(ts(event['received_at'])), json.dumps(event))).rowcount > 0
        if inserted:
            known=observed_at or max(ts(event['received_at']),ts(event.get('deliver_at',event['received_at'])))
            self.db.execute('INSERT OR IGNORE INTO event_observations VALUES (?,?)',(event['event_id'],iso(known)))
        return inserted

    def events(self, user, at):
        return [json.loads(r[0]) for r in self.db.execute(
            'SELECT body FROM events WHERE user_id=? AND received_at<=? ORDER BY received_at,event_id', (user, iso(ts(at))))]

    def decision(self, event_id):
        row = self.db.execute('SELECT body FROM decisions WHERE event_id=?', (event_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def active(self, user):
        row = self.db.execute("SELECT body FROM plans WHERE user_id=? AND status='open'", (user,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_plan(self, plan):
        self.db.execute('''INSERT INTO plans VALUES (?,?,?,?,?) ON CONFLICT(plan_id) DO UPDATE SET
                        goal_id=excluded.goal_id,status=excluded.status,body=excluded.body''',
                        (plan['plan_id'], plan['user_id'], plan['goal_id'], plan['status'], json.dumps(plan)))

    def save_outcome(self, outcome):
        self.db.execute('INSERT INTO outcomes VALUES (?,?,?) ON CONFLICT(plan_id) DO UPDATE SET body=excluded.body',
            (outcome['plan_id'],outcome['user_id'],json.dumps(outcome)))

    def assignment(self, user, arm):
        key='arm:'+user
        row=self.db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
        if row and row[0]!=arm:raise ValueError('Randomized arm changed for existing viewer')
        self.db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)',(key,arm))
        return row[0] if row else arm

    def sync_viewer(self, user, at):
        row = self.db.execute('SELECT body FROM viewers WHERE user_id=?', (user,)).fetchone()
        if row:
            viewer = json.loads(row[0])
            active = self.active(user)
            viewer['active_plan_id'] = active['plan_id'] if active else None
            viewer['state_updated_at'] = at
            self.db.execute('UPDATE viewers SET body=? WHERE user_id=?', (json.dumps(viewer), user))

    def published_actions(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT body FROM actions WHERE dispatch_state='PUBLISHED' ORDER BY rowid")]

    def emergency(self, decision):
        # Independent local failure journal: no claim that the unavailable DB committed.
        import os
        with self.failure_path.open('a', encoding='utf8') as stream:
            stream.write(json.dumps(decision)+'\n'); stream.flush(); os.fsync(stream.fileno())

    def log(self, value):
        self.db.execute('INSERT INTO audit(body) VALUES (?)', (json.dumps(value),))

    def records(self, table):
        if table not in {'decisions', 'plans', 'actions', 'viewers', 'cohorts', 'audit', 'outcomes'}:
            raise ValueError('Unsupported table')
        return [json.loads(row[0]) for row in self.db.execute(f'SELECT body FROM {table} ORDER BY rowid')]

    def close(self):
        self.db.commit()
        self.db.close()
