import http.client
import os
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).parents[1]))
import gui


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.private = patch.object(gui, 'PRIVATE', Path(cls.tmp.name))
        cls.private.start()
        cls.configfile=Path(cls.tmp.name)/'config.json'
        cls.configfile.write_text(json.dumps({'organization_id':'1','network_name':'Existing','api_key_file':str(Path(cls.tmp.name)/'key'),'state_dir':str(Path(cls.tmp.name)/'state')}))
        cls.env=patch.dict(os.environ,{'APP_CONFIG':str(cls.configfile)})
        cls.env.start()
        gui.init_auth()
        cls.password = (gui.PRIVATE / 'gui-initial-password').read_text().strip()
        cls.server = gui.ThreadingHTTPServer(('127.0.0.1', 0), gui.Handler)
        cls.server.secure = False
        cls.host = '127.0.0.1:' + str(cls.server.server_port)
        cls.server.allowed_hosts = {cls.host}
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.private.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        gui.ATTEMPTS.clear();gui.SESSIONS.clear()
        salt='ab'*16
        gui.atomic(gui.PRIVATE/'gui-auth.json',json.dumps({'username':'admin','salt':salt,'hash':gui.password_hash(self.password,salt)}))

    def req(self, path, body=None, **headers):
        c = http.client.HTTPConnection(self.host)
        h = {'Origin': 'http://' + self.host, 'Content-Type': 'application/json'}
        h.update(headers)
        c.request('GET' if body is None else 'POST', path,
                  body=None if body is None else json.dumps(body), headers=h)
        r = c.getresponse()
        result = r.status, dict(r.getheaders()), r.read()
        c.close()
        return result

    def login(self):
        code, headers, _ = self.req('/api/login', {'username': 'admin', 'password': self.password})
        self.assertEqual(code, 200)
        return headers['Set-Cookie'].split(';')[0]

    def test_unauthenticated_data_blocked(self):
        self.assertEqual(self.req('/api/state')[0], 401)
        self.assertEqual(self.req('/api/prefixes.txt')[0], 401)

    def test_cross_origin_and_host_blocked(self):
        self.assertEqual(self.req('/api/login', {}, Origin='http://evil.test')[0], 403)
        self.assertEqual(self.req('/', Host='evil.test')[0], 403)

    def test_login_logout_csrf(self):
        cookie = self.login()
        self.assertEqual(self.req('/api/refresh', {}, Cookie=cookie)[0], 403)
        with patch.object(gui, 'state', return_value={'safe': True}):
            code, _, data = self.req('/api/state', Cookie=cookie)
        self.assertEqual(code, 200)
        csrf = json.loads(data)['csrf']
        self.assertEqual(self.req('/api/logout', {}, Cookie=cookie, **{'X-CSRF-Token': csrf})[0], 200)
        self.assertEqual(self.req('/api/state', Cookie=cookie)[0], 401)

    def test_login_password_not_stored_plaintext(self):
        self.assertNotIn(self.password, (gui.PRIVATE / 'gui-auth.json').read_text())
        self.assertEqual((gui.PRIVATE / 'gui-auth.json').stat().st_mode & 0o777, 0o600)

    def test_password_change_revokes_sessions_and_old_password(self):
        first=self.login();second=self.login()
        with patch.object(gui,'state',return_value={}):
            csrf=json.loads(self.req('/api/state',Cookie=first)[2])['csrf']
        new='New-test-password-2026!'
        body={'current_password':self.password,'new_password':new,'confirm_password':new}
        self.assertEqual(self.req('/api/password',body,Cookie=first)[0],403)
        wrong=dict(body,current_password='incorrect')
        self.assertEqual(self.req('/api/password',wrong,Cookie=first,**{'X-CSRF-Token':csrf})[0],400)
        self.assertEqual(self.req('/api/password',body,Cookie=first,**{'X-CSRF-Token':csrf})[0],200)
        self.assertEqual(self.req('/api/state',Cookie=second)[0],401)
        self.assertEqual(self.req('/api/state',Cookie=first)[0],401)
        self.assertFalse((gui.PRIVATE/'gui-initial-password').exists())
        self.assertNotIn(new,(gui.PRIVATE/'gui-auth.json').read_text())
        self.assertEqual(self.req('/api/login',{'username':'admin','password':self.password})[0],401)
        self.assertEqual(self.req('/api/login',{'username':'admin','password':new})[0],200)

    def test_password_policy_and_throttling(self):
        cookie=self.login()
        with patch.object(gui,'state',return_value={}):
            csrf=json.loads(self.req('/api/state',Cookie=cookie)[2])['csrf']
        headers={'Cookie':cookie,'X-CSRF-Token':csrf}
        self.assertEqual(self.req('/api/password',{'current_password':self.password,'new_password':'short','confirm_password':'short'},**headers)[0],400)
        body={'current_password':'wrong','new_password':'Long-enough-password','confirm_password':'Long-enough-password'}
        for _ in range(5):self.assertEqual(self.req('/api/password',body,**headers)[0],400)
        self.assertEqual(self.req('/api/password',body,**headers)[0],429)

    def test_invalid_site_and_cross_site_plan_rejected(self):
        cookie=self.login()
        with patch.object(gui,'state',return_value={}):
            csrf=json.loads(self.req('/api/state',Cookie=cookie)[2])['csrf']
        self.assertEqual(self.req('/api/state?site=../../private',Cookie=cookie)[0],400)
        self.assertEqual(self.req('/api/deploy-apply',{'id':'f'*32},Cookie=cookie,**{'X-CSRF-Token':csrf})[0],400)

    def test_password_type_rejected(self):
        self.assertEqual(self.req('/api/login', {'username': 'admin', 'password': []})[0], 400)

    def test_site_creation_and_http_state_isolation(self):
        cookie=self.login()
        csrf=next(iter(gui.SESSIONS.values()))['csrf']
        obj={'organization_id':'2','network_name':'HTTP-test','key':'placeholder-api-key-testing-only'}
        with patch('preview.pages',return_value=[{'id':'N_http','name':'HTTP-test'}]):
            code,_,body=self.req('/api/sites',obj,Cookie=cookie,**{'X-CSRF-Token':csrf})
        self.assertEqual(code,201)
        site=json.loads(body)['id']
        from subprocess import CompletedProcess
        with patch.object(gui,'command',return_value=CompletedProcess([],0,'','')):
            with patch.object(gui.smartdns_manager,'state',return_value={}):
                code,_,body=self.req('/api/state',Cookie=cookie,**{'X-Site-ID':site})
                self.assertEqual(code,200)
                state=json.loads(body)
                self.assertEqual(state['network_name'],'HTTP-test')
                self.assertEqual(state['latest'],{})
                self.assertNotIn(obj['key'],body.decode())
                code,_,body=self.req('/api/state',Cookie=cookie)
                self.assertEqual(json.loads(body)['network_name'],'Existing')


if __name__ == '__main__':
    unittest.main()
