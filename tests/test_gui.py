import http.client
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
        cls.tmp.cleanup()

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

    def test_password_type_rejected(self):
        self.assertEqual(self.req('/api/login', {'username': 'admin', 'password': []})[0], 400)


if __name__ == '__main__':
    unittest.main()
