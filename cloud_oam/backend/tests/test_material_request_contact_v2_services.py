"""Focused service evidence on synthetic ORM SQLite, not migration/PG16 gates."""

from dataclasses import replace
import json
import uuid

import pytest
from sqlalchemy import select

from app.demand_models import MaterialRequest, MaterialRequestRevision, MaterialRequestLine
from app.formal_access import load_formal_principal
from app.formal_services import material_request_edit as edit_service
from app.formal_services.material_request_approval import (
    MaterialRequestApprovalInput, decide_material_request_approval,
)
from app.formal_services.material_request_draft import (
    MaterialRequestDraftError, amend_material_request_draft,
    create_material_request_draft, submit_material_request,
)
from app.formal_services.material_request_policy import ApprovalReturnInstruction
from test_material_request_draft_service import (
    NOW, SECRET as COMMAND_SECRET, _create_id, _draft, db, make_world,
)
from test_material_request_edit_service import _editable_world
from test_material_request_contact_v2 import (
    SECRET, make_cipher, protect, no_network,
)


def _install_v2(db):
    world, request_id, legacy = _editable_world(db)
    cipher, transport = make_cipher(legacy=legacy)
    # The retained legacy reader from this fixture uses its original key id.
    cipher.legacy_kms_key_id = "kms/rsc/material-request/edit-test"
    request = db.get(MaterialRequest, request_id)
    revision = db.scalar(select(MaterialRequestRevision).where(
        MaterialRequestRevision.request_id == request_id,
        MaterialRequestRevision.revision_no == request.revision_no,
    ))
    envelope = protect(cipher, request=request_id, person=world.actor_person.id)
    request.contact_snapshot_jsonb = dict(envelope)
    revision.contact_snapshot_jsonb = dict(envelope)
    db.commit()
    return world, request_id, cipher, transport


def _read(db, world, request_id, cipher, actor=None):
    return edit_service.material_request_editable_draft(
        db, actor=actor or world.actor, request_id=request_id, cipher=cipher,
        kms_key_id="", mobile_hmac_secret=SECRET, mobile_hash_version=1,
    )


def test_v2_owner_edit_and_masked_general_read_retain_permissions(db):
    world, request_id, cipher, transport = _install_v2(db)
    transport.calls.clear()
    detail = edit_service.query_service.material_request_detail(db, actor=world.actor, request_id=request_id)
    serialized = detail.model_dump_json()
    assert "13800138000" not in serialized and "张工程师" not in serialized
    assert "ciphertext_b64" not in serialized
    assert _read(db, world, request_id, cipher).draft.contact.mobile == "13800138000"
    assert len(transport.calls) == 1
    manager = load_formal_principal(db, world.manager_users[0].id, now=NOW)
    with pytest.raises(edit_service.MaterialRequestEditableDraftError) as denied:
        _read(db, world, request_id, cipher, actor=manager)
    assert denied.value.code == "material_request_edit_forbidden"
    assert len(transport.calls) == 1


def test_v2_owner_edit_rechecks_revision_after_authenticated_decryption(db, monkeypatch):
    world, request_id, cipher, transport = _install_v2(db)
    original = edit_service.query_service.material_request_detail
    calls = 0

    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        detail = original(*args, **kwargs)
        return detail if calls == 1 else detail.model_copy(update={"request_version": detail.request_version + 1})

    monkeypatch.setattr(edit_service.query_service, "material_request_detail", changed)
    with pytest.raises(edit_service.MaterialRequestEditableDraftError) as denied:
        _read(db, world, request_id, cipher)
    assert denied.value.code == "material_request_edit_snapshot_changed"
    assert calls == 2
    assert len(transport.calls) == 2  # One protection and one decryption.


def test_v2_draft_create_and_submit_recompute_versioned_binding(db):
    world = make_world(db)
    request_id = _create_id(world, "contact-v2-create")
    cipher, _ = make_cipher()
    draft = replace(_draft(world, request_id), contact_envelope=protect(
        cipher, request=request_id, person=world.actor_person.id,
    ))
    created = create_material_request_draft(
        db, actor=world.actor, material_request_id=request_id, draft=draft,
        idempotency_key="contact-v2-create", idempotency_hmac_secret=COMMAND_SECRET,
        trace_request_id="contact-v2-create",
    )
    submitted = submit_material_request(
        db, actor=world.actor, material_request_id=request_id, expected_version=created.request_version,
        idempotency_key="contact-v2-submit", idempotency_hmac_secret=COMMAND_SECRET,
        trace_request_id="contact-v2-submit",
    )
    assert submitted.revision_id == created.revision_id
    revision = db.get(MaterialRequestRevision, created.revision_id)
    assert revision.status == "sealed"
    assert revision.contact_snapshot_jsonb["schema"] == "rsc.material_request_contact.v2"


def test_v2_draft_cross_request_binding_fails_before_persisting(db):
    world = make_world(db)
    request_id = _create_id(world, "contact-v2-wrong")
    cipher, _ = make_cipher()
    draft = replace(_draft(world, request_id), contact_envelope=protect(
        cipher, request=uuid.uuid4(), person=world.actor_person.id,
    ))
    with pytest.raises(MaterialRequestDraftError) as failure:
        create_material_request_draft(
            db, actor=world.actor, material_request_id=request_id, draft=draft,
            idempotency_key="contact-v2-wrong", idempotency_hmac_secret=COMMAND_SECRET,
            trace_request_id="contact-v2-wrong",
        )
    assert failure.value.code == "material_request_contact_binding_invalid"
    assert db.get(MaterialRequest, request_id) is None


def test_v2_current_revision_preserves_v1_sealed_history(db):
    world, request_id, legacy = _editable_world(db)
    submitted = submit_material_request(
        db, actor=world.actor, material_request_id=request_id, expected_version=0,
        idempotency_key="contact-v1-submit", idempotency_hmac_secret=COMMAND_SECRET,
        trace_request_id="contact-v1-submit",
    )
    old = db.get(MaterialRequestRevision, submitted.revision_id)
    old_snapshot = json.dumps(old.contact_snapshot_jsonb, sort_keys=True)
    line = db.scalar(select(MaterialRequestLine).where(MaterialRequestLine.revision_id == old.id))
    returned = decide_material_request_approval(
        db, actor=load_formal_principal(db, world.manager_users[0].id, now=NOW),
        material_request_id=request_id, approval_step_id=submitted.approval_step_ids[0],
        expected_request_version=submitted.version, expected_step_version=0,
        decision=MaterialRequestApprovalInput(
            action="return", return_lines=(ApprovalReturnInstruction(
                request_line_id=line.id, required_review_qty=line.requested_qty, reason="补充信息",
            ),), comment="补充信息",
        ), idempotency_key="contact-return", idempotency_hmac_secret=COMMAND_SECRET,
        trace_request_id="contact-return",
    )
    cipher, _ = make_cipher(legacy=legacy)
    draft = replace(_draft(world, request_id), contact_envelope=protect(
        cipher, request=request_id, person=world.actor_person.id,
    ))
    amended = amend_material_request_draft(
        db, actor=world.actor, material_request_id=request_id,
        expected_version=returned.request_version, draft=draft,
        idempotency_key="contact-v2-amend", idempotency_hmac_secret=COMMAND_SECRET,
        trace_request_id="contact-v2-amend",
    )
    current = db.get(MaterialRequestRevision, amended.revision_id)
    assert current.previous_revision_id == old.id and current.id != old.id
    assert current.contact_snapshot_jsonb["schema"] == "rsc.material_request_contact.v2"
    assert old.contact_snapshot_jsonb["schema"] == "rsc.material_request_contact.v1"
    assert json.dumps(old.contact_snapshot_jsonb, sort_keys=True) == old_snapshot
    assert old.status == "sealed"
