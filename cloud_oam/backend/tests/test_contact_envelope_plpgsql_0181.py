"""Compile whole frozen trigger functions, not only their SQL expressions."""
from hashlib import sha256
from pathlib import Path
import runpy

from pglast import parser
import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('signature,request_expression,person', [
    ('rsc_guard_material_request_identity_0029()', 'NEW.id', 'NEW.requester_person_id'),
    ('rsc_guard_material_request_revision_0029()', 'NEW.request_id', 'requester_person'),
])
def test_frozen_contact_case_compiles_as_one_plpgsql_if_expression(signature, request_expression, person):
    migration = runpy.run_path(str(ROOT / 'alembic/versions/20261230_0181_material_request_contact_v2.py'))
    validation = runpy.run_path(str(ROOT / 'alembic/contact_envelope_0181/validation.py'))
    legacy = runpy.run_path(str(ROOT / 'alembic/versions/20260831_0029_material_request_approval_domain.py'))
    previous = legacy['_postgresql_contact_envelope_invalid']('NEW.contact_snapshot_jsonb', request_expression, person)
    expression = validation['dual_invalid'](previous, 'NEW.contact_snapshot_jsonb', request_expression, person)
    data = migration['DATA']
    entry = data['functions'][signature]
    frozen = data['catalog']['functions']['after'][signature]
    assert entry['before'].count(previous) == 1
    assert entry['after'] == entry['before'].replace(previous, '(' + expression + ')')
    assert entry['afterSha256'] == sha256(entry['after'].encode()).hexdigest()
    assert frozen['prosrc'] == entry['after'] and frozen['definition'].count(entry['after']) == 1
    parser.parse_plpgsql_json(frozen['definition'])
    # SQL CASE alone parses; PL/pgSQL needs the wrapper to disambiguate its
    # inner THEN from IF's THEN. This reproduces the real PG16 failure.
    broken = frozen['definition'].replace('(' + expression + ')', expression)
    with pytest.raises(parser.ParseError, match='syntax error at end of input'):
        parser.parse_plpgsql_json(broken)
