import sys,json,tempfile,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
import unittest
from unittest.mock import patch
import backup
class BackupTests(unittest.TestCase):
 def fixture(self,root):
  data={'name':'example','readOnly':'drop','secret':'masked'}
  p=root/'config.json';p.write_text(json.dumps(data))
  m={'network_id':'N_test','status':'captured_with_gaps','entries':[{'file':'config.json','status':'ok','sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'path':'/networks/N_test','restore_put':'updateNetwork','put_fields':['name'],'put_required':['name']}]}
  (root/'manifest.json').write_text(json.dumps(m));return m
 def test_integrity_detects_corruption(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.fixture(root);backup.verify(root)
   (root/'config.json').write_text('{}')
   with self.assertRaises(ValueError):backup.verify(root)
 def test_plan_is_non_executable_and_filters_read_only_fields(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.fixture(root);backup.generate_plan(root)
   plan=json.loads((root/'restore-plan.json').read_text())
   self.assertFalse(plan['executable']);self.assertEqual(plan['steps'][0]['body'],{'name':'example'})
 def test_gaps_block_before_change(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.fixture(root)
   with patch.object(backup,'capture',return_value=root):
    with self.assertRaisesRegex(RuntimeError,'gaps'):backup.backup_before_change({}, {'custom':[]})
   self.assertFalse((root/'proposed-change.json').exists())
