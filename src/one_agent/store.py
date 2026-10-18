"""Single-writer SQLite journal. A decision, plan and action commit atomically."""
import json
import sqlite3
from .common import iso, ts


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
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
        CREATE TABLE IF NOT EXISTS viewers (user_id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cohorts (user_id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')

    def ingest(self, event):
        return self.db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?)',
                               (event['event_id'], event['user_id'], iso(ts(event['received_at'])), json.dumps(event))).rowcount > 0

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
        self.db.execute('INSERT OR REPLACE INTO plans VALUES (?,?,?,?,?)',
                        (plan['plan_id'], plan['user_id'], plan['goal_id'], plan['status'], json.dumps(plan)))

    def log(self, value):
        self.db.execute('INSERT INTO audit(body) VALUES (?)', (json.dumps(value),))

    def records(self, table):
        if table not in {'decisions', 'plans', 'actions', 'viewers', 'cohorts', 'audit'}:
            raise ValueError('Unsupported table')
        return [json.loads(row[0]) for row in self.db.execute(f'SELECT body FROM {table} ORDER BY rowid')]

    def close(self):
        self.db.commit()
        self.db.close()
