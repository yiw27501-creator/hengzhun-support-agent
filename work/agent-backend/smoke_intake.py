"""HTTP -> SQL trigger -> background understanding smoke, one optional paid call."""
import argparse
import json
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from engine import Engine
from runtime import Runtime, load_local_key
from server import handler_for

def main():
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');args=p.parse_args()
    root=Path(__file__).parent
    with tempfile.TemporaryDirectory() as folder:
        db=Path(folder)/'engine.db';engine=Engine(db)
        runtime=Runtime(db,Path(folder)/'queue.db',load_local_key(root.parent/'model-experiment'/'.env') if args.live else None,True,1)
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(engine,'test-token-is-at-least-24-characters',runtime=runtime))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def call(path,body=None):
            request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/workflow/'+path,
                data=json.dumps(body).encode() if body is not None else None,
                headers={'Authorization':'LOCAL_TEST_TOKEN_PLACEHOLDER_000000','Content-Type':'application/json'})
            with opener.open(request,timeout=10) as response:return response.status,json.load(response)
        try:
            code,queued=call('dialogues',dict(event_id='smoke-event',messages=[dict(id='m1',role='customer',text='暂时别退款，我还没想好。')]))
            assert code==202 and queued['job']['status']=='QUEUED'
            runtime.start()
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                _,result=call('dialogues/smoke-event')
                if result['job']['status'] in {'DONE','DEAD'}:break
                time.sleep(.1)
            assert result['job']['status']=='DONE', result['job']['status']
            assert result['execution_authorized'] is False
            if args.live:assert result['result']['valid'] and result['result']['extraction']['intent']=='undecided'
            report=dict(live=args.live,http_accepted=code,result=result,budget=runtime.health())
            target=root/'data'/('intake-smoke-'+str(time.time_ns())+'.json');target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(dict(passed=True,report=str(target),reserved=runtime.health()['reserved_calls'])))
        finally:
            runtime.close();server.shutdown();server.server_close();thread.join()

if __name__=='__main__':main()
