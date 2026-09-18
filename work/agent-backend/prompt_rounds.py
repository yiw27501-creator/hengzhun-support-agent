"""Small version comparison on development cases only, maximum 12 paid calls."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from conversation import DialogueExtractor, PROMPTS
from runtime import load_local_key

ROOT=Path(__file__).parent
CASES=[
 dict(id='policy-not-consent',text='我只想问退款政策，暂时不退。',expected='undecided'),
 dict(id='shipping-not-replace',text='帮我改一下收货地址。',expected='undecided'),
 dict(id='withdrawal',messages=[dict(id='m1',role='customer',text='我要退款'),dict(id='m2',role='customer',text='先不要操作，我再想想')],expected='undecided'),
 dict(id='single-option',messages=[dict(id='m1',role='agent',text='您是否确认退款，不再换货？'),dict(id='m2',role='customer',text='确认，就退款。')],expected='refund')]

def main():
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');p.add_argument('--direct',action='store_true');args=p.parse_args()
    if not args.live:print('Dry run: 3 versions x 4 development cases = at most 12 calls');return
    key=load_local_key(ROOT.parent/'model-experiment'/'.env')
    report=dict(purpose='Development comparison, not independent held-out validation',cases=CASES,rounds=[])
    folder=ROOT/'data'/('prompt-rounds-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));folder.mkdir(parents=True)
    for version in PROMPTS:
        records=[];extractor=DialogueExtractor(key,args.direct,version)
        for case in CASES:
            messages=case.get('messages') or [dict(id='m1',role='customer',text=case['text'])]
            try:
                result=extractor.extract(messages)
                passed=result['valid'] and result['extraction']['intent']==case['expected']
                records.append(dict(id=case['id'],passed=passed,result=result))
            except Exception as exc:
                records.append(dict(id=case['id'],passed=False,error_type=type(exc).__name__))
                break
        report['rounds'].append(dict(version=version,prompt=PROMPTS[version],sha256=hashlib.sha256(PROMPTS[version].encode()).hexdigest(),records=records))
        (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(version=version,passed=sum(r['passed'] for r in records),attempted=len(records))),flush=True)
        if any('error_type' in r for r in records):break
    print(str(folder/'report.json'))

if __name__=='__main__':main()
