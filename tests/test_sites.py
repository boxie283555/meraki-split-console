import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).parents[1]))
import sites
from runtime import Jobs

class SitesTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.config=self.root/'config.json'
  self.cfg={'organization_id':'1','network_name':'BJ','api_key_file':str(self.root/'old-key'),'state_dir':str(self.root/'old-state')}
  self.config.write_text(json.dumps(self.cfg));(self.root/'old-key').write_text('original-key')
  env=patch.dict(os.environ,{'APP_CONFIG':str(self.config),'APP_DATA':str(self.root)});env.start();self.addCleanup(env.stop)
  self.obj={'organization_id':'2','network_name':'SGP','key':'test-api-key-placeholder-123'}
  pages=patch('preview.pages',return_value=[{'name':'SGP','id':'N_sgp'},{'name':'MN','id':'N_mn'}]);pages.start();self.addCleanup(pages.stop)
 def test_legacy_paths_and_site_storage_are_isolated(self):
  site=sites.create(self.obj);cfg=sites.get(site)
  self.assertEqual(sites.get()['state_dir'],self.cfg['state_dir'])
  self.assertNotEqual(cfg['state_dir'],self.cfg['state_dir'])
  self.assertEqual(cfg['network_id'],'N_sgp')
  self.assertEqual(Path(cfg['api_key_file']).stat().st_mode&0o777,0o600)
  self.assertNotIn(self.obj['key'],json.dumps(sites.listing()))
  self.assertEqual((self.root/'old-key').read_text(),'original-key')
 def test_duplicates_and_traversal_rejected(self):
  sites.create(self.obj)
  with self.assertRaises(ValueError):sites.create(self.obj)
  with self.assertRaises(ValueError):sites.get('../../private')
  with self.assertRaises(ValueError):sites.get('0'*24)
 def test_api_validation_failure_leaves_no_site(self):
  with patch('preview.pages',side_effect=OSError('unreachable')):
   with self.assertRaises(ValueError):sites.create(self.obj)
  self.assertEqual(len(sites.listing()),1)
 def test_jobs_pass_independent_config_and_deduplicate_per_site(self):
  a=sites.create(self.obj);b=sites.create(dict(self.obj,network_name='MN'))
  with patch('runtime.subprocess.Popen') as spawn:
   spawn.return_value.poll.return_value=None;jobs=Jobs()
   for site in [a,b,a]:jobs.command('start','meraki-split-preview.service',site=site)
   self.assertEqual(spawn.call_count,2)
   configs=[x.kwargs['env']['APP_CONFIG'] for x in spawn.call_args_list]
   self.assertEqual(configs,[str(sites.config_path(a)),str(sites.config_path(b))])
 def test_network_id_wins_over_rename(self):
  self.assertTrue(sites.matches({'id':'N_1','name':'renamed'},{'network_id':'N_1','network_name':'old'}))
  self.assertFalse(sites.matches({'id':'N_2','name':'old'},{'network_id':'N_1','network_name':'old'}))
