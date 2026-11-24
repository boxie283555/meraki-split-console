import sys,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
import unittest
import deploy
class DeployTests(unittest.TestCase):
 def setUp(self):
  self.manual={'protocol':'any','destination':'0.0.0.0/5','port':'any','source':{'vlanId':'11','port':'any'}}
  self.a=deploy.desired_rules(['114.114.114.114/32'])[0];self.b=deploy.desired_rules(['223.5.5.5/32'])[0]
 def test_preserve_manual_source_and_apps(self):
  snap={'custom':[self.manual],'applications':[{'id':'a','source':{'vlanId':'12'}}]}
  p=deploy.reconcile(snap,[],[self.a]);self.assertEqual(p['payload']['custom'],[self.manual,self.a]);self.assertEqual(p['payload']['applications'],snap['applications'])
 def test_noop(self):
  p=deploy.reconcile({'custom':[self.manual,self.a]},[self.a],[self.a]);self.assertFalse(p['added']);self.assertFalse(p['removed'])
 def test_remove_only_owned(self):
  p=deploy.reconcile({'custom':[self.manual,self.a]},[self.a],[self.b]);self.assertEqual(p['payload']['custom'],[self.manual,self.b]);self.assertEqual(p['removed'],[self.a])
 def test_existing_exact_match_not_claimed(self):
  p=deploy.reconcile({'custom':[self.a]},[],[self.a]);self.assertEqual(p['managed'],[]);self.assertEqual(p['satisfied_by_existing'],1)
 def test_modified_owned_blocks(self):
  edited=copy.deepcopy(self.a);edited['source']['vlanId']='12'
  with self.assertRaises(ValueError):deploy.reconcile({'custom':[self.manual,edited]},[self.a],[self.b])
 def test_readback_source_loss_detected(self):
  bad=copy.deepcopy(self.manual);bad.pop('source')
  self.assertFalse(deploy.matches({'custom':[bad]},{'custom':[self.manual]}))
 def test_private_destination_rejected(self):
  with self.assertRaises(ValueError):deploy.desired_rules(['192.168.20.0/24'])

class ApplyGuardTests(unittest.TestCase):
 def fixture(self,root,changed=False):
  import time
  cfg={'state_dir':str(root)};d=deploy.paths(cfg);snap={'networkId':'N','custom':[],'applications':[],'majorApplications':[]};owner={'rules':[]}
  p={'expires_at':time.time()+300,'blockers':[],'owner_hash':deploy.digest(owner),'before_hash':deploy.view_hash(snap),'added':[{'x':1}] if changed else [],'removed':[]}
  deploy.dump(d/'plans'/'test.json',p)
  return cfg,snap
 def test_no_change_never_backups_or_puts(self):
  import tempfile
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as d:
   cfg,snap=self.fixture(Path(d))
   with patch.object(deploy,'credentials',return_value='test'),patch.object(deploy,'live',return_value=snap),patch.object(deploy,'recover'),patch.object(deploy.backup,'capture') as capture,patch.object(deploy,'put') as put:
    self.assertEqual(deploy.apply(cfg,'test')['status'],'no_change');capture.assert_not_called();put.assert_not_called()
 def test_external_modification_blocks_put(self):
  import tempfile
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as d:
   cfg,snap=self.fixture(Path(d),True);snap['custom']=[{'protocol':'tcp'}]
   with patch.object(deploy,'credentials',return_value='test'),patch.object(deploy,'live',return_value=snap),patch.object(deploy,'recover'),patch.object(deploy,'put') as put:
    with self.assertRaisesRegex(ValueError,'配置已变化'):deploy.apply(cfg,'test')
    put.assert_not_called()

class OwnershipArchiveTests(unittest.TestCase):
 def test_archive_keeps_ownership_and_detects_corruption(self):
  import tempfile,json,tarfile
  with tempfile.TemporaryDirectory() as d:
   folder=Path(d)/'backup';folder.mkdir();deploy.dump(folder/'manifest.json',{'entries':[]})
   p={'id':'p','managed':[],'network_id':'N','before_hash':'h','payload':{'custom':[]}}
   deploy.archive_context(folder,{'rules':[]},p)
   deploy.backup.verify(folder)
   with tarfile.open(Path(d)/'backup.tar.gz') as archive:self.assertIn('backup/deployment-context.json',archive.getnames())
   (folder/'deployment-context.json').write_text('{}')
   with self.assertRaises(ValueError):deploy.backup.verify(folder)

class ReadOnlyRefreshTests(unittest.TestCase):
 def test_plan_with_changes_never_applies_even_when_auto_enabled(self):
  import tempfile,json
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);cfg={'state_dir':str(root/'state')};(root/'config.json').write_text(json.dumps(cfg))
   deploy.dump(deploy.paths(cfg)/'settings.json',{'automatic':True})
   with patch.object(deploy,'BASE',root),patch.object(sys,'argv',['deploy.py','--plan']),patch.object(deploy,'plan',return_value={'add':3,'remove':1}),patch.object(deploy,'apply') as apply:
    deploy.main();apply.assert_not_called()
