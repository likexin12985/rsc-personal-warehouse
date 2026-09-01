from __future__ import annotations

from dataclasses import replace
from datetime import timezone
from decimal import Decimal
import json
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.demand_models import (
    ApprovalExternalRegistration,
    ApprovalInstance,
    ApprovalStep,
    MaterialRequest,
    MaterialRequestLine,
)
from app.formal_access import load_formal_principal
from app.formal_services.material_request_approval import (
    ExternalApprovalRegistrationInput,
    MaterialRequestApprovalInput,
    decide_material_request_approval,
    register_external_approval_evidence,
)
from app.formal_services.material_request_policy import (
    ApprovalLineDecision,
    ApprovalReturnInstruction,
)
from app.formal_services.material_request_query import (
    MaterialRequestReadError,
    list_material_requests,
    material_request_detail,
)
from app.formal_services import formal_files
from app.formal_services import material_request_query as query_service
from app.foundation_models import FileObject, Permission, RolePermission

from test_material_request_draft_service import (
    NOW,
    SECRET,
    World,
    _assignment,
    _create,
    _create_id,
    _draft,
    _organization,
    _person,
    _user,
    amend_material_request_draft,
    create_material_request_draft,
    db,
    make_world,
    submit_material_request,
)


def _grant(
    db: Session,
    world: World,
    *,
    action: str,
    field_code: str = "",
    roles: tuple[str, ...],
) -> None:
    permission = db.scalar(
        select(Permission).where(
            Permission.resource == "material_request",
            Permission.action == action,
            Permission.field_code == field_code,
        )
    )
    if permission is None:
        permission = Permission(
            resource="material_request",
            action=action,
            field_code=field_code,
            description="query service test",
        )
        db.add(permission)
        db.flush()
    for role_code in roles:
        exists = db.scalar(
            select(RolePermission.id).where(
                RolePermission.role_id == world.roles[role_code].id,
                RolePermission.permission_id == permission.id,
            )
        )
        if exists is None:
            db.add(
                RolePermission(
                    role_id=world.roles[role_code].id,
                    permission_id=permission.id,
                    effect="allow",
                )
            )
    db.flush()


def _read_world(db: Session, *, star_payload: bool = False) -> World:
    world = make_world(db)
    _grant(
        db,
        world,
        action="read",
        roles=("technician", "provincial_manager", "admin"),
    )
    if star_payload:
        _grant(
            db,
            world,
            action="read_star_approval",
            field_code="approval_payload",
            roles=("admin",),
        )
    db.commit()
    return replace(
        world,
        actor=load_formal_principal(db, world.actor_user.id, now=NOW),
    )


def _extra_actor(
    db: Session,
    world: World,
    *,
    person_name: str,
    organization,
) -> World:
    person = _person(db, organization, person_name)
    user = _user(db, person)
    _assignment(
        db,
        user,
        world.roles["technician"],
        scope_type="person",
        scope_id=str(person.id),
        assigned_by=world.actor_user.id,
    )
    db.commit()
    principal = load_formal_principal(db, user.id, now=NOW)
    return replace(world, actor_user=user, actor_person=person, actor=principal)


def _submit_one(db: Session, world: World, key: str):
    request_id = _create_id(world, key)
    created = _create(db, world, request_id, key=key)
    submitted = submit_material_request(
        db,
        actor=world.actor,
        material_request_id=request_id,
        expected_version=created.request_version,
        idempotency_key=f"{key}-submit",
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}-submit",
    )
    db.commit()
    return request_id, created, submitted


def test_scope_masking_stable_pagination_and_fixed_batch_query_count(db: Session) -> None:
    world = _read_world(db)
    same_region = _extra_actor(
        db,
        world,
        person_name="同区域工程师",
        organization=world.department,
    )
    other_region = _organization(
        db,
        "OTHER-REGION",
        "region_company",
        parent=world.headquarters,
    )
    other_department = _organization(
        db,
        "OTHER-DEPT",
        "department",
        parent=other_region,
    )
    other = _extra_actor(
        db,
        world,
        person_name="外区域工程师",
        organization=other_department,
    )
    actors = (world, same_region, other)
    request_ids = []
    for index, actor_world in enumerate(actors, start=1):
        request_id = _create_id(actor_world, f"query-scope-{index}")
        create_material_request_draft(
            db,
            actor=actor_world.actor,
            material_request_id=request_id,
            draft=replace(
                _draft(actor_world, request_id),
                attachment_file_ids=(),
            ),
            idempotency_key=f"query-scope-{index}",
            idempotency_hmac_secret=SECRET,
            trace_request_id=f"trace-query-scope-{index}",
        )
        request_ids.append(request_id)
    db.commit()

    manager = load_formal_principal(db, world.manager_users[0].id, now=NOW)
    admin = load_formal_principal(db, world.admin_users[0].id, now=NOW)
    with patch.object(db, "flush", side_effect=AssertionError("query flushed")), patch.object(
        db, "commit", side_effect=AssertionError("query committed")
    ), patch.object(db, "rollback", side_effect=AssertionError("query rolled back")):
        engineer_page = list_material_requests(
            db, actor=world.actor, limit=100, now=NOW
        )
        manager_page = list_material_requests(db, actor=manager, limit=100, now=NOW)
        admin_page = list_material_requests(db, actor=admin, limit=100, now=NOW)
        detail = material_request_detail(
            db,
            actor=world.actor,
            request_id=request_ids[0],
            now=NOW,
        )

    assert [item.request_id for item in engineer_page.items] == [request_ids[0]]
    assert {item.request_id for item in manager_page.items} == {
        request_ids[0],
        request_ids[1],
    }
    assert {item.request_id for item in admin_page.items} == set(request_ids)
    assert detail.allowed_actions == ("update", "submit")
    serialized = json.dumps(detail.model_dump(mode="json"), ensure_ascii=False)
    request = db.get(MaterialRequest, request_ids[0])
    assert request is not None
    assert request.contact_snapshot_jsonb["ciphertext_b64"] not in serialized
    assert "contact_snapshot_jsonb" not in serialized
    assert "address_snapshot_jsonb" not in serialized
    assert "permission_keys" not in serialized

    with pytest.raises(MaterialRequestReadError) as hidden:
        material_request_detail(
            db,
            actor=world.actor,
            request_id=request_ids[1],
            now=NOW,
        )
    assert (hidden.value.code, hidden.value.status_code) == (
        "material_request_not_found",
        404,
    )

    ordered = tuple(sorted((request_ids[0], request_ids[1])))
    first = list_material_requests(db, actor=manager, limit=1, now=NOW)
    assert tuple(item.request_id for item in first.items) == ordered[:1]
    assert first.next_after_id == ordered[1]
    second = list_material_requests(
        db,
        actor=manager,
        limit=1,
        after_id=first.next_after_id,
        now=NOW,
    )
    assert tuple(item.request_id for item in second.items) == ordered[1:]
    assert second.next_after_id is None

    counts: list[int] = []
    engine = db.get_bind()
    for limit in (1, 100):
        count = 0

        def before_cursor(_connection, _cursor, statement, _params, _context, _many):
            nonlocal count
            if statement.lstrip().upper().startswith("SELECT"):
                count += 1

        event.listen(engine, "before_cursor_execute", before_cursor)
        try:
            list_material_requests(db, actor=manager, limit=limit, now=NOW)
        finally:
            event.remove(engine, "before_cursor_execute", before_cursor)
        counts.append(count)
    assert counts[0] == counts[1]
    assert counts[0] <= 20


def test_current_candidate_actions_self_review_and_stale_principal(db: Session) -> None:
    world = _read_world(db)
    request_id, _created, submitted = _submit_one(db, world, "query-actions")
    manager = load_formal_principal(db, world.manager_users[0].id, now=NOW)
    admin = load_formal_principal(db, world.admin_users[0].id, now=NOW)

    manager_detail = material_request_detail(
        db, actor=manager, request_id=request_id, now=NOW
    )
    requester_detail = material_request_detail(
        db, actor=world.actor, request_id=request_id, now=NOW
    )
    admin_detail = material_request_detail(
        db, actor=admin, request_id=request_id, now=NOW
    )
    assert manager_detail.approval_instance is not None
    assert manager_detail.approval_instance.current_step_id == submitted.approval_step_ids[0]
    assert manager_detail.allowed_actions == ("approve", "return", "reject")
    assert not {"approve", "return", "reject"}.intersection(
        requester_detail.allowed_actions
    )
    assert admin_detail.allowed_actions == ()

    manager_user = world.manager_users[0]
    manager_user.authorization_version += 1
    db.flush()
    with pytest.raises(MaterialRequestReadError) as stale:
        material_request_detail(db, actor=manager, request_id=request_id, now=NOW)
    assert (stale.value.code, stale.value.status_code) == (
        "material_request_actor_principal_stale",
        412,
    )
    refreshed_manager = load_formal_principal(db, manager_user.id, now=NOW)
    refreshed_detail = material_request_detail(
        db,
        actor=refreshed_manager,
        request_id=request_id,
        now=NOW,
    )
    assert refreshed_detail.allowed_actions == ()


def test_detail_keeps_complete_revision_and_approval_attempt_history(db: Session) -> None:
    world = _read_world(db)
    request_id, _created, submitted = _submit_one(db, world, "query-history")
    line = db.scalar(
        select(MaterialRequestLine).where(
            MaterialRequestLine.revision_id == submitted.revision_id
        )
    )
    assert line is not None
    manager = load_formal_principal(db, world.manager_users[0].id, now=NOW)
    returned = decide_material_request_approval(
        db,
        actor=manager,
        material_request_id=request_id,
        approval_step_id=submitted.approval_step_ids[0],
        expected_request_version=submitted.version,
        expected_step_version=0,
        decision=MaterialRequestApprovalInput(
            action="return",
            return_lines=(
                ApprovalReturnInstruction(
                    request_line_id=line.id,
                    required_review_qty=Decimal("2.000"),
                    reason="补充现场照片",
                ),
            ),
            comment="退回申请人补充证据",
        ),
        idempotency_key="query-history-return",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-query-history-return",
    )
    amended = amend_material_request_draft(
        db,
        actor=world.actor,
        material_request_id=request_id,
        expected_version=returned.request_version,
        draft=replace(_draft(world, request_id), purpose="补充证据后的维修需求"),
        idempotency_key="query-history-amend",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-query-history-amend",
    )
    resubmitted = submit_material_request(
        db,
        actor=world.actor,
        material_request_id=request_id,
        expected_version=amended.version,
        idempotency_key="query-history-resubmit",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-query-history-resubmit",
    )
    db.commit()

    detail = material_request_detail(
        db,
        actor=world.actor,
        request_id=request_id,
        now=NOW,
    )
    assert [item.revision_no for item in detail.revision_history] == [1, 2]
    assert [item.attempt_no for item in detail.approval_history] == [1, 2]
    assert detail.approval_history[0].status == "superseded"
    assert detail.approval_history[0].steps[0].status == "returned"
    assert detail.approval_history[0].return_line_facts is not None
    assert detail.approval_history[0].return_line_facts[0].reason == "补充现场照片"
    assert detail.approval_instance == detail.approval_history[-1]
    assert detail.approval_instance is not None
    assert detail.approval_instance.instance_id == resubmitted.approval_instance_id


def test_external_evidence_is_permission_gated_masked_and_two_person_action(db: Session) -> None:
    world = _read_world(db, star_payload=True)
    request_id, _created, submitted = _submit_one(db, world, "query-external")
    line = db.scalar(
        select(MaterialRequestLine).where(
            MaterialRequestLine.revision_id == submitted.revision_id
        )
    )
    assert line is not None
    manager = load_formal_principal(db, world.manager_users[0].id, now=NOW)
    region_result = decide_material_request_approval(
        db,
        actor=manager,
        material_request_id=request_id,
        approval_step_id=submitted.approval_step_ids[0],
        expected_request_version=submitted.version,
        expected_step_version=0,
        decision=MaterialRequestApprovalInput(
            action="approve",
            lines=(ApprovalLineDecision(line.id, Decimal("2.000")),),
        ),
        idempotency_key="query-external-region",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-query-external-region",
    )
    admin_1 = load_formal_principal(db, world.admin_users[0].id, now=NOW)
    admin_2 = load_formal_principal(db, world.admin_users[1].id, now=NOW)
    headquarters_result = decide_material_request_approval(
        db,
        actor=admin_1,
        material_request_id=request_id,
        approval_step_id=region_result.current_step_id,
        expected_request_version=region_result.request_version,
        expected_step_version=region_result.opened_step_version if hasattr(region_result, "opened_step_version") else 1,
        decision=MaterialRequestApprovalInput(
            action="approve",
            lines=(ApprovalLineDecision(line.id, Decimal("2.000")),),
        ),
        idempotency_key="query-external-headquarters",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-query-external-headquarters",
    )
    step_3 = db.get(ApprovalStep, headquarters_result.current_step_id)
    assert step_3 is not None
    with patch.object(
        query_service,
        "_permission_allowed",
        wraps=query_service._permission_allowed,
    ) as permission_check:
        registration_ready = material_request_detail(
            db,
            actor=admin_1,
            request_id=request_id,
            now=NOW,
        )
    assert registration_ready.allowed_actions == ("register_external_approval",)
    register_checks = [
        call.kwargs
        for call in permission_check.call_args_list
        if call.kwargs.get("action") == "register_external"
    ]
    assert len(register_checks) == 1
    assert register_checks[0]["target_scope_type"] == "organization"
    assert register_checks[0]["target_scope_id"] == str(world.region.id)

    external_decided_at = step_3.opened_at
    assert external_decided_at is not None
    if external_decided_at.tzinfo is None:
        external_decided_at = external_decided_at.replace(tzinfo=timezone.utc)
    evidence_file_id = uuid.uuid4()
    evidence_storage_key = (
        "formal-files/v1/external_approval_evidence/"
        f"{evidence_file_id.hex[:2]}/{evidence_file_id.hex}"
    )
    evidence_request_sha256 = formal_files._upload_request_hash(
        formal_files._PreparedUpload(
            purpose="external_approval_evidence",
            original_filename="star-approval.pdf",
            size_bytes=256,
            mime_type="application/pdf",
            sha256="e" * 64,
        )
    )
    evidence_file = FileObject(
        id=evidence_file_id,
        storage_key=evidence_storage_key,
        sha256="e" * 64,
        size_bytes=256,
        mime_type="application/pdf",
        original_filename="star-approval.pdf",
        uploaded_by=admin_1.user_id,
        status="available",
        metadata_jsonb={
            "authorization_version": admin_1.authorization_version,
            "file_id": str(evidence_file_id),
            "idempotency_key_hash": "a" * 64,
            "provider": "aliyun_oss_v2",
            "purpose": "external_approval_evidence",
            "request_sha256": evidence_request_sha256,
            "schema": "cloud_oam.formal_file_upload_intent.v1",
            "storage_key": evidence_storage_key,
            "uploader_person_id": str(admin_1.person_id),
            "uploader_user_id": admin_1.user_id,
            "completion": {
                "etag_sha256": "b" * 64,
                "head_manifest_sha256": "c" * 64,
                "verified_at": NOW.isoformat(),
            },
        },
        created_at=NOW,
    )
    db.add(evidence_file)
    db.flush()
    registered = register_external_approval_evidence(
        db,
        actor=admin_1,
        material_request_id=request_id,
        approval_step_id=step_3.id,
        expected_request_version=headquarters_result.request_version,
        expected_step_version=step_3.version,
        registration=ExternalApprovalRegistrationInput(
            evidence_file_id=evidence_file.id,
            external_approver_name="星星总部王审批员",
            external_reference_no="STAR-APPROVAL-001",
            external_decided_at=external_decided_at,
            action="approve",
            lines=(ApprovalLineDecision(line.id, Decimal("2.000")),),
        ),
        idempotency_key="query-external-register",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-query-external-register",
    )
    db.commit()

    verifier = material_request_detail(
        db, actor=admin_2, request_id=request_id, now=NOW
    )
    registrar = material_request_detail(
        db, actor=admin_1, request_id=request_id, now=NOW
    )
    assert verifier.allowed_actions == ("verify_external_approval",)
    assert registrar.allowed_actions == ()
    assert verifier.approval_instance is not None
    evidence = verifier.approval_instance.external_evidence_summaries
    assert evidence is not None and len(evidence) == 1
    assert evidence[0].registration_id == registered.registration_id
    assert evidence[0].external_approver_name_masked.startswith("星*")
    serialized = repr(verifier.model_dump(mode="json"))
    assert "星星总部王审批员" not in serialized
    assert "external_approver_snapshot_jsonb" not in serialized
    assert "registered_by_user_id" not in serialized

    permission = db.scalar(
        select(Permission).where(
            Permission.resource == "material_request",
            Permission.action == "read_star_approval",
            Permission.field_code == "approval_payload",
        )
    )
    assert permission is not None
    admin_role_permission = db.scalar(
        select(RolePermission).where(
            RolePermission.role_id == world.roles["admin"].id,
            RolePermission.permission_id == permission.id,
        )
    )
    assert admin_role_permission is not None
    db.delete(admin_role_permission)
    db.flush()
    admin_without_field = load_formal_principal(db, world.admin_users[1].id, now=NOW)
    hidden = material_request_detail(
        db,
        actor=admin_without_field,
        request_id=request_id,
        now=NOW,
    )
    assert hidden.approval_instance is not None
    assert hidden.approval_instance.external_evidence_summaries is None


def test_query_rejects_invalid_limits_and_preserves_supply_as_read_only(db: Session) -> None:
    world = _read_world(db)
    request_id = _create_id(world, "query-invalid-limit")
    _create(db, world, request_id, key="query-invalid-limit")
    db.commit()
    with pytest.raises(MaterialRequestReadError) as invalid:
        list_material_requests(db, actor=world.actor, limit=0, now=NOW)
    assert (invalid.value.code, invalid.value.status_code) == (
        "material_request_page_limit_invalid",
        422,
    )
