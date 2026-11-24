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
import sites
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

 def command(self, *args, site="default"):
  unit = next((x for x in args if x in TASKS or x.endswith('.timer')), '')
  if not unit:
   return subprocess.CompletedProcess(args, 1, '', 'Unknown task')
  if 'smartdns' in unit: site='default'
  sites.validate_id(site)
  taskkey=unit if site=='default' else site+':'+unit
  with self.lock:
   if args[0] == 'show':
    running = taskkey in self.processes and self.processes[taskkey].poll() is None
    active = running or (unit.endswith('.timer') and os.environ.get('ENABLE_SCHEDULE','1') == '1')
    return subprocess.CompletedProcess(args,0,'ActiveState='+('active' if active else 'inactive')+'\n','')
   if args[0] != 'start' or unit not in TASKS:
    return subprocess.CompletedProcess(args,1,'','Unsupported operation')
   old = self.processes.get(taskkey)
   if old is not None and old.poll() is None:
    return subprocess.CompletedProcess(args,0,'','')
   logs = (Path(os.environ['APP_DATA']) if os.environ.get('APP_DATA') else Path(sites.get()['state_dir']).expanduser())/'logs'
   logs.mkdir(parents=True,exist_ok=True,mode=0o700)
   env=os.environ.copy()
   if site!='default': env['APP_CONFIG']=str(sites.config_path(site))
   with (logs/(taskkey.replace(':','-')+'.log')).open('a') as output:
    self.processes[taskkey] = subprocess.Popen([sys.executable,str(BASE/TASKS[unit][0]),*TASKS[unit][1:]],stdout=output,stderr=output,start_new_session=True,env=env)
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
  container=os.environ.get('CONTAINER_RUNTIME')=='1'
  root=Path(os.environ['APP_DATA']) if os.environ.get('APP_DATA') else Path(sites.get()['state_dir']).expanduser()
  root.mkdir(parents=True,exist_ok=True)
  record=root/'schedule-multisite.json'
  try:done=json.loads(record.read_text())
  except (FileNotFoundError,ValueError):done={}
  if container:self.restart_dns()
  while not self.stop.wait(1):
   try:
    if container:
     dnsroot=Path(os.environ.get('SMARTDNS_STATE','/data/dns'))
     marker=dnsroot/'restart-request'
     if marker.exists():
      self.restart_dns();marker.unlink(missing_ok=True)
     elif self.dns is not None and self.dns.poll() is not None:self.restart_dns()
    if os.environ.get('ENABLE_SCHEDULE','1')!='1':continue
    now=dt.datetime.now(ZoneInfo('Asia/Shanghai'));day=now.date().isoformat()
    for row in sites.listing():
     site=row['id']
     # Existing Linux timers retain ownership of the legacy site and DNS service.
     if not container and site=='default':continue
     daily=site+':daily';weekly=site+':weekly'
     if now.hour>=5 and done.get(daily)!=day:
      self.command('start','meraki-split-preview.service',site=site)
      if container and site=='default':self.command('start','unity-smartdns-update.service')
      done[daily]=day
      from preview import atomic
      atomic(record,json.dumps(done))
     if now.weekday()==5 and now.hour==5 and done.get(weekly)!=day:
      key='meraki-split-preview.service' if site=='default' else site+':meraki-split-preview.service'
      preview=self.processes.get(key)
      if preview is not None and preview.poll()==0:
       self.command('start','meraki-split-weekly.service',site=site);done[weekly]=day
       from preview import atomic
       atomic(record,json.dumps(done))
   except Exception as exc:
    # Never kill all scheduling because one site's files are temporarily unavailable.
    print('Scheduler error: '+type(exc).__name__,flush=True)
    self.stop.wait(10)

jobs=Jobs()
