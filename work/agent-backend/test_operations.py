import json
import tempfile
import unittest
from pathlib import Path
from operations import Operations,parse,stats
from quality import Quality

LINE='T1,out_of_stock,C1,O1,SKU-A,129.00,2026-09-17 09:24:00,closed,refund,true,false,44,true,false,\n'

class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.ops=Operations(self.root/'operations.db')

    def write(self,text):
        path=self.root/'fixture.txt';path.write_text(text,encoding='utf-8');return path

    def test_idempotent_provenance_and_no_executable_tables(self):
        path=self.write(LINE)
        first=self.ops.import_file(path);second=self.ops.import_file(path)
        self.assertEqual(first,second);self.assertEqual(self.ops.report()['metrics']['total'],1)
        with self.ops.connect() as c:
            self.assertEqual(c.execute('SELECT raw FROM ops_datasets').fetchone()[0],path.read_bytes())
            self.assertFalse(c.execute("SELECT 1 FROM sqlite_master WHERE name='orders'").fetchone())

    def test_duplicates_conflicts_and_bad_rows(self):
        p=parse((LINE+LINE+'bad,row\n').encode())
        self.assertEqual(len(p['rows']),1);self.assertEqual(len(p['duplicates']),1);self.assertEqual(len(p['rejected']),1)
        with self.assertRaises(ValueError):parse((LINE+LINE.replace(',44,',',50,')).encode())
        for source in [LINE.replace('129.00','NaN'),LINE.replace(',44,',',-1,'),LINE.replace('true,false,44','maybe,false,44')]:
            self.assertEqual(len(parse(source.encode())['rejected']),1)

    def test_metrics_do_not_hide_errors_or_claim_success(self):
        rows=parse((LINE+LINE.replace('T1,','T2,').replace('true,false,\n','true,true,wrong_refund\n')).encode())['rows']
        m=stats(rows);self.assertEqual(m['auto_flag_rate'],1);self.assertEqual(m['clean_auto_proxy_rate'],.5)
        self.assertEqual(m['error_count'],1);self.assertEqual(m['p95_seconds'],44)
        self.assertIsNone(stats([])['auto_flag_rate'])

    def test_separate_snapshots_never_pool(self):
        a=self.ops.import_file(self.write(LINE))
        b=self.ops.import_file(self.write(LINE.replace(',44,',',50,')))
        self.assertEqual(self.ops.report()['metrics']['total'],1)
        self.assertEqual(self.ops.report(a['id'])['metrics']['mean_seconds'],44)
        self.assertNotEqual(a['id'],b['id'])

    def test_error_cases_idempotent_and_untriaged(self):
        meta=self.ops.import_file(self.write(LINE.replace('true,false,\n','true,true,duplicate_execution\n')))
        q=Quality(self.root/'quality.db')
        first=self.ops.register_errors(q,meta['id']);second=self.ops.register_errors(q,meta['id'])
        self.assertEqual(first,second);cases=q.list()['cases'];self.assertEqual(len(cases),1)
        self.assertEqual(cases[0]['state'],'NEW');self.assertEqual(cases[0]['source'],'synthetic')
        self.assertNotIn('cause',cases[0]);self.assertEqual(cases[0]['suggested_priority'],'P0')

    def test_current_user_dataset_controls(self):
        path=Path(__file__).parent/'data'/'operations-source'/'user-20260917.txt'
        if not path.exists():self.skipTest('User-provided data not bundled')
        meta=self.ops.import_file(path);report=self.ops.report(meta['id'])
        self.assertEqual(meta['input_rows'],100);self.assertEqual(meta['accepted'],100)
        self.assertEqual(len(meta['rejected']),0);self.assertEqual(len(meta['duplicates']),0)
        self.assertEqual(report['rows'][0]['ticket_id'],'T20260917011')
        self.assertEqual(report['rows'][-1]['ticket_id'],'T20260917110')
        m=report['metrics']
        self.assertEqual((m['auto_flagged'],m['human_takeover'],m['error_count'],m['receipt_verified'],m['clean_auto_proxy']),(67,33,5,83,62))
        self.assertAlmostEqual(m['mean_seconds'],187.72)
        self.assertEqual(m['p95_seconds'],515)

    def test_http_auth_and_quality_registration(self):
        import secrets
        import threading
        import urllib.request
        import urllib.error
        from http.server import ThreadingHTTPServer
        from engine import Engine
        from server import handler_for
        database=self.root/'sandbox.db'
        ops=Operations(database.with_suffix('.operations.db'))
        meta=ops.import_file(self.write(LINE.replace('true,false,\n','true,true,wrong_refund\n')))
        token=secrets.token_urlsafe(32)
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(Engine(database),token,True))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:opener.open(base+'/api/workflow/operations')
            self.assertEqual(error.exception.code,401)
            req=urllib.request.Request(base+'/api/workflow/operations',headers={'Authorization':'Bearer '+token})
            with opener.open(req) as r:self.assertEqual(json.load(r)['metrics']['total'],1)
            req=urllib.request.Request(base+'/api/workflow/operations/register-errors',data=json.dumps({'dataset_id':meta['id']}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            with opener.open(req) as r:self.assertEqual(json.load(r)['count'],1)
            with opener.open(base+'/operations') as r:self.assertEqual(r.status,200)
        finally:server.shutdown();server.server_close();thread.join()
