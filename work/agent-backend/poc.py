"""Reproducible synthetic technical POC. Paid calls require --live (max three)."""
import argparse
import json
import math
import os
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from engine import Engine
from jobqueue import Queue
from rag import RagPlanner, Retriever

ROOT = Path(__file__).resolve().parent
RETRIEVAL_CASES = [
    ('客户说可以换商品，后台却标成不接受，意愿冲突怎么办？', 'consent'),
    ('原话和结构化偏好不一致，需要核对哪个时间的意愿？', 'consent'),
    ('替代品价格比实付金额贵，可以直接换货吗？', 'replacement'),
    ('仓库库存数量为零，能自动给同意换货的客户退款吗？', 'replacement'),
    ('退款金额的自动上限是多少？', 'refund'),
    ('客户申请退款，能否超过实付金额？', 'refund'),
    ('身份还没有验证，能够继续操作吗？', 'risk'),
    ('支付状态未知，有人身安全风险，如何处置？', 'risk'),
    ('工具调用超时，但订单可能成功了，如何查询回执？', 'retry'),
    ('再次收到同一退款请求，怎样避免重复执行并对账？', 'retry'),
]


def retrieval_report():
    retriever = Retriever()
    records = []
    for query, expected in RETRIEVAL_CASES:
        hits = retriever.retrieve(query)
        ids = [d['id'] for d in hits]
        rank = ids.index(expected)+1 if expected in ids else None
        records.append(dict(query=query, expected=expected, retrieved=ids,
                            hit_at_3=rank is not None, reciprocal_rank=1/rank if rank else 0))
    return dict(method='BM25-style Chinese-bigram lexical retrieval; no embeddings',
                corpus_hash=retriever.corpus_hash, cases=len(records),
                hit_at_3=sum(r['hit_at_3'] for r in records)/len(records),
                mrr_at_3=sum(r['reciprocal_rank'] for r in records)/len(records), records=records)


def queue_report(directory, jobs, workers):
    engine = Engine(directory/'queue-business.db')
    queue = Queue(directory/'queue.db')
    ids = []
    for i in range(jobs):
        scenario = ['normal','conflict','tool_failure'][i % 3]
        task = engine.new_demo(scenario)
        ids.append(task['id'])
        queue.enqueue(task['id'])
        queue.enqueue(task['id'])  # Duplicate enqueue must not duplicate jobs/effects.
    start = time.monotonic()
    def work():
        local = Engine(directory/'queue-business.db')
        while time.monotonic()-start < 45:
            if queue.work_one(local):
                continue
            if all(r['status'] in {'DONE','DEAD'} for r in queue.rows()):
                return
            time.sleep(0.02)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda _: work(), range(workers)))
    elapsed = time.monotonic()-start
    rows = queue.rows()
    latencies = sorted((r['ended_at']-r['created_at'])*1000 for r in rows if r['ended_at'])
    outputs = [engine.get(task) for task in ids]
    with engine.connect() as c:
        execution_count = c.execute('SELECT COUNT(*) FROM executions').fetchone()[0]
    expected_exec = sum(i % 3 != 1 for i in range(jobs))
    completed = sum(t['state']=='COMPLETED' for t in outputs)
    human = sum(t['state']=='WAITING_HUMAN' for t in outputs)
    done = sum(r['status']=='DONE' for r in rows)
    unique_effects = execution_count == expected_exec
    ledger_ok = all(t['order']['facts']['stock'] == (2 if t['state']=='WAITING_HUMAN' else 1) for t in outputs)
    return dict(jobs=jobs, workers=workers, planner='rule-fixture-1', paid_api_calls=0,
                elapsed_seconds=round(elapsed,3), done=done, dead=sum(r['status']=='DEAD' for r in rows),
                completed_tasks=completed, human_handoffs=human, unique_executions=execution_count,
                expected_executions=expected_exec, ledger_ok=ledger_ok, jobs_per_second=round(done/elapsed,2),
                enqueue_to_done_p95_ms=round(latencies[max(0,math.ceil(len(latencies)*.95)-1)],2) if latencies else None,
                passed=len(rows)==jobs and done==jobs and completed==expected_exec and unique_effects and ledger_ok,
                limitation='Local SQLite rule-based worker test, not public HTTP load or LLM throughput; latency includes pre-start backlog creation.')


def read_key():
    key = os.environ.get('DEEPSEEK_API_KEY','').strip()
    if not key:
        env = ROOT.parent/'model-experiment'/'.env'
        if env.exists():
            for line in env.read_text(encoding='utf-8-sig').splitlines():
                if line.startswith('DEEPSEEK_API_KEY='):
                    key = line.split('=',1)[1].strip().strip('\"\'')
    if not key:
        raise ValueError('Missing local DEEPSEEK_API_KEY; never paste a key into chat')
    return key


def model_report(directory, direct, prompt_version='v1', replay=None):
    planner = RagPlanner(read_key(), direct, prompt_version)
    engine = Engine(directory/'model-business.db', planner)
    outputs = []
    for index, scenario in enumerate(['normal','conflict','tool_failure']):
        task = engine.new_demo(scenario)
        if replay:
            # Exact prior model input; expected labels never enter the prompt.
            facts = replay['model']['records'][index]['input']
            with engine.connect() as c:
                c.execute('UPDATE orders SET facts=? WHERE id=?',
                          (json.dumps(facts,ensure_ascii=False),task['order_id']))
        result = engine.run(task['id'])
        first_state = result['state']
        # Only reconcile when a receipt exists: no additional model retry or paid call.
        if result['state']=='RETRYABLE' and result['receipt']:
            result = engine.run(task['id'])
        expected_state = 'WAITING_HUMAN' if scenario=='conflict' else 'COMPLETED'
        expected_action = 'human' if scenario=='conflict' else 'replace'
        outputs.append(dict(scenario=scenario, first_state=first_state, expected_state=expected_state,
                            passed=result['state']==expected_state and bool(result['plan']) and
                                   result['plan']['action']==expected_action,
                            task=result))
        if planner.records[-1]['status']=='error':
            break  # Stop rather than repeatedly charge on upstream failure.
    calls = sum(r['status']!='retrieval_empty' for r in planner.records)
    return dict(attempted_calls=calls, max_calls=3, passed=sum(o['passed'] for o in outputs),
                completed_cases=len(outputs), records=planner.records, outputs=outputs,
                limitation='Three synthetic integration smoke cases; not independent model-quality evaluation or proof of RAG uplift.')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--direct',action='store_true')
    parser.add_argument('--jobs',type=int,default=60)
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--prompt',choices=['v1','v2'],default='v1')
    parser.add_argument('--replay',type=Path)
    args=parser.parse_args()
    if not 3<=args.jobs<=200 or not 1<=args.workers<=16:
        parser.error('jobs must be 3..200; workers 1..16')
    directory=ROOT/'data'/('poc-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    directory.mkdir(parents=True)
    report=dict(scope='synthetic technical POC, not business validation',created_at=datetime.now(timezone.utc).isoformat(),
                environment=dict(python=platform.python_version(),os=platform.system()),
                retrieval=retrieval_report(),queue=queue_report(directory,args.jobs,args.workers))
    if args.live:
        replay=json.loads(args.replay.read_text(encoding='utf-8')) if args.replay else None
        report['model']=model_report(directory,args.direct,args.prompt,replay)
    else:
        report['model']=dict(status='not_run',paid_api_calls=0)
    (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(retrieval_hit_at_3=report['retrieval']['hit_at_3'],queue=report['queue'],
                          model={k:v for k,v in report['model'].items() if k not in {'records','outputs'}},
                          report=str(directory/'report.json')),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
