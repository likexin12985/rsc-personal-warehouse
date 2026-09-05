"""Internal, offline consistency evidence; NOT an edge ingress or start API.

The expected catalogue must eventually come from an independently authorized,
versioned binding. Neither these models nor a matching hash authenticate it.
Only isolated synthetic evidence is wired to this contract in this slice.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints


Identifier = Annotated[
    str, StringConstraints(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
RecordKey = Annotated[str, StringConstraints(min_length=1, max_length=200)]
SourceInstance = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
]


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class InventoryFeedBinding(_EvidenceModel):
    source_system: Literal["starcharge_oam"]
    source_instance: SourceInstance
    company_id: Identifier
    org_code: Identifier
    scope_key: Identifier


class InventoryPositionBinding(_EvidenceModel):
    position_code: Identifier
    region_code: Identifier


class InventoryWarehouseBinding(_EvidenceModel):
    warehouse_code: Identifier
    warehouse_type: Identifier
    warehouse_attribute: Identifier
    positions: Annotated[
        tuple[InventoryPositionBinding, ...], Field(min_length=1, max_length=10000)
    ]


class InventoryCoverageExpectation(_EvidenceModel):
    schema_version: Literal["rsc.inventory_control_coverage.v1"]
    binding: InventoryFeedBinding
    catalog_revision: Identifier
    target_region_code: Identifier
    # Entire feed catalogue, not a catalogue inferred from inventory rows or
    # just the target province. This also supports the existing national feed.
    warehouses: Annotated[
        tuple[InventoryWarehouseBinding, ...], Field(min_length=1, max_length=10000)
    ]


class InventoryWarehouseQuery(_EvidenceModel):
    warehouseCode: Identifier
    warehouseType: Identifier
    warehouseAttribute: Identifier
    querySource: Literal["PC"]
    snDisplayFlag: Annotated[int, Field(ge=0, le=1)]


class InventoryCapturePage(_EvidenceModel):
    # Explicit successful source response, including the zero-row first page.
    # Legacy get_paged()'s absent amount -> 0 fallback cannot supply this proof.
    response_status: Literal["success"]
    page: Annotated[int, Field(ge=1, le=10000)]
    size: Annotated[int, Field(ge=1, le=1000)]
    source_total: Annotated[int, Field(ge=0, le=1000000)]
    record_keys: Annotated[tuple[RecordKey, ...], Field(max_length=1000)]
    records_sha256: Digest


class InventoryWarehouseCapture(_EvidenceModel):
    query: InventoryWarehouseQuery
    started_at: AwareDatetime
    completed_at: AwareDatetime
    pages: Annotated[
        tuple[InventoryCapturePage, ...], Field(min_length=1, max_length=10000)
    ]


class InventorySnapshotEvidence(_EvidenceModel):
    # Transport context is separate from the manifest, which has no source ID.
    # A future loader must verify it against authenticated staging metadata.
    source_instance: SourceInstance
    target_region_code: Identifier
    catalog_revision: Identifier
    base_snapshot_id: Identifier | None
    base_final_sha256: Digest | None
    manifest: dict[str, Any]
    batches: Annotated[tuple[dict[str, Any], ...], Field(max_length=10000)]
    warehouses: Annotated[
        tuple[InventoryWarehouseCapture, ...], Field(min_length=1, max_length=10000)
    ]


class InventoryControlEvidence(_EvidenceModel):
    schema_version: Literal["rsc.inventory_control_coverage.v1"]
    # Bounded replay proof, rooted in a full state, then zero or more increments.
    # This is not a restriction on production sync modes or retention policy.
    snapshots: Annotated[
        tuple[InventorySnapshotEvidence, ...], Field(min_length=1, max_length=64)
    ]
