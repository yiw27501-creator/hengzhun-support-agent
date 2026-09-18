"""Explicit paid one-call HTTP -> queue -> RAG -> model -> sandbox integration check."""
import argparse
import json
import secrets
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from engine import Engine
from runtime import Runtime,load_local_key
from server import handler_for


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--direct',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).parent
    directory=root/'data'/('http-smoke-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    directory.mkdir(parents=True)
    engine=Engine(directory/'business.db')
    runtime=Runtime(directory/'business.db',directory/'queue.db',
                    load_local_key(root.parent/'model-experiment'/'.env') if args.live else None,
                    args.direct,max_calls=1)
    token=secrets.token_urlsafe(32)
    service=ThreadingHTTPServer(('127.0.0.1',0),handler_for(engine,token,runtime=runtime))
    thread=threading.Thread(target=service.serve_forever,daemon=True)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(path,body=None):
        request=urllib.request.Request(f'http://127.0.0.1:{service.server_port}/api/workflow/'+path,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with opener.open(request,timeout=35) as response:return response.status,json.load(response)
    thread.start()
    try:
        _,task=api('demo',{'scenario':'normal'})
        code,accepted=api('tasks/'+task['id']+'/enqueue',{})
        # Check immediate acceptance before any consumer starts.
        assert code==202 and accepted['job']['status']=='QUEUED'
        runtime.start()
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            _,job=api('tasks/'+task['id']+'/job')
            if job['job']['status'] in {'DONE','DEAD'}:break
            time.sleep(.1)
        _,task=api('tasks/'+task['id'])
        report=dict(live=args.live,accepted_http_status=code,job=job['job'],task=task,budget=runtime.health(),
                    passed=task['state']=='COMPLETED' and task['order']['facts']['stock']==1 and job['job']['status']=='DONE')
        (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k not in {'task','job'}},ensure_ascii=False,indent=2))
        print('Report:',directory/'report.json')
    finally:
        service.shutdown();service.server_close();thread.join();runtime.close()


if __name__=='__main__':main()
