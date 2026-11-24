"""Isolated behavioral check against the installed SmartDNS binary."""
import socket,struct,threading,subprocess,time,tempfile
from pathlib import Path

def verify(folder):
 stop=threading.Event();seen={'china':0,'default':0};drop=threading.Event();sockets=[]
 def serve(sock,group):
  sock.settimeout(.2)
  while not stop.is_set():
   try:data,addr=sock.recvfrom(4096)
   except socket.timeout:continue
   except OSError:return
   seen[group]+=1
   if group=='china' and drop.is_set():continue
   end=12
   while data[end]:end+=data[end]+1
   end+=5
   answer=b'\xc0\x0c'+struct.pack('!HHIH',1,1,0,4)+socket.inet_aton('192.0.2.1' if group=='china' else '192.0.2.2')
   sock.sendto(data[:2]+struct.pack('!HHHHH',0x8180,1,1,0,0)+data[12:end]+answer,addr)
 try:
  ports={}
  for group in seen:
   sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.bind(('127.0.0.1',0));sockets.append(sock);ports[group]=sock.getsockname()[1]
   threading.Thread(target=serve,args=(sock,group),daemon=True).start()
  with tempfile.TemporaryDirectory(prefix='unity-dns-isolation-') as tmp:
   tmp=Path(tmp)
   conf=(folder/'probe.conf').read_text()
   conf='\n'.join(x for x in conf.splitlines() if not x.startswith(('server-https ','bind ','bind-tcp ','log-file ','cache-size ','serve-expired ')))
   conf+=f'\nbind 127.0.0.1:6054\ncache-size 0\nserve-expired no\nlog-file {tmp}/test.log\nserver 127.0.0.1:{ports["default"]}\nserver 127.0.0.1:{ports["china"]} -group china -exclude-default-group\n'
   (tmp/'test.conf').write_text(conf)
   with (tmp/'output').open('w') as log:
    proc=subprocess.Popen(['/usr/sbin/smartdns','-f','-c',str(tmp/'test.conf'),'-p','-'],stdout=log,stderr=log)
    try:
     time.sleep(1)
     def query(name):
      return subprocess.run(['/usr/bin/dig','@127.0.0.1','-p','6054',name,'A','+short','+time=2','+tries=1'],capture_output=True,text=True,timeout=4).stdout
     for name in ['baidu.com','www.baidu.com','qq.com','www.taobao.com','unity-isolation-unlisted.cn','unity-isolation.xn--fiqs8s']:
      before=seen['default']
      if '192.0.2.1' not in query(name) or seen['default']!=before:raise ValueError('国内域名未被隔离到国内 DNS 组：'+name)
     if '192.0.2.2' not in query('unity-isolation.example'):raise ValueError('默认 DNS 组测试失败')
     drop.set();before=seen['default'];china_before=seen['china']
     answer=query('failure-check.baidu.com')
     time.sleep(.3)
     if '192.0.2.' in answer or seen['default']!=before or seen['china']<=china_before:raise ValueError('国内 DNS 失效隔离验证失败')
    finally:
     proc.terminate()
     try:proc.wait(timeout=5)
     except subprocess.TimeoutExpired:proc.kill();proc.wait()
 finally:
  stop.set()
  for sock in sockets:sock.close()
 return {'china_group_only':True,'subdomains_checked':True,'no_default_fallback':True}

if __name__=='__main__':
 import sys,json
 print(json.dumps(verify(Path(sys.argv[1]))))
