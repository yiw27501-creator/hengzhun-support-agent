"""Loopback-only demo API. Never expose this development server to the internet."""
import argparse
import hmac
import json
import os
import secrets
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from engine import Conflict, DeepSeekPlanner, Engine
from runtime import Runtime, load_local_key
from faq import catalog
from quality import Quality
from knowledge import Knowledge
from business import Business
from business_chat import BusinessChat, Router
from operations import Operations


def handler_for(engine, token, desktop=False, runtime=None):
    quality = Quality(Path(engine.database).with_suffix('.quality.db'))
    knowledge = Knowledge(Path(engine.database).with_suffix('.knowledge.db'))
    business = Business(engine,runtime)
    business_chat = BusinessChat(engine,runtime)
    operations = Operations(Path(engine.database).with_suffix('.operations.db'))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # No accidental token, customer text or request-body logging.

        def reply(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def handle_api(self):
            expected_host = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != expected_host:
                return self.reply(403, {'error': 'host_not_allowed'})
            origin = self.headers.get('Origin')
            if origin and (not desktop or origin != 'http://' + expected_host):
                return self.reply(403, {'error': 'browser_origin_not_allowed'})
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.reply(403, {'error': 'cross_site_not_allowed'})
            assets = {'/': 'workbench.html', '/workbench.html': 'workbench.html',
                      '/workbench.js': 'workbench.js', '/workbench.css': 'workbench.css',
                      '/index.html': 'index.html', '/app.js': 'app.js', '/styles.css': 'styles.css'}
            local_assets = {'/':'home.html','/customer':'customer.html','/customer.js':'customer.js','/lab':'lab.html','/lab.html':'lab.html','/lab.js':'lab.js',
                            '/quality':'quality.html','/quality.html':'quality.html','/quality.js':'quality.js',
                            '/knowledge':'knowledge.html','/knowledge.js':'knowledge.js','/console.css':'console.css'}
            business_assets={'/service':'index.html','/service/app.js':'app.js','/service/style.css':'style.css',
                             '/operations':'operations.html','/service/operations.js':'operations.js'}
            if desktop and self.command=='GET' and self.path in business_assets:
                file=Path(__file__).parent.parent/'hengzhun-business'/business_assets[self.path]
                data=file.read_bytes();self.send_response(200)
                self.send_header('Content-Type',{'.html':'text/html','.js':'text/javascript','.css':'text/css'}[file.suffix]+'; charset=utf-8')
                self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)))
                self.end_headers();return self.wfile.write(data)
            if desktop and self.command == 'GET' and self.path in local_assets:
                name = local_assets[self.path]
                data = Path(__file__).with_name(name).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', {'.js':'text/javascript','.css':'text/css','.html':'text/html'}[Path(name).suffix]+'; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                return self.wfile.write(data)
            if desktop and self.command == 'GET' and self.path in assets:
                file = Path(__file__).parent.parent / 'support-agent' / assets[self.path]
                if not file.is_file():
                    return self.reply(404, {'error': 'asset_not_found'})
                data = file.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', {'.html': 'text/html', '.js': 'text/javascript',
                                 '.css': 'text/css'}[file.suffix] + '; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                return self.wfile.write(data)
            if desktop and self.command == 'POST' and self.path == '/api/workflow/session':
                if origin != 'http://' + expected_host or self.headers.get('X-Workbench') != '1':
                    return self.reply(403, {'error': 'same_origin_required'})
                self.send_response(200)
                self.send_header('Set-Cookie', f'hengzhun_session={token}; HttpOnly; SameSite=Strict; Path=/api/workflow; Max-Age=28800')
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return self.wfile.write(b'{"environment":"local-sandbox"}')
            supplied = self.headers.get('Authorization', '')
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get('Cookie', ''))
            except Exception:
                pass
            session = cookie.get('hengzhun_session')
            cookie_ok = desktop and session and hmac.compare_digest(session.value.encode(), token.encode())
            if not cookie_ok and not hmac.compare_digest(supplied.encode(), ('Bearer ' + token).encode()):
                return self.reply(401, {'error': 'unauthorized'})
            try:
                parts = self.path.strip('/').split('/')
                if parts[:2] == ['api', 'workflow']:
                    parts = parts[2:]
                if self.command == 'GET' and parts == ['health']:
                    return self.reply(200, dict(environment='sandbox', planner=engine.planner.version,
                                               async_runtime=runtime.health() if runtime else None))
                if self.command == 'GET' and len(parts)==3 and parts[0]=='tasks' and parts[2]=='job' and runtime:
                    engine.get(parts[1])
                    return self.reply(200, dict(job=runtime.queue.get(parts[1])))
                if self.command == 'GET' and parts == ['tasks']:
                    return self.reply(200, dict(tasks=engine.list_tasks()))
                if self.command == 'GET' and parts == ['quality']:
                    return self.reply(200, quality.list())
                if self.command=='GET' and parts==['operations']:
                    return self.reply(200,operations.report())
                if self.command=='GET' and len(parts)==2 and parts[0]=='operations':
                    return self.reply(200,operations.report(parts[1]))
                if self.command=='GET' and parts==['business']:
                    return self.reply(200,business.list())
                if self.command=='GET' and len(parts)==2 and parts[0]=='business':
                    return self.reply(200,business.detail(parts[1]))
                if self.command == 'GET' and parts == ['quality','metrics']:
                    return self.reply(200,quality.metrics())
                if self.command == 'GET' and parts == ['quality','safety-cases']:
                    return self.reply(200,json.loads(Path(__file__).with_name('safety_cases.json').read_text(encoding='utf-8')))
                if self.command == 'GET' and parts == ['knowledge']:
                    return self.reply(200,dict(items=knowledge.items(),active=knowledge.active()))
                if self.command == 'GET' and parts == ['faq','published']:
                    return self.reply(200,dict(items=[d for d in knowledge.active() if d['kind']=='faq']))
                if self.command == 'GET' and parts == ['faq']:
                    return self.reply(200,catalog(Path(__file__).parent/'data'/'bitext-corpus-20260915'/'support.db'))
                if self.command == 'GET' and len(parts)==2 and parts[0]=='dialogues' and runtime:
                    return self.reply(200,runtime.intake.get(parts[1]))
                if self.command == 'GET' and len(parts) == 2 and parts[0] == 'tasks':
                    return self.reply(200, engine.get(parts[1]))
                if self.command != 'POST':
                    return self.reply(404, {'error': 'not_found'})
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384:
                    return self.reply(400, {'error': 'body_size_invalid'})
                self.connection.settimeout(5)
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('JSON object required')
                if parts==['business','batch']:
                    return self.reply(201,business.batch(**body))
                if parts==['operations','register-errors']:
                    if set(body)!={'dataset_id'}:raise ValueError('dataset_id required')
                    return self.reply(200,operations.register_errors(quality,body['dataset_id']))
                if len(parts)==3 and parts[0]=='business' and parts[2]=='chat':
                    return self.reply(200,business_chat.submit(parts[1],**body))
                if len(parts)==3 and parts[0]=='business' and parts[2]=='advance':
                    return self.reply(200,business.advance(parts[1],**body))
                if parts == ['quality', 'cases']:
                    return self.reply(201, quality.collect(**body))
                if parts == ['knowledge']:
                    return self.reply(201,knowledge.create(**body))
                if parts == ['knowledge','import']:
                    if body:raise ValueError('Empty body required')
                    return self.reply(201,knowledge.import_drafts())
                if parts == ['knowledge','transition']:
                    return self.reply(200,knowledge.transition(**body))
                if parts == ['quality','protocol']:
                    return self.reply(201,quality.protocol(**body))
                if parts == ['quality','evaluate']:
                    return self.reply(201,quality.evaluate(**body))
                if len(parts) == 3 and parts[:2] == ['quality', 'cases']:
                    return self.reply(200, quality.advance(parts[2], **body))
                if parts == ['dialogues'] and runtime:
                    if set(body)!={'event_id','messages'}:raise ValueError('event_id and messages required')
                    return self.reply(202,runtime.intake.submit(**body))
                if parts == ['demo']:
                    if set(body) != {'scenario'} or not isinstance(body['scenario'], str):
                        raise ValueError('scenario required')
                    return self.reply(201, engine.new_demo(body['scenario']))
                if parts == ['tasks']:
                    if set(body) != {'event_id', 'order_id'}:
                        raise ValueError('Only event_id and order_id accepted')
                    return self.reply(201, engine.create(**body))
                if len(parts) == 3 and parts[0] == 'tasks':
                    if parts[2]=='messages':
                        if set(body) not in ({'message_id','text','expected_version'},{'message_id','text','expected_version','reply_to'}):
                            raise ValueError('message_id, text and expected_version required')
                        return self.reply(201,engine.append_message(parts[1],**body))
                    if parts[2]=='enqueue' and runtime:
                        if body:
                            raise ValueError('empty body required')
                        task=engine.get(parts[1])
                        if task['state'] not in {'READY','RETRYABLE'}:
                            raise Conflict('Only READY or RETRYABLE tasks can be queued')
                        if runtime.key and runtime.health()['remaining_calls']==0 and not task['receipt']:
                            return self.reply(429, {'error':'model_call_budget_exhausted'})
                        existing=runtime.queue.get(parts[1])
                        if existing and existing['status']=='DEAD':
                            raise Conflict('Dead job requires investigation, not blind resubmission')
                        runtime.queue.resume(parts[1])
                        return self.reply(202, dict(task_id=parts[1],job=runtime.queue.get(parts[1])))
                    if parts[2] == 'run':
                        if runtime:
                            raise Conflict('Async mode requires enqueue; direct execution is disabled')
                        if body:
                            raise ValueError('run body must be empty object')
                        return self.reply(200, engine.run(parts[1]))
                    if parts[2] == 'confirm':
                        job=runtime.queue.get(parts[1]) if runtime else None
                        if job and job['status'] in {'RUNNING','QUEUED'}:
                            raise Conflict('Wait for the active job before confirming')
                        if set(body) not in ({'intent', 'source_message_id', 'note'}, {'intent', 'source_message_id', 'note','expected_version'}):
                            raise ValueError('intent, source_message_id and note required')
                        return self.reply(200, engine.confirm(parts[1], actor='local-operator', **body))
                self.reply(404, {'error': 'not_found'})
            except (ValueError, TypeError):
                self.reply(400, {'error': 'invalid_request'})
            except KeyError:
                self.reply(404, {'error': 'not_found'})
            except Conflict as exc:
                self.reply(409, {'error': str(exc)})
            except Exception:
                self.reply(500, {'error': 'internal_error'})

        do_GET = handle_api
        do_POST = handle_api
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', default=str(Path(__file__).parent / 'data' / 'sandbox.db'))
    parser.add_argument('--seed', action='store_true')
    parser.add_argument('--planner', choices=['rule', 'deepseek', 'rag'], default='rule')
    parser.add_argument('--async-jobs',action='store_true')
    parser.add_argument('--dialogue-router',choices=['legacy','split'],default='legacy')
    parser.add_argument('--max-model-calls',type=int,default=3)
    parser.add_argument('--key-file',type=Path)
    parser.add_argument('--business-model',action='store_true',help='Enable bounded real-model shortage conversation; execution remains rule-based')
    parser.add_argument('--direct',action='store_true')
    parser.add_argument('--desktop', action='store_true', help='Serve same-origin local workbench with a sandbox session')
    args = parser.parse_args()
    if args.business_model and (not args.async_jobs or args.planner!='rule'):
        parser.error('business-model requires async-jobs and rule execution planner')
    if not 1 <= args.max_model_calls <= 20:
        parser.error('max-model-calls must be 1..20')
    if args.planner=='rag' and not args.async_jobs:
        parser.error('rag requires --async-jobs for bounded execution')
    if args.async_jobs and args.planner=='deepseek':
        parser.error('Use rag for budget-limited live asynchronous requests')
    token = os.environ.get('SANDBOX_API_TOKEN', '')
    if args.desktop and not token:
        token = secrets.token_urlsafe(32)
    if len(token) < 24 or not token.isascii():
        parser.error('Set a random ASCII SANDBOX_API_TOKEN of at least 24 characters locally')
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    engine = Engine(args.db, DeepSeekPlanner() if args.planner == 'deepseek' else None)
    runtime = None
    if args.async_jobs:
        runtime=Runtime(args.db,str(Path(args.db).with_suffix('.queue.db')),
                        load_local_key(args.key_file) if args.planner=='rag' else None,
                        args.direct,args.max_model_calls,args.dialogue_router)
        if args.business_model:
            runtime.business_router=Router(load_local_key(args.key_file),args.direct)
        runtime.start()
    if args.seed:
        engine.seed()
    service = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(engine, token, args.desktop,runtime))
    print(f'Sandbox API: http://127.0.0.1:{args.port}; planner={engine.planner.version}', flush=True)
    try:
        service.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.server_close()
        if runtime:
            runtime.close()


if __name__ == '__main__':
    main()
