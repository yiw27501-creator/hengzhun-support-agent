"""Real local HTTP + fixed subprocess regression; no model API calls."""
import json
import threading
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from engine import Engine
from quality import Quality
from server import handler_for


def main():
    folder = Path(__file__).parent / 'data' / ('quality-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(parents=True)
    engine = Engine(folder / 'business.db')
    token = 'local-quality-smoke-token-not-a-secret'
    service = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(engine, token, desktop=True))
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, body=None):
        req = urllib.request.Request(f'http://127.0.0.1:{service.server_port}' + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with opener.open(req) as response:
            return json.load(response)

    try:
        with opener.open(f'http://127.0.0.1:{service.server_port}/quality') as response:
            assert response.status == 200
        policy=request('/api/workflow/knowledge',dict(id='http-policy',kind='policy',title='HTTP 合成政策',text='只作测试',source='synthetic'))
        for action in ['submit','approve','publish']:
            policy=request('/api/workflow/knowledge/transition',dict(id=policy['id'],version=policy['version'],revision=policy['revision'],action=action,note='HTTP test'))
        faq=request('/api/workflow/knowledge',dict(id='http-faq',kind='faq',title='HTTP FAQ',text='只作测试',source='synthetic',policy_refs=['http-policy@1']))
        for action in ['submit','approve','publish']:
            faq=request('/api/workflow/knowledge/transition',dict(id=faq['id'],version=faq['version'],revision=faq['revision'],action=action,note='HTTP test'))
        assert len(request('/api/workflow/faq/published')['items'])==1
        request('/api/workflow/knowledge/transition',dict(id=policy['id'],version=policy['version'],revision=policy['revision'],action='retire',note='test dependency revocation'))
        assert request('/api/workflow/faq/published')['items']==[]
        case = request('/api/workflow/quality/cases', dict(title='Windows SQLite handle cleanup',
            expected='Temporary database can be cleaned after use', actual='Initial six quality tests failed during cleanup with WinError32',
            evidence='test_quality.py; first local regression on 2026-09-16', source='local_trace'))
        endpoint = '/api/workflow/quality/cases/' + case['id']
        case = request(endpoint, dict(revision=0, action='triage', cause='tool', priority='P1',
            safety=False, owner='local-operator', rationale='sqlite context commits but does not close connection'))
        case = request(endpoint, dict(revision=1, action='fix', change_ref='quality.py: Quality.connect',
            hypothesis='Close connection in finally after transaction completion'))
        report = Quality(folder / 'business.quality.db').regress(case['id'])
        assert report['passed'], report
        case = request(endpoint, dict(revision=2, action='decide', decision='release',run_id=report['id'],
            acceptance_evidence='Current fixed engineering suite passed; local HTTP workflow completed',
            note='Record local engineering acceptance only, no cloud deployment or model release'))
        assert case['state'] == 'RELEASE_RECORDED'
        result = dict(case=case, regression=report, http=True, knowledge_publish_revoke_http=True, paid_model_calls=0,
                      limitation='No visual UI QA; no production release; no model-quality claim')
        (folder / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(report=str(folder / 'report.json'), passed=True, model_calls=0)))
    finally:
        service.shutdown()
        service.server_close()
        thread.join()


if __name__ == '__main__':
    main()
