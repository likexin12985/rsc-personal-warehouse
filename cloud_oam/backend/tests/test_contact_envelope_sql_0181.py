"""New contact SQL structural cases; SQLite is not PG16/AAD evidence."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import runpy
import sqlite3

import pytest


ROOT = Path(__file__).resolve().parents[1]
SQL = runpy.run_path(str(ROOT / 'alembic/contact_envelope_0181/validation.py'))
OLD = runpy.run_path(str(ROOT / 'alembic/versions/20260831_0029_material_request_approval_domain.py'))
LEGACY = OLD['_sqlite_contact_envelope_invalid']('envelope.document')
INVALID = SQL['dual_invalid'](LEGACY, 'envelope.document', sqlite=True)


def envelope():
    return dict(schema='rsc.material_request_contact.v2', provider='openbao_transit_v1',
        purpose='material_request_contact', environment='test', provider_instance_id='isolated-contact-test',
        key_path='transit/keys/rsc-material-request-contact', application_key_version=3, transit_key_version=2,
        ciphertext_b64=base64.b64encode(bytes(range(17))).decode(), nonce_b64=base64.b64encode(bytes(range(12))).decode(),
        aad_sha256='d0aeb7a4d27a3920e42e2978357c36efa87544405637f0572898fd62981a4fbf',
        mobile_hmac='hmac:1:'+'a'*64, contact_hmac='hmac:1:'+'b'*64)


@pytest.fixture
def db():
    connection=sqlite3.connect(':memory:')
    connection.executescript('''
CREATE TABLE openbao_data_key_pins(purpose TEXT, environment TEXT, provider_instance_id TEXT,
    key_path TEXT, application_key_version INTEGER, transit_key_version INTEGER,
    ciphertext_sha256 TEXT, context_sha256 TEXT, associated_data_sha256 TEXT, created_at TEXT);
CREATE TABLE application_key_version_claims(purpose TEXT, application_key_version INTEGER,
    provider TEXT, ciphertext_sha256 TEXT, created_at TEXT);
''')
    value=envelope()
    connection.execute('INSERT INTO openbao_data_key_pins VALUES (?,?,?,?,?,?,?,?,?,?)',
        tuple(value[key] for key in ('purpose','environment','provider_instance_id','key_path',
              'application_key_version','transit_key_version'))+('c'*64,'d'*64,'e'*64,'2026-10-09T00:00:00Z'))
    connection.execute('INSERT INTO application_key_version_claims VALUES (?,?,?,?,?)',
        (value['purpose'],value['application_key_version'],value['provider'],'c'*64,'2026-10-09T00:00:00Z'))
    yield connection
    connection.close()


def invalid(db, value):
    return db.execute('SELECT ('+INVALID+') FROM (SELECT json(?) AS document) AS envelope',
                      (json.dumps(value,ensure_ascii=False),)).fetchone()[0]


def test_v2_exact_shape_and_independent_pin_claim_coordinates_are_admitted(db):
    assert invalid(db,envelope())==0
    from app.formal_services.material_request_contact_openbao import V2_ENVELOPE_KEYS
    assert frozenset(SQL['KEYS'])==V2_ENVELOPE_KEYS==frozenset(envelope())


@pytest.mark.parametrize('key', tuple(envelope()))
def test_v2_each_missing_or_null_field_is_rejected(db,key):
    value=envelope();del value[key]
    assert invalid(db,value)==1
    value=envelope();value[key]=None
    assert invalid(db,value)==1


@pytest.mark.parametrize('changes',[
    {'unexpected':'field'}, {'kms_key_id':'legacy-key'}, {'key_version':3},
    {'schema':'rsc.material_request_contact.v1'}, {'schema':'unknown'}, {'provider':'aliyun_kms'},
    {'purpose':'authentication_idempotency'}, {'environment':'production'},
    {'provider_instance_id':'another-instance'}, {'provider_instance_id':'ab'},
    {'provider_instance_id':'-ab'}, {'provider_instance_id':'Abc'}, {'provider_instance_id':'abc界'},
    {'provider_instance_id':'abc\x00suffix'}, {'key_path':'transit/keys/rsc-authentication-idempotency'},
    {'application_key_version':True}, {'application_key_version':3.0}, {'application_key_version':'3'},
    {'application_key_version':0}, {'application_key_version':2147483648}, {'application_key_version':4},
    {'transit_key_version':True}, {'transit_key_version':2.0}, {'transit_key_version':'2'},
    {'transit_key_version':0}, {'transit_key_version':2147483648}, {'transit_key_version':1},
    {'ciphertext_b64':'A'*22+'=='}, {'ciphertext_b64':'A'*22+'=B'}, {'ciphertext_b64':'A'*23},
    {'nonce_b64':'A'*15}, {'nonce_b64':'A'*15+'='}, {'aad_sha256':'A'*64},
    {'mobile_hmac':'hmac:01:'+'a'*64}, {'contact_hmac':'hmac:0:'+'b'*64},
])
def test_v2_shape_provider_version_and_coordinate_changes_fail_closed(db,changes):
    assert invalid(db,envelope()|changes)==1


@pytest.mark.parametrize('key',SQL['STRING_KEYS'])
def test_v2_text_with_embedded_nul_is_rejected_by_sqlite_despite_glob_terminator(db,key):
    value=envelope();value[key]+='\x00'
    assert invalid(db,value)==1


@pytest.mark.parametrize('statement',[
    "DELETE FROM openbao_data_key_pins",
    "DELETE FROM application_key_version_claims",
    "UPDATE application_key_version_claims SET provider='aliyun_kms'",
    "UPDATE application_key_version_claims SET ciphertext_sha256='unrelated'",
    "UPDATE application_key_version_claims SET created_at='2026-10-08T00:00:00Z'",
    "INSERT INTO application_key_version_claims SELECT * FROM application_key_version_claims",
    "INSERT INTO openbao_data_key_pins SELECT * FROM openbao_data_key_pins",
])
def test_v2_claim_missing_conflicting_or_duplicated_cannot_admit_contact(db,statement):
    # Deliberately weakened isolated tables let us prove the query rejects
    # corruption; real 0180 catalog checks remain independently mandatory.
    db.execute(statement)
    assert invalid(db,envelope())==1


def test_v1_nine_fields_retain_the_exact_predecessor_expression_without_openbao_pin(db):
    value=envelope()
    value={key:value[key] for key in ('ciphertext_b64','nonce_b64','aad_sha256','mobile_hmac','contact_hmac')}
    value.update(schema='rsc.material_request_contact.v1',provider='aliyun_kms',kms_key_id='synthetic-old-key',key_version=1)
    db.execute('DELETE FROM openbao_data_key_pins');db.execute('DELETE FROM application_key_version_claims')
    assert INVALID.count(LEGACY)==1
    assert invalid(db,value)==0
    previous=db.execute('SELECT ('+LEGACY+') FROM (SELECT json(?) AS document) AS envelope',
                        (json.dumps(value),)).fetchone()[0]
    assert previous==0


def test_pg_aad_uses_the_frozen_order_bytea_nuls_and_separate_provider_versions():
    result=SQL['postgres_aad']('NEW.contact_snapshot_jsonb','NEW.id','NEW.requester_person_id')
    keys=('provider','purpose','environment','provider_instance_id','key_path',
          'application_key_version','transit_key_version','request_id','requester_person_id')
    positions=[result.index("convert_to('"+key+'=') for key in keys]
    assert positions==sorted(positions)
    assert result.count("decode('00', 'hex')")==len(keys)
    assert "chr(0)" not in result and '\x00' not in result
    assert result.endswith("convert_to('requester_person_id=' || (NEW.requester_person_id)::text, 'UTF8')), 'hex')")
