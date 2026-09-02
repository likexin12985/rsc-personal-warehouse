from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deployment" / "redact_legacy_oam_work_order_details.sql"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _normalized() -> str:
    return " ".join(_source().upper().split())


def test_redaction_artifact_defaults_to_non_executing_review():
    source = _source()
    normalized = _normalized()

    assert "\\set ON_ERROR_STOP on" in source
    assert "edge_source_instance is required" in source
    assert "scope_key is required" in source
    assert "\\set replacement_snapshot_id ''" in source
    assert "\\set expected_audit_sha256 ''" in source
    assert "\\set confirm_cleanup ''" in source
    assert "DRY_RUN complete; no data was changed" in source
    assert "ROLLBACK" in normalized
    assert (
        "I_UNDERSTAND_PURGE_LEGACY_OAM_WORK_ORDER_DETAILS" in source
    )
    assert "cleanup_confirmation_exact" in source
    assert "cleanup_replacement_exact" in source
    assert "cleanup_audit_hash_exact" in source
    assert "expected_audit_sha256" in source


def test_redaction_artifact_serializes_the_exact_source_scope():
    source = _source()
    normalized = _normalized()

    assert "BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE" in normalized
    assert "PG_TRY_ADVISORY_XACT_LOCK" in normalized
    assert "HASHTEXTEXTENDED" in normalized
    assert "EXTERNAL-SYNC-SCOPE:" in normalized
    assert "LENGTH(CLEANUP_INPUT.EDGE_SOURCE_INSTANCE)" in normalized
    assert "LENGTH(CLEANUP_INPUT.SCOPE_KEY)" in normalized
    assert "FOR UPDATE" in normalized
    assert "LOCK_TIMEOUT" in normalized
    assert "STATEMENT_TIMEOUT" in normalized
    assert "SESSION_USER <> 'STAR_OAM_MIGRATOR'" in normalized
    assert "CURRENT_USER <> 'STAR_OAM_MIGRATOR'" in normalized
    assert "PG_GET_USERBYID" in normalized
    assert "PG_CONSTRAINT" in normalized
    assert re.search(r"CONSTRAINT_ROW\.CONTYP(?!E)", normalized) is None
    assert "CONSTRAINT_ROW.CONTYPE = 'F'" in normalized
    assert "'PUBLIC.EXTERNAL_SYNC_CURRENT_RECORDS'::REGCLASS" in normalized


def test_redaction_requires_a_newer_complete_work_order_only_full_snapshot():
    normalized = _normalized()

    for boundary in (
        "SNAPSHOT.SOURCE_SYSTEM = 'STARCHARGE_OAM'",
        "SNAPSHOT.STATUS = 'COMPLETE'",
        "SNAPSHOT.SYNC_MODE = 'FULL'",
        "IS JSON OBJECT WITH UNIQUE KEYS",
        "REPLACEMENT.MANIFEST_SHA256",
        "SHA256",
        "JSONB_ARRAY_LENGTH",
        "<> 'WORK_ORDER'",
        "FINAL_RECORD_COUNT",
        "DELTA_RECORD_COUNT",
        "BATCH_COUNT",
        "RECORD.OPERATION <> 'UPSERT'",
        "CURRENT_RECORD.LAST_SNAPSHOT_ID <> REPLACEMENT.ID",
        "EXCEPT",
        "CANDIDATE.SNAPSHOT_AT >= REPLACEMENT.SNAPSHOT_AT",
        "CANDIDATE.RECEIVED_AT >= REPLACEMENT.COMPLETED_AT",
    ):
        assert boundary in normalized

    assert re.search(
        r"COALESCE\s*\(\s*PG_CATALOG\.JSONB_ARRAY_LENGTH\s*"
        r"\([^)]*ENTITIES[^)]*\)\s*,\s*-1\s*\)\s*<>\s*1",
        normalized,
    )
    assert "WORK_ORDER_DETAIL', 'WORK_ORDER_RELATION" in normalized


def test_redaction_proves_each_replacement_row_is_the_exact_seven_field_contract():
    source = _source()
    normalized = _normalized()

    assert "LEGACY_OAM_DETAIL_CLEANUP_REPLACEMENT_ROWS" in normalized
    assert "IS JSON OBJECT WITH UNIQUE KEYS" in normalized
    assert "JSONB_OBJECT_LENGTH(ROW_EVIDENCE.PARSED_PAYLOAD) <> 7" in normalized
    for field in (
        "authCompanyId",
        "code",
        "executorId",
        "id",
        "province",
        "statusCode",
        "updateTime",
    ):
        assert f"'{field}'" in source
    assert "CANONICAL_PAYLOAD_JSON <> ROW_EVIDENCE.PAYLOAD_JSON" in normalized
    assert "ROW_EVIDENCE.PAYLOAD_SHA256 <> PG_CATALOG.ENCODE" in normalized
    assert (
        "ROW_EVIDENCE.PAYLOAD_SOURCE_UPDATED_AT "
        "<> ROW_EVIDENCE.SOURCE_UPDATED_AT"
    ) in normalized
    assert "ROW_EVIDENCE.SOURCE_UPDATED_AT > REPLACEMENT.SNAPSHOT_AT" in normalized
    assert "COUNT(DISTINCT ROW_EVIDENCE.PARSED_PAYLOAD->>'ID')" in normalized
    assert "COUNT(DISTINCT ROW_EVIDENCE.PARSED_PAYLOAD->>'CODE')" in normalized
    assert normalized.count("FOR SHARE") >= 3


def test_redaction_recomputes_manifest_and_original_batch_wire_evidence():
    normalized = _normalized()

    assert "FINAL_WIRE_JSON" in normalized
    assert "DELTA_WIRE_JSON" in normalized
    assert "INTEGRITY.FINAL_SHA256 <> MANIFEST_ENTITY->>'FINAL_SHA256'" in normalized
    assert "INTEGRITY.DELTA_SHA256 <> MANIFEST_ENTITY->>'DELTA_SHA256'" in normalized
    assert "CANONICAL_MANIFEST_BODY_JSON" in normalized
    assert "RECONSTRUCTED_MANIFEST_SHA256" in normalized
    assert (
        "REPLACEMENT.MANIFEST_SHA256 IS DISTINCT FROM "
        "INTEGRITY.RECONSTRUCTED_MANIFEST_SHA256"
    ) in normalized
    assert "INTEGRITY.RECORD_COUNT <> FINAL_RECORD_COUNT" in normalized
    assert "INTEGRITY.RECORD_COUNT <> DELTA_RECORD_COUNT" in normalized
    assert "LEGACY_OAM_DETAIL_CLEANUP_REPLACEMENT_BATCHES" in normalized
    assert "CANONICAL_BODY_JSON" in normalized
    assert "RECONSTRUCTED_BODY_SHA256" in normalized
    assert "BATCH.BODY_SHA256 <> BATCH.RECONSTRUCTED_BODY_SHA256" in normalized
    assert "BATCH.ASSIGNED_RECORD_COUNT <> BATCH.RECORD_COUNT" in normalized
    assert (
        "BATCH.BATCH_ID <> REPLACEMENT.SNAPSHOT_ID || '-WORK_ORDER-' "
        "|| BATCH.SEQUENCE::TEXT"
    ) in normalized
    assert "INTEGRITY.BATCH_RECORD_COUNT <> DELTA_RECORD_COUNT" in normalized
    assert "IS DISTINCT FROM REPLACEMENT.SOURCE_SYSTEM" in normalized
    assert "IS DISTINCT FROM 'WORK_ORDER'" in normalized
    assert "COALESCE(MANIFEST_ENTITY->>'FINAL_RECORD_COUNT', '')" in normalized


def test_redaction_audit_hash_binds_actual_replacement_rows():
    normalized = _normalized()
    summary = normalized.split(
        "CREATE TEMPORARY TABLE LEGACY_OAM_DETAIL_CLEANUP_SUMMARY", 1
    )[1].split("SELECT 'DRY_RUN_PLAN' AS RESULT", 1)[0]

    assert "RSC.LEGACY-OAM-WORK-ORDER-DETAIL-REDACTION.V2" in summary
    assert "REPLACEMENT_INTEGRITY.SNAPSHOT_ROWS_EVIDENCE_SHA256" in summary
    assert "REPLACEMENT_INTEGRITY.MANIFEST_JSON_SHA256" in summary
    assert "REPLACEMENT_INTEGRITY.RECONSTRUCTED_MANIFEST_SHA256" in summary
    assert "REPLACEMENT_INTEGRITY.CURRENT_ROWS_EVIDENCE_SHA256" in summary
    assert "REPLACEMENT_INTEGRITY.BATCHES_EVIDENCE_SHA256" in summary
    assert "REPLACEMENT_INTEGRITY.REPLACEMENT_EVIDENCE_SHA256" in summary
    assert summary.index("REPLACEMENT_INTEGRITY.REPLACEMENT_EVIDENCE_SHA256") < (
        summary.index(") AS AUDIT_SHA256")
    )
    assert "SUMMARY.REPLACEMENT_EVIDENCE_SHA256" in normalized


def test_redaction_preserves_formal_projection_and_snapshot_identity_evidence():
    source = _source()
    normalized = _normalized()

    assert "OAM-WORK-ORDER:" in normalized
    assert "CHR(0)" in normalized
    assert "SYNC_RUNS" in normalized
    assert "SYNC_BATCHES" in normalized
    assert "SYNC_INBOX_EVENTS" in normalized
    assert "FORMAL_EVIDENCE_SHA256" in normalized
    assert "FORMAL PROJECTION RUN EVIDENCE CHANGED" in normalized
    assert "PENDING', 'RECEIVING', 'VALIDATING', 'VALIDATED', 'PROJECTING" in normalized

    for forbidden_write in (
        "DELETE FROM PUBLIC.EXTERNAL_SYNC_SNAPSHOTS",
        "DELETE FROM PUBLIC.SYNC_RUNS",
        "DELETE FROM PUBLIC.SYNC_BATCHES",
        "DELETE FROM PUBLIC.SYNC_INBOX_EVENTS",
        "UPDATE PUBLIC.SYNC_RUNS",
        "UPDATE PUBLIC.SYNC_BATCHES",
        "UPDATE PUBLIC.SYNC_INBOX_EVENTS",
    ):
        assert forbidden_write not in normalized

    # The original manifest and hash remain immutable evidence; only status is
    # changed so the old snapshot can no longer enter the projector queue.
    assert "SET STATUS = 'REDACTED_LEGACY'" in normalized
    assert "SET MANIFEST_JSON" not in normalized
    assert "SET MANIFEST_SHA256" not in normalized
    assert "SNAPSHOT.STATUS <> 'REDACTED_LEGACY'" in normalized
    assert "manifest_sha256" in source


def test_redaction_deletes_only_the_two_retired_payload_entities():
    normalized = _normalized()

    assert "DELETE FROM PUBLIC.EXTERNAL_SYNC_CURRENT_RECORDS" in normalized
    assert "DELETE FROM PUBLIC.EXTERNAL_SYNC_SNAPSHOT_RECORDS" in normalized
    assert "DELETE FROM PUBLIC.EXTERNAL_SYNC_SNAPSHOT_BATCHES" in normalized
    assert "CURRENT_RECORD.SOURCE_INSTANCE = CLEANUP_INPUT.EDGE_SOURCE_INSTANCE" in normalized
    assert "CURRENT_RECORD.SCOPE_KEY = CLEANUP_INPUT.SCOPE_KEY" in normalized
    assert "CURRENT_RECORD.LAST_SNAPSHOT_ID IN" in normalized
    assert "RECORD.SNAPSHOT_REF_ID IN" in normalized
    assert "BATCH.SNAPSHOT_REF_ID IN" in normalized

    delete_sections = normalized.split("DELETE FROM PUBLIC.")[1:]
    assert len(delete_sections) == 3
    for section in delete_sections:
        statement = section.split("RETURNING", 1)[0]
        assert "WORK_ORDER_DETAIL" in statement
        assert "WORK_ORDER_RELATION" in statement
        assert "WORK_ORDER'" not in statement.replace("WORK_ORDER_DETAIL", "").replace(
            "WORK_ORDER_RELATION", ""
        )


def test_redaction_outputs_payload_free_counts_and_hashes():
    source = _source()
    normalized = _normalized()

    for output in (
        "DRY_RUN_PLAN",
        "SNAPSHOT_PLAN",
        "EXECUTED",
        "CURRENT_RECORD_COUNT",
        "SNAPSHOT_RECORD_COUNT",
        "SNAPSHOT_BATCH_COUNT",
        "CURRENT_RECORD_EVIDENCE_SHA256",
        "SNAPSHOT_RECORD_EVIDENCE_SHA256",
        "SNAPSHOT_BATCH_EVIDENCE_SHA256",
        "MANIFEST_EVIDENCE_SHA256",
        "FORMAL_EVIDENCE_SHA256",
        "AUDIT_SHA256",
    ):
        assert output in normalized

    # Hash the stored SHA evidence and identifiers; never print or aggregate
    # the retired payload bodies themselves.
    plan_start = normalized.index("CREATE TEMPORARY TABLE LEGACY_OAM_DETAIL_CLEANUP_PLAN")
    plan_end = normalized.index("CREATE TEMPORARY TABLE LEGACY_OAM_DETAIL_CLEANUP_FORMAL_EVIDENCE")
    plan = normalized[plan_start:plan_end]
    assert "PAYLOAD_SHA256" in plan
    assert "PAYLOAD_JSON" not in plan
    assert "SELECT * FROM PUBLIC.EXTERNAL_SYNC_SNAPSHOT_RECORDS" not in normalized
