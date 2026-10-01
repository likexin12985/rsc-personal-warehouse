"""Exact startup registration for condition-aware receipt inbound constraints."""
from pathlib import Path
import hashlib
import json

from sqlalchemy import text


RAW = Path(__file__).with_suffix('.json').read_bytes()
if hashlib.sha256(RAW).hexdigest() != '4df556b6228e798123d5cc57e0418e81789aeb36cf521c3607f59a70ef88cce0':
    raise ValueError('0162 runtime catalog digest mismatch')
DATA = json.loads(RAW)


def register(namespace):
    registry = namespace['MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256']
    for row in DATA['patches']:
        name, arguments = row['signature'].removeprefix('public.').split('(', 1)
        coordinate = name, arguments[:-1]
        if registry.get(coordinate) != row['beforeSha256']:
            raise ValueError('0162 inbound proof source discontinuity: ' + name)
        registry[coordinate] = row['afterSha256']
    guard = 'rsc_require_return_inbound_quality_0162'
    coordinate = guard, ''
    if coordinate in namespace['FORMAL_FILE_INTERNAL_FUNCTIONS']:
        raise ValueError('0162 duplicate new-fact guard')
    namespace['FORMAL_FILE_INTERNAL_FUNCTIONS'][coordinate] = ('v', True, 'plpgsql', ('search_path=pg_catalog',))
    namespace['FORMAL_FILE_INTERNAL_FUNCTION_SHAPES'][coordinate] = ('f', 'trigger', False)
    namespace['FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256'][coordinate] = DATA['newGuardSha256']
    namespace['EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS']['trg_return_inbound_quality_0162'] = (
        'stock_operation_return_inbounds', guard, 'A', 7, False, False, False)


def verify(db):
    rows = tuple(db.execute(text("""SELECT c.conname,c.contype,c.convalidated,c.condeferrable,c.condeferred,
        ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY AS key(attnum,ord)
              JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=key.attnum ORDER BY key.ord)
        FROM pg_constraint c WHERE c.conrelid='public.stock_operation_return_inbound_lines'::regclass
          AND c.conname IN ('uq_stock_operation_return_inbound_lines_origin',
                            'uq_stock_operation_return_inbound_lines_condition') ORDER BY c.conname""")))
    if [tuple(row) for row in rows] != [('uq_stock_operation_return_inbound_lines_condition',
            'u', True, False, False, ['inbound_id', 'receipt_line_id', 'condition_code'])]:
        raise ValueError('0162 exact condition partition uniqueness required')
