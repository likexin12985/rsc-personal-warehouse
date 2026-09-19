"""Prepare detached facts for a future inventory control publication.

This module deliberately stops before persistence, publication, or opening
stocktake admission.  It turns the already isolated evidence consistency
result into four separately hashed facts so a later trusted loader can bind
them to a forward database model without collapsing source, catalogue,
capture, and control-manifest evidence into one transport hash.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from .inventory_control_evidence import (
    InventoryControlEvidenceError,
    _parse,
    _sha,
    validate_inventory_control_evidence,
)
from .inventory_control_evidence_schemas import (
    InventoryControlEvidence,
    InventoryCoverageExpectation,
)


PUBLICATION_SCHEMA = "rsc.inventory_control_publication.v1"


@dataclass(frozen=True, slots=True)
class InventoryControlPublicationFacts:
    """Non-authorizing, content-addressed facts for a future publisher."""

    schema_version: Literal["rsc.inventory_control_publication.v1"]
    source_binding_sha256: str
    catalog_sha256: str
    capture_chain_sha256: str
    control_manifest_sha256: str
    evidence_status: Literal["evidence_consistent"]
    projection_published: Literal[False] = False
    start_ready: Literal[False] = False

    def as_dict(self) -> dict[str, Any]:
        """Return only sealed facts, never source inventory or identities."""

        return {
            "schema_version": self.schema_version,
            "source_binding_sha256": self.source_binding_sha256,
            "catalog_sha256": self.catalog_sha256,
            "capture_chain_sha256": self.capture_chain_sha256,
            "control_manifest_sha256": self.control_manifest_sha256,
            "evidence_status": self.evidence_status,
            "projection_published": self.projection_published,
            "start_ready": self.start_ready,
        }


def _json_model(value: Any) -> Any:
    """Use JSON-compatible model output before hashing detached facts."""

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_json_model(item) for item in value]
    if isinstance(value, list):
        return [_json_model(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_model(item) for key, item in value.items()}
    return value


def prepare_inventory_control_publication(
    *, expected_json: str, evidence_json: str, checked_at: datetime,
) -> InventoryControlPublicationFacts:
    """Validate detached evidence and seal independent forward-model hashes.

    The function has no database/session, filesystem, network, lock, ID
    generation, or write-capable dependency.  A successful return is still
    only an internally consistent preparation result; authentication,
    catalogue approval, immutable capture storage, and publication ACLs are
    intentionally absent.
    """

    report = validate_inventory_control_evidence(
        expected_json=expected_json,
        evidence_json=evidence_json,
        checked_at=checked_at,
    )
    expected = _parse(expected_json, InventoryCoverageExpectation)
    evidence = _parse(evidence_json, InventoryControlEvidence)

    source_binding = _json_model(expected.binding)
    catalogue = {
        "catalog_revision": expected.catalog_revision,
        "target_region_code": expected.target_region_code,
        "warehouses": _json_model(expected.warehouses),
    }
    capture_chain = {
        "schema_version": evidence.schema_version,
        "snapshots": _json_model(evidence.snapshots),
    }
    source_binding_sha256 = _sha(source_binding)
    catalog_sha256 = _sha(catalogue)
    capture_chain_sha256 = _sha(capture_chain)
    control_manifest = {
        "schema_version": PUBLICATION_SCHEMA,
        "evidence_status": report.status,
        "sync_mode": report.sync_mode,
        "checked_snapshots": report.checked_snapshots,
        "target_warehouse_count": report.target_warehouse_count,
        "target_position_count": report.target_position_count,
        "target_record_count": report.target_record_count,
        "oldest_capture_age_seconds": report.oldest_capture_age_seconds,
        "within_freshness_target": report.within_freshness_target,
        "source_binding_sha256": source_binding_sha256,
        "catalog_sha256": catalog_sha256,
        "capture_chain_sha256": capture_chain_sha256,
        "projection_published": False,
        "start_ready": False,
    }
    return InventoryControlPublicationFacts(
        schema_version=PUBLICATION_SCHEMA,
        source_binding_sha256=source_binding_sha256,
        catalog_sha256=catalog_sha256,
        capture_chain_sha256=capture_chain_sha256,
        control_manifest_sha256=_sha(control_manifest),
        evidence_status=report.status,
    )


__all__ = [
    "InventoryControlPublicationFacts",
    "InventoryControlEvidenceError",
    "PUBLICATION_SCHEMA",
    "prepare_inventory_control_publication",
]
