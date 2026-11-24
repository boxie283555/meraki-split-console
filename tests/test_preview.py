import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('preview', Path(__file__).parents[1] / 'preview.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
DATA = b'apnic|CN|ipv4|1.0.0.0|256|20200101|allocated\napnic|CN|ipv4|1.0.1.0|256|20200101|allocated\napnic|JP|ipv4|2.0.0.0|256|20200101|allocated\n'


class Tests(unittest.TestCase):
    def test_aggregate_without_extra_addresses(self):
        self.assertEqual(p.china_prefixes(DATA), ['1.0.0.0/23'])
        with self.assertRaises(ValueError):
            p.china_prefixes(b'<html>error</html>')

    def test_non_power_of_two_allocation(self):
        self.assertEqual(p.china_prefixes(b'apnic|CN|ipv4|1.0.0.0|768|20200101|allocated'),
                         ['1.0.0.0/23', '1.0.2.0/24'])

    def test_credential_destination_guard(self):
        with self.assertRaises(ValueError):
            p.get('https://example.com/', 'test-secret')

    def test_no_key_and_bad_download_preserves_latest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = {'api_key_file': str(root / 'absent')}
            with patch.object(p, 'get', return_value=(DATA, {})):
                p.run(cfg, root)
            before = (root / 'latest.json').read_bytes()
            self.assertEqual(json.loads(before)['meraki_status'], 'not_configured')
            with patch.object(p, 'get', return_value=(b'bad response', {})):
                with self.assertRaises(ValueError):
                    p.run(cfg, root)
            self.assertEqual((root / 'latest.json').read_bytes(), before)

    def test_snapshot_preserves_unknown_source_fields(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            key = root / 'key'
            key.write_text('test-secret')
            key.chmod(0o600)
            current = {'networkId': 'N_test', 'custom': [{'protocol': 'any',
                       'destination': '1.0.0.0/23', 'port': 'any', 'source': {'vlan': 11}}]}
            with patch.object(p, 'get', return_value=(DATA, {})), patch.object(p, 'pages',
                      side_effect=[[{'name': 'BJ-Site', 'id': 'N_test'}], [current]]):
                p.run({'api_key_file': str(key), 'organization_id': 'test',
                       'network_name': 'BJ-Site'}, root)
            report = json.loads((root / 'latest.json').read_text())
            self.assertFalse(report['deployment_allowed'])
            self.assertEqual(report['exact_destination_matches'], 0)
            snapshots = list(root.glob('runs/*/meraki-api-snapshot.json'))
            self.assertEqual(json.loads(snapshots[0].read_text()), current)


if __name__ == '__main__':
    unittest.main()
