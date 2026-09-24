"""Actual old publication input with unit-only local capture time adjustment."""
import json,unittest
from pathlib import Path
from datetime import timedelta
from copy import deepcopy
from app.daily_reconciliation import kernel
from app.daily_reconciliation.capture_bridge import adapt,compare_captures,at,BridgeError
HERE=Path(__file__).resolve().parent

def seal(v):v['content_sha256']=kernel.digest({k:x for k,x in v.items() if k!='content_sha256'});return v

def inputs():
 source=json.loads((HERE/'fixtures/daily_reconciliation/non-target-source.json').read_text())
 ledger=json.loads((HERE/'fixtures/daily_reconciliation/ledger.json').read_text())
 mapping=json.loads((HERE/'fixtures/daily_reconciliation/mapping.json').read_text())
 mapping.update(catalog_sha256=source['catalog_sha256'],source_system_id=source['source_system_id'],region_id=source['region_id'],warehouses=['W-A'])
 for row in mapping['location_bindings']:row['warehouse_code']='W-A'
 ledger['observation']['captured_at']=(at(source['captured_at'])+timedelta(seconds=5)).isoformat();ledger['completed_at']=ledger['observation']['captured_at']
 return source,seal(ledger),seal(mapping)

class CoverageTests(unittest.TestCase):
 def test_preserved_other_region_coverage_not_target_stock(self):
  source,ledger,mapping=inputs();before=deepcopy(source)
  r=compare_captures(day=at(source['captured_at']).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date(),source=source,ledger=ledger,mapping=mapping)
  self.assertEqual(source,before);self.assertEqual(r['input_hashes']['source'],source['content_sha256'])
  self.assertTrue(all(row['warehouse_code']=='W-A' for row in r['comparison']['items']))
  self.assertEqual([w['warehouse_code'] for w in source['coverage'] if not w['target_positions']],['W-B'])
 def test_missing_target_still_fails(self):
  s,l,m=inputs();s['coverage']=[w for w in s['coverage'] if w['warehouse_code']=='W-B'];seal(s)
  with self.assertRaisesRegex(BridgeError,'source_coverage_incomplete'):adapt(source=s,ledger=l,mapping=m)
 def test_duplicate_non_target_fails(self):
  s,l,m=inputs();s['coverage'].append(deepcopy(s['coverage'][1]));seal(s)
  with self.assertRaisesRegex(BridgeError,'source_coverage_incomplete'):adapt(source=s,ledger=l,mapping=m)
 def test_non_target_origin_is_rejected(self):
  s,l,m=inputs();s['rows'][0].update(warehouse_code='W-B',position_code='P-B');seal(s)
  with self.assertRaisesRegex(BridgeError,'source_origin_outside_positions'):adapt(source=s,ledger=l,mapping=m)

if __name__=='__main__':unittest.main(verbosity=2)
