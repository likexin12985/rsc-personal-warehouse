"""Exact internal mapping shape. Current directory proof is checked in PG16."""
from copy import deepcopy
from sqlalchemy import text
from . import kernel as kernel

FIELDS={'schema','version_id','source_system_id','region_id','catalog_sha256','warehouses','location_bindings',
 'included_buckets','location_facts_sha256','organization_facts_sha256','scope_policy','source_quantity_policy','content_sha256'}
def validate_rules(value):
 if type(value) is not dict or set(value)!={'revision','mapping'}:raise ValueError('daily mapping wrapper invalid')
 m=value['mapping']
 if type(m) is not dict or set(m)!=FIELDS or m['schema']!='rsc.daily_comparison_mapping_candidate.v1':raise ValueError('daily mapping fields invalid')
 if kernel.identifier(m['version_id'])!=value['revision']:raise ValueError('daily mapping version invalid')
 for key in ['source_system_id','region_id']:kernel.identifier(m[key])
 for key in ['catalog_sha256','location_facts_sha256','organization_facts_sha256','content_sha256']:kernel.hash_value(m[key])
 if kernel.digest({k:v for k,v in m.items() if k!='content_sha256'})!=m['content_sha256']:raise ValueError('daily mapping digest invalid')
 if m['scope_policy']!='explicit_physical_location' or m['source_quantity_policy']!='published_quantity_locked_separate':raise ValueError('daily mapping policy invalid')
 for key in ['warehouses','included_buckets']:
  v=m[key]
  if type(v) is not list or not v or any(type(x) is not str for x in v) or len(set(v))!=len(v):raise ValueError('daily mapping list invalid')
  for x in v:kernel.text(x)
 if not set(m['included_buckets'])<=kernel.BUCKETS:raise ValueError('daily mapping bucket invalid')
 bindings=m['location_bindings']
 if type(bindings) is not list or not bindings or len(bindings)>100000:raise ValueError('daily mapping locations invalid')
 seen=set()
 for row in bindings:
  if type(row) is not dict or set(row)!={'location_id','region_id','warehouse_code'}:raise ValueError('daily mapping location shape invalid')
  location=kernel.identifier(row['location_id']);region=kernel.identifier(row['region_id'])
  if location in seen:raise ValueError('daily mapping location duplicated')
  seen.add(location)
  if region==m['region_id']:
   if row['warehouse_code'] not in m['warehouses']:raise ValueError('daily mapping warehouse invalid')
  elif row['warehouse_code'] is not None:raise ValueError('daily mapping outside region must be explicit')
 return deepcopy(value)

def check_directory(db,rules,binding_id,catalog_id):
 import json
 # Mapping covers the whole captured reference set. SHARE blocks reference
 # INSERT/UPDATE/DELETE, including phantoms, until caller commit/rollback.
 # It does not lock inventory movements or stock balance rows.
 db.execute(text('LOCK TABLE public.organizations, public.stock_locations IN SHARE MODE'))
 db.execute(text('SELECT public.rsc_validate_daily_mapping_0130(CAST(:rules AS jsonb),:binding,:catalog)'),
  dict(rules=json.dumps(rules),binding=binding_id,catalog=catalog_id))
