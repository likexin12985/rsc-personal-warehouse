"""Synthetic, detached evidence only; never connect to a source or database."""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from app import inventory_control_evidence as service


BASE_TIME = datetime(2026, 9, 5, 1, 0, tzinfo=timezone.utc)
CHECK_TIME = BASE_TIME + timedelta(minutes=5)
SCHEMA = "rsc.inventory_control_coverage.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def stamp(value):
    return value.isoformat()


def fake_expectation():
    """Independent feed catalogue: includes a second province and a zero position."""
    return {
        "schema_version": SCHEMA,
        "binding": {
            "source_system": "starcharge_oam", "source_instance": "fake-edge-001",
            "company_id": "fake-company", "org_code": "fake-org", "scope_key": "all",
        },
        "catalog_revision": "fake-catalog-v1", "target_region_code": "fake-region-A",
        "warehouses": [
            {"warehouse_code": "fake-W-A", "warehouse_type": "supplyWarehouse",
             "warehouse_attribute": "fake-spare",
             "positions": [
                 {"position_code": "fake-P-A", "region_code": "fake-region-A"},
                 {"position_code": "fake-P-ZERO", "region_code": "fake-region-A"},
             ]},
            {"warehouse_code": "fake-W-B", "warehouse_type": "serviceWarehouse",
             "warehouse_attribute": "fake-spare",
             "positions": [{"position_code": "fake-P-B", "region_code": "fake-region-B"}]},
        ],
    }


def fake_record(key="fake-key-A", *, warehouse="fake-W-A", position="fake-P-A", quantity="3.000"):
    return {
        "business_key": key, "source_updated_at": stamp(BASE_TIME - timedelta(minutes=5)),
        "data": {
            "companyId": "fake-company", "orgCode": "fake-org",
            "warehouseCode": warehouse,
            "warehouseType": "supplyWarehouse" if warehouse == "fake-W-A" else "serviceWarehouse",
            "warehouseAttribute": "fake-spare", "positionCode": position,
            "materialCode": "fake-SKU-001", "qtyStock": quantity, "qtyLock": "0.000",
        },
    }


def fake_records():
    return [fake_record(), fake_record("fake-key-A2"),
            fake_record("fake-key-B", warehouse="fake-W-B", position="fake-P-B")]


def upsert(record):
    return {**deepcopy(record), "operation": "upsert"}


def delete(key):
    return {"business_key": key, "source_updated_at": None, "data": {}, "operation": "delete"}


def fake_snapshot(expected, records, *, number=1, mode="full", previous=None,
                  changes=None, page_size=2, batch_size=2):
    """Construct transport hashes independently; captures describe the final state.

    Catalogue comes from the separate fixture, not from the inventory rows. Even
    an entirely empty warehouse must have a successful, explicit zero page.
    """
    records = sorted(deepcopy(records), key=lambda row: row["business_key"])
    changes = sorted(deepcopy(changes if changes is not None else [upsert(row) for row in records]),
                     key=lambda row: row["business_key"])
    moment = BASE_TIME + timedelta(minutes=10 * (number - 1))
    binding = expected["binding"]
    header = {
        "source_system": binding["source_system"], "snapshot_id": f"fake-snapshot-{number:03d}",
        "scope_key": binding["scope_key"], "sync_mode": mode,
        "company_id": binding["company_id"], "org_code": binding["org_code"],
        "snapshot_at": stamp(moment),
    }
    chunks = [changes[index:index + batch_size] for index in range(0, len(changes), batch_size)]
    entity = {
        "entity_type": "inventory", "final_record_count": len(records), "final_sha256": digest(records),
        "delta_record_count": len(changes), "delta_sha256": digest(changes), "batch_count": len(chunks),
    }
    captures = []
    for warehouse in expected["warehouses"]:
        code = warehouse["warehouse_code"]
        local = [row for row in records if row["data"].get("warehouseCode") == code]
        pages = [local[index:index + page_size] for index in range(0, len(local), page_size)] or [[]]
        captures.append({
            "query": {"warehouseCode": code, "warehouseType": warehouse["warehouse_type"],
                      "warehouseAttribute": warehouse["warehouse_attribute"], "querySource": "PC",
                      "snDisplayFlag": int(warehouse["warehouse_type"] == "supplyWarehouse")},
            "started_at": stamp(moment - timedelta(minutes=2)),
            "completed_at": stamp(moment - timedelta(minutes=1)),
            "pages": [{"response_status": "success", "page": index + 1, "size": page_size,
                       "source_total": len(local), "record_keys": [row["business_key"] for row in page],
                       "records_sha256": digest(page)} for index, page in enumerate(pages)],
        })
    return {
        "source_instance": binding["source_instance"], "target_region_code": expected["target_region_code"],
        "catalog_revision": expected["catalog_revision"],
        "base_snapshot_id": previous["manifest"]["snapshot_id"] if previous else None,
        "base_final_sha256": previous["manifest"]["entities"][0]["final_sha256"] if previous else None,
        "manifest": {**header, "entities": [entity]},
        "batches": [{**header, "entity_type": "inventory", "sequence": index + 1,
                     "total_sequences": len(chunks), "records": chunk} for index, chunk in enumerate(chunks)],
        "warehouses": captures,
    }


def fake_bundle(records=None, **snapshot_options):
    expected = fake_expectation()
    snapshot = fake_snapshot(expected, fake_records() if records is None else records, **snapshot_options)
    return expected, {"schema_version": SCHEMA, "snapshots": [snapshot]}


def validate(expected, evidence, *, checked_at=CHECK_TIME):
    return service.validate_inventory_control_evidence(
        expected_json=canonical(expected), evidence_json=canonical(evidence), checked_at=checked_at,
    )


def assert_rejected(expected, evidence, code=None, *, checked_at=CHECK_TIME):
    with pytest.raises(service.InventoryControlEvidenceError) as caught:
        validate(expected, evidence, checked_at=checked_at)
    if code:
        assert caught.value.code == code
    assert str(caught.value) == caught.value.code
    assert "fake-" not in str(caught.value)


def entity(evidence):
    return evidence["snapshots"][0]["manifest"]["entities"][0]


def test_full_national_feed_selects_target_region_without_summing_other_province():
    expected, evidence = fake_bundle()
    report = validate(expected, evidence)
    assert report.status == "evidence_consistent"
    assert report.sync_mode == "full"
    assert report.checked_snapshots == 1
    assert report.target_warehouse_count == 1
    assert report.target_position_count == 2
    assert report.target_record_count == 2
    assert report.oldest_capture_age_seconds == 420
    assert report.within_freshness_target is True


@pytest.mark.parametrize("records", [[], [fake_record("fake-key-B", warehouse="fake-W-B", position="fake-P-B")]])
def test_explicit_successful_zero_capture_is_not_unknown_coverage(records):
    expected, evidence = fake_bundle(records)
    report = validate(expected, evidence)
    assert report.target_record_count == 0
    assert report.target_position_count == 2
    assert report.start_ready is False


def test_warehouse_scoped_feed_supported_without_changing_national_feed_contract():
    expected = fake_expectation()
    expected["warehouses"] = expected["warehouses"][:1]
    expected["binding"]["scope_key"] = "warehouse:fake-W-A"
    evidence = {"schema_version": SCHEMA, "snapshots": [fake_snapshot(expected, [fake_record()])]}
    assert validate(expected, evidence).target_record_count == 1


@pytest.mark.parametrize("length,accepted", [(1, True), (128, True), (129, False)])
def test_source_instance_length_matches_authenticated_transport_contract(length, accepted):
    expected = fake_expectation()
    expected["binding"]["source_instance"] = "x" * length
    evidence = {"schema_version": SCHEMA, "snapshots": [fake_snapshot(expected, [])]}
    if accepted:
        assert validate(expected, evidence).source_authenticated is False
    else:
        assert_rejected(expected, evidence, "invalid_evidence_shape")


@pytest.mark.parametrize("code", ["-WH", ".WH", "_WH", "WH:1", "x" * 129])
def test_consistently_tampered_catalogue_and_capture_cannot_authorize_invalid_warehouse_scope(code):
    expected = fake_expectation()
    expected["warehouses"] = expected["warehouses"][:1]
    expected["warehouses"][0]["warehouse_code"] = code
    expected["binding"]["scope_key"] = f"warehouse:{code}"
    evidence = {"schema_version": SCHEMA, "snapshots": [fake_snapshot(expected, [])]}
    assert_rejected(expected, evidence, "unsupported_or_incomplete_feed_scope")


@pytest.mark.parametrize("code", ["W", "W.H_1-2", "x" * 128])
def test_valid_warehouse_scope_boundaries(code):
    expected = fake_expectation()
    expected["warehouses"] = expected["warehouses"][:1]
    expected["warehouses"][0]["warehouse_code"] = code
    expected["binding"]["scope_key"] = f"warehouse:{code}"
    evidence = {"schema_version": SCHEMA, "snapshots": [fake_snapshot(expected, [])]}
    assert validate(expected, evidence).target_warehouse_count == 1


def test_multi_page_multi_batch_order_is_irrelevant_but_all_members_must_match():
    records = [fake_record(f"fake-key-{number}") for number in range(5)]
    expected, evidence = fake_bundle(records, page_size=2, batch_size=2)
    snapshot = evidence["snapshots"][0]
    snapshot["batches"].reverse()
    snapshot["warehouses"][0]["pages"].reverse()
    snapshot["warehouses"].reverse()
    expected["warehouses"].reverse()
    assert validate(expected, evidence).target_record_count == 5


@pytest.mark.parametrize("action", ["upsert", "insert", "delete", "noop", "delete_all"])
def test_full_then_incremental_reconstructs_complete_final_state(action):
    expected, evidence = fake_bundle()
    records = fake_records()
    if action == "upsert":
        records[0]["data"]["qtyStock"] = "8.000"
        changes = [upsert(records[0])]
    elif action == "insert":
        records.append(fake_record("fake-new-key"))
        changes = [upsert(records[-1])]
    elif action == "delete":
        changes = [delete(records.pop(0)["business_key"])]
    elif action == "delete_all":
        changes, records = [delete(row["business_key"]) for row in records], []
    else:
        changes = []
    evidence["snapshots"].append(fake_snapshot(expected, records, number=2, mode="incremental",
                                            previous=evidence["snapshots"][0], changes=changes))
    report = validate(expected, evidence, checked_at=BASE_TIME + timedelta(minutes=15))
    assert report.sync_mode == "incremental"
    assert report.checked_snapshots == 2
    assert report.target_record_count == sum(row["data"]["warehouseCode"] == "fake-W-A" for row in records)


def test_multiple_incremental_steps_link_each_immediate_base_and_recheck_full_coverage():
    expected, evidence = fake_bundle()
    for number in (2, 3, 4):
        records = fake_records()
        records[0]["data"]["qtyStock"] = f"{number}.000"
        previous = evidence["snapshots"][-1]
        evidence["snapshots"].append(fake_snapshot(expected, records, number=number,
                                                mode="incremental", previous=previous,
                                                changes=[upsert(records[0])]))
    report = validate(expected, evidence, checked_at=BASE_TIME + timedelta(minutes=35))
    assert report.checked_snapshots == 4
    assert report.oldest_capture_age_seconds == 420


@pytest.mark.parametrize("field", ["source_instance", "target_region_code", "catalog_revision"])
def test_snapshot_identity_must_match_independent_catalogue(field):
    expected, evidence = fake_bundle()
    evidence["snapshots"][0][field] = "fake-wrong-binding"
    assert_rejected(expected, evidence, "source_binding_mismatch" if field == "source_instance" else "coverage_binding_mismatch")


@pytest.mark.parametrize("field", ["company_id", "org_code", "scope_key"])
def test_manifest_source_scope_must_match_independent_binding(field):
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["manifest"][field] = "fake-wrong-binding"
    assert_rejected(expected, evidence, "source_binding_mismatch")


def test_different_source_system_cannot_borrow_matching_catalogue():
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["manifest"]["source_system"] = "other_source"
    assert_rejected(expected, evidence, "invalid_manifest")


@pytest.mark.parametrize("field", ["companyId", "orgCode", "warehouseType", "warehouseAttribute"])
def test_transport_hash_consistency_does_not_bypass_record_source_scope(field):
    records = fake_records()
    records[0]["data"][field] = "fake-other-scope"
    expected, evidence = fake_bundle(records)
    assert_rejected(expected, evidence, "inventory_scope_mismatch")


@pytest.mark.parametrize("field", ["warehouseCode", "positionCode"])
def test_unknown_inventory_warehouse_or_position_is_not_inferred_from_rows(field):
    records = fake_records()
    records[0]["data"][field] = "fake-unknown"
    expected, evidence = fake_bundle(records)
    assert_rejected(expected, evidence, "inventory_position_unmapped")


@pytest.mark.parametrize("scope", ["province:fake-region-A", "warehouse:fake-W-A", "warehouse:missing"])
def test_scope_does_not_silently_narrow_independent_feed_catalogue(scope):
    expected, evidence = fake_bundle()
    expected["binding"]["scope_key"] = scope
    assert_rejected(expected, evidence, "unsupported_or_incomplete_feed_scope")


def test_expected_region_must_have_explicit_position_coverage():
    expected, evidence = fake_bundle()
    expected["target_region_code"] = "fake-region-missing"
    assert_rejected(expected, evidence, "target_region_not_covered")


@pytest.mark.parametrize("kind", ["warehouse", "position"])
def test_duplicate_expected_catalogue_members_rejected(kind):
    expected, evidence = fake_bundle()
    if kind == "warehouse":
        expected["warehouses"].append(deepcopy(expected["warehouses"][0]))
    else:
        expected["warehouses"][0]["positions"].append(deepcopy(expected["warehouses"][0]["positions"][0]))
    assert_rejected(expected, evidence, f"duplicate_catalog_{kind}")


@pytest.mark.parametrize("empty", [False, True])
def test_omitted_warehouse_never_means_zero_inventory(empty):
    expected, evidence = fake_bundle([] if empty else None)
    evidence["snapshots"][0]["warehouses"].pop()
    assert_rejected(expected, evidence, "incomplete_warehouse_coverage")


def test_duplicate_captured_warehouse_rejected():
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["warehouses"].append(deepcopy(evidence["snapshots"][0]["warehouses"][0]))
    assert_rejected(expected, evidence, "duplicate_captured_warehouse")


def test_unmapped_captured_warehouse_rejected():
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["warehouses"][0]["query"]["warehouseCode"] = "fake-unknown"
    assert_rejected(expected, evidence, "captured_warehouse_unmapped")


@pytest.mark.parametrize("field,value", [("warehouseType", "other"), ("warehouseAttribute", "other"), ("snDisplayFlag", 0)])
def test_capture_query_exact_selector_binding(field, value):
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["warehouses"][0]["query"][field] = value
    assert_rejected(expected, evidence, "capture_selector_mismatch")


@pytest.mark.parametrize("extra", ["materialCode", "positionCode", "qtyStock", "status", "complete"])
def test_additional_capture_filters_or_completeness_flags_forbidden(extra):
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["warehouses"][0]["query"][extra] = "fake-restriction"
    assert_rejected(expected, evidence, "invalid_evidence_shape")


def test_capture_page_gap_rejected():
    expected, evidence = fake_bundle([fake_record(f"fake-key-{i}") for i in range(5)])
    evidence["snapshots"][0]["warehouses"][0]["pages"].pop(1)
    assert_rejected(expected, evidence, "incomplete_capture_pages")


@pytest.mark.parametrize("page", [1, 4])
def test_duplicate_or_out_of_range_capture_page_rejected(page):
    expected, evidence = fake_bundle([fake_record(f"fake-key-{i}") for i in range(5)])
    evidence["snapshots"][0]["warehouses"][0]["pages"][1]["page"] = page
    assert_rejected(expected, evidence, "invalid_capture_sequence")


@pytest.mark.parametrize("field,value", [("source_total", 6), ("size", 3)])
def test_capture_total_or_page_size_drift_is_not_success(field, value):
    expected, evidence = fake_bundle([fake_record(f"fake-key-{i}") for i in range(5)])
    evidence["snapshots"][0]["warehouses"][0]["pages"][1][field] = value
    assert_rejected(expected, evidence, "capture_total_drift")


def test_capture_total_does_not_match_reconstructed_final_rows():
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["warehouses"][0]["pages"][0]["source_total"] = 1
    assert_rejected(expected, evidence, "incomplete_capture_pages")


def test_missing_capture_record_cannot_be_hidden_by_success_status():
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["warehouses"][0]["pages"][0]["record_keys"].pop()
    assert_rejected(expected, evidence, "incomplete_capture_page")


@pytest.mark.parametrize("key", ["fake-key-A", "fake-unknown", "fake-key-B"])
def test_duplicate_unknown_or_cross_warehouse_capture_record_rejected(key):
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["warehouses"][0]["pages"][0]["record_keys"][1] = key
    assert_rejected(expected, evidence, "capture_record_mismatch")


def test_hash_of_page_content_is_verified_even_when_count_and_keys_match():
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["warehouses"][0]["pages"][0]["records_sha256"] = "0" * 64
    assert_rejected(expected, evidence, "capture_hash_mismatch")


@pytest.mark.parametrize("field", ["source_total", "response_status"])
def test_zero_capture_requires_explicit_total_and_source_success(field):
    expected, evidence = fake_bundle([])
    del evidence["snapshots"][0]["warehouses"][0]["pages"][0][field]
    assert_rejected(expected, evidence, "invalid_evidence_shape")


def test_missing_transport_batch_rejected():
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["batches"].pop()
    assert_rejected(expected, evidence, "incomplete_transport_batches")


def test_repeated_transport_sequence_rejected():
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["batches"][1]["sequence"] = 1
    assert_rejected(expected, evidence, "duplicate_transport_sequence")


@pytest.mark.parametrize("field,value", [("snapshot_id", "fake-other-snapshot"), ("company_id", "other"),
                                       ("snapshot_at", stamp(BASE_TIME - timedelta(seconds=1))),
                                       ("entity_type", "employee"), ("total_sequences", 3)])
def test_batch_transport_headers_must_match_manifest(field, value):
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["batches"][0][field] = value
    assert_rejected(expected, evidence, "transport_header_mismatch")


@pytest.mark.parametrize("field,value,code", [("final_record_count", 9, "final_manifest_mismatch"),
                                            ("delta_record_count", 9, "delta_manifest_mismatch"),
                                            ("final_sha256", "0" * 64, "final_manifest_mismatch"),
                                            ("delta_sha256", "0" * 64, "delta_manifest_mismatch")])
def test_transport_hashes_and_counts_independently_verified(field, value, code):
    expected, evidence = fake_bundle()
    entity(evidence)[field] = value
    assert_rejected(expected, evidence, code)


@pytest.mark.parametrize("across_batches", [False, True])
def test_repeated_business_key_rejected_even_with_matching_delta_manifest(across_batches):
    records = fake_records()
    changes = [upsert(records[0]), upsert(records[0])]
    expected, evidence = fake_bundle(records, changes=changes, batch_size=1 if across_batches else 2)
    assert_rejected(expected, evidence, "duplicate_delta_record" if across_batches else "invalid_transport_batch")


def incremental_bundle():
    expected, evidence = fake_bundle()
    evidence["snapshots"].append(fake_snapshot(expected, fake_records(), number=2, mode="incremental",
                                            previous=evidence["snapshots"][0], changes=[]))
    return expected, evidence


@pytest.mark.parametrize("field,value", [("base_snapshot_id", None), ("base_snapshot_id", "fake-unknown-base"),
                                       ("base_final_sha256", None), ("base_final_sha256", "0" * 64)])
def test_incremental_base_must_match_exact_previous_snapshot_and_final_hash(field, value):
    expected, evidence = incremental_bundle()
    evidence["snapshots"][1][field] = value
    assert_rejected(expected, evidence, "incremental_base_mismatch", checked_at=BASE_TIME + timedelta(minutes=15))


def test_incremental_without_full_reconstruction_root_rejected():
    expected, evidence = incremental_bundle()
    evidence["snapshots"].pop(0)
    assert_rejected(expected, evidence, "incremental_base_mismatch", checked_at=BASE_TIME + timedelta(minutes=15))


@pytest.mark.parametrize("field,value", [("base_snapshot_id", "fake-previous"), ("base_final_sha256", "0" * 64)])
def test_full_snapshot_must_not_claim_an_incremental_base(field, value):
    expected, evidence = fake_bundle()
    evidence["snapshots"][0][field] = value
    assert_rejected(expected, evidence, "unexpected_full_base")


@pytest.mark.parametrize("mode", ["full", "incremental"])
def test_delete_requires_known_incremental_base_record(mode):
    if mode == "full":
        expected, evidence = fake_bundle(changes=[delete("fake-unknown")])
    else:
        expected, evidence = incremental_bundle()
        evidence["snapshots"][1] = fake_snapshot(expected, fake_records(), number=2, mode="incremental",
                                               previous=evidence["snapshots"][0], changes=[delete("fake-unknown")])
    assert_rejected(expected, evidence, "invalid_delete_base", checked_at=BASE_TIME + timedelta(minutes=15))


def test_duplicate_snapshot_id_rejected_before_base_reuse():
    expected, evidence = fake_bundle()
    evidence["snapshots"].append(deepcopy(evidence["snapshots"][0]))
    assert_rejected(expected, evidence, "duplicate_snapshot_id")


def test_snapshot_chain_must_be_strictly_chronological():
    expected, evidence = incremental_bundle()
    later = evidence["snapshots"][1]
    later["manifest"]["snapshot_at"] = stamp(BASE_TIME)
    assert_rejected(expected, evidence, "snapshot_time_mismatch", checked_at=BASE_TIME + timedelta(minutes=15))


def test_incremental_capture_cannot_predate_its_claimed_baseline():
    expected, evidence = incremental_bundle()
    evidence["snapshots"][1]["warehouses"][0]["started_at"] = stamp(BASE_TIME - timedelta(seconds=1))
    assert_rejected(expected, evidence, "capture_chain_time_mismatch", checked_at=BASE_TIME + timedelta(minutes=15))


@pytest.mark.parametrize("minutes,within", [(43, True), (43.01, False), (10080, False)])
def test_45_minute_freshness_is_reported_not_an_unapproved_hard_expiry(minutes, within):
    expected, evidence = fake_bundle()
    report = validate(expected, evidence, checked_at=BASE_TIME + timedelta(minutes=minutes))
    assert report.within_freshness_target is within
    assert report.oldest_capture_age_seconds == pytest.approx((minutes + 2) * 60)
    assert report.start_ready is False


def test_future_snapshot_rejected_not_negative_freshness():
    expected, evidence = fake_bundle()
    assert_rejected(expected, evidence, "snapshot_time_mismatch", checked_at=BASE_TIME - timedelta(seconds=1))


@pytest.mark.parametrize("field,value", [("started_at", BASE_TIME), ("completed_at", BASE_TIME + timedelta(seconds=1))])
def test_capture_time_order_is_independent_of_manifest_hashes(field, value):
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["warehouses"][0][field] = stamp(value)
    assert_rejected(expected, evidence, "capture_time_mismatch")


@pytest.mark.parametrize("checked_at", [None, "2026-09-05T01:05:00Z", datetime(2026, 9, 5, 1, 5), True])
def test_check_time_requires_explicit_aware_datetime(checked_at):
    expected, evidence = fake_bundle()
    assert_rejected(expected, evidence, "invalid_check_time", checked_at=checked_at)


@pytest.mark.parametrize("location", ["capture_start", "capture_end", "manifest", "batch", "record"])
def test_all_timestamp_evidence_requires_timezone(location):
    expected, evidence = fake_bundle()
    snapshot = evidence["snapshots"][0]
    value = "2026-09-05T01:00:00"
    if location.startswith("capture"):
        snapshot["warehouses"][0]["started_at" if location == "capture_start" else "completed_at"] = value
    elif location in {"manifest", "batch"}:
        (snapshot["manifest"] if location == "manifest" else snapshot["batches"][0])["snapshot_at"] = value
    else:
        snapshot["batches"][0]["records"][0]["source_updated_at"] = value
    assert_rejected(expected, evidence)


@pytest.mark.parametrize("field", ["final_record_count", "delta_record_count", "batch_count"])
@pytest.mark.parametrize("value", [True, False, "1", 1.0])
def test_manifest_counts_never_coerce_booleans_strings_or_floats(field, value):
    expected, evidence = fake_bundle()
    entity(evidence)[field] = value
    assert_rejected(expected, evidence, "invalid_manifest")


@pytest.mark.parametrize("field", ["page", "size", "source_total"])
@pytest.mark.parametrize("value", [True, False, "1", 1.0])
def test_capture_counts_never_coerce_booleans_strings_or_floats(field, value):
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["warehouses"][0]["pages"][0][field] = value
    assert_rejected(expected, evidence, "invalid_evidence_shape")


@pytest.mark.parametrize("field", ["sequence", "total_sequences"])
@pytest.mark.parametrize("value", [True, "1", 1.0])
def test_batch_counts_never_coerce_booleans_strings_or_floats(field, value):
    expected, evidence = fake_bundle()
    evidence["snapshots"][0]["batches"][0][field] = value
    assert_rejected(expected, evidence, "invalid_transport_batch")


@pytest.mark.parametrize("location", ["root", "snapshot", "warehouse", "page", "manifest", "entity", "batch", "record", "data"])
def test_unknown_evidence_fields_fail_closed(location):
    expected, evidence = fake_bundle()
    snapshot = evidence["snapshots"][0]
    nodes = {"root": evidence, "snapshot": snapshot, "warehouse": snapshot["warehouses"][0],
             "page": snapshot["warehouses"][0]["pages"][0], "manifest": snapshot["manifest"],
             "entity": entity(evidence), "batch": snapshot["batches"][0],
             "record": snapshot["batches"][0]["records"][0],
             "data": snapshot["batches"][0]["records"][0]["data"]}
    nodes[location]["complete"] = True
    assert_rejected(expected, evidence)


@pytest.mark.parametrize("location", ["root", "binding", "warehouse", "position"])
def test_expected_catalogue_unknown_fields_fail_closed(location):
    expected, evidence = fake_bundle()
    nodes = {"root": expected, "binding": expected["binding"], "warehouse": expected["warehouses"][0],
             "position": expected["warehouses"][0]["positions"][0]}
    nodes[location]["verified"] = True
    assert_rejected(expected, evidence, "invalid_evidence_shape")


@pytest.mark.parametrize("field", ["expected_json", "evidence_json"])
@pytest.mark.parametrize("value", [None, {}, [], b"{}", "", "null", "[]", "{"])
def test_input_must_be_detached_bounded_json_object(field, value):
    expected, evidence = fake_bundle()
    arguments = {"expected_json": canonical(expected), "evidence_json": canonical(evidence), "checked_at": CHECK_TIME}
    arguments[field] = value
    with pytest.raises(service.InventoryControlEvidenceError, match="invalid_evidence_shape"):
        service.validate_inventory_control_evidence(**arguments)


@pytest.mark.parametrize("field", ["expected_json", "evidence_json"])
@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_non_finite_numbers_never_reach_hash_or_quantity_processing(field, literal):
    expected, evidence = fake_bundle()
    arguments = {"expected_json": canonical(expected), "evidence_json": canonical(evidence), "checked_at": CHECK_TIME}
    arguments[field] = '{"nonfinite":' + literal + "}"
    with pytest.raises(service.InventoryControlEvidenceError) as caught:
        service.validate_inventory_control_evidence(**arguments)
    assert caught.value.code in {"non_finite_json_number", "invalid_evidence_shape"}


@pytest.mark.parametrize("field", ["expected_json", "evidence_json"])
@pytest.mark.parametrize("raw", ['{"same":1,"same":2}', '{"nested":{"same":1,"same":1}}'])
def test_duplicate_json_keys_rejected_at_any_depth_even_same_values(field, raw):
    expected, evidence = fake_bundle()
    arguments = {"expected_json": canonical(expected), "evidence_json": canonical(evidence), "checked_at": CHECK_TIME}
    arguments[field] = raw
    with pytest.raises(service.InventoryControlEvidenceError, match="duplicate_json_key"):
        service.validate_inventory_control_evidence(**arguments)


@pytest.mark.parametrize("text", ["x" * 301, "界" * 100])
def test_json_size_limits_account_for_utf8_bytes(monkeypatch, text):
    expected, evidence = fake_bundle()
    monkeypatch.setattr(service, "MAX_JSON_BYTES", 300)
    with pytest.raises(service.InventoryControlEvidenceError):
        service.validate_inventory_control_evidence(expected_json=canonical({"x": text}),
                                                    evidence_json=canonical(evidence), checked_at=CHECK_TIME)


def test_report_is_frozen_contains_no_source_data_and_never_grants_readiness():
    expected, evidence = fake_bundle()
    report = validate(expected, evidence)
    fields = asdict(report)
    readiness = {"source_authenticated", "catalog_authenticated", "projection_published", "start_ready"}
    assert all(fields[name] is False for name in readiness)
    assert set(fields) == readiness | {"status", "sync_mode", "checked_snapshots", "target_warehouse_count",
                                     "target_position_count", "target_record_count", "oldest_capture_age_seconds",
                                     "within_freshness_target"}
    assert "fake-" not in canonical(fields)
    with pytest.raises(FrozenInstanceError):
        report.start_ready = True


@pytest.mark.parametrize("field", ["source_authenticated", "catalog_authenticated", "projection_published", "start_ready"])
def test_report_constructor_cannot_accept_caller_supplied_readiness(field):
    expected, evidence = fake_bundle()
    values = asdict(validate(expected, evidence))
    for name in ("source_authenticated", "catalog_authenticated", "projection_published", "start_ready"):
        values.pop(name)
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        service.InventoryControlValidationReport(**values, **{field: True})


def test_validation_does_not_mutate_inputs_or_access_external_resources(monkeypatch):
    expected, evidence = fake_bundle()
    before = canonical([expected, evidence])
    reference = validate(expected, evidence)

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline validation attempted external I/O")

    with monkeypatch.context() as context:
        context.setattr("builtins.open", forbidden)
        context.setattr(Path, "open", forbidden)
        context.setattr(Path, "read_text", forbidden)
        context.setattr(Path, "write_text", forbidden)
        context.setattr(socket, "socket", forbidden)
        context.setattr(socket, "create_connection", forbidden)
        assert validate(expected, evidence) == reference
    assert canonical([expected, evidence]) == before


def test_service_and_schema_have_no_database_network_filesystem_or_runtime_clock_dependencies():
    files = [PROJECT_ROOT / "backend/app/inventory_control_evidence.py",
             PROJECT_ROOT / "backend/app/inventory_control_evidence_schemas.py"]
    allowed = {"__future__", "dataclasses", "datetime", "hashlib", "json", "re", "typing", "pydantic",
               "inventory_control_evidence_schemas", "schemas"}
    forbidden_calls = {"open", "exec", "eval", "compile", "__import__", "now", "utcnow", "today",
                       "uuid4", "execute", "commit", "flush", "add", "add_all", "connect", "request"}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert {item.name for item in node.names} <= allowed
            elif isinstance(node, ast.ImportFrom):
                assert node.module in allowed
            elif isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                # set.add is pure in-memory bookkeeping, not Session.add.
                assert name not in forbidden_calls - {"add"}


def test_inventory_data_allowlist_matches_collector_without_importing_live_collector():
    tree = ast.parse((PROJECT_ROOT / "edge_sync/oam_edge_sync.py").read_text(encoding="utf-8"))
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "INVENTORY_FIELDS" for target in node.targets)]
    assert len(assignments) == 1
    collector_fields = ast.literal_eval(assignments[0].value)
    assert service.INVENTORY_DATA_FIELDS == frozenset(collector_fields)


def test_import_and_validation_need_no_application_config_or_database_modules():
    expected, evidence = fake_bundle()
    code = (
        "import sys\n"
        "from datetime import datetime\n"
        "from app.inventory_control_evidence import validate_inventory_control_evidence\n"
        f"report = validate_inventory_control_evidence(expected_json={canonical(expected)!r}, "
        f"evidence_json={canonical(evidence)!r}, checked_at=datetime.fromisoformat({stamp(CHECK_TIME)!r}))\n"
        "assert report.start_ready is False\n"
        "assert report.target_record_count == 2\n"
        "assert not any(name == 'sqlalchemy' or name.startswith('sqlalchemy.') "
        "or name in {'app.database', 'app.config', 'app.formal_services'} for name in sys.modules)\n"
        "print('offline-import-and-validation-ok')\n"
    )
    # Do not inherit database URLs, provider credentials, or deployment settings.
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=PROJECT_ROOT,
        env={"PATH": os.defpath, "PYTHONPATH": str(PROJECT_ROOT / "backend"), "PYTHONNOUSERSITE": "1"},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "offline-import-and-validation-ok"


def detached_collector_hash_functions():
    """Execute only four inspected pure functions, never import the edge module.

    Extracting the exact source prevents a duplicate local hash implementation
    from passing while the collector wire protocol silently drifts.
    """
    source = (PROJECT_ROOT / "edge_sync/oam_edge_sync.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {"canonical_json", "record_sha256", "records_sha256", "prepare_entity"}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == names
    assert len(functions) == len(names)
    for function in functions:
        assert not function.decorator_list
        assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(function))
        for node in ast.walk(function):
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                assert name in {"dumps", "encode", "sha256", "hexdigest", "sorted", "set", "len", "get", "append", "sort",
                                "record_sha256", "records_sha256", "canonical_json"}
    extracted = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                                 *functions], type_ignores=[])
    namespace = {"json": json, "hashlib": hashlib}
    exec(compile(ast.fix_missing_locations(extracted), "<detached-collector-pure-functions>", "exec"), namespace)
    return namespace


def test_actual_collector_utf8_canonicalization_and_record_hash_are_compatible():
    wire = detached_collector_hash_functions()
    record = fake_record()
    record["data"]["materialName"] = "隔离测试备件"
    assert wire["canonical_json"](record) == canonical(record).encode("utf-8")
    assert wire["record_sha256"](record) == digest(record)
    assert wire["records_sha256"]([record]) == digest([record])


@pytest.mark.parametrize("operation", ["full", "upsert", "insert", "delete", "noop", "delete_all"])
def test_actual_collector_prepare_entity_output_validates_full_and_incremental(operation):
    wire = detached_collector_hash_functions()
    expected = fake_expectation()
    records = fake_records()
    records[0]["data"]["materialName"] = "隔离测试备件"
    prepared = wire["prepare_entity"](entity_type="inventory", records=records, previous_index={}, sync_mode="full")
    full = fake_snapshot(expected, records, changes=prepared["deltaRecords"])
    evidence = {"schema_version": SCHEMA, "snapshots": [full]}
    final_prepared = prepared
    if operation != "full":
        final_records = deepcopy(records)
        if operation == "upsert":
            final_records[0]["data"]["qtyStock"] = "8.000"
        elif operation == "insert":
            final_records.append(fake_record("fake-new-wire-key"))
        elif operation == "delete":
            final_records.pop(0)
        elif operation == "delete_all":
            final_records = []
        final_prepared = wire["prepare_entity"](entity_type="inventory", records=final_records,
                                               previous_index=prepared["finalIndex"], sync_mode="incremental")
        evidence["snapshots"].append(fake_snapshot(expected, final_records, number=2, mode="incremental",
                                                previous=full, changes=final_prepared["deltaRecords"]))
    summary = evidence["snapshots"][-1]["manifest"]["entities"][0]
    assert summary["delta_sha256"] == final_prepared["deltaSha256"]
    assert summary["final_sha256"] == final_prepared["finalSha256"]
    assert summary["delta_record_count"] == final_prepared["deltaRecordCount"]
    assert summary["final_record_count"] == final_prepared["finalRecordCount"]
    report = validate(expected, evidence, checked_at=BASE_TIME + timedelta(minutes=15))
    assert report.sync_mode == ("full" if operation == "full" else "incremental")
    assert report.start_ready is False
