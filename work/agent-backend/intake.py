"""Durable message intake: SQL outbox trigger + leased worker + audited result.
Message understanding does not authorize business effects.
"""
import hashlib
import json
import time
from conversation import validate_messages
from engine import Conflict
from jobqueue import Queue


class Intake:
    def __init__(self,path):
        self.queue=Queue(path)
        with self.queue.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS dialogue_submissions(
              id TEXT PRIMARY KEY,messages TEXT NOT NULL,digest TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'QUEUED',result TEXT,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS intake_audit(
              seq INTEGER PRIMARY KEY AUTOINCREMENT,submission_id TEXT NOT NULL,
              event TEXT NOT NULL,at REAL NOT NULL,details TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS dialogue_received AFTER INSERT ON dialogue_submissions
            BEGIN
              INSERT INTO jobs(id,task_id,status,available_at,created_at)
                VALUES(NEW.id,NEW.id,'QUEUED',NEW.created_at,NEW.created_at);
              INSERT INTO intake_audit(submission_id,event,at,details)
                VALUES(NEW.id,'dialogue.received',NEW.created_at,'{}');
            END;
            ''')

    def submit(self,event_id,messages):
        if not isinstance(event_id,str) or not 1<=len(event_id)<=80 or not event_id.isascii():
            raise ValueError('Invalid event ID')
        validate_messages(messages)
        raw=json.dumps(messages,ensure_ascii=False,sort_keys=True)
        digest=hashlib.sha256(raw.encode()).hexdigest()
        with self.queue.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            existing=c.execute('SELECT digest FROM dialogue_submissions WHERE id=?',(event_id,)).fetchone()
            if existing and existing['digest']!=digest:raise Conflict('Event ID reused with different messages')
            if not existing:
                if c.execute('SELECT COUNT(*) FROM dialogue_submissions').fetchone()[0]>=1000:raise Conflict('Local intake capacity reached')
                c.execute('INSERT INTO dialogue_submissions(id,messages,digest,created_at) VALUES(?,?,?,?)',(event_id,raw,digest,time.time()))
        return self.get(event_id)

    def get(self,event_id):
        with self.queue.connect() as c:
            row=c.execute('SELECT * FROM dialogue_submissions WHERE id=?',(event_id,)).fetchone()
            if row is None:raise KeyError(event_id)
            result=dict(row)
            result['messages']=json.loads(result['messages'])
            result['result']=json.loads(result['result']) if result['result'] else None
            result['audit']=[dict(r) for r in c.execute('SELECT * FROM intake_audit WHERE submission_id=? ORDER BY seq',(event_id,))]
        result['job']=self.queue.get(event_id)
        if result['job'] and result['job']['status']!='DONE':
            result['status']=result['job']['status']
        result['execution_authorized']=False
        return result

    def work_one(self,extractor=None,reserve=None):
        job=self.queue.claim()
        if not job:return False
        try:
            if extractor is None:
                output=dict(valid=False,execution_authorized=False,next_step='human_review_model_disabled')
            else:
                if reserve:reserve()
                output=extractor.extract(self.get(job['task_id'])['messages'])
            state='NEEDS_CLARIFICATION' if output.get('valid') and output['extraction']['needs_clarification'] else 'NEEDS_REVIEW'
            if output.get('valid') and output['extraction'].get('dialogue_state')=='paused':state='PAUSED'
            # Save output and acknowledge atomically; stale workers cannot overwrite a newer result.
            with self.queue.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                changed=c.execute("UPDATE jobs SET status='DONE',ended_at=? WHERE id=? AND lease_token=? AND status='RUNNING'",(time.time(),job['id'],job['lease_token'])).rowcount
                if changed:
                    c.execute('UPDATE dialogue_submissions SET status=?,result=? WHERE id=?',(state,json.dumps(output,ensure_ascii=False),job['task_id']))
                    c.execute('INSERT INTO intake_audit(submission_id,event,at,details) VALUES(?,?,?,?)',(job['task_id'],'dialogue.understood',time.time(),json.dumps(dict(valid=output.get('valid'),prompt_sha256=output.get('prompt_sha256')))))
        except Exception as exc:
            if self.queue.settle(job,retry=True,error_type=type(exc).__name__):
                with self.queue.connect() as c:
                    c.execute('INSERT INTO intake_audit(submission_id,event,at,details) VALUES(?,?,?,?)',
                              (job['task_id'],'dialogue.failed',time.time(),json.dumps(dict(error_type=type(exc).__name__))))
        return True
