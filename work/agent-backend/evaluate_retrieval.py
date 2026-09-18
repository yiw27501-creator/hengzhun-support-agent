"""Offline retrieval diagnostic; relevance is synthetic, not externally annotated."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from rag import Retriever

ROOT = Path(__file__).parent

def evaluate(cases, retriever):
    rows = []
    for c in cases:
        ids = [d['id'] for d in retriever.retrieve(c['query'])]
        relevant = set(c['relevant'])
        rows.append(dict(c, retrieved=ids,
                         recall_at_3=len(relevant.intersection(ids))/len(relevant) if relevant else None,
                         reciprocal_rank=next((1/(i+1) for i, doc in enumerate(ids) if doc in relevant), 0) if relevant else None,
                         unexpected_candidates=bool(ids) if not relevant else None))
    known = [r for r in rows if r['relevant']]
    unknown = [r for r in rows if not r['relevant']]
    return dict(records=rows, metrics=dict(
        answerable_count=len(known), unanswerable_count=len(unknown),
        macro_recall_at_3=sum(r['recall_at_3'] for r in known)/len(known) if known else None,
        mrr_at_3=sum(r['reciprocal_rank'] for r in known)/len(known) if known else None,
        unanswerable_candidate_rate=sum(r['unexpected_candidates'] for r in unknown)/len(unknown) if unknown else None),
        limitation='Nonempty candidates are not proof of answerability; candidate rate is not a hallucination metric.')

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--split', choices=['dev','test'], default='dev')
    args=p.parse_args()
    source=ROOT/'rag_eval_cases.json'
    cases=[c for c in json.loads(source.read_text(encoding='utf-8')) if c['split']==args.split]
    retriever=Retriever()
    report=evaluate(cases,retriever)
    report.update(split=args.split, dataset_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), corpus_sha256=retriever.corpus_hash)
    folder=ROOT/'data'/('retrieval-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(parents=True)
    path=folder/'report.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(metrics=report['metrics'],report=str(path))))

if __name__=='__main__':main()
