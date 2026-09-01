from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy import inspect

from app.models import Base


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"
REVISION_0028 = "20260831_0028"
REVISION_0029 = "20260831_0029"
NOW = "2026-08-31 00:00:00+00:00"
LATER = "2026-08-31 00:00:01+00:00"
HASH_A = "a" * 64
HASH_B = "b" * 64


def _uuid(number: int) -> str:
    return uuid.UUID(int=number).hex


def _contact_envelope(request_id: str, requester_person_id: str) -> dict[str, object]:
    request_uuid = str(uuid.UUID(hex=request_id))
    person_uuid = str(uuid.UUID(hex=requester_person_id))
    aad = (
        "cloud_oam.material_request.contact.envelope.v1\0"
        f"request_id={request_uuid}\0requester_person_id={person_uuid}"
    ).encode("ascii")
    return {
        "schema": "rsc.material_request_contact.v1",
        "provider": "aliyun_kms",
        "kms_key_id": "kms/rsc/material-request/schema-test",
        "key_version": 1,
        "ciphertext_b64": base64.b64encode(b"\x91" * 32).decode("ascii"),
        "nonce_b64": base64.b64encode(b"\x72" * 12).decode("ascii"),
        "aad_sha256": hashlib.sha256(aad).hexdigest(),
        "mobile_hmac": f"hmac:1:{HASH_B}",
        "contact_hmac": f"hmac:1:{HASH_A}",
    }


def _config(database_url: str, *, output_buffer=None) -> Config:
    config = Config(str(ALEMBIC_INI), output_buffer=output_buffer)
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _migrated(tmp_path: Path, name: str) -> tuple[Config, sa.Engine]:
    url = f"sqlite+pysqlite:///{tmp_path / name}"
    config = _config(url)
    command.upgrade(config, REVISION_0029)
    engine = sa.create_engine(url)
    return config, engine


def _insert_principal_graph(connection: sa.Connection, seed: int) -> dict[str, str]:
    ids = {
        "source": _uuid(seed + 1),
        "org": _uuid(seed + 2),
        "material": _uuid(seed + 3),
        "substitute_material": _uuid(seed + 4),
        "material_object": _uuid(seed + 5),
        "substitute_object": _uuid(seed + 6),
        "requester_person": _uuid(seed + 10),
        "region_person": _uuid(seed + 11),
        "hq_person": _uuid(seed + 12),
        "registrar_person": _uuid(seed + 13),
        "verifier_person": _uuid(seed + 14),
        "requester_assignment": _uuid(seed + 20),
        "region_assignment": _uuid(seed + 21),
        "hq_assignment": _uuid(seed + 22),
        "registrar_assignment": _uuid(seed + 23),
        "verifier_assignment": _uuid(seed + 24),
        "request_file": _uuid(seed + 30),
        "second_request_file": _uuid(seed + 31),
        "pending_file": _uuid(seed + 32),
        "evidence_file": _uuid(seed + 33),
        "delegation_file": _uuid(seed + 34),
    }
    ids.update(
        {
            "requester_user": f"req-{seed}",
            "region_user": f"region-{seed}",
            "hq_user": f"hq-{seed}",
            "registrar_user": f"registrar-{seed}",
            "verifier_user": f"verifier-{seed}",
        }
    )
    connection.exec_driver_sql(
        "INSERT INTO source_systems "
        "(id, code, name, mode, enabled, configuration_jsonb, created_at, updated_at) "
        "VALUES (?, ?, ?, 'read_only', 1, '{}', ?, ?)",
        (ids["source"], f"src-{seed}", f"Source {seed}", NOW, NOW),
    )
    for object_id, external_id in (
        (ids["material_object"], f"material-{seed}"),
        (ids["substitute_object"], f"substitute-{seed}"),
    ):
        connection.exec_driver_sql(
            "INSERT INTO external_objects "
            "(id, source_system_id, entity_type, external_id, current_version_id, "
            "deleted_at, created_at, updated_at) VALUES (?, ?, 'material', ?, "
            "NULL, NULL, ?, ?)",
            (object_id, ids["source"], external_id, NOW, NOW),
        )
    connection.exec_driver_sql(
        "INSERT INTO organizations "
        "(id, external_object_id, code, name, parent_id, org_type, province_code, "
        "status, created_at, updated_at) VALUES (?, NULL, ?, ?, NULL, "
        "'region_company', '320000', 'active', ?, ?)",
        (ids["org"], f"org-{seed}", f"Region {seed}", NOW, NOW),
    )
    principal_rows = (
        ("requester", "requester_user", "requester_person"),
        ("region", "region_user", "region_person"),
        ("hq", "hq_user", "hq_person"),
        ("registrar", "registrar_user", "registrar_person"),
        ("verifier", "verifier_user", "verifier_person"),
    )
    for ordinal, (label, user_key, person_key) in enumerate(principal_rows, start=1):
        connection.exec_driver_sql(
            "INSERT INTO people "
            "(id, external_object_id, organization_id, employee_no, name, "
            "mobile_encrypted, mobile_hash, employment_status, source_updated_at, "
            "created_at, updated_at) VALUES (?, NULL, ?, ?, ?, NULL, NULL, "
            "'active', NULL, ?, ?)",
            (
                ids[person_key],
                ids["org"],
                f"E-{seed}-{ordinal}",
                f"{label}-{seed}",
                NOW,
                NOW,
            ),
        )
        connection.exec_driver_sql(
            "INSERT INTO users "
            "(id, mobile, name, password_hash, role, province, is_active, "
            "require_password_change, person_id, account_status, last_login_at, "
            "authorization_version, created_at, updated_at) VALUES (?, ?, ?, "
            "'not-used', 'technician', '江苏', 1, 0, ?, 'active', NULL, 1, ?, ?)",
            (
                ids[user_key],
                f"18{seed % 100000000:08d}{ordinal}",
                f"{label}-{seed}",
                ids[person_key],
                NOW,
                NOW,
            ),
        )
    role_by_assignment = {
        "requester_assignment": "10000000000040008000000000000003",
        "region_assignment": "10000000000040008000000000000002",
        "hq_assignment": "10000000000040008000000000000001",
        "registrar_assignment": "10000000000040008000000000000001",
        "verifier_assignment": "10000000000040008000000000000001",
    }
    identity_by_assignment = {
        "requester_assignment": ("requester_user", "person", ids["requester_person"]),
        "region_assignment": ("region_user", "organization", ids["org"]),
        "hq_assignment": ("hq_user", "national", "*"),
        "registrar_assignment": ("registrar_user", "national", "*"),
        "verifier_assignment": ("verifier_user", "national", "*"),
    }
    for assignment_key, role_id in role_by_assignment.items():
        user_key, scope_type, scope_id = identity_by_assignment[assignment_key]
        connection.exec_driver_sql(
            "INSERT INTO role_assignments "
            "(id, user_id, role_id, scope_type, scope_id, valid_from, valid_to, "
            "status, assigned_by, revoked_at, revoked_by, reason, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, NULL, 'active', ?, NULL, "
            "NULL, 'schema test', ?, ?)",
            (
                ids[assignment_key],
                ids[user_key],
                role_id,
                scope_type,
                scope_id,
                NOW,
                ids[user_key],
                NOW,
                NOW,
            ),
        )
    for material_id, object_id, sku in (
        (ids["material"], ids["material_object"], f"SKU-{seed}"),
        (
            ids["substitute_material"],
            ids["substitute_object"],
            f"SUB-{seed}",
        ),
    ):
        connection.exec_driver_sql(
            "INSERT INTO materials "
            "(id, external_object_id, sku_code, name, specification, base_unit, "
            "status, source_updated_at, created_at, updated_at) VALUES (?, ?, ?, "
            "?, '', '件', 'active', ?, ?, ?)",
            (material_id, object_id, sku, sku, NOW, NOW, NOW),
        )
    for file_id, status, suffix in (
        (ids["request_file"], "available", "request"),
        (ids["second_request_file"], "available", "supplement"),
        (ids["pending_file"], "pending", "pending"),
        (ids["evidence_file"], "available", "evidence"),
        (ids["delegation_file"], "available", "delegation"),
    ):
        connection.exec_driver_sql(
            "INSERT INTO files "
            "(id, storage_key, sha256, size_bytes, mime_type, original_filename, "
            "uploaded_by, status, metadata_jsonb, created_at) VALUES (?, ?, ?, 10, "
            "'application/pdf', ?, ?, ?, '{}', ?)",
            (
                file_id,
                f"tests/{seed}/{suffix}.pdf",
                f"{ordinal:064x}"[-64:],
                f"{suffix}.pdf",
                ids["requester_user"],
                status,
                NOW,
            ),
        )
    return ids


def _insert_request(
    connection: sa.Connection,
    ids: dict[str, str],
    seed: int,
    *,
    status: str = "draft",
    requested_qty: str = "10.000",
) -> dict[str, str]:
    request_id = _uuid(seed + 100)
    line_id = _uuid(seed + 101)
    revision_id = _uuid(seed + 103)
    address = json.dumps(
        {
            "province_code": "320000",
            "province_name": "江苏省",
            "city_name": "南京市",
            "district_name": "鼓楼区",
            "detail": "敏感测试地址 1 号",
        }
    )
    address_masked = json.dumps(
        {
            "province_code": "320000",
            "province_name": "江苏省",
            "city_name": "南京市",
            "district_name": "鼓楼区",
            "detail_masked": "敏感测试地址***号",
        }
    )
    contact = json.dumps(_contact_envelope(request_id, ids["requester_person"]))
    contact_masked = json.dumps(
        {"name_masked": "测*", "mobile_masked": "138****0000"}
    )
    # Match the production write order: the complete mutable line set exists
    # before submission makes request quantities and line membership immutable.
    connection.exec_driver_sql(
        "INSERT INTO material_requests "
        "(id, request_no, requester_user_id, requester_person_id, requester_org_id, "
        "work_order_id, purpose, urgency, expected_date, address_snapshot_jsonb, "
        "address_masked_jsonb, contact_snapshot_jsonb, contact_masked_jsonb, "
        "note, approval_mode, status, revision_no, version, "
        "submitted_at, decided_at, withdrawn_at, cancelled_at, created_by_user_id, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, NULL, 'repair', 'normal', "
        "NULL, ?, ?, ?, ?, '', 'external_registration', 'draft', 1, 0, NULL, NULL, "
        "NULL, NULL, ?, ?, ?)",
        (
            request_id,
            f"MR-{seed}",
            ids["requester_user"],
            ids["requester_person"],
            ids["org"],
            address,
            address_masked,
            contact,
            contact_masked,
            ids["requester_user"],
            NOW,
            NOW,
        ),
    )
    connection.exec_driver_sql(
        "INSERT INTO material_request_revisions "
        "(id, request_id, revision_no, previous_revision_id, work_order_id, "
        "purpose, urgency, expected_date, address_snapshot_jsonb, "
        "address_masked_jsonb, contact_snapshot_jsonb, contact_masked_jsonb, "
        "note, approval_mode, status, content_manifest_sha256, sealed_at, "
        "sealed_by_user_id, created_by_user_id, created_at, updated_at) "
        "VALUES (?, ?, 1, NULL, NULL, 'repair', 'normal', NULL, ?, ?, ?, ?, '', "
        "'external_registration', 'draft', NULL, NULL, NULL, ?, ?, ?)",
        (
            revision_id,
            request_id,
            address,
            address_masked,
            contact,
            contact_masked,
            ids["requester_user"],
            NOW,
            NOW,
        ),
    )
    connection.exec_driver_sql(
        "INSERT INTO material_request_lines "
        "(id, request_id, revision_id, revision_no, line_no, client_line_key, material_id, "
        "suggested_substitute_material_id, requested_qty, required_date, note, "
        "status, final_approved_qty, cancelled_qty, version, created_at, updated_at) "
        "VALUES (?, ?, ?, 1, 1, ?, ?, ?, ?, NULL, 'line note', ?, ?, 0, 0, ?, ?)",
        (
            line_id,
            request_id,
            revision_id,
            _uuid(seed + 102),
            ids["material"],
            ids["substitute_material"],
            requested_qty,
            "draft",
            "0.000",
            NOW,
            NOW,
        ),
    )
    if status != "draft":
        line_status = "approval_pending"
        final_approved_qty = "0.000"
        decided_at = None
        if status == "partially_approved":
            line_status = "partially_approved"
            final_approved_qty = "5.000"
            decided_at = NOW
        elif status == "approved":
            line_status = "approved"
            final_approved_qty = requested_qty
            decided_at = NOW
        elif status == "rejected":
            line_status = "rejected"
            decided_at = NOW
        connection.exec_driver_sql(
            "UPDATE material_request_lines SET status=?, final_approved_qty=?, "
            "updated_at=? WHERE id=?",
            (line_status, final_approved_qty, NOW, line_id),
        )
        connection.exec_driver_sql(
            "UPDATE material_request_revisions SET status='sealed', "
            "content_manifest_sha256=?, sealed_at=?, sealed_by_user_id=?, "
            "updated_at=? WHERE id=?",
            (HASH_A, NOW, ids["requester_user"], NOW, revision_id),
        )
        connection.exec_driver_sql(
            "UPDATE material_requests SET status=?, submitted_at=?, decided_at=?, "
            "updated_at=? WHERE id=?",
            (status, NOW, decided_at, NOW, request_id),
        )
    return {"request": request_id, "revision": revision_id, "line": line_id}


def _insert_approval_graph(
    connection: sa.Connection,
    ids: dict[str, str],
    request: dict[str, str],
    seed: int,
) -> dict[str, str]:
    graph = {
        "instance": _uuid(seed + 200),
        "step1": _uuid(seed + 201),
        "step2": _uuid(seed + 202),
        "step3": _uuid(seed + 203),
    }
    connection.exec_driver_sql(
        "UPDATE material_request_revisions SET status='sealed', "
        "content_manifest_sha256=?, sealed_at=?, sealed_by_user_id=?, "
        "updated_at=? WHERE id=?",
        (HASH_A, NOW, ids["requester_user"], NOW, request["revision"]),
    )
    connection.exec_driver_sql(
        "UPDATE material_requests SET status = 'approval_in_progress', "
        "submitted_at = ?, updated_at = ? WHERE id = ?",
        (NOW, NOW, request["request"]),
    )
    connection.exec_driver_sql(
        "UPDATE material_request_lines SET status = 'approval_pending', "
        "updated_at = ? WHERE id = ?",
        (NOW, request["line"]),
    )
    connection.exec_driver_sql(
        "INSERT INTO approval_instances "
        "(id, request_id, request_revision_id, revision_no, route_version_id, "
        "attempt_no, status, current_step_no, version, completed_at, created_at, "
        "updated_at) VALUES (?, ?, ?, 1, ?, 1, 'active', 1, 0, NULL, ?, ?)",
        (
            graph["instance"],
            request["request"],
            request["revision"],
            "22000000000040008000000000000001",
            NOW,
            NOW,
        ),
    )
    for step_id, step_no, predecessor, source, status, assignee in (
        (graph["step1"], 1, None, "internal", "open", ids["region_user"]),
        (graph["step2"], 2, graph["step1"], "internal", "pending", ids["hq_user"]),
        (graph["step3"], 3, graph["step2"], "external_registration", "pending", None),
    ):
        connection.exec_driver_sql(
            "INSERT INTO approval_steps "
            "(id, instance_id, step_no, attempt_no, predecessor_step_id, "
            "source_mode, status, assignee_user_id, assignee_snapshot_jsonb, "
            "decision_manifest_sha256, opened_at, decided_at, version, "
            "created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?, ?, '{}', NULL, "
            "?, NULL, 0, ?, ?)",
            (
                step_id,
                graph["instance"],
                step_no,
                predecessor,
                source,
                status,
                assignee,
                NOW if status == "open" else None,
                NOW,
                NOW,
            ),
        )
    candidates = (
        (graph["step1"], "region", "assignee"),
        (graph["step2"], "hq", "assignee"),
        (graph["step3"], "registrar", "registrar"),
        (graph["step3"], "verifier", "verifier"),
    )
    for ordinal, (step_id, prefix, kind) in enumerate(candidates, start=1):
        connection.exec_driver_sql(
            "INSERT INTO approval_step_candidates "
            "(id, step_id, user_id, person_id, role_assignment_id, "
            "authorization_version, candidate_kind, snapshot_jsonb, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, '{}', ?)",
            (
                _uuid(seed + 210 + ordinal),
                step_id,
                ids[f"{prefix}_user"],
                ids[f"{prefix}_person"],
                ids[f"{prefix}_assignment"],
                kind,
                NOW,
            ),
        )
    return graph


def test_0029_orm_contract_registers_exact_state_axes_and_input_evidence() -> None:
    required = {
        "material_requests",
        "material_request_revisions",
        "material_request_lines",
        "material_request_files",
        "approval_route_versions",
        "approval_route_step_defs",
        "approval_instances",
        "approval_steps",
        "approval_step_candidates",
        "material_request_commands",
        "approval_external_registrations",
        "approval_external_registration_lines",
        "approval_step_line_decisions",
        "approval_actions",
        "substitution_decisions",
        "supply_tasks",
    }
    assert required <= set(Base.metadata.tables)
    request = Base.metadata.tables["material_requests"]
    defaults = {
        name: str(request.c[name].server_default.arg)
        for name in (
            "allocation_status",
            "reservation_status",
            "outbound_status",
            "shipment_status",
            "logistics_signature_status",
            "oam_receipt_status",
            "personal_inbound_status",
            "notification_status",
            "reconciliation_status",
        )
    }
    assert defaults == {
        "allocation_status": "not_allocated",
        "reservation_status": "not_reserved",
        "outbound_status": "not_started",
        "shipment_status": "not_started",
        "logistics_signature_status": "not_signed",
        "oam_receipt_status": "not_occurred",
        "personal_inbound_status": "not_started",
        "notification_status": "not_started",
        "reconciliation_status": "not_started",
    }
    line = Base.metadata.tables["material_request_lines"]
    assert {
        "revision_id",
        "revision_no",
        "suggested_substitute_material_id",
        "note",
    } <= set(line.c.keys())
    revision = Base.metadata.tables["material_request_revisions"]
    assert {
        "previous_revision_id",
        "address_masked_jsonb",
        "contact_snapshot_jsonb",
        "contact_masked_jsonb",
        "content_manifest_sha256",
        "sealed_at",
    } <= set(revision.c.keys())
    assert {
        "address_masked_jsonb",
        "contact_masked_jsonb",
    } <= set(request.c.keys())
    instance = Base.metadata.tables["approval_instances"]
    assert {"request_revision_id", "revision_no"} <= set(instance.c.keys())
    assert "derived projection only" in (line.c.final_approved_qty.comment or "")
    assert "derived projection only" in (line.c.cancelled_qty.comment or "")
    audit_checks = " ".join(
        str(row.sqltext)
        for row in Base.metadata.tables["audit_events"].constraints
        if isinstance(row, sa.CheckConstraint)
    )
    assert "'material_request'" in audit_checks


def test_0029_empty_upgrade_seeds_only_static_route_permissions_and_audit_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    config, engine = _migrated(tmp_path, "demand-empty.db")
    try:
        inspector = inspect(engine)
        assert "material_requests" in inspector.get_table_names()
        assert "material_request_files" in inspector.get_table_names()
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0029
            assert connection.exec_driver_sql(
                "SELECT id FROM audit_chain_heads "
                "WHERE stream_key = 'material_request'"
            ).scalar_one() == "30000000000040008000000000000004"
            route = connection.exec_driver_sql(
                "SELECT step_no, role_code, source_mode, scope_type "
                "FROM approval_route_step_defs ORDER BY step_no"
            ).all()
            assert route == [
                (1, "provincial_manager", "internal", "organization"),
                (2, "admin", "internal", "national"),
                (3, "star_headquarters_approver", "external_registration", "document"),
            ]
            assert connection.exec_driver_sql(
                "SELECT COUNT(*) FROM permissions "
                "WHERE resource IN ('material_request', 'supply_task')"
            ).scalar_one() == 16
            assert connection.exec_driver_sql(
                "SELECT COUNT(*) FROM material_requests"
            ).scalar_one() == 0
        command.downgrade(config, REVISION_0028)
        assert "material_requests" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_0029_submission_freezes_original_quantity_and_attachment_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    _, engine = _migrated(tmp_path, "demand-freeze.db")
    try:
        with engine.begin() as connection:
            ids = _insert_principal_graph(connection, 1000)
            request = _insert_request(connection, ids, 1000)
            connection.exec_driver_sql(
                "INSERT INTO material_request_files "
                "(id, request_id, revision_id, revision_no, request_line_id, "
                "file_id, purpose, created_by_user_id, created_at) "
                "VALUES (?, ?, ?, 1, NULL, ?, "
                "'request_attachment', ?, ?)",
                (
                    _uuid(1150),
                    request["request"],
                    request["revision"],
                    ids["request_file"],
                    ids["requester_user"],
                    NOW,
                ),
            )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO material_request_files "
                    "(id, request_id, revision_id, revision_no, request_line_id, "
                    "file_id, purpose, created_by_user_id, created_at) "
                    "VALUES (?, ?, ?, 1, NULL, ?, "
                    "'request_attachment', ?, ?)",
                    (
                        _uuid(1151),
                        request["request"],
                        request["revision"],
                        ids["pending_file"],
                        ids["requester_user"],
                        NOW,
                    ),
                )
            connection.exec_driver_sql(
                "UPDATE material_request_revisions SET status='sealed', "
                "content_manifest_sha256=?, sealed_at=?, sealed_by_user_id=?, "
                "updated_at=? WHERE id=?",
                (HASH_A, NOW, ids["requester_user"], NOW, request["revision"]),
            )
            connection.exec_driver_sql(
                "UPDATE material_requests SET status = 'approval_in_progress', "
                "submitted_at = ?, updated_at = ? WHERE id = ?",
                (NOW, NOW, request["request"]),
            )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "UPDATE material_request_lines SET requested_qty = 11 "
                    "WHERE id = ?",
                    (request["line"],),
                )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "DELETE FROM material_request_files WHERE request_id = ?",
                    (request["request"],),
                )
    finally:
        engine.dispose()


def test_0029_contact_envelope_and_masked_read_projection_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    _, engine = _migrated(tmp_path, "demand-contact-guard.db")
    try:
        with engine.begin() as connection:
            ids = _insert_principal_graph(connection, 1500)
            request = _insert_request(connection, ids, 1500)
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "UPDATE material_requests SET contact_masked_jsonb=? WHERE id=?",
                    (
                        json.dumps(
                            {
                                "name_masked": "李珂鑫",
                                "mobile_masked": "13800138000",
                            }
                        ),
                        request["request"],
                    ),
                )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "UPDATE material_request_revisions SET contact_snapshot_jsonb="
                    "json_set(contact_snapshot_jsonb, '$.name', 'plaintext') "
                    "WHERE id=?",
                    (request["revision"],),
                )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "UPDATE material_request_revisions SET contact_snapshot_jsonb="
                    "json_set(contact_snapshot_jsonb, '$.contact_hmac', 'bad') "
                    "WHERE id=?",
                    (request["revision"],),
                )
    finally:
        engine.dispose()


def test_0029_returned_amend_appends_revision_and_preserves_old_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    _, engine = _migrated(tmp_path, "demand-revision-chain.db")
    try:
        with engine.begin() as connection:
            ids = _insert_principal_graph(connection, 1750)
            request = _insert_request(connection, ids, 1750)
            graph = _insert_approval_graph(connection, ids, request, 1750)
            connection.exec_driver_sql(
                "UPDATE approval_instances SET status='returned', current_step_no=NULL, "
                "completed_at=?, updated_at=? WHERE id=?",
                (LATER, LATER, graph["instance"]),
            )
            connection.exec_driver_sql(
                "UPDATE material_requests SET status='returned', updated_at=? WHERE id=?",
                (LATER, request["request"]),
            )
            revision2 = _uuid(1900)
            connection.exec_driver_sql(
                "INSERT INTO material_request_revisions "
                "(id, request_id, revision_no, previous_revision_id, work_order_id, "
                "purpose, urgency, expected_date, address_snapshot_jsonb, "
                "address_masked_jsonb, contact_snapshot_jsonb, contact_masked_jsonb, "
                "note, approval_mode, status, content_manifest_sha256, sealed_at, "
                "sealed_by_user_id, created_by_user_id, created_at, updated_at) "
                "SELECT ?, request_id, 2, id, work_order_id, purpose, urgency, "
                "expected_date, address_snapshot_jsonb, address_masked_jsonb, "
                "contact_snapshot_jsonb, contact_masked_jsonb, note, approval_mode, "
                "'draft', NULL, NULL, NULL, ?, ?, ? "
                "FROM material_request_revisions WHERE id=?",
                (
                    revision2,
                    ids["requester_user"],
                    LATER,
                    LATER,
                    request["revision"],
                ),
            )
            connection.exec_driver_sql(
                "UPDATE material_requests SET revision_no=2, version=version+1, "
                "updated_at=? WHERE id=?",
                (LATER, request["request"]),
            )
            line2 = _uuid(1901)
            connection.exec_driver_sql(
                "INSERT INTO material_request_lines "
                "(id, request_id, revision_id, revision_no, line_no, client_line_key, "
                "material_id, suggested_substitute_material_id, requested_qty, "
                "required_date, note, status, final_approved_qty, cancelled_qty, "
                "version, created_at, updated_at) VALUES (?, ?, ?, 2, 1, ?, ?, "
                "NULL, 12, NULL, 'returned supplement', 'draft', 0, 0, 0, ?, ?)",
                (
                    line2,
                    request["request"],
                    revision2,
                    _uuid(1902),
                    ids["material"],
                    LATER,
                    LATER,
                ),
            )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "UPDATE material_request_lines SET requested_qty=9 WHERE id=?",
                    (request["line"],),
                )
            connection.exec_driver_sql(
                "UPDATE material_request_revisions SET status='sealed', "
                "content_manifest_sha256=?, sealed_at=?, sealed_by_user_id=?, "
                "updated_at=? WHERE id=?",
                (HASH_B, LATER, ids["requester_user"], LATER, revision2),
            )
            connection.exec_driver_sql(
                "UPDATE approval_instances SET status='superseded', updated_at=? "
                "WHERE id=?",
                (LATER, graph["instance"]),
            )
            instance2 = _uuid(1903)
            connection.exec_driver_sql(
                "INSERT INTO approval_instances "
                "(id, request_id, request_revision_id, revision_no, route_version_id, "
                "attempt_no, status, current_step_no, version, completed_at, "
                "created_at, updated_at) VALUES (?, ?, ?, 2, ?, 2, 'active', 1, "
                "0, NULL, ?, ?)",
                (
                    instance2,
                    request["request"],
                    revision2,
                    "22000000000040008000000000000001",
                    LATER,
                    LATER,
                ),
            )
            assert connection.exec_driver_sql(
                "SELECT COUNT(*) FROM material_request_revisions WHERE request_id=?",
                (request["request"],),
            ).scalar_one() == 2
            assert connection.exec_driver_sql(
                "SELECT COUNT(*) FROM material_request_lines WHERE request_id=?",
                (request["request"],),
            ).scalar_one() == 2
            assert connection.exec_driver_sql(
                "SELECT request_revision_id FROM approval_instances WHERE id=?",
                (graph["instance"],),
            ).scalar_one() == request["revision"]
    finally:
        engine.dispose()


def test_0029_higher_stage_quantity_is_causal_and_decisions_are_append_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    _, engine = _migrated(tmp_path, "demand-quantity.db")
    try:
        with engine.begin() as connection:
            ids = _insert_principal_graph(connection, 2000)
            request = _insert_request(connection, ids, 2000)
            graph = _insert_approval_graph(connection, ids, request, 2000)
            decision1 = _uuid(2250)
            connection.exec_driver_sql(
                "INSERT INTO approval_step_line_decisions "
                "(id, step_id, request_line_id, input_qty, approved_qty, "
                "rejected_qty, reason, decision_source, external_registration_id, "
                "decided_by_user_id, decided_by_person_id, "
                "decided_role_assignment_id, authorization_version, decided_at, "
                "created_at) VALUES (?, ?, ?, 10, 8, 2, 'reduce', 'internal', "
                "NULL, ?, ?, ?, 1, ?, ?)",
                (
                    decision1,
                    graph["step1"],
                    request["line"],
                    ids["region_user"],
                    ids["region_person"],
                    ids["region_assignment"],
                    NOW,
                    NOW,
                ),
            )
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status = 'partially_approved', "
                "decision_manifest_sha256 = ?, decided_at = ?, updated_at = ? "
                "WHERE id = ?",
                (HASH_A, NOW, NOW, graph["step1"]),
            )
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status = 'open', opened_at = ?, "
                "updated_at = ? WHERE id = ?",
                (NOW, NOW, graph["step2"]),
            )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO approval_step_line_decisions "
                    "(id, step_id, request_line_id, input_qty, approved_qty, "
                    "rejected_qty, reason, decision_source, external_registration_id, "
                    "decided_by_user_id, decided_by_person_id, "
                    "decided_role_assignment_id, authorization_version, decided_at, "
                    "created_at) VALUES (?, ?, ?, 9, 8, 1, 'bad input', "
                    "'internal', NULL, ?, ?, ?, 1, ?, ?)",
                    (
                        _uuid(2251),
                        graph["step2"],
                        request["line"],
                        ids["hq_user"],
                        ids["hq_person"],
                        ids["hq_assignment"],
                        NOW,
                        NOW,
                    ),
                )
            decision2 = _uuid(2252)
            connection.exec_driver_sql(
                "INSERT INTO approval_step_line_decisions "
                "(id, step_id, request_line_id, input_qty, approved_qty, "
                "rejected_qty, reason, decision_source, external_registration_id, "
                "decided_by_user_id, decided_by_person_id, "
                "decided_role_assignment_id, authorization_version, decided_at, "
                "created_at) VALUES (?, ?, ?, 8, 6, 2, 'reduce again', "
                "'internal', NULL, ?, ?, ?, 1, ?, ?)",
                (
                    decision2,
                    graph["step2"],
                    request["line"],
                    ids["hq_user"],
                    ids["hq_person"],
                    ids["hq_assignment"],
                    NOW,
                    NOW,
                ),
            )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "UPDATE approval_step_line_decisions SET reason = 'tamper' "
                    "WHERE id = ?",
                    (decision2,),
                )
    finally:
        engine.dispose()


def test_0029_external_evidence_requires_separate_verifier_and_exact_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    _, engine = _migrated(tmp_path, "demand-external.db")
    try:
        with engine.begin() as connection:
            ids = _insert_principal_graph(connection, 3000)
            request = _insert_request(connection, ids, 3000)
            graph = _insert_approval_graph(connection, ids, request, 3000)
            for step_id, actor, person, assignment, input_qty, approved_qty in (
                (graph["step1"], "region_user", "region_person", "region_assignment", 10, 8),
                (graph["step2"], "hq_user", "hq_person", "hq_assignment", 8, 6),
            ):
                if step_id == graph["step2"]:
                    connection.exec_driver_sql(
                        "UPDATE approval_steps SET status='partially_approved', "
                        "decision_manifest_sha256=?, decided_at=?, updated_at=? "
                        "WHERE id=?",
                        (HASH_A, NOW, NOW, graph["step1"]),
                    )
                    connection.exec_driver_sql(
                        "UPDATE approval_steps SET status='open', opened_at=?, "
                        "updated_at=? WHERE id=?",
                        (NOW, NOW, graph["step2"]),
                    )
                connection.exec_driver_sql(
                    "INSERT INTO approval_step_line_decisions "
                    "(id, step_id, request_line_id, input_qty, approved_qty, "
                    "rejected_qty, reason, decision_source, external_registration_id, "
                    "decided_by_user_id, decided_by_person_id, "
                    "decided_role_assignment_id, authorization_version, decided_at, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, 'bounded', 'internal', "
                    "NULL, ?, ?, ?, 1, ?, ?)",
                    (
                        _uuid(3300 + input_qty),
                        step_id,
                        request["line"],
                        input_qty,
                        approved_qty,
                        input_qty - approved_qty,
                        ids[actor],
                        ids[person],
                        ids[assignment],
                        NOW,
                        NOW,
                    ),
                )
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status='partially_approved', "
                "decision_manifest_sha256=?, decided_at=?, updated_at=? WHERE id=?",
                (HASH_B, NOW, NOW, graph["step2"]),
            )
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status='awaiting_external_evidence', "
                "opened_at=?, updated_at=? WHERE id=?",
                (NOW, NOW, graph["step3"]),
            )
            registration = _uuid(3350)
            connection.exec_driver_sql(
                "INSERT INTO approval_external_registrations "
                "(id, step_id, registration_no, external_action, status, "
                "evidence_file_id, external_approver_snapshot_jsonb, "
                "external_decided_at, decision_manifest_sha256, "
                "registered_by_user_id, registered_by_person_id, "
                "registered_role_assignment_id, authorization_version, "
                "registered_at, verified_by_user_id, verified_by_person_id, "
                "verified_role_assignment_id, verified_authorization_version, "
                "verification_comment, verified_at, version, created_at, updated_at) "
                "VALUES (?, ?, ?, 'partial_approve', 'pending_verification', ?, "
                "'{}', ?, ?, ?, ?, ?, 1, ?, NULL, NULL, NULL, NULL, '', NULL, 0, ?, ?)",
                (
                    registration,
                    graph["step3"],
                    "EXT-3000",
                    ids["evidence_file"],
                    NOW,
                    HASH_A,
                    ids["registrar_user"],
                    ids["registrar_person"],
                    ids["registrar_assignment"],
                    NOW,
                    NOW,
                    NOW,
                ),
            )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO approval_external_registration_lines "
                    "(id, registration_id, request_line_id, input_qty, "
                    "approved_qty, rejected_qty, reason, created_at) "
                    "VALUES (?, ?, ?, 7, 5, 2, 'wrong predecessor', ?)",
                    (_uuid(3351), registration, request["line"], NOW),
                )
            connection.exec_driver_sql(
                "INSERT INTO approval_external_registration_lines "
                "(id, registration_id, request_line_id, input_qty, approved_qty, "
                "rejected_qty, reason, created_at) VALUES (?, ?, ?, 6, 5, 1, "
                "'external reduction', ?)",
                (_uuid(3352), registration, request["line"], NOW),
            )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "UPDATE approval_external_registrations SET status='accepted', "
                    "verified_by_user_id=?, verified_by_person_id=?, "
                    "verified_role_assignment_id=?, verified_authorization_version=1, "
                    "verified_at=?, updated_at=? WHERE id=?",
                    (
                        ids["registrar_user"],
                        ids["registrar_person"],
                        ids["registrar_assignment"],
                        NOW,
                        NOW,
                        registration,
                    ),
                )
            connection.exec_driver_sql(
                "UPDATE approval_external_registrations SET status='accepted', "
                "verified_by_user_id=?, verified_by_person_id=?, "
                "verified_role_assignment_id=?, verified_authorization_version=1, "
                "verified_at=?, updated_at=? WHERE id=?",
                (
                    ids["verifier_user"],
                    ids["verifier_person"],
                    ids["verifier_assignment"],
                    LATER,
                    LATER,
                    registration,
                ),
            )
    finally:
        engine.dispose()


def test_0029_supply_tasks_are_shortage_plans_bounded_by_final_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    _, engine = _migrated(tmp_path, "demand-supply.db")
    try:
        with engine.begin() as connection:
            ids = _insert_principal_graph(connection, 4000)
            request = _insert_request(
                connection, ids, 4000, status="partially_approved"
            )
            connection.exec_driver_sql(
                "INSERT INTO supply_tasks "
                "(id, task_no, request_line_id, substitution_decision_id, "
                "supply_type, reference_no, expected_qty, original_equivalent_qty, "
                "expected_date, status, created_by_user_id, created_by_person_id, "
                "created_role_assignment_id, authorization_version, "
                "cancelled_by_user_id, cancelled_at, version, created_at, updated_at) "
                "VALUES (?, 'ST-4000-1', ?, NULL, 'headquarters_replenishment', "
                "NULL, 3, 3, NULL, 'open', ?, ?, ?, 1, NULL, NULL, 0, ?, ?)",
                (
                    _uuid(4250),
                    request["line"],
                    ids["hq_user"],
                    ids["hq_person"],
                    ids["hq_assignment"],
                    NOW,
                    NOW,
                ),
            )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO supply_tasks "
                    "(id, task_no, request_line_id, substitution_decision_id, "
                    "supply_type, reference_no, expected_qty, original_equivalent_qty, "
                    "expected_date, status, created_by_user_id, created_by_person_id, "
                    "created_role_assignment_id, authorization_version, "
                    "cancelled_by_user_id, cancelled_at, version, created_at, updated_at) "
                    "VALUES (?, 'ST-4000-2', ?, NULL, 'star_replenishment', NULL, "
                    "3, 3, NULL, 'awaiting_supply', ?, ?, ?, 1, NULL, NULL, 0, ?, ?)",
                    (
                        _uuid(4251),
                        request["line"],
                        ids["hq_user"],
                        ids["hq_person"],
                        ids["hq_assignment"],
                        NOW,
                        NOW,
                    ),
                )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "UPDATE supply_tasks SET status='fulfilled' WHERE id=?",
                    (_uuid(4250),),
                )
    finally:
        engine.dispose()


def test_0029_downgrade_refuses_business_facts_before_any_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    config, engine = _migrated(tmp_path, "demand-downgrade-blocked.db")
    try:
        with engine.begin() as connection:
            ids = _insert_principal_graph(connection, 5000)
            _insert_request(connection, ids, 5000)
        with pytest.raises(RuntimeError, match="material-request facts"):
            command.downgrade(config, REVISION_0028)
        assert "material_requests" in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0029
    finally:
        engine.dispose()


def test_0029_postgresql_offline_sql_contains_guards_and_independent_audit_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    command.upgrade(
        _config(
            "postgresql+psycopg://migration_user:password@localhost/rsc",
            output_buffer=output,
        ),
        f"{REVISION_0028}:{REVISION_0029}",
        sql=True,
    )
    sql = output.getvalue()
    assert "'material_request'" in sql
    assert "30000000-0000-4000-8000-000000000004" in sql
    assert "rsc_guard_material_request_decision_quantity_0029" in sql
    assert "rsc_guard_material_request_revision_0029" in sql
    assert "rsc_guard_material_request_approval_instance_0029" in sql
    assert "rsc_guard_external_registration_core_0029" in sql
    assert "rsc_guard_supply_task_quantity_0029" in sql
    assert "BEFORE TRUNCATE ON public.material_request_commands" in sql
    assert "contact_hmac" in sql
    assert "address_masked_jsonb" in sql
    assert "request_revision_id" in sql
    assert "sha256(" in sql
    assert "GRANT INSERT" not in sql
    assert "GRANT UPDATE" not in sql
    assert "fulfilled" not in sql[sql.index("CREATE TABLE supply_tasks") :]
