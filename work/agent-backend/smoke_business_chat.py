"""Explicit live HTTP smoke: at most two paid calls with synthetic data only."""
import argparse
import json
import secrets
import threading
import time
import urllib.request
from datetime import datetime,timezone
from pathlib import Path
from http.server import ThreadingHTTPServer
from engine import Engine
from runtime import Runtime,load_local_key
from business_chat import Router
from server import handler_for

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');parser.add_argument('--key-file',type=Path);args=parser.parse_args()
    if not args.live:print('Dry run. --live performs at most two paid synthetic calls.');return
    folder=Path('data')/('business-chat-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));folder.mkdir(parents=True)
    engine=Engine(folder/'demo.db');runtime=Runtime(folder/'demo.db',folder/'queue.db',max_calls=2)
    runtime.business_router=Router(load_local_key(args.key_file),direct=True)
    token=secrets.token_urlsafe(32)
    service=ThreadingHTTPServer(('127.0.0.1',0),handler_for(engine,token,False,runtime))
    thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start();runtime.start()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base='http://127.0.0.1:'+str(service.server_port)+'/api/workflow/'
    def request(path,body=None):
        req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with opener.open(req,timeout=40) as response:return json.load(response)
    evidence=dict(scope='real DeepSeek routing; simulated read tools; explicit customer option; rule sandbox execution',passed=False)
    try:
        request('business/batch',dict(event_id='smoke',count=6))
        task=next(x['id'] for x in request('business')['items'] if x['kind']=='stock')
        first=request('business/'+task+'/chat',dict(request_id='inventory-message',text='页面说库存不足，请先帮我查一下库存，不要直接退款。',expected_version=1))
        evidence['inventory']=first
        assert first['status']=='DONE' and first['result']['decision']['tool']=='inventory'
        assert request('business/'+task)['task']['receipt'] is None
        second=request('business/'+task+'/chat',dict(request_id='pause-message',text='刚才说可以换，现在先不要处理，我再想想。',expected_version=2))
        evidence['pause']=second
        assert second['status']=='DONE' and second['result']['decision']['tool']=='pause'
        detail=request('business/'+task)
        assert detail['task']['receipt'] is None and detail['meta']['journey']['stage']=='paused'
        request('business/'+task+'/advance',dict(action='replace',expected_version=detail['meta']['journey']['version']))
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            detail=request('business/'+task)
            if detail['task']['state']=='COMPLETED':break
            time.sleep(.2)
        evidence['receipt']=detail['task']['receipt'];evidence['task_id']=task
        assert detail['task']['receipt']['action']=='replace'
        evidence['passed']=True
    except Exception as exc:
        evidence['error_type']=type(exc).__name__
    finally:
        evidence['budget']=runtime.health();service.shutdown();service.server_close();runtime.close()
        (folder/'report.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(report=str(folder/'report.json'),passed=evidence['passed'],reserved=evidence['budget']['reserved_calls'],error_type=evidence.get('error_type')),ensure_ascii=True))
    if not evidence['passed']:raise SystemExit(1)

if __name__=='__main__':main()
