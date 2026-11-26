import http.client
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
import gui

class TLSTests(unittest.TestCase):
    def test_idle_handshake_does_not_block_other_clients_and_expires(self):
        with tempfile.TemporaryDirectory() as tmp:
            cert=Path(tmp)/'cert.pem';key=Path(tmp)/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            server=gui.ConsoleHTTPServer(('127.0.0.1',0),gui.Handler)
            server.secure=True
            server.handshake_timeout=1
            server.allowed_hosts={'127.0.0.1:'+str(server.server_port)}
            ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key)
            server.tls_context=ctx
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            idle=socket.create_connection(server.server_address,timeout=3)
            try:
                client=http.client.HTTPSConnection(*server.server_address,context=ssl._create_unverified_context(),timeout=2)
                client.request('GET','/');r=client.getresponse()
                self.assertEqual(r.status,200);self.assertIn(b'html',r.read());client.close()
                self.assertEqual(idle.recv(1),b'')
            finally:
                idle.close();server.shutdown();server.server_close();thread.join()
