import json
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch
from pathlib import Path
from http.server import ThreadingHTTPServer
from engine import Engine
from runtime import Runtime
from server import handler_for


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.engine=Engine(self.root/'business.db')
        self.runtime=Runtime(self.root/'business.db',self.root/'queue.db')

    def wait_done(self, task):
        end=time.monotonic()+5
        while time.monotonic()<end:
            job=self.runtime.queue.get(task)
            if job and job['status'] in {'DONE','DEAD'}:return job
            time.sleep(.02)
        self.fail('Queue did not finish')

    def test_background_execution_and_confirmation_resume(self):
        self.runtime.start();self.addCleanup(self.runtime.close)
        task=self.engine.new_demo('conflict')['id']
        self.runtime.queue.enqueue(task);self.wait_done(task)
        self.assertEqual(self.engine.get(task)['state'],'WAITING_HUMAN')
        self.engine.confirm(task,'refund','tester',self.engine.get(task)['order']['facts']['message_id'],'核对')
        self.runtime.queue.resume(task);self.wait_done(task)
        self.assertEqual(self.engine.get(task)['state'],'COMPLETED')

    def test_budget_persists_and_atomic_reservation(self):
        self.runtime.max_calls=1
        self.runtime.reserve()
        other=Runtime(self.root/'business.db',self.root/'queue.db',max_calls=1)
        with self.assertRaises(RuntimeError):other.reserve()
        self.assertEqual(other.health()['reserved_calls'],1)

    def test_outbox_failure_does_not_starve_execution(self):
        task=self.engine.new_demo('normal')['id']
        self.runtime.queue.enqueue(task)
        with patch.object(Engine,'dispatch_understanding',side_effect=ValueError('private diagnostic')):
            self.runtime.start()
            try:self.assertEqual(self.wait_done(task)['status'],'DONE')
            finally:self.runtime.close()
        self.assertEqual(self.engine.get(task)['state'],'COMPLETED')
        self.assertEqual(self.runtime.health()['worker_errors']['dispatch']['error_type'],'ValueError')
        self.assertNotIn('private diagnostic',json.dumps(self.runtime.health()))

    def test_http_returns_accepted_before_worker_runs(self):
        token='test-runtime-authorization-token'
        service=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.engine,token,runtime=self.runtime))
        thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
        def close():service.shutdown();service.server_close();thread.join()
        self.addCleanup(close)
        task=self.engine.new_demo('normal')['id']
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request=urllib.request.Request(f'http://127.0.0.1:{service.server_port}/api/workflow/tasks/{task}/enqueue',
                                      data=b'{}',headers={'Authorization':'Bearer '+token})
        with opener.open(request) as response:
            self.assertEqual(response.status,202)
            self.assertEqual(json.load(response)['job']['status'],'QUEUED')
        self.assertEqual(self.engine.get(task)['state'],'READY')


if __name__=='__main__':unittest.main()
