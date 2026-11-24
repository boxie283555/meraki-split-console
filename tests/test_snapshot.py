import unittest,json,tempfile,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
import gui
class SnapshotTests(unittest.TestCase):
 def test_load_exact_successful_snapshot_with_source(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);folder=root/'runs'/'20261122T004956.857693Z';folder.mkdir(parents=True)
   snapshot={'networkId':'N_test','custom':[{'source':{'vlanId':'11','port':'any'},'destination':'8.0.0.0/7'}]}
   (folder/'meraki-api-snapshot.json').write_text(json.dumps(snapshot))
   latest={'meraki_status':'read_success','network_id':'N_test','generated_at_utc':'2026-11-22T00:49:56.857693+00:00'}
   self.assertEqual(gui.latest_snapshot(root,latest),snapshot)
   latest['network_id']='N_wrong';self.assertIsNone(gui.latest_snapshot(root,latest))
 def test_absent_snapshot(self):
  with tempfile.TemporaryDirectory() as d:
   self.assertIsNone(gui.latest_snapshot(Path(d),{}))
   self.assertIsNone(gui.latest_snapshot(Path(d),{'meraki_status':'read_success','network_id':'N','generated_at_utc':'2026-11-22T00:00:00+00:00'}))
