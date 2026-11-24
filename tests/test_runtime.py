import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import sys
sys.path.insert(0,str(Path(__file__).parents[1]))
from runtime import Jobs
import smartdns_manager as dns

class RuntimeTests(unittest.TestCase):
 def test_task_deduplicated_and_unknown_rejected(self):
  with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'APP_DATA':d}), patch('runtime.subprocess.Popen') as spawn:
   spawn.return_value.poll.return_value=None
   jobs=Jobs()
   self.assertEqual(jobs.command('start','bad.service').returncode,1)
   self.assertEqual(jobs.command('start','meraki-split-preview.service').returncode,0)
   self.assertEqual(jobs.command('start','meraki-split-preview.service').returncode,0)
   self.assertEqual(spawn.call_count,1)
   self.assertIn('ActiveState=active',jobs.command('show','meraki-split-preview.service').stdout)
   spawn.return_value.poll.return_value=0
   self.assertIn('ActiveState=inactive',jobs.command('show','meraki-split-preview.service').stdout)
 def test_disabled_schedule_reported(self):
  with patch.dict(os.environ,{'ENABLE_SCHEDULE':'0'}):
   self.assertIn('inactive',Jobs().command('show','meraki-split-preview.timer').stdout)
 def test_dns_container_state_without_systemd(self):
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'CONTAINER_RUNTIME':'1'}),patch.object(dns,'ROOT',Path(d)),patch.object(dns,'query',return_value=False),patch.object(dns.subprocess,'run') as run:
   self.assertFalse(dns.state()['active']);run.assert_not_called()
 def test_dns_feed_preserves_china_exclusion(self):
  config=dns.config('/data/dns/current',5353,'0.0.0.0','/data/dns/log')
  self.assertIn('-group china -exclude-default-group',config)
  self.assertIn('bind 0.0.0.0:5353',config)
