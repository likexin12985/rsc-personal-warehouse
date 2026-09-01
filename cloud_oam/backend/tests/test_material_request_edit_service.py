from __future__ import annotations

from dataclasses import replace
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.demand_models import (
    MaterialRequest,
    MaterialRequestLine,
    MaterialRequestRevision,
)
from app.formal_access import load_formal_principal
from app.formal_services.material_request_contact import (
    protect_material_request_contact,
)
from app.formal_services.material_request_edit import (
    MaterialRequestEditableDraftError,
    material_request_editable_draft,
)
from app.formal_services.material_request_approval import (
    MaterialRequestApprovalInput,
    decide_material_request_approval,
)
from app.formal_services.material_request_draft import (
    mask_material_request_contact,
    submit_material_request,
)
from app.formal_services.material_request_policy import ApprovalReturnInstruction

from test_material_request_draft_service import (
    NOW,
    SECRET,
    _create,
    _create_id,
    db,
    make_world,
)
from test_material_request_query_service import _grant


CONTACT_SECRET = b"editable-demand-contact-hmac-secret-32-bytes-minimum"
KMS_KEY_ID = "kms/rsc/material-request/edit-test"


class _RoundTripCipher:
    def __init__(self) -> None:
        self._plaintext_by_ciphertext: dict[bytes, bytes] = {}
        self.decrypt_calls = 0

    def active_key_version(self) -> int:
        return 4

    def encrypt(self, plaintext: bytes, *, aad: bytes, key_version: int):
        ciphertext = hashlib.sha256(aad + plaintext).digest()
        self._plaintext_by_ciphertext[ciphertext] = plaintext
        return SimpleNamespace(
            ciphertext=ciphertext,
            nonce=b"editable-nce",
            key_version=key_version,
        )

    def decrypt(
        self,
        ciphertext: bytes,
        *,
        nonce: bytes,
        aad: bytes,
        key_version: int,
    ) -> bytes:
        self.decrypt_calls += 1
        assert nonce == b"editable-nce"
        assert key_version == 4
        return self._plaintext_by_ciphertext[ciphertext]


def _editable_world(db: Session):
    world = make_world(db)
    _grant(
        db,
        world,
        action="read",
        roles=("technician", "provincial_manager", "admin"),
    )
    db.commit()
    world = replace(
        world,
        actor=load_formal_principal(db, world.actor_user.id, now=NOW),
    )
    request_id = _create_id(world, "editable-draft")
    created = _create(db, world, request_id, key="editable-draft")
    request = db.get(MaterialRequest, request_id)
    assert request is not None
    revision = db.scalar(
        select(MaterialRequestRevision).where(
            MaterialRequestRevision.id == created.revision_id
        )
    )
    assert revision is not None
    cipher = _RoundTripCipher()
    revision.contact_snapshot_jsonb = protect_material_request_contact(
        cipher=cipher,
        kms_key_id=KMS_KEY_ID,
        mobile_hmac_secret=CONTACT_SECRET,
        mobile_hash_version=1,
        request_id=request.id,
        requester_person_id=world.actor_person.id,
        name="张工程师",
        mobile="13800138000",
    )
    revision.contact_masked_jsonb = mask_material_request_contact(
        name="张工程师",
        mobile="13800138000",
    )
    request.contact_snapshot_jsonb = dict(revision.contact_snapshot_jsonb)
    request.contact_masked_jsonb = dict(revision.contact_masked_jsonb)
    db.commit()
    return world, request_id, cipher


def _read(db: Session, world, request_id, cipher):
    return material_request_editable_draft(
        db,
        actor=world.actor,
        request_id=request_id,
        cipher=cipher,
        kms_key_id=KMS_KEY_ID,
        mobile_hmac_secret=CONTACT_SECRET,
        mobile_hash_version=1,
    )


def test_owner_only_editable_projection_is_select_only_and_exact(db: Session) -> None:
    world, request_id, cipher = _editable_world(db)
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        output = _read(db, world, request_id, cipher)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)

    assert set(statements) == {"SELECT"}
    assert output.schema_version == "1.0"
    assert output.request_id == request_id
    assert output.request_version == 0
    assert output.draft.contact.name == "张工程师"
    assert output.draft.contact.mobile == "13800138000"
    assert output.draft.address.detail == "敏感测试地址 1 号"
    assert output.draft.attachment_file_ids == (world.attachment.id,)
    assert len(output.draft.lines) == 1
    assert cipher.decrypt_calls == 1


def test_manager_visibility_does_not_grant_plaintext_edit_access(db: Session) -> None:
    world, request_id, cipher = _editable_world(db)
    manager = load_formal_principal(db, world.manager_users[0].id, now=NOW)

    with pytest.raises(MaterialRequestEditableDraftError) as denied:
        material_request_editable_draft(
            db,
            actor=manager,
            request_id=request_id,
            cipher=cipher,
            kms_key_id=KMS_KEY_ID,
            mobile_hmac_secret=CONTACT_SECRET,
            mobile_hash_version=1,
        )

    assert denied.value.code == "material_request_edit_forbidden"
    assert denied.value.http_status_code == 403
    assert cipher.decrypt_calls == 0


def test_returned_sealed_revision_is_valid_edit_source(db: Session) -> None:
    world, request_id, cipher = _editable_world(db)
    submitted = submit_material_request(
        db,
        actor=world.actor,
        material_request_id=request_id,
        expected_version=0,
        idempotency_key="editable-submit",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-editable-submit",
    )
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
                    required_review_qty=line.requested_qty,
                    reason="补充现场证据",
                ),
            ),
            comment="退回申请人修改",
        ),
        idempotency_key="editable-return",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-editable-return",
    )
    db.commit()

    output = _read(db, world, request_id, cipher)

    assert output.request_version == returned.request_version
    assert output.draft.contact.name == "张工程师"
    assert output.draft.lines[0].requested_qty == line.requested_qty


def test_encrypted_or_masked_projection_tampering_fails_closed(db: Session) -> None:
    world, request_id, cipher = _editable_world(db)
    request = db.get(MaterialRequest, request_id)
    assert request is not None
    revision = db.scalar(
        select(MaterialRequestRevision).where(
            MaterialRequestRevision.request_id == request.id,
            MaterialRequestRevision.revision_no == request.revision_no,
        )
    )
    assert revision is not None
    revision.contact_masked_jsonb = {
        "name_masked": "李**",
        "mobile_masked": "*******9999",
    }
    db.commit()

    with pytest.raises(MaterialRequestEditableDraftError) as failure:
        _read(db, world, request_id, cipher)

    assert failure.value.code == "material_request_edit_contact_projection_invalid"
    assert failure.value.http_status_code == 503


def test_concurrent_version_change_after_decryption_returns_no_snapshot(
    db: Session,
    monkeypatch,
) -> None:
    world, request_id, cipher = _editable_world(db)
    from app.formal_services import material_request_edit as edit_service

    original = edit_service.query_service.material_request_detail
    calls = 0

    def changed_on_reread(*args, **kwargs):
        nonlocal calls
        calls += 1
        detail = original(*args, **kwargs)
        return detail if calls == 1 else detail.model_copy(
            update={"request_version": detail.request_version + 1}
        )

    monkeypatch.setattr(
        edit_service.query_service,
        "material_request_detail",
        changed_on_reread,
    )
    with pytest.raises(MaterialRequestEditableDraftError) as conflict:
        _read(db, world, request_id, cipher)

    assert conflict.value.code == "material_request_edit_snapshot_changed"
    assert conflict.value.http_status_code == 409
    assert cipher.decrypt_calls == 1
