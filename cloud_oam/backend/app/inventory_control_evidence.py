"""Pure validation of synthetic inventory control evidence, without publishing.

A consistent bundle is NOT authenticated source data, a province control
SyncRun, personal stock, or permission to start a stocktake. No production
collector/route/worker invokes this module. Future publication needs its own
authorized catalogue loader, authenticated capture evidence and forward ACL/
schema migration. Existing full/incremental ingress and historical starts are
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import json
import re
from typing import Any, Literal

from pydantic import AwareDatetime, ConfigDict, ValidationError

from .inventory_control_evidence_schemas import (
    InventoryControlEvidence,
    InventoryCoverageExpectation,
    InventorySnapshotEvidence,
)
from .schemas import EdgeSyncEntityManifestIn, EdgeSyncSnapshotBatchIn, EdgeSyncSnapshotCompleteIn


MAX_JSON_BYTES = 16 * 1024 * 1024
FRESHNESS_TARGET = timedelta(minutes=45)
INVENTORY_DATA_FIELDS = frozenset({
    "id", "stockId", "materialStockId", "companyId", "companyName", "orgCode",
    "warehouseName", "warehouseCode", "warehouseType", "warehouseAttribute",
    "positionName", "positionCode", "bizAttrCode", "bizTypeCode", "materialName",
    "materialCode", "materialModel", "materialStatus", "materialStockType",
    "qtyStock", "qtyLock", "unitName", "warehousePlant",
    "materialStockTypeRefNumber", "snNo",
})
_RECORD_FIELDS = {"business_key", "source_updated_at", "data"}
_HEADER_FIELDS = {
    "source_system", "snapshot_id", "scope_key", "sync_mode", "company_id",
    "org_code", "snapshot_at",
}


class InventoryControlEvidenceError(RuntimeError):
    """Payload-free errors: never interpolate source inventory or identities."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class InventoryControlValidationReport:
    status: Literal["evidence_consistent"]
    sync_mode: Literal["full", "incremental"]
    checked_snapshots: int
    target_warehouse_count: int
    target_position_count: int
    target_record_count: int
    oldest_capture_age_seconds: float
    within_freshness_target: bool
    # These are statements of non-readiness, not caller-supplied capabilities.
    source_authenticated: Literal[False] = field(default=False, init=False)
    catalog_authenticated: Literal[False] = field(default=False, init=False)
    projection_published: Literal[False] = field(default=False, init=False)
    start_ready: Literal[False] = field(default=False, init=False)


class _StrictEntityManifest(EdgeSyncEntityManifestIn):
    model_config = ConfigDict(extra="forbid", strict=True)


class _StrictManifest(EdgeSyncSnapshotCompleteIn):
    model_config = ConfigDict(extra="forbid", strict=True)
    snapshot_at: AwareDatetime
    entities: list[_StrictEntityManifest]


class _StrictBatch(EdgeSyncSnapshotBatchIn):
    model_config = ConfigDict(extra="forbid", strict=True)
    snapshot_at: AwareDatetime


def _fail(code: str) -> None:
    raise InventoryControlEvidenceError(code) from None


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _invalid_number(_: str) -> None:
    _fail("non_finite_json_number")


def _parse(raw: str, model: type):
    if type(raw) is not str or len(raw) > MAX_JSON_BYTES:
        _fail("invalid_evidence_shape")
    try:
        if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
            _fail("evidence_size_exceeded")
        value = json.loads(raw, object_pairs_hook=_object,
                           parse_constant=_invalid_number)
        # Canonicalizing rejects overflowed floats (e.g. 1e999), too. Parse as
        # JSON so strict timestamp/tuple validation does not coerce Python data.
        return model.model_validate_json(_canonical(value))
    except (ValidationError, ValueError, TypeError, UnicodeError, RecursionError):
        _fail("invalid_evidence_shape")


def _record(raw: Any, *, delta: bool) -> dict[str, Any]:
    required = _RECORD_FIELDS | ({"operation"} if delta else set())
    if type(raw) is not dict or set(raw) != required:
        _fail("invalid_inventory_record")
    key = raw["business_key"]
    if type(key) is not str or not key or key != key.strip() or len(key) > 200:
        _fail("invalid_inventory_record")
    stamp = raw["source_updated_at"]
    if stamp is not None:
        if type(stamp) is not str:
            _fail("invalid_inventory_record")
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.utcoffset() is None:
                _fail("invalid_inventory_record")
        except ValueError:
            _fail("invalid_inventory_record")
    data = raw["data"]
    if type(data) is not dict or len(_canonical(data).encode("utf-8")) > 64 * 1024:
        _fail("invalid_inventory_record")
    if delta and raw["operation"] == "delete":
        if data:
            _fail("invalid_inventory_record")
    else:
        if delta and raw["operation"] != "upsert":
            _fail("invalid_inventory_record")
        if not data or not set(data) <= INVENTORY_DATA_FIELDS:
            _fail("invalid_inventory_record")
        if type(data.get("materialCode")) is not str or not data["materialCode"].strip():
            _fail("invalid_inventory_record")
    return {field: raw[field] for field in _RECORD_FIELDS}


def _catalog(expected: InventoryCoverageExpectation):
    warehouses = {}
    positions = {}
    for warehouse in expected.warehouses:
        code = warehouse.warehouse_code
        if code in warehouses:
            _fail("duplicate_catalog_warehouse")
        warehouses[code] = warehouse
        for position in warehouse.positions:
            key = (code, position.position_code)
            if key in positions:
                _fail("duplicate_catalog_position")
            positions[key] = position.region_code
    target = {key for key, region in positions.items()
              if region == expected.target_region_code}
    if not target:
        _fail("target_region_not_covered")
    scope = expected.binding.scope_key
    if scope != "all" and (
        not re.fullmatch(r"warehouse:[A-Za-z0-9][A-Za-z0-9._-]{0,127}", scope)
        or set(warehouses) != {scope.removeprefix("warehouse:")}
    ):
        _fail("unsupported_or_incomplete_feed_scope")
    return warehouses, positions, target


def _manifest(evidence: InventorySnapshotEvidence, expected: InventoryCoverageExpectation):
    raw = evidence.manifest
    if set(raw) != _HEADER_FIELDS | {"entities"}:
        _fail("invalid_manifest")
    try:
        manifest = _StrictManifest.model_validate_json(_canonical(raw))
    except (ValidationError, ValueError, TypeError):
        _fail("invalid_manifest")
    binding = expected.binding
    if (evidence.source_instance != binding.source_instance
        or manifest.source_system != binding.source_system
        or manifest.company_id != binding.company_id
        or manifest.org_code != binding.org_code
        or manifest.scope_key != binding.scope_key):
        _fail("source_binding_mismatch")
    if (evidence.target_region_code != expected.target_region_code
        or evidence.catalog_revision != expected.catalog_revision):
        _fail("coverage_binding_mismatch")
    if len(manifest.entities) != 1 or manifest.entities[0].entity_type != "inventory":
        _fail("inventory_only_evidence_required")
    return manifest, manifest.entities[0]


def _reconstruct(evidence, manifest, entity, previous):
    if len(evidence.batches) != entity.batch_count:
        _fail("incomplete_transport_batches")
    sequences = set()
    changes = {}
    for raw in evidence.batches:
        if set(raw) != _HEADER_FIELDS | {"entity_type", "sequence", "total_sequences", "records"}:
            _fail("invalid_transport_batch")
        try:
            batch = _StrictBatch.model_validate_json(_canonical(raw))
        except (ValidationError, ValueError, TypeError):
            _fail("invalid_transport_batch")
        if (any(raw[field] != evidence.manifest[field] for field in _HEADER_FIELDS)
            or batch.entity_type != "inventory"
            or batch.total_sequences != entity.batch_count):
            _fail("transport_header_mismatch")
        if batch.sequence in sequences:
            _fail("duplicate_transport_sequence")
        sequences.add(batch.sequence)
        for change in raw["records"]:
            _record(change, delta=True)
            key = change["business_key"]
            if key in changes:
                _fail("duplicate_delta_record")
            changes[key] = change
    if sequences != set(range(1, entity.batch_count + 1)):
        _fail("incomplete_transport_batches")
    delta = sorted(changes.values(), key=lambda item: item["business_key"])
    if len(delta) != entity.delta_record_count or _sha(delta) != entity.delta_sha256:
        _fail("delta_manifest_mismatch")
    current = {} if manifest.sync_mode == "full" else dict(previous)
    for change in delta:
        key = change["business_key"]
        if change["operation"] == "delete":
            if manifest.sync_mode == "full" or key not in current:
                _fail("invalid_delete_base")
            del current[key]
        else:
            current[key] = _record(change, delta=True)
    records = sorted(current.values(), key=lambda item: item["business_key"])
    if len(records) != entity.final_record_count or _sha(records) != entity.final_sha256:
        _fail("final_manifest_mismatch")
    return current


def _capture(evidence, manifest, expected, warehouses, positions, current):
    per_warehouse = {code: {} for code in warehouses}
    for key, record in current.items():
        data = record["data"]
        warehouse_code, position_code = data.get("warehouseCode"), data.get("positionCode")
        if type(warehouse_code) is not str or type(position_code) is not str:
            _fail("inventory_position_unmapped")
        if (warehouse_code, position_code) not in positions:
            _fail("inventory_position_unmapped")
        warehouse = warehouses[warehouse_code]
        if (data.get("companyId") != expected.binding.company_id
            or data.get("orgCode") != expected.binding.org_code
            or data.get("warehouseType") != warehouse.warehouse_type
            or data.get("warehouseAttribute") != warehouse.warehouse_attribute):
            _fail("inventory_scope_mismatch")
        per_warehouse[warehouse_code][key] = record
    seen_warehouses = set()
    oldest_target_capture = manifest.snapshot_at
    for capture in evidence.warehouses:
        query = capture.query
        code = query.warehouseCode
        if code in seen_warehouses:
            _fail("duplicate_captured_warehouse")
        seen_warehouses.add(code)
        if code not in warehouses:
            _fail("captured_warehouse_unmapped")
        warehouse = warehouses[code]
        if (query.warehouseType != warehouse.warehouse_type
            or query.warehouseAttribute != warehouse.warehouse_attribute
            or query.snDisplayFlag != int(warehouse.warehouse_type == "supplyWarehouse")):
            _fail("capture_selector_mismatch")
        if not capture.started_at <= capture.completed_at <= manifest.snapshot_at:
            _fail("capture_time_mismatch")
        if any(pos.region_code == expected.target_region_code for pos in warehouse.positions):
            oldest_target_capture = min(oldest_target_capture, capture.started_at)
        first = capture.pages[0]
        total, size = first.source_total, first.size
        page_count = max(1, (total + size - 1) // size)
        if len(capture.pages) != page_count or total != len(per_warehouse[code]):
            _fail("incomplete_capture_pages")
        seen_pages, seen_keys = set(), set()
        for page in capture.pages:
            if page.source_total != total or page.size != size:
                _fail("capture_total_drift")
            if page.page in seen_pages or page.page > page_count:
                _fail("invalid_capture_sequence")
            seen_pages.add(page.page)
            required_count = min(size, max(0, total - (page.page - 1) * size))
            if len(page.record_keys) != required_count:
                _fail("incomplete_capture_page")
            records = []
            for key in page.record_keys:
                if key in seen_keys or key not in per_warehouse[code]:
                    _fail("capture_record_mismatch")
                seen_keys.add(key)
                records.append(per_warehouse[code][key])
            if _sha(sorted(records, key=lambda item: item["business_key"])) != page.records_sha256:
                _fail("capture_hash_mismatch")
        if seen_pages != set(range(1, page_count + 1)) or seen_keys != set(per_warehouse[code]):
            _fail("incomplete_capture_pages")
    if seen_warehouses != set(warehouses):
        _fail("incomplete_warehouse_coverage")
    return oldest_target_capture


def validate_inventory_control_evidence(
    *, expected_json: str, evidence_json: str, checked_at: datetime,
) -> InventoryControlValidationReport:
    """Check detached JSON against an independent expected catalogue.

    No database/session, clock, filesystem, network, locking, generated IDs,
    aggregation, or write-capable dependencies. Caller supplies the check time.
    A matching bundle only proves internal consistency, never authenticity.
    """
    if type(checked_at) is not datetime or checked_at.utcoffset() is None:
        _fail("invalid_check_time")
    expected = _parse(expected_json, InventoryCoverageExpectation)
    evidence = _parse(evidence_json, InventoryControlEvidence)
    warehouses, positions, target = _catalog(expected)
    current = {}
    previous_id = previous_hash = previous_time = None
    seen_ids = set()
    for snapshot in evidence.snapshots:
        manifest, entity = _manifest(snapshot, expected)
        if manifest.snapshot_id in seen_ids:
            _fail("duplicate_snapshot_id")
        seen_ids.add(manifest.snapshot_id)
        if manifest.snapshot_at > checked_at or (
            previous_time is not None and manifest.snapshot_at <= previous_time
        ):
            _fail("snapshot_time_mismatch")
        if manifest.sync_mode == "full":
            if snapshot.base_snapshot_id is not None or snapshot.base_final_sha256 is not None:
                _fail("unexpected_full_base")
        elif (previous_id is None or snapshot.base_snapshot_id != previous_id
              or snapshot.base_final_sha256 != previous_hash):
            _fail("incremental_base_mismatch")
        current = _reconstruct(snapshot, manifest, entity, current)
        oldest = _capture(snapshot, manifest, expected, warehouses, positions, current)
        if previous_time is not None and any(
            capture.started_at < previous_time for capture in snapshot.warehouses
        ):
            _fail("capture_chain_time_mismatch")
        previous_id, previous_hash, previous_time = (
            manifest.snapshot_id, entity.final_sha256, manifest.snapshot_at,
        )
    target_count = sum(
        (record["data"]["warehouseCode"], record["data"]["positionCode"]) in target
        for record in current.values()
    )
    age = checked_at - oldest
    return InventoryControlValidationReport(
        status="evidence_consistent", sync_mode=manifest.sync_mode,
        checked_snapshots=len(evidence.snapshots),
        target_warehouse_count=len({code for code, _ in target}),
        target_position_count=len(target), target_record_count=target_count,
        oldest_capture_age_seconds=age.total_seconds(),
        within_freshness_target=age <= FRESHNESS_TARGET,
    )
