"""Read-only, exact 0180 binding catalog and pin/claim correspondence.

No DDL, provider discovery, decryption or migration imports are allowed here.
The API is admitted only when the independently frozen PG16 catalog matches.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from sqlalchemy import text

from .stock_scrap_security_probe import snapshot

TABLES = ('application_key_version_claims', 'openbao_data_key_pins')
OBSERVED_TABLES = ('application_key_version_claims', 'kms_data_key_pins', 'openbao_data_key_pins')
FUNCTIONS = ('rsc_reject_key_provider_binding_mutation_0180',
             'rsc_guard_application_key_version_claim_0180',
             'rsc_claim_application_key_version_0180')
RAW = Path(__file__).with_suffix('.json').read_bytes()
if sha256(RAW).hexdigest() != 'c1d937eb943c35f3aa30412f96989147d09e865d22effc12401eb1d6c51b96cc':
    raise ValueError('0180 key-provider catalog digest mismatch')
DATA = json.loads(RAW)

_CORRESPONDENCE = text('''
WITH pins AS (
 SELECT purpose, application_key_version, 'aliyun_kms'::varchar AS provider,
        ciphertext_sha256, created_at FROM public.kms_data_key_pins
 UNION ALL
 SELECT purpose, application_key_version, 'openbao_transit_v1'::varchar AS provider,
        ciphertext_sha256, created_at FROM public.openbao_data_key_pins
), missing AS (
 SELECT * FROM pins EXCEPT SELECT purpose, application_key_version, provider,
        ciphertext_sha256, created_at FROM public.application_key_version_claims
), extra AS (
 SELECT purpose, application_key_version, provider, ciphertext_sha256, created_at
   FROM public.application_key_version_claims EXCEPT SELECT * FROM pins
)
SELECT NOT EXISTS(SELECT 1 FROM missing) AND NOT EXISTS(SELECT 1 FROM extra)
   AND (SELECT count(*) FROM pins) = (SELECT count(*) FROM public.application_key_version_claims)
''')


def register(namespace):
    """Extend explicit read/internal-function registries without runtime grants."""
    registries = ('FORMAL_FILE_INTERNAL_FUNCTIONS',
                  'FORMAL_FILE_INTERNAL_FUNCTION_SHAPES',
                  'FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256',
                  'EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS')
    if any(type(namespace.get(name)) is not dict for name in registries) \
            or type(namespace.get('RUNTIME_READ_TABLES')) not in (set, frozenset):
        raise ValueError('0180 complete registration namespace required')
    if set(DATA) != {'tables', 'functions'} or set(DATA['tables']) != set(OBSERVED_TABLES) \
            or set(DATA['functions']) != {name+'()' for name in FUNCTIONS}:
        raise ValueError('0180 exact catalog identity set required')
    if set(TABLES) & namespace['RUNTIME_READ_TABLES']:
        raise ValueError('0180 duplicate table registration')
    updates = []
    for name in FUNCTIONS:
        coordinate = name, ''
        row = DATA['functions'][name+'()']
        if any(coordinate in namespace[name] for name in registries[:3]):
            raise ValueError('0180 duplicate function registration')
        if row['owner'] != 'star_oam_migrator' or row['prosecdef'] is not False \
                or row['proconfig'] != ['search_path=pg_catalog, public'] \
                or row['provolatile'] != 'v' or row['identity_arguments'] != '' \
                or row['acl'] != [{'grantee':'star_oam_migrator','privilege':'EXECUTE','grantable':False}]:
            raise ValueError('0180 unexpected binding function contract')
        updates.append((coordinate,
                        (row['provolatile'], row['prosecdef'], 'plpgsql', tuple(row['proconfig'])),
                        ('f', 'trigger', row['proisstrict']),
                        sha256(row['prosrc'].encode()).hexdigest()))
    legacy = 'trg_kms_data_key_pins_claim_0180'
    if legacy in namespace['EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS']:
        raise ValueError('0180 duplicate legacy claim trigger')
    namespace['RUNTIME_READ_TABLES'] |= frozenset(TABLES)
    for coordinate, contract, shape, digest in updates:
        namespace['FORMAL_FILE_INTERNAL_FUNCTIONS'][coordinate] = contract
        namespace['FORMAL_FILE_INTERNAL_FUNCTION_SHAPES'][coordinate] = shape
        namespace['FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256'][coordinate] = digest
    namespace['EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS'][legacy] = (
        'kms_data_key_pins', 'rsc_claim_application_key_version_0180', 'A', 5)


def verify(db):
    """Use the caller's one catalog snapshot; reject absence or any extra detail."""
    actual = snapshot(db, table_names=OBSERVED_TABLES, function_names=FUNCTIONS)
    if actual != DATA:
        raise ValueError('0180 key-provider full catalog mismatch')
    if db.scalar(_CORRESPONDENCE) is not True:
        raise ValueError('0180 key-provider pin/claim correspondence mismatch')
