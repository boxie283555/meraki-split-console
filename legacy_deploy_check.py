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
