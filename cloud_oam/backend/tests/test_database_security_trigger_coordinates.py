"""A trigger must be checked on each table even when its name is shared."""
import pytest

from app import database_security as security
from test_database_security import (
    _valid_material_request_approval_trigger_rows,
    _assert_valid_material_request_approval_catalog,
    _valid_audit_trigger_rows,
)


@pytest.mark.parametrize('mutation', ['none', 'missing', 'duplicate', 'disabled', 'wrong_table'])
def test_same_trigger_name_does_not_hide_another_table(monkeypatch, mutation):
    rows = _valid_material_request_approval_trigger_rows()
    first = rows[0]
    name = first['trigger_name']
    other_table = 'stock_scrap_recovery_requests'
    expected = security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]
    monkeypatch.setitem(security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS,
        (other_table, name), (other_table, *expected[1:]))
    other = dict(first, table_name=other_table)
    rows.append(other)
    if mutation == 'missing': rows.pop()
    elif mutation == 'duplicate': rows.append(dict(other))
    elif mutation == 'disabled': other['enabled'] = 'D'
    elif mutation == 'wrong_table': other['table_name'] = 'stock_scrap_lines'
    if mutation == 'none':
        _assert_valid_material_request_approval_catalog(monkeypatch, triggers=rows)
    else:
        with pytest.raises(security.DatabaseSecurityBoundaryError, match='material-request approval guard'):
            _assert_valid_material_request_approval_catalog(monkeypatch, triggers=rows)


@pytest.mark.parametrize('mutation', ['none', 'missing', 'duplicate', 'disabled', 'wrong_table'])
def test_audit_event_and_chain_head_triggers_keep_separate_identity(monkeypatch, mutation):
    rows = _valid_audit_trigger_rows()
    first = next(row for row in rows if row['table_name'] == 'audit_events'
                 and row['trigger_name'] in security.EXPECTED_AUDIT_TRIGGERS
                 and ('audit_chain_heads', row['trigger_name']) not in security.EXPECTED_AUDIT_TRIGGERS)
    name = first['trigger_name']
    expected = security.EXPECTED_AUDIT_TRIGGERS[name]
    monkeypatch.setitem(security.EXPECTED_AUDIT_TRIGGERS,
        ('audit_chain_heads', name), ('audit_chain_heads', *expected[1:]))
    other = dict(first, table_name='audit_chain_heads')
    rows.append(other)
    if mutation == 'missing': rows.pop()
    elif mutation == 'duplicate': rows.append(dict(other))
    elif mutation == 'disabled': other['enabled'] = 'D'
    elif mutation == 'wrong_table': other['table_name'] = 'stock_scrap_lines'
    if mutation == 'none':
        security._assert_audit_trigger_guards(rows)
    else:
        with pytest.raises(security.DatabaseSecurityBoundaryError, match='audit trigger guard'):
            security._assert_audit_trigger_guards(rows)
