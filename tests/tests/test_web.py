import http.client
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import server
from agent.memory import Memory


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'AGENT_DB': self.tmp.name+'/t.db', 'AGENT_USER': 'web-test', 'LLM_API_KEY': ''})
        self.env.start()
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); self.env.stop(); self.tmp.cleanup()

    def request(self, path, data=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.srv.server_port)
        hs = {'Content-Type':'application/json', 'X-Local-Token':server.TOKEN}
        hs.update(headers or {})
        c.request('POST' if data is not None else 'GET', path, json.dumps(data) if data is not None else None, hs)
        r=c.getresponse(); status=r.status; body=r.read(); resp_headers=dict(r.getheaders()); c.close()
        return status,body,resp_headers

    def test_static_and_csp(self):
        status, body, headers=self.request('/')
        self.assertEqual(status,200); self.assertIn(b'Search & Recall',body)
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.assertEqual(self.request('/../server.py')[0],404)

    def test_config_never_returns_key(self):
        with patch.dict(os.environ,{'LLM_API_KEY':'secret-test-do-not-return'}):
            status,body,_=self.request('/api/config')
            self.assertEqual(status,200); self.assertNotIn(b'secret-test',body)
            self.assertTrue(json.loads(body)['model_configured'])

    def test_missing_configuration_and_validation(self):
        self.assertEqual(self.request('/api/chat',{'question':'hi','session':'abc'})[0],503)
        self.assertEqual(self.request('/api/chat',{'question':'','session':'abc'})[0],400)
        self.assertEqual(self.request('/api/chat',{'question':'hi','session':'../bad'})[0],400)

    def test_csrf_and_host(self):
        self.assertEqual(self.request('/api/chat',{}, {'X-Local-Token':'bad'})[0],403)
        self.assertEqual(self.request('/api/chat',{}, {'Origin':'http://evil.invalid'})[0],403)
        self.assertEqual(self.request('/api/config',headers={'Host':'evil.invalid'})[0],403)

    def test_scoped_persistent_history(self):
        m=Memory(server.db_path());m.add('web-test','old','user','my earlier question');m.add('other','hidden','user','private');m.db.close()
        data=json.loads(self.request('/api/sessions')[1]);self.assertEqual(len(data['sessions']),1)
        self.assertEqual(data['sessions'][0]['session'],'old')
        msgs=json.loads(self.request('/api/session/old')[1]);self.assertEqual(msgs['messages'][0]['content'],'my earlier question')
        self.assertEqual(json.loads(self.request('/api/session/hidden')[1])['messages'],[])

    def test_busy_and_real_agent_control_flow_with_stub(self):
        with patch.dict(os.environ,{'LLM_API_KEY':'test'}):
            server.LOCK.acquire()
            try:self.assertEqual(self.request('/api/chat',{'question':'hi','session':'s'})[0],409)
            finally:server.LOCK.release()
            with patch('agent.core.LLM.chat',return_value={'role':'assistant','content':'controlled test response'}):
                status,body,_=self.request('/api/chat',{'question':'test question','session':'s'})
            self.assertEqual(status,200);self.assertEqual(json.loads(body)['answer'],'controlled test response')
            self.assertEqual(len(json.loads(self.request('/api/session/s')[1])['messages']),2)
