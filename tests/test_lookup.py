import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
import unittest
from unittest.mock import patch
import ipaddress
import lookup as l

class LookupTests(unittest.TestCase):
 def test_validation(self):
  self.assertEqual(l.normalize(' Baidu.COM. '),('domain','baidu.com'))
  self.assertEqual(l.normalize('2001:4860:4860::8888')[0],'ip')
  for value in ['https://baidu.com','a;id','--help','999.1.1.1','a..com','1.1.1.1/24',None]:
   with self.assertRaises(ValueError):l.normalize(value)
 def test_private_not_sent_to_rdap(self):
  with patch.object(l,'fetch_json') as fetch:
   self.assertEqual(l.owner('192.168.20.169')['status'],'not_public');fetch.assert_not_called()
 def test_registry_longest_prefix_and_https(self):
  data={'services':[[['0.0.0.0/0'],['https://rdap.arin.net/registry/']],[['1.0.0.0/8'],['https://rdap.apnic.net/']]]}
  self.assertEqual(l.registry_url(ipaddress.ip_address('1.1.1.1'),data),'https://rdap.apnic.net/ip/1.1.1.1')
  with self.assertRaises(ValueError):l.fetch_json('http://127.0.0.1/secret')
 def test_owner_uses_registrant_not_abuse_contact(self):
  data={'name':'NET','entities':[{'roles':['abuse'],'vcardArray':['vcard',[['fn',{},'text','Abuse desk']]]},{'roles':['registrant'],'vcardArray':['vcard',[['fn',{},'text','Example Org']]]}]}
  self.assertEqual(l.parse_owner(data,'https://rdap.apnic.net/ip/1.1.1.1')['owner'],'Example Org')
 def test_multiple_addresses_partial_owner_failure(self):
  def fake_dns(mode,q):return {'values':['8.8.8.8','1.1.1.1','8.8.8.8']} if mode=='forward' else {'values':[],'error':'No PTR'}
  with patch.object(l,'dns',side_effect=fake_dns),patch.object(l,'owner',return_value={'status':'error','owner':'暂未获取'}):
   r=l.lookup('example.com')
  self.assertEqual(r['total'],2);self.assertEqual(r['rows'][0]['ip'],'1.1.1.1');self.assertEqual(r['rows'][1]['ptr'],[])

if __name__=='__main__':unittest.main()
