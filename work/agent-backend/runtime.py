"""Restricted local async runtime. Persistent call reservation, no public model endpoint."""
import threading
import time
from pathlib import Path

from engine import Engine
from jobqueue import Queue
from rag import RagPlanner
from intake import Intake
from conversation import DialogueExtractor
from intent_router import StructuredRouter
from quality import Quality
from knowledge import Knowledge


class Runtime:
    def __init__(self, database, queue_path, key=None, direct=False, max_calls=3, router='legacy'):
        self.database = database
        self.queue = Queue(queue_path)
        self.intake = Intake(str(Path(queue_path).with_suffix('.intake.db')))
        self.key = key
        self.direct = direct
        self.max_calls = max_calls
        if router not in {'legacy','split'}:raise ValueError('Unknown router')
        self.router=router
        self.stop_event = threading.Event()
        self.thread = None
        self.worker_errors = {}
        self.quality = Quality(Path(database).with_suffix('.quality.db'))
        self.knowledge = Knowledge(Path(database).with_suffix('.knowledge.db'))
        with self.queue.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS model_calls(id INTEGER PRIMARY KEY AUTOINCREMENT,reserved_at REAL NOT NULL)')

    def reserve(self):
        with self.queue.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            count = c.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0]
            if count >= self.max_calls:
                raise RuntimeError('Persistent model call budget exhausted')
            # A timeout may already be billed. Reservations are never refunded automatically.
            c.execute('INSERT INTO model_calls(reserved_at) VALUES(?)', (time.time(),))

    def health(self):
        with self.queue.connect() as c:
            used = c.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0]
        return dict(enabled=True, worker_alive=bool(self.thread and self.thread.is_alive()),
                    paid_model=bool(self.key or getattr(self,'business_router',None)), business_model=bool(getattr(self,'business_router',None)), reserved_calls=used, max_calls=self.max_calls,
                    remaining_calls=max(0,self.max_calls-used),
                    dialogue_router=self.router,
                    worker_errors=dict(self.worker_errors),
                    planner='rag-deepseek-poc-v2' if self.key else 'rule-fixture-1')

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        def worker():
            planner = RagPlanner(self.key,self.direct,'v2',self.reserve) if self.key else None
            if planner:
                planner.retriever.knowledge = self.knowledge
            extractor = ((StructuredRouter(self.key,self.direct) if self.router=='split' else DialogueExtractor(self.key,self.direct,'v3')) if self.key else None)
            engine = Engine(self.database,planner)
            while not self.stop_event.is_set():
                worked=False
                # One failing stage must not starve unrelated execution/reconciliation.
                stages=[('dispatch',lambda:engine.dispatch_understanding(self.intake)),
                        ('execute',lambda:self.queue.work_one(engine)),
                        ('understand',lambda:self.intake.work_one(extractor,self.reserve)),
                        ('collect',lambda:engine.collect_understanding(self.intake)),
                        ('quality',lambda:self.quality.scan(engine,[self.queue,self.intake.queue]))]
                for phase,action in stages:
                    try:worked=bool(action()) or worked
                    except Exception as exc:
                        self.worker_errors[phase]=dict(error_type=type(exc).__name__,count=self.worker_errors.get(phase,{}).get('count',0)+1)
                if not worked:
                    self.stop_event.wait(.2)
        self.thread = threading.Thread(target=worker,name='sandbox-worker',daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=35)


def load_local_key(path=None):
    import os
    key=os.environ.get('DEEPSEEK_API_KEY','').strip()
    if not key and path:
        for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
            if line.startswith('DEEPSEEK_API_KEY='):
                key=line.split('=',1)[1].strip().strip('\"\'')
    if not key:
        raise ValueError('Missing locally configured DEEPSEEK_API_KEY')
    return key
