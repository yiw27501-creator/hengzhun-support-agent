"""Two paid understanding calls through real HTTP and durable order outbox.
The final execution planner is explicitly deterministic, not a paid model.
"""
import argparse
import json
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from engine import Engine
from runtime import Runtime,load_local_key
from server import handler_for

def main():
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');p.add_argument('--clarification',action='store_true');args=p.parse_args()
    if not args.live:print('Dry run: at most 2 paid understanding calls; rule-based sandbox execution');return
    root=Path(__file__).parent
    with tempfile.TemporaryDirectory() as tmp:
        engine=Engine(Path(tmp)/'engine.db')
        runtime=Runtime(engine.database,Path(tmp)/'queue.db',load_local_key(root.parent/'model-experiment/.env'),True,2,'split')
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(engine,'smoke-sandbox-token-at-least-24',runtime=runtime))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();runtime.start()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def call(path,body=None):
            req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/workflow/'+path,
                data=json.dumps(body).encode() if body is not None else None,
                headers={'Authorization':'LOCAL_TEST_TOKEN_PLACEHOLDER_000000','Content-Type':'application/json'})
            with opener.open(req,timeout=10) as r:return json.load(r)
        def wait(task):
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                result=call('tasks/'+task)
                if result['understanding'] and result['understanding']['state'] in {'REVIEW','FAILED'}:return result
                time.sleep(.1)
            raise RuntimeError('Understanding timeout')
        try:
            task=call('demo',{'scenario':'normal'})['id']
            call('tasks/'+task+'/messages',dict(message_id='pause',text='帮我处理一下这个订单，我还没有说明想怎么处理。' if args.clarification else '我原本想申请退款，但现在先别操作，我再想想。',expected_version=0))
            first=wait(task);ex=first['understanding']['result']
            assert ex['valid'] and ex['extraction']['dialogue_state']==('clarify' if args.clarification else 'paused')
            assert first['receipt'] is None and first['order']['facts']['authorized_version'] is None
            reply=dict(message_id='resume',text='我已经想好了，现在确认申请退款。',expected_version=1)
            if args.clarification:
                assert first['clarification']
                reply['reply_to']=first['clarification']['id']
            call('tasks/'+task+'/messages',reply)
            second=wait(task);ex=second['understanding']['result']
            assert ex['valid'] and ex['extraction']['intent']=='get_refund' and ex['extraction']['dialogue_state']=='ready'
            assert second['order']['facts']['authorized_version'] is None
            call('tasks/'+task+'/confirm',dict(intent='refund',source_message_id='resume',expected_version=2,note='沙箱操作者核对最新原话'))
            completed=engine.run(task)  # Explicitly rule planner: do not hide a third paid call.
            assert completed['receipt']['action']=='refund'
            report=dict(passed=True,clarification=args.clarification,first=first,second=second,completed=completed,health=runtime.health(),execution_planner='rule-fixture-1')
            output=root/'data'/('order-understanding-smoke-'+str(time.time_ns())+'.json')
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(dict(passed=True,calls=runtime.health()['reserved_calls'],report=str(output))),flush=True)
        finally:runtime.close();server.shutdown();server.server_close();thread.join()

if __name__=='__main__':main()
