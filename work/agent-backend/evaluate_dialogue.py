import argparse
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
from conversation import DialogueExtractor
from runtime import load_local_key

ROOT=Path(__file__).parent
def main():
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');p.add_argument('--direct',action='store_true');p.add_argument('--split',choices=['dev','test'],default='dev');args=p.parse_args()
    source=ROOT/'dialogue_cases.json';cases=[c for c in json.loads(source.read_text(encoding='utf-8')) if c['split']==args.split]
    if not args.live:print(f'Dry run: {len(cases)} cases; no model requests');return
    extractor=DialogueExtractor(load_local_key(ROOT.parent/'model-experiment'/'.env'),args.direct)
    folder=ROOT/'data'/('dialogue-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));folder.mkdir(parents=True)
    report=dict(split=args.split,dataset_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),records=[])
    for c in cases:
        try:
            output=extractor.extract(c['messages'])
            evidence=output['extraction'].get('evidence',[])
            passed=output['valid'] and output['extraction']['intent']==c['expected'] and (c['evidence_id'] is None or any(e.get('message_id')==c['evidence_id'] for e in evidence))
            report['records'].append(dict(case=c,output=output,passed=passed))
        except Exception as e:
            report['records'].append(dict(case_id=c['id'],error_type=type(e).__name__,passed=False));break
    report['passed']=sum(r['passed'] for r in report['records']);report['attempted']=len(report['records'])
    (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'attempted':report['attempted'],'report':str(folder/'report.json')}))
if __name__=='__main__':main()
