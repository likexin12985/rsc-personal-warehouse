from dataclasses import replace
from datetime import timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import select, update

from app.demand_models import MaterialRequest, MaterialRequestLine
from app.formal_access import load_formal_principal
from app.formal_services.material_request_overview import material_request_overview
from app.formal_services.material_request_query import MaterialRequestReadError
from app.material_request_overview_schemas import MaterialRequestOverviewOut
from app.models import User
from test_material_request_query_service import (
    NOW, SECRET, _read_world, _extra_actor, _organization, _create_id, _draft,
    _grant, _submit_one, db, create_material_request_draft, amend_material_request_draft,
    submit_material_request, decide_material_request_approval, MaterialRequestApprovalInput,
    ApprovalReturnInstruction,
)
from test_material_request_lifecycle_service import _approved_request


def create_draft(db, world, key):
    identifier = _create_id(world, key)
    create_material_request_draft(db, actor=world.actor, material_request_id=identifier,
        draft=replace(_draft(world, identifier), attachment_file_ids=()),
        idempotency_key=key, idempotency_hmac_secret=SECRET, trace_request_id='trace-'+key)
    db.commit()
    return identifier


def principal(db, world, role='manager'):
    user = world.manager_users[0] if role == 'manager' else world.admin_users[0]
    return load_formal_principal(db, user.id, now=NOW)


def test_report_scope_counts_all_requests_without_contacts_or_side_effects(db):
    world = _read_world(db)
    other_region = _organization(db, 'OTHER-REGION', 'region_company', parent=world.headquarters)
    other = _extra_actor(db, world, person_name='外区域工程师', organization=other_region)
    create_draft(db, world, 'overview-own-1')
    create_draft(db, world, 'overview-own-2')
    create_draft(db, other, 'overview-other')
    manager, admin = principal(db, world), principal(db, world, 'admin')
    with patch.object(db, 'flush', side_effect=AssertionError('report flushed')), patch.object(db, 'commit', side_effect=AssertionError('report committed')):
        regional = material_request_overview(db, actor=manager, now=NOW)
        national = material_request_overview(db, actor=admin, now=NOW)
    assert regional.matched_requests == 2
    assert national.matched_requests == 3
    assert regional.counts['request_status']['draft'] == 2
    assert regional.counts['approval_level_1']['not_started'] == 2
    assert all(sum(counts.values()) == 2 for counts in regional.counts.values())
    assert 'ciphertext' not in regional.model_dump_json()
    assert 'contact' not in regional.model_dump_json()
    for actor, organization in [(world.actor, None), (manager, other_region.id)]:
        with pytest.raises(MaterialRequestReadError) as denied:
            material_request_overview(db, actor=actor, organization_id=organization, now=NOW)
        assert denied.value.status_code == 403


def test_full_approval_is_independent_of_fulfillment_and_inbound(db):
    world, request, _, _ = _approved_request(db, key='overview-approved')
    _grant(db, world, action='read', roles=('admin', 'provincial_manager'))
    db.commit()
    result = material_request_overview(db, actor=principal(db, world), now=NOW)
    assert result.matched_requests == 1
    assert result.counts['request_status']['approved'] == 1
    for level in (1, 2, 3):
        assert result.counts[f'approval_level_{level}']['approved'] == 1
    assert result.counts['reservation_status']['not_reserved'] == 1
    assert result.counts['shipment_status']['not_started'] == 1
    assert result.counts['personal_inbound_status']['not_started'] == 1
    assert result.counts['personal_inbound_status']['posted'] == 0


def test_resubmission_counts_current_revision_once(db):
    world = _read_world(db)
    identifier, _, submitted = _submit_one(db, world, 'overview-rework')
    line = db.scalar(select(MaterialRequestLine).where(MaterialRequestLine.revision_id == submitted.revision_id))
    manager = principal(db, world)
    returned = decide_material_request_approval(db, actor=manager,
        material_request_id=identifier, approval_step_id=submitted.approval_step_ids[0],
        expected_request_version=submitted.version, expected_step_version=0,
        decision=MaterialRequestApprovalInput(action='return', return_lines=(ApprovalReturnInstruction(
            request_line_id=line.id, required_review_qty=Decimal('2.000'), reason='补充证据'),), comment='补充证据'),
        idempotency_key='overview-return', idempotency_hmac_secret=SECRET, trace_request_id='overview-return')
    amended = amend_material_request_draft(db, actor=world.actor, material_request_id=identifier,
        expected_version=returned.request_version, draft=replace(_draft(world, identifier), purpose='补充后的需求'),
        idempotency_key='overview-amend', idempotency_hmac_secret=SECRET, trace_request_id='overview-amend')
    submit_material_request(db, actor=world.actor, material_request_id=identifier,
        expected_version=amended.version, idempotency_key='overview-resubmit',
        idempotency_hmac_secret=SECRET, trace_request_id='overview-resubmit')
    db.commit()
    result = material_request_overview(db, actor=manager, now=NOW)
    assert result.matched_requests == 1
    assert result.counts['approval_level_1']['open'] == 1
    assert result.counts['approval_level_1']['returned'] == 0


def test_creation_interval_is_half_open_and_empty_is_explicit_zero(db):
    world = _read_world(db)
    identifier = create_draft(db, world, 'overview-time')
    created = db.get(MaterialRequest, identifier).created_at.replace(tzinfo=timezone.utc)
    actor = principal(db, world)
    found = material_request_overview(db, actor=actor, created_from=created,
        created_before=created+timedelta(seconds=1), now=NOW)
    empty = material_request_overview(db, actor=actor, created_before=created, now=NOW)
    assert found.matched_requests == 1
    assert empty.matched_requests == 0
    assert all(value == 0 for values in empty.counts.values() for value in values.values())
    with pytest.raises(MaterialRequestReadError) as invalid:
        material_request_overview(db, actor=actor, created_from=created.replace(tzinfo=None), now=NOW)
    assert invalid.value.status_code == 422


def test_revocation_after_aggregate_blocks_output(db):
    world = _read_world(db)
    create_draft(db, world, 'overview-revoked')
    actor = principal(db, world)
    original = db.execute
    changed = False
    def execute(statement, *args, **kwargs):
        nonlocal changed
        result = original(statement, *args, **kwargs)
        if not changed and 'overview_requests' in str(statement):
            changed = True
            original(update(User).where(User.id == actor.user_id).values(authorization_version=User.authorization_version+1))
        return result
    with patch.object(db, 'execute', side_effect=execute), pytest.raises(MaterialRequestReadError) as revoked:
        material_request_overview(db, actor=actor, now=NOW)
    assert changed
    assert revoked.value.status_code == 412


def test_output_contract_rejects_incomplete_or_inflated_axes(db):
    world = _read_world(db)
    result = material_request_overview(db, actor=principal(db, world), now=NOW)
    body = result.model_dump()
    body['counts']['personal_inbound_status']['posted'] = 1
    with pytest.raises(ValidationError):
        MaterialRequestOverviewOut.model_validate(body)
