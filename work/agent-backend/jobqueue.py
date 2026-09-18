"""Durable local at-least-once queue. Sandbox effects remain guarded by Engine."""
import sqlite3
import time
import uuid
from contextlib import contextmanager
from engine import Conflict


class Queue:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, task_id TEXT UNIQUE NOT NULL, status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT, lease_until REAL,
                available_at REAL NOT NULL, created_at REAL NOT NULL, ended_at REAL, error_type TEXT)''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_jobs_status_available ON jobs(status,available_at)')

    def get(self, task):
        with self.connect() as c:
            row = c.execute('SELECT * FROM jobs WHERE task_id=?', (task,)).fetchone()
            if not row:
                return None
            # Lease tokens are internal fencing credentials, never returned to a browser.
            return {k: row[k] for k in row.keys() if k != 'lease_token'}

    def resume(self, task):
        """Only called after Engine confirms the task is READY; never revives DEAD jobs."""
        with self.connect() as c:
            c.execute("UPDATE jobs SET status='QUEUED',attempts=0,available_at=?,ended_at=NULL WHERE task_id=? AND status='DONE'",
                      (time.time(), task))
        return self.enqueue(task)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=35)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def enqueue(self, task):
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO jobs(id,task_id,status,available_at,created_at) VALUES(?,?,?,?,?)',
                      (uuid.uuid4().hex, task, 'QUEUED', time.time(), time.time()))
            return dict(c.execute('SELECT * FROM jobs WHERE task_id=?', (task,)).fetchone())

    def claim(self, lease_seconds=60):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            now = time.time()
            c.execute("UPDATE jobs SET status='DEAD',ended_at=? WHERE status='RUNNING' AND lease_until<? AND attempts>=3", (now, now))
            job = c.execute("SELECT * FROM jobs WHERE attempts<3 AND ((status='QUEUED' AND available_at<=?) OR (status='RUNNING' AND lease_until<?)) ORDER BY created_at LIMIT 1", (now, now)).fetchone()
            if not job:
                return None
            token = uuid.uuid4().hex
            c.execute("UPDATE jobs SET status='RUNNING',attempts=attempts+1,lease_token=?,lease_until=? WHERE id=?", (token, now+lease_seconds, job['id']))
            return dict(c.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone())

    def settle(self, job, retry=False, error_type=None):
        status = ('QUEUED' if job['attempts'] < 3 else 'DEAD') if retry else 'DONE'
        with self.connect() as c:
            changed = c.execute("UPDATE jobs SET status=?,available_at=?,ended_at=?,error_type=? WHERE id=? AND lease_token=? AND status='RUNNING'",
                (status, time.time()+0.1, None if status=='QUEUED' else time.time(), error_type, job['id'], job['lease_token'])).rowcount
            return changed == 1

    def work_one(self, engine):
        job = self.claim()
        if not job:
            return False
        try:
            task = engine.get(job['task_id'])
            if task['state'] in {'READY','RETRYABLE'}:
                task = engine.run(task['id'])
            # WAITING_HUMAN is a successful handoff, not autonomous completion.
            self.settle(job, retry=task['state']=='RETRYABLE')
        except Conflict:
            self.settle(job, retry=True, error_type='StateConflict')
        except Exception as exc:
            self.settle(job, retry=True, error_type=type(exc).__name__)
        return True

    def rows(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM jobs ORDER BY created_at')]
