"""Local quality operations. Release decisions are records, never deployments."""
import hashlib
import json
import sqlite3
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from engine import Conflict

ROOT = Path(__file__).parent
SUITE = ('test_engine', 'test_consent_version', 'test_clarification', 'test_poc',
         'test_retrieval_eval', 'test_intent_iteration', 'test_split_router', 'test_quality', 'test_governance', 'test_policy_fence', 'test_product',
         'test_business', 'test_returns', 'test_business_chat', 'test_operations', 'test_tool_gateway')
CAUSES = {'data', 'knowledge', 'retrieval', 'prompt', 'tool', 'authorization', 'ux'}


def required(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError('Nonempty bounded text required')
    return value.strip()


class Quality:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS quality_cases(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS quality_runs(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS quality_events(seq INTEGER PRIMARY KEY, at TEXT, kind TEXT, body TEXT);
            CREATE TABLE IF NOT EXISTS quality_sources(source_key TEXT PRIMARY KEY, case_id TEXT);
            CREATE TABLE IF NOT EXISTS quality_protocols(case_id TEXT PRIMARY KEY, body TEXT);
            CREATE TABLE IF NOT EXISTS quality_evals(id TEXT PRIMARY KEY, body TEXT);
            ''')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def event(self, c, kind, body):
        c.execute('INSERT INTO quality_events(at,kind,body) VALUES(?,?,?)',
                  (datetime.now(timezone.utc).isoformat(), kind, json.dumps(body, ensure_ascii=False)))

    def list(self):
        with self.connect() as c:
            return dict(cases=[json.loads(r[0]) for r in c.execute('SELECT body FROM quality_cases ORDER BY rowid DESC')],
                        runs=[json.loads(r[0]) for r in c.execute('SELECT body FROM quality_runs ORDER BY rowid DESC LIMIT 20')],
                        evaluations=[json.loads(r[0]) for r in c.execute('SELECT body FROM quality_evals ORDER BY rowid DESC LIMIT 20')],
                        protocols=[dict(case_id=r[0],**json.loads(r[1])) for r in c.execute('SELECT case_id,body FROM quality_protocols')],
                        scope='local engineering regression; not model quality or deployment')

    def metrics(self):
        now=datetime.now(timezone.utc)
        with self.connect() as c:
            cases=[json.loads(r[0]) for r in c.execute('SELECT body FROM quality_cases')]
            runs=[json.loads(r[0]) for r in c.execute('SELECT body FROM quality_runs')]
            events=[(datetime.fromisoformat(r[0]),r[1],json.loads(r[2])) for r in c.execute('SELECT at,kind,body FROM quality_events ORDER BY seq')]
        opened={};fixed={};decisions=[];weeks=[0,0]
        for at,kind,body in events:
            id=body.get('id')
            if kind in {'case.collected','case.auto_collected'}:opened.setdefault(id,at)
            if kind=='case.fix':fixed.setdefault(id,at)
            if kind=='case.decide':decisions.append(body)
            if kind=='case.triage' and body.get('priority')=='P0':
                if now-timedelta(days=7)<=at<=now:weeks[0]+=1
                elif now-timedelta(days=14)<=at<now-timedelta(days=7):weeks[1]+=1
        durations=[(at-opened[id]).total_seconds()/3600 for id,at in fixed.items() if id in opened]
        causes={cause:sum(x.get('cause')==cause for x in cases) for cause in sorted(CAUSES)}
        return dict(as_of=now.isoformat(),scope='local records, not production business metrics',
                    total_cases=len(cases),untriaged=sum(x['state']=='NEW' for x in cases),
                    open_p0=sum(x.get('priority')=='P0' and x['state'] not in {'RELEASE_RECORDED','ROLLBACK_RECORDED'} for x in cases),
                    p0_triaged_last_7d=weeks[0],p0_triaged_previous_7d=weeks[1],
                    p0_change_rate=(weeks[0]-weeks[1])/weeks[1] if weeks[1] else None,
                    causes=causes,regression_count=len(runs),regression_passed=sum(x['passed'] for x in runs),
                    regression_pass_rate=sum(x['passed'] for x in runs)/len(runs) if runs else None,
                    decision_count=len(decisions),rollback_count=sum(x['state']=='ROLLBACK_RECORDED' for x in decisions),
                    mean_hours_to_first_fix=sum(durations)/len(durations) if durations else None,
                    fix_sample_count=len(durations))

    def collect(self, title, expected, actual, evidence, source='synthetic'):
        if source not in {'synthetic', 'public_dataset', 'local_trace'}:
            raise ValueError('Unsupported source')
        case = dict(id=uuid.uuid4().hex, title=required(title), expected=required(expected),
                    actual=required(actual), evidence=required(evidence), source=source,
                    state='NEW', revision=0)
        with self.connect() as c:
            c.execute('INSERT INTO quality_cases VALUES(?,?)', (case['id'], json.dumps(case)))
            self.event(c, 'case.collected', case)
        return case

    def scan(self, engine, queues):
        """Idempotent discovery, not automatic root-cause adjudication."""
        signals=[]
        with engine.connect() as c:
            for r in c.execute("SELECT seq,task_id,event FROM traces WHERE event IN ('planner.failed','guardrail.blocked')"):
                signals.append((f'trace:{r[0]}',r[1],r[2]))
        for i,queue in enumerate(queues):
            with queue.connect() as c:
                for r in c.execute("SELECT id,task_id FROM jobs WHERE status='DEAD'"):
                    signals.append((f'dead:{i}:{r[0]}',r[1],'queue.dead'))
        count=0
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for key,task,event in signals:
                if c.execute('SELECT 1 FROM quality_sources WHERE source_key=?',(key,)).fetchone():continue
                case=dict(id=uuid.uuid4().hex,title='待归因 · '+event,expected='核对是否为合理拦截或需修复的异常',
                          actual=event,evidence='task:'+task+'; '+key,source='local_trace',state='NEW',revision=0)
                c.execute('INSERT INTO quality_cases VALUES(?,?)',(case['id'],json.dumps(case)))
                c.execute('INSERT INTO quality_sources VALUES(?,?)',(key,case['id']))
                self.event(c,'case.auto_collected',case);count+=1
        return count

    def protocol(self,case_id,cases):
        if not isinstance(cases,list) or not 1<=len(cases)<=100:raise ValueError('1..100 cases required')
        seen=set()
        for row in cases:
            if not isinstance(row,dict) or set(row)!={'id','expected','safety'}:raise ValueError('Invalid case')
            required(row['id']);required(row['expected'])
            if type(row['safety']) is not bool or row['id'] in seen:raise ValueError('Duplicate or invalid case')
            seen.add(row['id'])
        if not any(r['safety'] for r in cases):raise ValueError('Safety cases required')
        with self.connect() as c:
            row=c.execute('SELECT body FROM quality_cases WHERE id=?',(case_id,)).fetchone()
            if not row:raise KeyError(case_id)
            if json.loads(row[0])['state']!='TRIAGED':raise Conflict('Freeze protocol before registering fix')
            protocol=dict(cases=cases,hash=hashlib.sha256(json.dumps(cases,sort_keys=True).encode()).hexdigest(),
                          gate='candidate >= baseline; all safety cases pass',at=datetime.now(timezone.utc).isoformat())
            try:c.execute('INSERT INTO quality_protocols VALUES(?,?)',(case_id,json.dumps(protocol)))
            except sqlite3.IntegrityError:raise Conflict('Protocol already frozen') from None
            self.event(c,'evaluation.frozen',dict(case_id=case_id,**protocol))
        return protocol

    def evaluate(self,case_id,baseline,candidate):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT body FROM quality_cases WHERE id=?',(case_id,)).fetchone()
            protocol=c.execute('SELECT body FROM quality_protocols WHERE case_id=?',(case_id,)).fetchone()
            if not row or not protocol:raise ValueError('Case and frozen protocol required')
            case=json.loads(row[0]);protocol=json.loads(protocol[0])
            if case['state']!='FIXED':raise Conflict('Register fix first')
            def score(rows):
                if not isinstance(rows,list) or len(rows)!=len(protocol['cases']):raise ValueError('Complete report required')
                records={}
                for r in rows:
                    if not isinstance(r,dict) or set(r)!={'id','prediction','response_id','model','prompt_hash'}:raise ValueError('Invalid model record')
                    for value in r.values():required(value)
                    if r['id'] in records:raise ValueError('Duplicate record')
                    records[r['id']]=r
                if set(records)!={r['id'] for r in protocol['cases']}:raise ValueError('Dataset mismatch')
                correct=[records[r['id']]['prediction']==r['expected'] for r in protocol['cases']]
                safe=all(ok for ok,r in zip(correct,protocol['cases']) if r['safety'])
                return sum(correct),safe
            b,_=score(baseline);n,safe=score(candidate)
            report=dict(id=uuid.uuid4().hex,case_id=case_id,case_revision=case['revision'],dataset_hash=protocol['hash'],
                        source_hash=source_hash(),baseline_correct=b,candidate_correct=n,count=len(candidate),safety_passed=safe,
                        passed=n>=b and safe,baseline=baseline,candidate=candidate,
                        provenance='operator-imported; provider authenticity not independently verified')
            c.execute('INSERT INTO quality_evals VALUES(?,?)',(report['id'],json.dumps(report)))
            self.event(c,'evaluation.completed',dict(id=report['id'],passed=report['passed']))
        return report

    def advance(self, case_id, revision, action, **fields):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body FROM quality_cases WHERE id=?', (case_id,)).fetchone()
            if not row:
                raise KeyError(case_id)
            case = json.loads(row[0])
            if type(revision) is not int or revision != case['revision']:
                raise Conflict('Stale case revision')
            if action == 'triage' and case['state'] == 'NEW':
                if fields.get('cause') not in CAUSES or fields.get('priority') not in {'P0', 'P1', 'P2'}:
                    raise ValueError('Invalid classification')
                if type(fields.get('safety')) is not bool:
                    raise ValueError('Explicit safety classification required')
                case.update(cause=fields['cause'], priority='P0' if fields['safety'] else fields['priority'],
                            safety=fields['safety'], owner=required(fields.get('owner')),
                            rationale=required(fields.get('rationale')), state='TRIAGED')
            elif action == 'fix' and case['state'] == 'TRIAGED':
                case.update(change_ref=required(fields.get('change_ref')),
                            hypothesis=required(fields.get('hypothesis')), state='FIXED')
            elif action == 'decide' and case['state'] == 'FIXED':
                decision = fields.get('decision')
                if decision not in {'release', 'rollback'}:
                    raise ValueError('Invalid decision')
                run = c.execute('SELECT body FROM quality_runs WHERE id=?', (fields.get('run_id'),)).fetchone()
                if not run:
                    raise ValueError('Recorded regression required')
                run = json.loads(run[0])
                if run['case_id'] != case_id or run['case_revision'] != revision:
                    raise Conflict('Run does not match case revision')
                if decision == 'release' and (not run['passed'] or run['source_hash'] != source_hash()):
                    raise Conflict('Failed or stale regression cannot release')
                if decision=='release' and case['cause'] in {'prompt','retrieval','knowledge','data'}:
                    ev=c.execute('SELECT body FROM quality_evals WHERE id=?',(fields.get('evaluation_id'),)).fetchone()
                    ev=json.loads(ev[0]) if ev else {}
                    if not (ev.get('passed') and ev.get('case_id')==case_id and ev.get('case_revision')==revision and ev.get('source_hash')==source_hash()):
                        raise Conflict('Matching passed model evaluation required')
                    case['evaluation_id']=ev['id']
                # A human result assessment is mandatory; unit tests alone do not measure model quality.
                case.update(state='RELEASE_RECORDED' if decision == 'release' else 'ROLLBACK_RECORDED',
                            run_id=run['id'], acceptance_evidence=required(fields.get('acceptance_evidence')),
                            decision_note=required(fields.get('note')))
            else:
                raise Conflict('Invalid workflow transition')
            case['revision'] += 1
            c.execute('UPDATE quality_cases SET body=? WHERE id=?', (json.dumps(case), case_id))
            self.event(c, 'case.' + action, case)
        return case

    def regress(self, case_id):
        case = next((x for x in self.list()['cases'] if x['id'] == case_id), None)
        if not case:
            raise KeyError(case_id)
        if case['state'] != 'FIXED':
            raise Conflict('Record fix before regression')
        before = source_hash()
        try:
            result = subprocess.run([sys.executable, '-m', 'unittest', *SUITE], cwd=ROOT,
                                    capture_output=True, timeout=120)
            code = result.returncode
            output_hash = hashlib.sha256(result.stdout + result.stderr).hexdigest()
        except subprocess.TimeoutExpired:
            code, output_hash = -1, None
        report = dict(id=uuid.uuid4().hex, case_id=case_id, case_revision=case['revision'],
                      suite=list(SUITE), suite_hash=hashlib.sha256(json.dumps(SUITE).encode()).hexdigest(),
                      source_hash=before, passed=code == 0 and before == source_hash(),
                      exit_code=code, output_hash=output_hash, model_calls=0,
                      scope='engineering regression only', at=datetime.now(timezone.utc).isoformat())
        with self.connect() as c:
            c.execute('INSERT INTO quality_runs VALUES(?,?)', (report['id'], json.dumps(report)))
            self.event(c, 'regression.completed', report)
        return report


def source_hash():
    digest = hashlib.sha256()
    for p in sorted(ROOT.iterdir()):
        if p.suffix in {'.py', '.json', '.js', '.html', '.css'} and p.is_file():
            digest.update(p.name.encode())
            digest.update(p.read_bytes())
    return digest.hexdigest()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Run fixed, zero-fee regression for a FIXED case')
    parser.add_argument('--db', required=True)
    parser.add_argument('--case', required=True)
    args = parser.parse_args()
    print(json.dumps(Quality(args.db).regress(args.case), ensure_ascii=False))
