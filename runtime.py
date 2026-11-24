"""Container job runner; no host systemd or Docker socket access."""
import datetime as dt
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
TASKS = {
 'meraki-split-preview.service': ['preview.py'],
 'meraki-config-backup.service': ['backup.py'],
 'meraki-split-deploy.service': ['deploy.py', '--queued'],
 'unity-smartdns-update.service': ['smartdns_manager.py'],
 'meraki-split-weekly.service': ['deploy.py', '--auto'],
}

class Jobs:
 def __init__(self):
  self.processes = {}
  self.lock = threading.RLock()
  self.dns = None
  self.stop = threading.Event()

 def command(self, *args):
  unit = next((x for x in args if x in TASKS or x.endswith('.timer')), '')
  if not unit:
   return subprocess.CompletedProcess(args, 1, '', 'Unknown task')
  with self.lock:
   if args[0] == 'show':
    running = unit in self.processes and self.processes[unit].poll() is None
    active = running or (unit.endswith('.timer') and os.environ.get('ENABLE_SCHEDULE','1') == '1')
    return subprocess.CompletedProcess(args,0,'ActiveState='+('active' if active else 'inactive')+'\n','')
   if args[0] != 'start' or unit not in TASKS:
    return subprocess.CompletedProcess(args,1,'','Unsupported operation')
   old = self.processes.get(unit)
   if old is not None and old.poll() is None:
    return subprocess.CompletedProcess(args,0,'','')
   logs = Path(os.environ.get('APP_DATA','/data'))/'logs'
   logs.mkdir(parents=True,exist_ok=True,mode=0o700)
   with (logs/(unit+'.log')).open('a') as output:
    self.processes[unit] = subprocess.Popen([sys.executable,str(BASE/TASKS[unit][0]),*TASKS[unit][1:]],stdout=output,stderr=output,start_new_session=True)
   return subprocess.CompletedProcess(args,0,'','')

 def request_dns_restart(self):
  root=Path(os.environ.get('SMARTDNS_STATE','/data/dns'))
  marker=root/'restart-request'
  from preview import atomic
  atomic(marker,str(time.time_ns()))
  for _ in range(100):
   if not marker.exists(): return True
   time.sleep(.1)
  raise ValueError('DNS restart timed out')

 def restart_dns(self):
  root=Path(os.environ.get('SMARTDNS_STATE','/data/dns'))
  conf=root/'current/smartdns.conf'
  if not conf.exists():return
  if self.dns is not None and self.dns.poll() is None:
   self.dns.terminate()
   try:self.dns.wait(timeout=5)
   except subprocess.TimeoutExpired:self.dns.kill();self.dns.wait()
  with (root/'daemon.log').open('a') as output:
   self.dns=subprocess.Popen(['/usr/sbin/smartdns','-f','-c',str(conf),'-p','-'],stdout=output,stderr=output)

 def start_scheduler(self):
  threading.Thread(target=self.loop,daemon=True).start()

 def loop(self):
  root=Path(os.environ.get('APP_DATA','/data'))
  record=root/'schedule.json'
  try:done=json.loads(record.read_text())
  except (FileNotFoundError,ValueError):done={}
  self.restart_dns()
  while not self.stop.wait(1):
   dnsroot=Path(os.environ.get('SMARTDNS_STATE','/data/dns'))
   marker=dnsroot/'restart-request'
   if marker.exists():
    self.restart_dns();marker.unlink(missing_ok=True)
   elif self.dns is not None and self.dns.poll() is not None:
    self.restart_dns()
   if os.environ.get('ENABLE_SCHEDULE','1')!='1':continue
   now=dt.datetime.now(ZoneInfo('Asia/Shanghai'));day=now.date().isoformat()
   if now.hour>=5 and done.get('daily')!=day:
    for task in ('meraki-split-preview.service','unity-smartdns-update.service'):self.command('start',task)
    done['daily']=day
    from preview import atomic
    atomic(record,json.dumps(done))
   if now.weekday()==5 and now.hour==5 and done.get('weekly')!=day:
    preview=self.processes.get('meraki-split-preview.service')
    if preview is not None and preview.poll()==0:
     self.command('start','meraki-split-weekly.service');done['weekly']=day
     from preview import atomic
     atomic(record,json.dumps(done))

jobs=Jobs()
