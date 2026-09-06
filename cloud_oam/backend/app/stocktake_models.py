"""Formal V1.0 opening-stocktake evidence and establishment schema.

The tables in this module are deliberately separate from the quarantined v0.9
stocktake prototype.  They record count evidence, two independent reviews, the
link to an immutable inventory posting, and the final per-asset-owner/location
opening fact.  No row in this module is populated from legacy balances or OAM
control quantities by schema migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    FetchedValue,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


UUID_TYPE = Uuid(as_uuid=True)
QUANTITY = Numeric(18, 3)
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")
NULLABLE_JSON_DOCUMENT = JSON(none_as_null=True).with_variant(
    JSONB(none_as_null=True),
    "postgresql",
)


def uuid4_value() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class FormalStocktakeTask(TimestampMixin, Base):
    __tablename__ = "stocktake_tasks"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_formal_stocktake_tasks"),
        UniqueConstraint("task_no", name="uq_formal_stocktake_tasks_no"),
        CheckConstraint(
            "task_type IN ('opening', 'full', 'sample', 'ad_hoc', "
            "'personal', 'termination')",
            name="ck_formal_stocktake_tasks_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'issued', 'frozen', 'counting', 'submitted', "
            "'region_review', 'hq_review', 'approved', 'recount_required', "
            "'posted', 'closed', 'cancelled')",
            name="ck_formal_stocktake_tasks_status",
        ),
        CheckConstraint(
            "cutoff_ledger_cursor IS NULL OR cutoff_ledger_cursor >= 0",
            name="ck_formal_stocktake_tasks_cutoff_cursor",
        ),
        CheckConstraint(
            "(cutoff_ledger_cursor IS NULL) = (cutoff_at IS NULL)",
            name="ck_formal_stocktake_tasks_cutoff_pair",
        ),
        CheckConstraint(
            "current_round_no >= 0 AND version >= 0",
            name="ck_formal_stocktake_tasks_versions",
        ),
        CheckConstraint(
            "(scope_manifest_sha256 IS NULL OR "
            "length(scope_manifest_sha256) = 64) AND "
            "(snapshot_manifest_sha256 IS NULL OR "
            "length(snapshot_manifest_sha256) = 64) AND "
            "(control_manifest_sha256 IS NULL OR "
            "length(control_manifest_sha256) = 64)",
            name="ck_formal_stocktake_tasks_hashes",
        ),
        CheckConstraint(
            "task_type <> 'opening' OR status = 'draft' OR "
            "(control_source_system_id IS NOT NULL AND "
            "control_sync_run_id IS NOT NULL AND control_snapshot_at IS NOT NULL "
            "AND control_manifest_sha256 IS NOT NULL)",
            name="ck_formal_stocktake_tasks_opening_control",
        ),
        CheckConstraint(
            "submitted_at IS NULL OR frozen_at IS NOT NULL",
            name="ck_formal_stocktake_tasks_submission_order",
        ),
        CheckConstraint(
            "posted_at IS NULL OR submitted_at IS NOT NULL",
            name="ck_formal_stocktake_tasks_posting_order",
        ),
        CheckConstraint(
            "closed_at IS NULL OR posted_at IS NOT NULL",
            name="ck_formal_stocktake_tasks_close_order",
        ),
        Index(
            "uq_formal_stocktake_tasks_active_opening_region",
            "region_org_id",
            unique=True,
            postgresql_where=text(
                "task_type = 'opening' AND status NOT IN ('closed', 'cancelled')"
            ),
            sqlite_where=text(
                "task_type = 'opening' AND status NOT IN ('closed', 'cancelled')"
            ),
        ),
        Index(
            "ix_formal_stocktake_tasks_region_status", "region_org_id", "status"
        ),
        Index("ix_formal_stocktake_tasks_type_status", "task_type", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_no: Mapped[str] = mapped_column(String(100))
    task_type: Mapped[str] = mapped_column(String(24), default="opening")
    # Region is the task/batch boundary.  Asset ownership remains a separate
    # dimension on each scope and must never be inferred from this field.
    region_org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32), default="draft")
    blind_count: Mapped[bool] = mapped_column(Boolean, default=True)
    cutoff_ledger_cursor: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    cutoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scope_manifest_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    snapshot_manifest_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    control_source_system_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("source_systems.id", ondelete="RESTRICT"),
        nullable=True,
    )
    control_sync_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("sync_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    control_snapshot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    control_manifest_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    current_round_no: Mapped[int] = mapped_column(Integer, default=0)
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    frozen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(BigInteger, default=0)
    note: Mapped[str] = mapped_column(Text, default="")


class FormalStocktakeScope(CreatedAtMixin, Base):
    __tablename__ = "stocktake_scopes"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_formal_stocktake_scopes"),
        UniqueConstraint(
            "task_id", "scope_no", name="uq_formal_stocktake_scopes_number"
        ),
        UniqueConstraint(
            "task_id", "scope_key", name="uq_formal_stocktake_scopes_key"
        ),
        UniqueConstraint(
            "task_id", "scope_sha256", name="uq_formal_stocktake_scopes_hash"
        ),
        UniqueConstraint(
            "id", "task_id", name="uq_formal_stocktake_scopes_id_task"
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "scope_key",
            name="uq_formal_stocktake_scopes_freeze_binding",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "owner_org_id",
            "location_id",
            name="uq_formal_stocktake_scopes_establishment",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["stocktake_tasks.id"],
            name="fk_formal_stocktake_scopes_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "scope_no > 0", name="ck_formal_stocktake_scopes_number"
        ),
        CheckConstraint(
            "scope_mode IN ('location_all', 'filtered')",
            name="ck_formal_stocktake_scopes_mode",
        ),
        CheckConstraint(
            "(scope_mode = 'location_all' AND material_id IS NULL AND "
            "condition_code IS NULL AND availability_bucket IS NULL) OR "
            "(scope_mode = 'filtered' AND (material_id IS NOT NULL OR "
            "condition_code IS NOT NULL OR availability_bucket IS NOT NULL))",
            name="ck_formal_stocktake_scopes_filter_binding",
        ),
        CheckConstraint(
            "condition_code IS NULL OR condition_code IN "
            "('new', 'used', 'damaged', 'scrapped')",
            name="ck_formal_stocktake_scopes_condition",
        ),
        CheckConstraint(
            "availability_bucket IS NULL OR availability_bucket IN "
            "('available', 'reserved', 'picking', 'outbound', 'in_transit', "
            "'arrived_pending', 'frozen', 'return_pending', 'scrap_pending')",
            name="ck_formal_stocktake_scopes_availability",
        ),
        CheckConstraint(
            "length(scope_sha256) = 64",
            name="ck_formal_stocktake_scopes_hash",
        ),
        Index("ix_formal_stocktake_scopes_location", "location_id"),
        Index(
            "ix_formal_stocktake_scopes_assignee", "assignee_user_id", "task_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    scope_no: Mapped[int] = mapped_column(Integer)
    scope_mode: Mapped[str] = mapped_column(String(24), default="location_all")
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_locations.id", ondelete="RESTRICT")
    )
    owner_org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    custodian_person_id_snapshot: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"), nullable=True
    )
    assignee_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    material_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("materials.id", ondelete="RESTRICT"), nullable=True
    )
    condition_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    availability_bucket: Mapped[str | None] = mapped_column(
        String(24), nullable=True
    )
    scope_key: Mapped[str] = mapped_column(String(300))
    scope_sha256: Mapped[str] = mapped_column(String(64))


class StocktakeControlSnapshotLine(CreatedAtMixin, Base):
    __tablename__ = "stocktake_control_snapshot_lines"
    __table_args__ = (
        PrimaryKeyConstraint(
            "id", name="pk_stocktake_control_snapshot_lines"
        ),
        UniqueConstraint(
            "task_id",
            "line_no",
            name="uq_stocktake_control_snapshot_lines_number",
        ),
        UniqueConstraint(
            "task_id",
            "external_business_key",
            name="uq_stocktake_control_snapshot_lines_business",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            name="uq_stocktake_control_snapshot_lines_id_task",
        ),
        CheckConstraint(
            "line_no > 0", name="ck_stocktake_control_snapshot_lines_number"
        ),
        CheckConstraint(
            "control_qty >= 0",
            name="ck_stocktake_control_snapshot_lines_quantity",
        ),
        CheckConstraint(
            "mapping_status IN ('resolved', 'unresolved')",
            name="ck_stocktake_control_snapshot_lines_mapping",
        ),
        CheckConstraint(
            "(mapping_status = 'resolved' AND material_id IS NOT NULL AND "
            "condition_code IS NOT NULL) OR "
            "(mapping_status = 'unresolved' AND length(trim(mapping_note)) > 0)",
            name="ck_stocktake_control_snapshot_lines_resolution",
        ),
        CheckConstraint(
            "condition_code IS NULL OR condition_code IN "
            "('new', 'used', 'damaged', 'scrapped')",
            name="ck_stocktake_control_snapshot_lines_condition",
        ),
        CheckConstraint(
            "length(payload_sha256) = 64",
            name="ck_stocktake_control_snapshot_lines_hash",
        ),
        Index(
            "ix_stocktake_control_snapshot_lines_material", "material_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stocktake_tasks.id", ondelete="RESTRICT")
    )
    line_no: Mapped[int] = mapped_column(Integer)
    external_business_key: Mapped[str] = mapped_column(String(300))
    external_object_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("external_object_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    material_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("materials.id", ondelete="RESTRICT"), nullable=True
    )
    condition_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    control_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    mapping_status: Mapped[str] = mapped_column(String(20))
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload_sha256: Mapped[str] = mapped_column(String(64))
    mapping_note: Mapped[str] = mapped_column(Text, default="")


class InventoryFreeze(TimestampMixin, Base):
    __tablename__ = "inventory_freezes"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_inventory_freezes"),
        UniqueConstraint(
            "task_id", "stocktake_scope_id", name="uq_inventory_freezes_scope"
        ),
        ForeignKeyConstraint(
            ["stocktake_scope_id", "task_id", "scope_key"],
            [
                "stocktake_scopes.id",
                "stocktake_scopes.task_id",
                "stocktake_scopes.scope_key",
            ],
            name="fk_inventory_freezes_scope_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "freeze_mode IN ('hard', 'cutoff_replay')",
            name="ck_inventory_freezes_mode",
        ),
        CheckConstraint(
            "status IN ('active', 'released', 'cancelled')",
            name="ck_inventory_freezes_status",
        ),
        CheckConstraint(
            "(status = 'active' AND valid_to IS NULL) OR "
            "(status IN ('released', 'cancelled') AND valid_to IS NOT NULL)",
            name="ck_inventory_freezes_status_time",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_inventory_freezes_validity",
        ),
        CheckConstraint("version >= 0", name="ck_inventory_freezes_version"),
        Index(
            "uq_inventory_freezes_active_scope",
            "scope_key",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index("ix_inventory_freezes_status_time", "status", "valid_from"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    stocktake_scope_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    scope_key: Mapped[str] = mapped_column(String(300))
    freeze_mode: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(20), default="active")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    released_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    release_reason: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(BigInteger, default=0)


class StocktakeSnapshotLine(CreatedAtMixin, Base):
    __tablename__ = "stocktake_snapshot_lines"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_stocktake_snapshot_lines"),
        UniqueConstraint(
            "task_id",
            "stock_account_id",
            name="uq_stocktake_snapshot_lines_account",
        ),
        ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_snapshot_lines_scope_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "book_qty >= 0", name="ck_stocktake_snapshot_lines_quantity"
        ),
        CheckConstraint(
            "ledger_cursor >= 0", name="ck_stocktake_snapshot_lines_cursor"
        ),
        CheckConstraint(
            "serial_count >= 0", name="ck_stocktake_snapshot_lines_serial_count"
        ),
        CheckConstraint(
            "length(account_dimension_sha256) = 64 AND "
            "length(serial_snapshot_sha256) = 64",
            name="ck_stocktake_snapshot_lines_hashes",
        ),
        Index("ix_stocktake_snapshot_lines_scope", "scope_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    stock_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_accounts.id", ondelete="RESTRICT")
    )
    book_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    ledger_cursor: Mapped[int] = mapped_column(BigInteger)
    account_dimension_sha256: Mapped[str] = mapped_column(String(64))
    serial_snapshot_jsonb: Mapped[list[Any]] = mapped_column(
        JSON_DOCUMENT, default=list
    )
    serial_snapshot_sha256: Mapped[str] = mapped_column(String(64))
    serial_count: Mapped[int] = mapped_column(Integer, default=0)


class StocktakeRound(TimestampMixin, Base):
    __tablename__ = "stocktake_rounds"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_stocktake_rounds"),
        UniqueConstraint(
            "task_id", "round_no", name="uq_stocktake_rounds_number"
        ),
        UniqueConstraint("id", "task_id", name="uq_stocktake_rounds_id_task"),
        UniqueConstraint(
            "idempotency_key_hash", name="uq_stocktake_rounds_idempotency"
        ),
        CheckConstraint(
            "round_no > 0", name="ck_stocktake_rounds_number"
        ),
        CheckConstraint(
            "round_type IN ('initial', 'recount')",
            name="ck_stocktake_rounds_type",
        ),
        CheckConstraint(
            "(round_no = 1 AND round_type = 'initial') OR "
            "(round_no > 1 AND round_type = 'recount')",
            name="ck_stocktake_rounds_type_number",
        ),
        CheckConstraint(
            "status IN ('counting', 'submitted', 'superseded')",
            name="ck_stocktake_rounds_status",
        ),
        CheckConstraint(
            "(status = 'counting' AND submitted_at IS NULL AND "
            "submitted_by_user_id IS NULL AND count_manifest_sha256 IS NULL) OR "
            "(status IN ('submitted', 'superseded') AND submitted_at IS NOT NULL "
            "AND submitted_by_user_id IS NOT NULL AND "
            "count_manifest_sha256 IS NOT NULL)",
            name="ck_stocktake_rounds_submission",
        ),
        CheckConstraint(
            "length(idempotency_key_hash) = 64 AND "
            "(count_manifest_sha256 IS NULL OR "
            "length(count_manifest_sha256) = 64)",
            name="ck_stocktake_rounds_hashes",
        ),
        Index(
            "uq_stocktake_rounds_recount_case_0018",
            "recount_case_id",
            unique=True,
            postgresql_where=text("recount_case_id IS NOT NULL"),
            sqlite_where=text("recount_case_id IS NOT NULL"),
        ),
        Index(
            "uq_stocktake_rounds_one_counting_0018",
            "task_id",
            unique=True,
            postgresql_where=text("status = 'counting'"),
            sqlite_where=text("status = 'counting'"),
        ),
        Index("ix_stocktake_rounds_task_status", "task_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stocktake_tasks.id", ondelete="RESTRICT")
    )
    round_no: Mapped[int] = mapped_column(Integer)
    round_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="counting")
    submitted_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    count_manifest_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    recount_case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey(
            "stocktake_recount_cases.id",
            name="fk_stocktake_rounds_recount_case_0018",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )


class StocktakeStartCompletion(CreatedAtMixin, Base):
    """Immutable database-sealed proof of one non-opening task start.

    The application appends this row only after the cutoff snapshot, freezes,
    initial round, transition events and audit event have been staged.  On
    PostgreSQL the 0047 guard owns ``graph_manifest_sha256`` and the deferred
    graph validator prevents any partial or rewritten start graph from being
    committed.  SQLite keeps the generated column nullable until its test-only
    AFTER INSERT trigger installs a structural seal.
    """

    __tablename__ = "stocktake_start_completions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "id", name="pk_stocktake_start_completions_0047"
        ),
        UniqueConstraint(
            "task_id", name="uq_stocktake_start_completions_task_0047"
        ),
        UniqueConstraint(
            "initial_round_id",
            name="uq_stocktake_start_completions_round_0047",
        ),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_start_completions_idempotency_0047",
        ),
        UniqueConstraint(
            "id", "task_id", name="uq_stocktake_start_completions_id_task_0047"
        ),
        ForeignKeyConstraint(
            ["initial_round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_start_completions_round_0047",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "expected_task_version >= 0 AND "
            "started_task_version = expected_task_version + 1",
            name="ck_stocktake_start_completions_versions_0047",
        ),
        CheckConstraint(
            "cutoff_ledger_cursor >= 0 AND scope_count > 0 AND "
            "snapshot_line_count >= 0 AND active_freeze_count = scope_count",
            name="ck_stocktake_start_completions_counts_0047",
        ),
        CheckConstraint(
            "authorization_version > 0 AND "
            "((role_code = 'admin' AND scope_type = 'national' AND "
            "scope_id_snapshot = '*') OR "
            "(role_code = 'provincial_manager' AND "
            "scope_type = 'organization' AND "
            "length(trim(scope_id_snapshot)) > 0) OR "
            "(role_code = 'technician' AND scope_type = 'person' AND "
            "length(trim(scope_id_snapshot)) > 0))",
            name="ck_stocktake_start_completions_authorization_0047",
        ),
        CheckConstraint(
            "length(scope_manifest_sha256) = 64 AND "
            "length(snapshot_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64 AND "
            "length(authorization_sha256) = 64 AND "
            "length(graph_manifest_sha256) = 64",
            name="ck_stocktake_start_completions_hashes_0047",
        ),
        CheckConstraint(
            "cutoff_at <= started_at AND created_at = started_at",
            name="ck_stocktake_start_completions_chronology_0047",
        ),
        Index(
            "ix_stocktake_start_completions_actor_0047",
            "started_by_user_id",
            "started_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stocktake_tasks.id", ondelete="RESTRICT")
    )
    initial_round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    expected_task_version: Mapped[int] = mapped_column(BigInteger)
    started_task_version: Mapped[int] = mapped_column(BigInteger)
    cutoff_ledger_cursor: Mapped[int] = mapped_column(BigInteger)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scope_count: Mapped[int] = mapped_column(Integer)
    snapshot_line_count: Mapped[int] = mapped_column(Integer)
    active_freeze_count: Mapped[int] = mapped_column(Integer)
    scope_manifest_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_manifest_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    started_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    started_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    started_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    role_code: Mapped[str] = mapped_column(String(40))
    scope_type: Mapped[str] = mapped_column(String(24))
    scope_id_snapshot: Mapped[str] = mapped_column(String(80))
    authorization_sha256: Mapped[str] = mapped_column(String(64))
    graph_manifest_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True, server_default=FetchedValue()
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StocktakeCountLine(TimestampMixin, Base):
    __tablename__ = "stocktake_count_lines"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_stocktake_count_lines"),
        UniqueConstraint(
            "round_id",
            "stock_account_id",
            name="uq_stocktake_count_lines_account",
        ),
        UniqueConstraint(
            "id", "round_id", name="uq_stocktake_count_lines_id_round"
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "round_id",
            name="uq_stocktake_count_lines_id_task_round",
        ),
        ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_count_lines_round_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_count_lines_scope_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "counted_qty >= 0", name="ck_stocktake_count_lines_quantity"
        ),
        CheckConstraint(
            "count_method IN ('scan', 'manual', 'import')",
            name="ck_stocktake_count_lines_method",
        ),
        Index("ix_stocktake_count_lines_scope", "scope_id"),
        Index("ix_stocktake_count_lines_counter", "counted_by_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    stock_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_accounts.id", ondelete="RESTRICT")
    )
    counted_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    count_method: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    remark: Mapped[str] = mapped_column(Text, default="")
    counted_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    counted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StocktakeCountSerial(CreatedAtMixin, Base):
    __tablename__ = "stocktake_count_serials"
    __table_args__ = (
        PrimaryKeyConstraint(
            "count_line_id", "serial_id", name="pk_stocktake_count_serials"
        ),
        UniqueConstraint(
            "round_id", "serial_id", name="uq_stocktake_count_serials_round"
        ),
        ForeignKeyConstraint(
            ["count_line_id", "round_id"],
            ["stocktake_count_lines.id", "stocktake_count_lines.round_id"],
            name="fk_stocktake_count_serials_line_round",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "result IN ('present', 'missing', 'unexpected', 'wrong_location', "
            "'wrong_condition', 'wrong_lot', 'wrong_serial')",
            name="ck_stocktake_count_serials_result",
        ),
    )

    count_line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    serial_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_serials.id", ondelete="RESTRICT")
    )
    result: Mapped[str] = mapped_column(String(24))


class StocktakeCountObservation(CreatedAtMixin, Base):
    """Immutable physical evidence for a dimension with no cutoff account.

    Raw identifiers are retained even when a material, lot, or serial cannot be
    uniquely mapped.  Such rows remain ``pending_verification``; the count path
    must never create an empty stock account merely to record what was seen.
    """

    __tablename__ = "stocktake_count_observations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_stocktake_count_observations"),
        UniqueConstraint(
            "round_id",
            "observation_no",
            name="uq_stocktake_count_observations_number",
        ),
        UniqueConstraint(
            "round_id",
            "dimension_sha256",
            name="uq_stocktake_count_observations_dimension",
        ),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_count_observations_idempotency",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "round_id",
            name="uq_stocktake_count_observations_id_task_round",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "round_id",
            "scope_id",
            name="uq_stocktake_count_observations_difference_binding",
        ),
        ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_count_observations_round_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_count_observations_scope_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "observation_no > 0",
            name="ck_stocktake_count_observations_number",
        ),
        CheckConstraint(
            "counted_qty > 0",
            name="ck_stocktake_count_observations_quantity",
        ),
        CheckConstraint(
            "condition_code IN ('new', 'used', 'damaged', 'scrapped')",
            name="ck_stocktake_count_observations_condition",
        ),
        CheckConstraint(
            "availability_bucket IN ('available', 'reserved', 'picking', "
            "'outbound', 'in_transit', 'arrived_pending', 'frozen', "
            "'return_pending', 'scrap_pending')",
            name="ck_stocktake_count_observations_availability",
        ),
        CheckConstraint(
            "count_method IN ('scan', 'manual', 'import')",
            name="ck_stocktake_count_observations_method",
        ),
        CheckConstraint(
            "material_identifier_type IN "
            "('sku_code', 'qr_code', 'external_code', 'unknown') AND "
            "(serial_no_raw IS NULL AND serial_identifier_type IS NULL OR "
            "serial_no_raw IS NOT NULL AND serial_identifier_type IN "
            "('serial_no', 'qr_code', 'unknown'))",
            name="ck_stocktake_count_observations_identifier_types",
        ),
        CheckConstraint(
            "verification_status IN ('verified', 'pending_verification')",
            name="ck_stocktake_count_observations_verification_status",
        ),
        CheckConstraint(
            "length(trim(material_identifier_raw)) > 0 AND "
            "(lot_no_raw IS NULL OR length(trim(lot_no_raw)) > 0) AND "
            "(serial_no_raw IS NULL OR length(trim(serial_no_raw)) > 0)",
            name="ck_stocktake_count_observations_raw_identifiers",
        ),
        CheckConstraint(
            "(verification_status = 'verified' AND material_id IS NOT NULL "
            "AND (lot_no_raw IS NULL OR lot_id IS NOT NULL) "
            "AND (serial_no_raw IS NULL OR serial_id IS NOT NULL)) OR "
            "(verification_status = 'pending_verification' AND "
            "(material_id IS NULL OR "
            "(lot_no_raw IS NOT NULL AND lot_id IS NULL) OR "
            "(serial_no_raw IS NOT NULL AND serial_id IS NULL)))",
            name="ck_stocktake_count_observations_verification_binding",
        ),
        CheckConstraint(
            "(lot_id IS NULL OR (material_id IS NOT NULL AND "
            "lot_no_raw IS NOT NULL)) AND "
            "(serial_id IS NULL OR (material_id IS NOT NULL AND "
            "serial_no_raw IS NOT NULL)) AND "
            "(serial_no_raw IS NULL OR counted_qty = 1)",
            name="ck_stocktake_count_observations_tracking_binding",
        ),
        CheckConstraint(
            "length(dimension_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64",
            name="ck_stocktake_count_observations_hashes",
        ),
        CheckConstraint(
            "created_at = counted_at",
            name="ck_stocktake_count_observations_chronology",
        ),
        Index(
            "ix_stocktake_count_observations_scope",
            "scope_id",
            "round_id",
        ),
        Index(
            "ix_stocktake_count_observations_material",
            "material_id",
            "verification_status",
        ),
        Index(
            "ix_stocktake_count_observations_serial_raw", "serial_no_raw"
        ),
        Index(
            "uq_stocktake_count_observations_round_serial_raw",
            "round_id",
            "serial_identifier_type",
            "serial_no_raw",
            unique=True,
            postgresql_where=text("serial_no_raw IS NOT NULL"),
            sqlite_where=text("serial_no_raw IS NOT NULL"),
        ),
        Index(
            "uq_stocktake_count_observations_round_serial_id",
            "round_id",
            "serial_id",
            unique=True,
            postgresql_where=text("serial_id IS NOT NULL"),
            sqlite_where=text("serial_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    observation_no: Mapped[int] = mapped_column(Integer)
    owner_org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_locations.id", ondelete="RESTRICT")
    )
    custodian_person_id_snapshot: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"), nullable=True
    )
    material_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("materials.id", ondelete="RESTRICT"), nullable=True
    )
    material_identifier_raw: Mapped[str] = mapped_column(String(300))
    material_identifier_type: Mapped[str] = mapped_column(String(24))
    condition_code: Mapped[str] = mapped_column(String(20))
    availability_bucket: Mapped[str] = mapped_column(String(24))
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_lots.id", ondelete="RESTRICT"), nullable=True
    )
    lot_no_raw: Mapped[str | None] = mapped_column(String(160), nullable=True)
    serial_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("inventory_serials.id", ondelete="RESTRICT"),
        nullable=True,
    )
    serial_no_raw: Mapped[str | None] = mapped_column(String(200), nullable=True)
    serial_identifier_type: Mapped[str | None] = mapped_column(
        String(24), nullable=True
    )
    counted_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    verification_status: Mapped[str] = mapped_column(String(24))
    count_method: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    remark: Mapped[str] = mapped_column(Text, default="")
    counted_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    counted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dimension_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))


class StocktakeScopeCountCompletion(CreatedAtMixin, Base):
    """Immutable proof that one scope was explicitly counted or zero-counted."""

    __tablename__ = "stocktake_scope_count_completions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "id", name="pk_stocktake_scope_count_completions"
        ),
        UniqueConstraint(
            "round_id",
            "scope_id",
            name="uq_stocktake_scope_count_completions_scope",
        ),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_scope_count_completions_idempotency",
        ),
        ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_scope_count_completions_round_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_scope_count_completions_scope_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "count_line_count >= 0 AND observation_line_count >= 0 AND "
            "serial_count >= 0 AND total_counted_qty >= 0",
            name="ck_stocktake_scope_count_completions_totals",
        ),
        CheckConstraint(
            "(zero_confirmed AND count_line_count = 0 AND "
            "observation_line_count = 0 AND serial_count = 0 AND "
            "total_counted_qty = 0) OR "
            "(NOT zero_confirmed AND "
            "count_line_count + observation_line_count > 0)",
            name="ck_stocktake_scope_count_completions_nonblank",
        ),
        CheckConstraint(
            "length(evidence_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64",
            name="ck_stocktake_scope_count_completions_hashes",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_stocktake_scope_count_completions_authorization_version",
        ),
        CheckConstraint(
            "count_ledger_cursor IS NULL OR count_ledger_cursor >= 0",
            name="ck_stocktake_scope_count_completions_count_cursor",
        ),
        CheckConstraint(
            "role_code IN ('admin', 'provincial_manager', 'technician') AND "
            "scope_type IN ('national', 'organization', 'person') AND "
            "length(trim(scope_id_snapshot)) > 0 AND "
            "length(authorization_sha256) = 64",
            name="ck_stocktake_scope_count_completions_authorization_snapshot",
        ),
        CheckConstraint(
            "created_at = completed_at",
            name="ck_stocktake_scope_count_completions_chronology",
        ),
        Index(
            "ix_stocktake_scope_count_completions_task",
            "task_id",
            "round_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    count_line_count: Mapped[int] = mapped_column(Integer)
    observation_line_count: Mapped[int] = mapped_column(Integer)
    serial_count: Mapped[int] = mapped_column(Integer)
    total_counted_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    zero_confirmed: Mapped[bool] = mapped_column(Boolean)
    evidence_manifest_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    # Opening counts persist the canonical caller request so the database can
    # prove its hash without trying to reverse resolved/aggregated result rows.
    # The column remains nullable for legacy and non-opening count facts; the
    # PostgreSQL opening graph requires a strict object for every new opening
    # completion and migration 0052 only backfills historically provable rows.
    request_jsonb: Mapped[dict[str, Any] | None] = mapped_column(
        NULLABLE_JSON_DOCUMENT,
        nullable=True,
    )
    # The immutable resolution manifest binds every canonical request item to
    # the exact count line or unresolved observation produced from it.  It is
    # nullable only so non-opening and pre-migration facts remain representable;
    # the PostgreSQL opening graph requires a strict v1 document.
    request_resolution_jsonb: Mapped[dict[str, Any] | None] = mapped_column(
        NULLABLE_JSON_DOCUMENT,
        nullable=True,
    )
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    completed_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    completed_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    completed_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    role_code: Mapped[str] = mapped_column(String(40))
    scope_type: Mapped[str] = mapped_column(String(24))
    scope_id_snapshot: Mapped[str] = mapped_column(String(80))
    authorization_sha256: Mapped[str] = mapped_column(String(64))
    # Revision 0033 deliberately keeps this nullable so pre-0033 evidence can
    # be retained without inventing a historical boundary.  Every new formal
    # non-opening count seals a server-derived value and all downstream paths
    # fail closed when an older row has no cursor.
    count_ledger_cursor: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StocktakeRoundSubmission(CreatedAtMixin, Base):
    """Immutable post-child manifest required before a round may submit."""

    __tablename__ = "stocktake_round_submissions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_stocktake_round_submissions"),
        UniqueConstraint(
            "task_id",
            "round_id",
            name="uq_stocktake_round_submissions_round",
        ),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_round_submissions_idempotency",
        ),
        ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_round_submissions_round_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "scope_count > 0 AND zero_scope_count >= 0 AND "
            "zero_scope_count <= scope_count AND count_line_count >= 0 AND "
            "observation_line_count >= 0 AND serial_count >= 0 AND "
            "total_counted_qty >= 0",
            name="ck_stocktake_round_submissions_totals",
        ),
        CheckConstraint(
            "length(round_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64",
            name="ck_stocktake_round_submissions_hashes",
        ),
        CheckConstraint(
            "length(count_manifest_sha256) = 64",
            name="ck_stocktake_round_submissions_count_manifest_sha256",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_stocktake_round_submissions_authorization_version",
        ),
        CheckConstraint(
            "created_at = submitted_at",
            name="ck_stocktake_round_submissions_chronology",
        ),
        Index("ix_stocktake_round_submissions_task", "task_id"),
        Index(
            "uq_stocktake_round_submissions_sealing_completion",
            "sealing_completion_id",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    sealing_completion_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        nullable=True,
    )
    scope_count: Mapped[int] = mapped_column(Integer)
    zero_scope_count: Mapped[int] = mapped_column(Integer)
    count_line_count: Mapped[int] = mapped_column(Integer)
    observation_line_count: Mapped[int] = mapped_column(Integer)
    serial_count: Mapped[int] = mapped_column(Integer)
    total_counted_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    round_manifest_sha256: Mapped[str] = mapped_column(String(64))
    # This count-line/SN manifest is intentionally independent from the
    # completion/observation round manifest above.  The former remains the
    # immutable inventory-posting guard; the latter seals every scope and raw
    # observation without pretending that unresolved evidence is inventory.
    count_manifest_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    submitted_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    submitted_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    submitted_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StocktakeObservationDisposition(CreatedAtMixin, Base):
    """Append-only handling fact for one pending physical observation.

    A resolved disposition may only bind existing material/lot/SN masters.
    It never names or creates a stock account, balance, movement or ledger
    transaction.  Pending and recount outcomes deliberately retain no guessed
    resolved identifiers.
    """

    __tablename__ = "stocktake_observation_dispositions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_stocktake_observation_dispositions"),
        UniqueConstraint(
            "observation_id",
            name="uq_stocktake_observation_dispositions_observation",
        ),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_observation_dispositions_idempotency",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "round_id",
            name="uq_stocktake_observation_dispositions_id_task_round",
        ),
        ForeignKeyConstraint(
            ["observation_id", "task_id", "round_id", "scope_id"],
            [
                "stocktake_count_observations.id",
                "stocktake_count_observations.task_id",
                "stocktake_count_observations.round_id",
                "stocktake_count_observations.scope_id",
            ],
            name="fk_stocktake_observation_dispositions_observation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["resolved_lot_id", "resolved_material_id"],
            ["inventory_lots.id", "inventory_lots.material_id"],
            name="fk_stocktake_observation_dispositions_lot_material",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "disposition IN ('resolved_existing_master', "
            "'pending_verification', 'requires_recount')",
            name="ck_stocktake_observation_dispositions_disposition",
        ),
        CheckConstraint(
            "(disposition = 'resolved_existing_master' AND "
            "resolved_material_id IS NOT NULL) OR "
            "(disposition IN ('pending_verification', 'requires_recount') AND "
            "resolved_material_id IS NULL AND resolved_lot_id IS NULL AND "
            "resolved_serial_id IS NULL)",
            name="ck_stocktake_observation_dispositions_resolution",
        ),
        CheckConstraint(
            "length(trim(reason_code)) > 0 AND "
            "(disposition = 'resolved_existing_master' OR "
            "length(trim(comment)) > 0)",
            name="ck_stocktake_observation_dispositions_reason",
        ),
        CheckConstraint(
            "length(disposition_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64 AND "
            "length(authorization_sha256) = 64",
            name="ck_stocktake_observation_dispositions_hashes",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_stocktake_observation_dispositions_authorization_version",
        ),
        CheckConstraint(
            "(role_code = 'admin' AND scope_type = 'national' AND "
            "scope_id_snapshot = '*') OR "
            "(role_code = 'provincial_manager' AND "
            "scope_type = 'organization' AND "
            "length(trim(scope_id_snapshot)) > 0)",
            name="ck_stocktake_observation_dispositions_authorization_snapshot",
        ),
        CheckConstraint(
            "disposition <> 'resolved_existing_master' OR "
            "(role_code = 'admin' AND scope_type = 'national' AND "
            "scope_id_snapshot = '*')",
            name="ck_stocktake_observation_dispositions_resolution_role",
        ),
        CheckConstraint(
            "created_at = decided_at",
            name="ck_stocktake_observation_dispositions_chronology",
        ),
        Index(
            "ix_stocktake_observation_dispositions_round",
            "task_id",
            "round_id",
        ),
        Index(
            "ix_stocktake_observation_dispositions_status",
            "disposition",
            "decided_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    observation_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    disposition: Mapped[str] = mapped_column(String(32))
    resolved_material_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("materials.id", ondelete="RESTRICT"),
        nullable=True,
    )
    resolved_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        nullable=True,
    )
    resolved_serial_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("inventory_serials.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reason_code: Mapped[str] = mapped_column(String(80))
    comment: Mapped[str] = mapped_column(Text, default="")
    disposition_manifest_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    decided_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    decided_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    decided_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    role_code: Mapped[str] = mapped_column(String(40))
    scope_type: Mapped[str] = mapped_column(String(24))
    scope_id_snapshot: Mapped[str] = mapped_column(String(80))
    authorization_sha256: Mapped[str] = mapped_column(String(64))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StocktakeDifferenceSetCompletion(CreatedAtMixin, Base):
    """Immutable post-child seal for one round's complete difference set."""

    __tablename__ = "stocktake_difference_set_completions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "id", name="pk_stocktake_difference_set_completions"
        ),
        UniqueConstraint(
            "task_id",
            "round_id",
            name="uq_stocktake_difference_set_completions_round",
        ),
        UniqueConstraint(
            "round_submission_id",
            name="uq_stocktake_difference_set_completions_submission",
        ),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_difference_set_completions_idempotency",
        ),
        ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_difference_set_completions_round_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "difference_count >= 0 AND physical_difference_count >= 0 AND "
            "control_difference_count >= 0 AND "
            "physical_difference_count + control_difference_count = "
            "difference_count AND pending_observation_difference_count >= 0 "
            "AND pending_observation_difference_count <= "
            "physical_difference_count AND total_affected_qty >= 0",
            name="ck_stocktake_difference_set_completions_totals",
        ),
        CheckConstraint(
            "length(difference_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64 AND "
            "length(authorization_sha256) = 64",
            name="ck_stocktake_difference_set_completions_hashes",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_stocktake_difference_set_completions_authorization_version",
        ),
        CheckConstraint(
            "created_at = completed_at",
            name="ck_stocktake_difference_set_completions_chronology",
        ),
        CheckConstraint(
            "role_code IN ('admin', 'provincial_manager', 'technician') AND "
            "scope_type IN ('national', 'organization', 'person') AND "
            "length(trim(scope_id_snapshot)) > 0",
            name="ck_stocktake_diff_completions_authorization_0031",
        ),
        Index(
            "ix_stocktake_difference_set_completions_task",
            "task_id",
            "round_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("stocktake_round_submissions.id", ondelete="RESTRICT"),
    )
    difference_count: Mapped[int] = mapped_column(Integer)
    physical_difference_count: Mapped[int] = mapped_column(Integer)
    control_difference_count: Mapped[int] = mapped_column(Integer)
    pending_observation_difference_count: Mapped[int] = mapped_column(Integer)
    total_affected_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    difference_manifest_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    completed_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    completed_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    completed_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    role_code: Mapped[str] = mapped_column(String(40))
    scope_type: Mapped[str] = mapped_column(String(24))
    scope_id_snapshot: Mapped[str] = mapped_column(String(80))
    authorization_sha256: Mapped[str] = mapped_column(String(64))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StocktakeDifference(CreatedAtMixin, Base):
    __tablename__ = "stocktake_differences"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_stocktake_differences"),
        UniqueConstraint(
            "task_id",
            "round_id",
            "difference_no",
            name="uq_stocktake_differences_number",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "round_id",
            name="uq_stocktake_differences_id_task_round",
        ),
        ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_differences_round_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_differences_scope_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["control_snapshot_line_id", "task_id"],
            [
                "stocktake_control_snapshot_lines.id",
                "stocktake_control_snapshot_lines.task_id",
            ],
            name="fk_stocktake_differences_control_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["observed_line_id", "task_id", "round_id", "scope_id"],
            [
                "stocktake_count_observations.id",
                "stocktake_count_observations.task_id",
                "stocktake_count_observations.round_id",
                "stocktake_count_observations.scope_id",
            ],
            name="fk_stocktake_differences_observed_line",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "difference_no > 0", name="ck_stocktake_differences_number"
        ),
        CheckConstraint(
            "difference_type IN ('missing', 'excess', 'wrong_location', "
            "'wrong_condition', 'wrong_lot', 'wrong_serial', "
            "'control_unassigned')",
            name="ck_stocktake_differences_type",
        ),
        CheckConstraint(
            "book_qty >= 0 AND counted_qty >= 0 AND affected_qty > 0",
            name="ck_stocktake_differences_quantities",
        ),
        CheckConstraint(
            "difference_qty = counted_qty - book_qty",
            name="ck_stocktake_differences_arithmetic",
        ),
        CheckConstraint(
            "(difference_type = 'missing' AND scope_id IS NOT NULL AND "
            "control_snapshot_line_id IS NULL AND material_id IS NOT NULL AND "
            "expected_account_id IS NOT NULL AND observed_account_id IS NULL "
            "AND observed_line_id IS NULL "
            "AND difference_qty < 0) OR "
            "(difference_type = 'excess' AND scope_id IS NOT NULL AND "
            "control_snapshot_line_id IS NULL AND "
            "expected_account_id IS NULL AND "
            "((observed_account_id IS NOT NULL AND observed_line_id IS NULL "
            "AND material_id IS NOT NULL) OR "
            "(observed_account_id IS NULL AND observed_line_id IS NOT NULL)) "
            "AND difference_qty > 0) OR "
            "(difference_type IN ('wrong_location', 'wrong_condition', "
            "'wrong_lot') AND scope_id IS NOT NULL AND "
            "control_snapshot_line_id IS NULL AND "
            "expected_account_id IS NOT NULL AND "
            "((observed_account_id IS NOT NULL AND observed_line_id IS NULL "
            "AND material_id IS NOT NULL AND "
            "expected_account_id <> observed_account_id) OR "
            "(observed_account_id IS NULL AND observed_line_id IS NOT NULL))) OR "
            "(difference_type = 'wrong_serial' AND scope_id IS NOT NULL AND "
            "control_snapshot_line_id IS NULL AND "
            "((observed_line_id IS NULL AND material_id IS NOT NULL AND "
            "serial_id IS NOT NULL AND (expected_account_id IS NOT NULL OR "
            "observed_account_id IS NOT NULL)) OR "
            "(observed_line_id IS NOT NULL AND observed_account_id IS NULL))) "
            "OR (difference_type = 'control_unassigned' AND "
            "scope_id IS NULL AND control_snapshot_line_id IS NOT NULL AND "
            "expected_account_id IS NULL AND observed_account_id IS NULL AND "
            "observed_line_id IS NULL AND "
            "difference_qty <> 0)",
            name="ck_stocktake_differences_binding",
        ),
        Index(
            "ix_stocktake_differences_task_type",
            "task_id",
            "round_id",
            "difference_type",
        ),
        Index("ix_stocktake_differences_material", "material_id"),
        Index("ix_stocktake_differences_serial", "serial_id"),
        Index(
            "ix_stocktake_differences_control_line", "control_snapshot_line_id"
        ),
        Index("ix_stocktake_differences_observed_line", "observed_line_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    control_snapshot_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    difference_no: Mapped[int] = mapped_column(Integer)
    difference_type: Mapped[str] = mapped_column(String(28))
    material_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("materials.id", ondelete="RESTRICT"), nullable=True
    )
    expected_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("stock_accounts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    observed_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("stock_accounts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    observed_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    serial_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("inventory_serials.id", ondelete="RESTRICT"),
        nullable=True,
    )
    book_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    counted_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    difference_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    affected_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reason_text: Mapped[str] = mapped_column(Text, default="")
    evidence_required: Mapped[bool] = mapped_column(Boolean, default=True)


class StocktakeReview(CreatedAtMixin, Base):
    __tablename__ = "stocktake_reviews"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_stocktake_reviews"),
        UniqueConstraint(
            "task_id",
            "round_id",
            "review_stage",
            name="uq_stocktake_reviews_stage",
        ),
        UniqueConstraint(
            "id", "task_id", "round_id", name="uq_stocktake_reviews_id_task_round"
        ),
        UniqueConstraint(
            "idempotency_key_hash", name="uq_stocktake_reviews_idempotency"
        ),
        ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_reviews_round_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "review_stage IN ('region', 'headquarters')",
            name="ck_stocktake_reviews_stage",
        ),
        CheckConstraint(
            "decision IN ('approve', 'recount', 'reject')",
            name="ck_stocktake_reviews_decision",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_stocktake_reviews_authorization_version",
        ),
        CheckConstraint(
            "((expected_task_version IS NULL AND resulting_task_version IS NULL) "
            "OR (expected_task_version IS NOT NULL "
            "AND resulting_task_version IS NOT NULL "
            "AND expected_task_version >= 0 "
            "AND resulting_task_version >= 0 "
            "AND resulting_task_version = expected_task_version + 1))",
            name="ck_stocktake_reviews_task_version_pair_0063",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "length(decision_manifest_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64",
            name="ck_stocktake_reviews_hashes",
        ),
        Index(
            "ix_stocktake_reviews_reviewer", "reviewer_user_id", "reviewed_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    review_stage: Mapped[str] = mapped_column(String(20))
    reviewer_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    reviewer_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    reviewer_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    # Non-opening review commands persist both sides of the optimistic
    # concurrency coordinate.  Opening reviews share this table for historical
    # compatibility and intentionally keep these fields NULL until a later
    # opening-specific migration defines their own coordinate semantics.
    expected_task_version: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    resulting_task_version: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    decision: Mapped[str] = mapped_column(String(20))
    comment: Mapped[str] = mapped_column(Text, default="")
    decision_manifest_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StocktakeRecountCase(CreatedAtMixin, Base):
    """Immutable authorization and causality fact for one successor round.

    A recount never rewrites or marks its submitted predecessor as superseded.
    This case is the append-only edge from the reviewed source round to the
    next contiguous round, including the exact source evidence and request,
    scope, assignment and authorization manifests used when the edge opened.
    """

    __tablename__ = "stocktake_recount_cases"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_stocktake_recount_cases"),
        UniqueConstraint(
            "source_round_id",
            name="uq_stocktake_recount_cases_source_round_0018",
        ),
        UniqueConstraint(
            "source_round_submission_id",
            name="uq_stocktake_recount_cases_source_submission_0018",
        ),
        UniqueConstraint(
            "source_difference_completion_id",
            name="uq_stocktake_recount_cases_source_completion_0018",
        ),
        UniqueConstraint(
            "trigger_review_id",
            name="uq_stocktake_recount_cases_trigger_review_0018",
        ),
        UniqueConstraint(
            "task_id",
            "next_round_no",
            name="uq_stocktake_recount_cases_task_next_round_0018",
        ),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_recount_cases_idempotency_0018",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "source_round_id",
            name="uq_stocktake_recount_cases_id_task_source_0018",
        ),
        ForeignKeyConstraint(
            ["source_round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_recount_cases_source_round_0018",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["trigger_review_id", "task_id", "source_round_id"],
            [
                "stocktake_reviews.id",
                "stocktake_reviews.task_id",
                "stocktake_reviews.round_id",
            ],
            name="fk_stocktake_recount_cases_trigger_review_0018",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "next_round_no > 1 AND scope_count > 0",
            name="ck_stocktake_recount_cases_round_0018",
        ),
        CheckConstraint(
            "length(scope_manifest_sha256) = 64 AND "
            "length(assignment_manifest_sha256) = 64 AND "
            "length(recount_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64 AND "
            "length(authorization_sha256) = 64",
            name="ck_stocktake_recount_cases_hashes_0018",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_stocktake_recount_cases_authorization_version_0018",
        ),
        CheckConstraint(
            "(role_code = 'admin' AND scope_type = 'national' AND "
            "scope_id_snapshot = '*') OR "
            "(role_code = 'provincial_manager' AND "
            "scope_type = 'organization' AND "
            "length(trim(scope_id_snapshot)) > 0)",
            name="ck_stocktake_recount_cases_authorization_snapshot_0018",
        ),
        CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_stocktake_recount_cases_reason_0018",
        ),
        CheckConstraint(
            "created_at = opened_at",
            name="ck_stocktake_recount_cases_chronology_0018",
        ),
        Index(
            "ix_stocktake_recount_cases_task_0018",
            "task_id",
            "next_round_no",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stocktake_tasks.id", ondelete="RESTRICT")
    )
    source_round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    source_round_submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("stocktake_round_submissions.id", ondelete="RESTRICT"),
    )
    source_difference_completion_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey(
            "stocktake_difference_set_completions.id", ondelete="RESTRICT"
        ),
    )
    trigger_review_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    next_round_no: Mapped[int] = mapped_column(Integer)
    scope_count: Mapped[int] = mapped_column(Integer)
    scope_manifest_sha256: Mapped[str] = mapped_column(String(64))
    assignment_manifest_sha256: Mapped[str] = mapped_column(String(64))
    recount_manifest_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    opened_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    opened_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    opened_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    role_code: Mapped[str] = mapped_column(String(40))
    scope_type: Mapped[str] = mapped_column(String(24))
    scope_id_snapshot: Mapped[str] = mapped_column(String(80))
    authorization_sha256: Mapped[str] = mapped_column(String(64))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StocktakeRecountScopeAssignment(CreatedAtMixin, Base):
    """Immutable per-round assignee and authorization snapshot for one scope."""

    __tablename__ = "stocktake_recount_scope_assignments"
    __table_args__ = (
        PrimaryKeyConstraint(
            "id", name="pk_stocktake_recount_scope_assignments"
        ),
        UniqueConstraint(
            "recount_case_id",
            "scope_id",
            name="uq_stocktake_recount_scope_assignments_case_scope_0018",
        ),
        ForeignKeyConstraint(
            ["recount_case_id", "task_id", "source_round_id"],
            [
                "stocktake_recount_cases.id",
                "stocktake_recount_cases.task_id",
                "stocktake_recount_cases.source_round_id",
            ],
            name="fk_stocktake_recount_scope_assignments_case_0018",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_recount_scope_assignments_scope_0018",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_recount_scope_assignments_auth_version_0018",
        ),
        CheckConstraint(
            "role_code IN ('admin', 'provincial_manager', 'technician') AND "
            "((role_code = 'admin' AND scope_type = 'national' AND "
            "scope_id_snapshot = '*') OR "
            "(role_code = 'provincial_manager' AND "
            "scope_type = 'organization' AND "
            "length(trim(scope_id_snapshot)) > 0) OR "
            "(role_code = 'technician' AND scope_type = 'person' AND "
            "length(trim(scope_id_snapshot)) > 0))",
            name="ck_recount_scope_assignments_auth_snapshot_0018",
        ),
        CheckConstraint(
            "length(authorization_sha256) = 64 AND "
            "length(assignment_sha256) = 64",
            name="ck_stocktake_recount_scope_assignments_hashes_0018",
        ),
        CheckConstraint(
            "created_at = assigned_at",
            name="ck_stocktake_recount_scope_assignments_chronology_0018",
        ),
        Index(
            "ix_stocktake_recount_scope_assignments_assignee_0018",
            "assignee_user_id",
            "task_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    recount_case_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    source_round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    assignee_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    assignee_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    assignee_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    role_code: Mapped[str] = mapped_column(String(40))
    scope_type: Mapped[str] = mapped_column(String(24))
    scope_id_snapshot: Mapped[str] = mapped_column(String(80))
    authorization_sha256: Mapped[str] = mapped_column(String(64))
    assignment_sha256: Mapped[str] = mapped_column(String(64))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StocktakeReviewItem(CreatedAtMixin, Base):
    __tablename__ = "stocktake_review_items"
    __table_args__ = (
        PrimaryKeyConstraint(
            "review_id", "difference_id", name="pk_stocktake_review_items"
        ),
        ForeignKeyConstraint(
            ["review_id", "task_id", "round_id"],
            ["stocktake_reviews.id", "stocktake_reviews.task_id", "stocktake_reviews.round_id"],
            name="fk_stocktake_review_items_review",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["difference_id", "task_id", "round_id"],
            [
                "stocktake_differences.id",
                "stocktake_differences.task_id",
                "stocktake_differences.round_id",
            ],
            name="fk_stocktake_review_items_difference",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "decision IN ('accept_for_posting', 'pending_verification', "
            "'no_adjustment', 'recount', 'reject')",
            name="ck_stocktake_review_items_decision",
        ),
        Index("ix_stocktake_review_items_difference", "difference_id"),
    )

    review_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    difference_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    decision: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(Text, default="")


class StocktakeEffectiveApprovalCompletion(CreatedAtMixin, Base):
    """Immutable task-wide seal produced by the final headquarters review.

    A recount can replace only selected scopes.  The terminal round therefore
    cannot, by itself, identify the effective evidence for every task scope.
    This row and its scope/item children freeze that complete causal manifest;
    inventory posting consumes this fact rather than inferring approval from a
    historical top-level review status.
    """

    __tablename__ = "stocktake_effective_approval_completions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "id", name="pk_stocktake_effective_approval_completions_0035"
        ),
        UniqueConstraint(
            "task_id", name="uq_stocktake_effective_approval_task_0035"
        ),
        UniqueConstraint(
            "terminal_headquarters_review_id",
            name="uq_stocktake_effective_approval_hq_review_0035",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            name="uq_stocktake_effective_approval_id_task_0035",
        ),
        ForeignKeyConstraint(
            ["terminal_round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_effective_approval_round_0035",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["terminal_headquarters_review_id", "task_id", "terminal_round_id"],
            ["stocktake_reviews.id", "stocktake_reviews.task_id", "stocktake_reviews.round_id"],
            name="fk_stocktake_effective_approval_hq_review_0035",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "scope_count > 0 AND difference_count >= 0 AND "
            "accepted_difference_count >= 0 AND no_adjustment_count >= 0 AND "
            "accepted_difference_count + no_adjustment_count = difference_count",
            name="ck_stocktake_effective_approval_counts_0035",
        ),
        CheckConstraint(
            "expected_task_version >= 0 AND approved_task_version = "
            "expected_task_version + 1",
            name="ck_stocktake_effective_approval_versions_0035",
        ),
        CheckConstraint(
            "authorization_version > 0 AND role_code = 'admin' AND "
            "scope_type = 'national' AND scope_id_snapshot = '*'",
            name="ck_stocktake_effective_approval_authorization_0035",
        ),
        CheckConstraint(
            "length(approval_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(authorization_sha256) = 64",
            name="ck_stocktake_effective_approval_hashes_0035",
        ),
        CheckConstraint(
            "created_at = completed_at",
            name="ck_stocktake_effective_approval_chronology_0035",
        ),
        Index(
            "ix_stocktake_effective_approval_completed_0035",
            "completed_by_user_id",
            "completed_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stocktake_tasks.id", ondelete="RESTRICT")
    )
    terminal_round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    terminal_headquarters_review_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    expected_task_version: Mapped[int] = mapped_column(BigInteger)
    approved_task_version: Mapped[int] = mapped_column(BigInteger)
    scope_count: Mapped[int] = mapped_column(Integer)
    difference_count: Mapped[int] = mapped_column(Integer)
    accepted_difference_count: Mapped[int] = mapped_column(Integer)
    no_adjustment_count: Mapped[int] = mapped_column(Integer)
    approval_manifest_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    completed_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    completed_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    completed_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    role_code: Mapped[str] = mapped_column(String(40))
    scope_type: Mapped[str] = mapped_column(String(24))
    scope_id_snapshot: Mapped[str] = mapped_column(String(80))
    authorization_sha256: Mapped[str] = mapped_column(String(64))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StocktakeEffectiveApprovalScope(CreatedAtMixin, Base):
    __tablename__ = "stocktake_effective_approval_scopes"
    __table_args__ = (
        PrimaryKeyConstraint(
            "completion_id",
            "scope_id",
            name="pk_stocktake_effective_approval_scopes_0035",
        ),
        ForeignKeyConstraint(
            ["completion_id", "task_id"],
            ["stocktake_effective_approval_completions.id", "stocktake_effective_approval_completions.task_id"],
            name="fk_stocktake_effective_approval_scopes_completion_0035",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_effective_approval_scopes_scope_0035",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_effective_approval_scopes_round_0035",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_difference_completion_id"],
            ["stocktake_difference_set_completions.id"],
            name="fk_stocktake_eff_approval_scopes_diff_completion_0035",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["regional_review_id", "task_id", "source_round_id"],
            ["stocktake_reviews.id", "stocktake_reviews.task_id", "stocktake_reviews.round_id"],
            name="fk_stocktake_effective_approval_scopes_region_review_0035",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "difference_count >= 0",
            name="ck_stocktake_effective_approval_scopes_count_0035",
        ),
        CheckConstraint(
            "length(scope_manifest_sha256) = 64",
            name="ck_stocktake_effective_approval_scopes_hash_0035",
        ),
        Index(
            "ix_stocktake_effective_approval_scopes_source_0035",
            "task_id",
            "source_round_id",
        ),
    )

    completion_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    source_round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    source_difference_completion_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    regional_review_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    difference_count: Mapped[int] = mapped_column(Integer)
    scope_manifest_sha256: Mapped[str] = mapped_column(String(64))


class StocktakeEffectiveApprovalItem(CreatedAtMixin, Base):
    __tablename__ = "stocktake_effective_approval_items"
    __table_args__ = (
        PrimaryKeyConstraint(
            "completion_id",
            "difference_id",
            name="pk_stocktake_effective_approval_items_0035",
        ),
        UniqueConstraint(
            "difference_id",
            name="uq_stocktake_effective_approval_items_difference_0035",
        ),
        ForeignKeyConstraint(
            ["completion_id", "scope_id"],
            ["stocktake_effective_approval_scopes.completion_id", "stocktake_effective_approval_scopes.scope_id"],
            name="fk_stocktake_effective_approval_items_scope_0035",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["difference_id", "task_id", "source_round_id"],
            ["stocktake_differences.id", "stocktake_differences.task_id", "stocktake_differences.round_id"],
            name="fk_stocktake_effective_approval_items_difference_0035",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["regional_review_id", "task_id", "source_round_id"],
            ["stocktake_reviews.id", "stocktake_reviews.task_id", "stocktake_reviews.round_id"],
            name="fk_stocktake_effective_approval_items_region_review_0035",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "regional_decision IN ('accept_for_posting', 'no_adjustment') AND "
            "headquarters_decision = regional_decision",
            name="ck_stocktake_effective_approval_items_decisions_0035",
        ),
        CheckConstraint(
            "length(trim(headquarters_comment)) > 0",
            name="ck_stocktake_effective_approval_items_comment_0035",
        ),
        CheckConstraint(
            "length(item_manifest_sha256) = 64",
            name="ck_stocktake_effective_approval_items_hash_0035",
        ),
        Index(
            "ix_stocktake_effective_approval_items_scope_0035",
            "completion_id",
            "scope_id",
        ),
    )

    completion_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    difference_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    source_round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    regional_review_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    regional_decision: Mapped[str] = mapped_column(String(32))
    headquarters_decision: Mapped[str] = mapped_column(String(32))
    headquarters_comment: Mapped[str] = mapped_column(Text)
    item_manifest_sha256: Mapped[str] = mapped_column(String(64))


class StocktakePosting(CreatedAtMixin, Base):
    __tablename__ = "stocktake_postings"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_stocktake_postings"),
        UniqueConstraint(
            "task_id",
            "round_id",
            "posting_kind",
            name="uq_stocktake_postings_kind",
        ),
        UniqueConstraint(
            "inventory_transaction_id",
            name="uq_stocktake_postings_transaction",
        ),
        UniqueConstraint(
            "id", "task_id", "round_id", name="uq_stocktake_postings_id_task_round"
        ),
        UniqueConstraint(
            "idempotency_key_hash", name="uq_stocktake_postings_idempotency"
        ),
        ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_postings_round_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "posting_kind IN ('opening', 'difference_adjustment', "
            "'difference_gain', 'difference_loss', 'difference_transfer', "
            "'difference_status_change')",
            name="ck_stocktake_postings_kind",
        ),
        CheckConstraint(
            "(posting_kind IN ('difference_gain', 'difference_loss', "
            "'difference_transfer', 'difference_status_change') AND "
            "effective_approval_completion_id IS NOT NULL) OR "
            "(posting_kind IN ('opening', 'difference_adjustment') AND "
            "effective_approval_completion_id IS NULL)",
            name="ck_stocktake_postings_effective_approval_0035",
        ),
        CheckConstraint(
            "total_quantity >= 0", name="ck_stocktake_postings_quantity"
        ),
        CheckConstraint(
            "(total_quantity = 0 AND inventory_transaction_id IS NULL) OR "
            "(total_quantity > 0 AND inventory_transaction_id IS NOT NULL)",
            name="ck_stocktake_postings_transaction_binding",
        ),
        CheckConstraint(
            "length(idempotency_key_hash) = 64 AND length(request_hash) = 64",
            name="ck_stocktake_postings_hashes",
        ),
        Index("ix_stocktake_postings_task", "task_id", "posted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    posting_kind: Mapped[str] = mapped_column(String(28))
    effective_approval_completion_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey(
            "stocktake_effective_approval_completions.id", ondelete="RESTRICT"
        ),
        nullable=True,
    )
    inventory_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("inventory_transactions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    total_quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    posted_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StocktakePostingItem(CreatedAtMixin, Base):
    __tablename__ = "stocktake_posting_items"
    __table_args__ = (
        PrimaryKeyConstraint(
            "posting_id", "inventory_movement_id", name="pk_stocktake_posting_items"
        ),
        UniqueConstraint(
            "inventory_movement_id", name="uq_stocktake_posting_items_movement"
        ),
        ForeignKeyConstraint(
            ["posting_id", "task_id", "round_id"],
            ["stocktake_postings.id", "stocktake_postings.task_id", "stocktake_postings.round_id"],
            name="fk_stocktake_posting_items_posting",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["count_line_id", "task_id", "round_id"],
            [
                "stocktake_count_lines.id",
                "stocktake_count_lines.task_id",
                "stocktake_count_lines.round_id",
            ],
            name="fk_stocktake_posting_items_count_line",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["difference_id", "task_id", "round_id"],
            [
                "stocktake_differences.id",
                "stocktake_differences.task_id",
                "stocktake_differences.round_id",
            ],
            name="fk_stocktake_posting_items_difference",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(count_line_id IS NOT NULL AND difference_id IS NULL) OR "
            "(count_line_id IS NULL AND difference_id IS NOT NULL)",
            name="ck_stocktake_posting_items_source",
        ),
        CheckConstraint(
            "quantity > 0", name="ck_stocktake_posting_items_quantity"
        ),
        Index("ix_stocktake_posting_items_count_line", "count_line_id"),
        Index("ix_stocktake_posting_items_difference", "difference_id"),
        Index(
            "uq_stocktake_posting_items_difference_0035",
            "difference_id",
            unique=True,
            postgresql_where=text("difference_id IS NOT NULL"),
            sqlite_where=text("difference_id IS NOT NULL"),
        ),
    )

    posting_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    inventory_movement_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_movements.id", ondelete="RESTRICT")
    )
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    count_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    difference_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)


class StocktakePostingCompletion(CreatedAtMixin, Base):
    """Immutable completion of one task-wide, atomic difference posting."""

    __tablename__ = "stocktake_posting_completions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "id", name="pk_stocktake_posting_completions_0035"
        ),
        UniqueConstraint(
            "task_id", name="uq_stocktake_posting_completions_task_0035"
        ),
        UniqueConstraint(
            "effective_approval_completion_id",
            name="uq_stocktake_posting_completions_approval_0035",
        ),
        UniqueConstraint(
            "id", "task_id", name="uq_stocktake_posting_completions_id_task_0035"
        ),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_posting_completions_idempotency_0035",
        ),
        ForeignKeyConstraint(
            ["effective_approval_completion_id", "task_id"],
            ["stocktake_effective_approval_completions.id", "stocktake_effective_approval_completions.task_id"],
            name="fk_stocktake_posting_completions_approval_0035",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["terminal_round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_posting_completions_round_0035",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "scope_count > 0 AND difference_count >= 0 AND "
            "accepted_difference_count >= 0 AND no_adjustment_count >= 0 AND "
            "accepted_difference_count + no_adjustment_count = difference_count AND "
            "transaction_count >= 0 AND movement_count = accepted_difference_count AND "
            "total_quantity >= 0",
            name="ck_stocktake_posting_completions_counts_0035",
        ),
        CheckConstraint(
            "expected_task_version >= 0 AND posted_task_version = "
            "expected_task_version + 1",
            name="ck_stocktake_posting_completions_versions_0035",
        ),
        CheckConstraint(
            "(transaction_count = 0 AND first_ledger_cursor IS NULL AND "
            "last_ledger_cursor IS NULL AND movement_count = 0 AND "
            "total_quantity = 0) OR "
            "(transaction_count > 0 AND first_ledger_cursor > 0 AND "
            "last_ledger_cursor >= first_ledger_cursor AND "
            "last_ledger_cursor - first_ledger_cursor + 1 = transaction_count AND "
            "movement_count > 0 AND total_quantity > 0)",
            name="ck_stocktake_posting_completions_cursors_0035",
        ),
        CheckConstraint(
            "authorization_version > 0 AND role_code = 'admin' AND "
            "scope_type = 'national' AND scope_id_snapshot = '*'",
            name="ck_stocktake_posting_completions_authorization_0035",
        ),
        CheckConstraint(
            "length(approval_manifest_sha256) = 64 AND "
            "length(posting_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64 AND "
            "length(authorization_sha256) = 64",
            name="ck_stocktake_posting_completions_hashes_0035",
        ),
        CheckConstraint(
            "created_at = posted_at",
            name="ck_stocktake_posting_completions_chronology_0035",
        ),
        Index(
            "ix_stocktake_posting_completions_actor_0035",
            "posted_by_user_id",
            "posted_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    effective_approval_completion_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    terminal_round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    expected_task_version: Mapped[int] = mapped_column(BigInteger)
    posted_task_version: Mapped[int] = mapped_column(BigInteger)
    scope_count: Mapped[int] = mapped_column(Integer)
    difference_count: Mapped[int] = mapped_column(Integer)
    accepted_difference_count: Mapped[int] = mapped_column(Integer)
    no_adjustment_count: Mapped[int] = mapped_column(Integer)
    transaction_count: Mapped[int] = mapped_column(Integer)
    movement_count: Mapped[int] = mapped_column(Integer)
    total_quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    first_ledger_cursor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_ledger_cursor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    approval_manifest_sha256: Mapped[str] = mapped_column(String(64))
    posting_manifest_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    # Nullable for historical completions created before migration 0066.
    # New writers must bind this to the exact client request coordinate.
    request_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    posted_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    posted_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    posted_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    role_code: Mapped[str] = mapped_column(String(40))
    scope_type: Mapped[str] = mapped_column(String(24))
    scope_id_snapshot: Mapped[str] = mapped_column(String(80))
    authorization_sha256: Mapped[str] = mapped_column(String(64))
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StocktakePostingCompletionItem(CreatedAtMixin, Base):
    __tablename__ = "stocktake_posting_completion_items"
    __table_args__ = (
        PrimaryKeyConstraint(
            "completion_id",
            "difference_id",
            name="pk_stocktake_posting_completion_items_0035",
        ),
        UniqueConstraint(
            "difference_id",
            name="uq_stocktake_posting_completion_items_difference_0035",
        ),
        UniqueConstraint(
            "inventory_movement_id",
            name="uq_stocktake_posting_completion_items_movement_0035",
        ),
        ForeignKeyConstraint(
            ["completion_id", "task_id"],
            ["stocktake_posting_completions.id", "stocktake_posting_completions.task_id"],
            name="fk_stocktake_posting_completion_items_completion_0035",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["difference_id", "task_id", "source_round_id"],
            ["stocktake_differences.id", "stocktake_differences.task_id", "stocktake_differences.round_id"],
            name="fk_stocktake_posting_completion_items_difference_0035",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["posting_id", "task_id", "source_round_id"],
            ["stocktake_postings.id", "stocktake_postings.task_id", "stocktake_postings.round_id"],
            name="fk_stocktake_posting_completion_items_posting_0035",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "decision IN ('accept_for_posting', 'no_adjustment')",
            name="ck_stocktake_posting_completion_items_decision_0035",
        ),
        CheckConstraint(
            "(decision = 'accept_for_posting' AND posting_id IS NOT NULL AND "
            "inventory_transaction_id IS NOT NULL AND inventory_movement_id IS NOT NULL AND "
            "posting_kind IN ('difference_gain', 'difference_loss', "
            "'difference_transfer', 'difference_status_change') AND quantity > 0) OR "
            "(decision = 'no_adjustment' AND posting_id IS NULL AND "
            "inventory_transaction_id IS NULL AND inventory_movement_id IS NULL AND "
            "posting_kind IS NULL AND quantity = 0)",
            name="ck_stocktake_posting_completion_items_binding_0035",
        ),
        CheckConstraint(
            "length(item_manifest_sha256) = 64",
            name="ck_stocktake_posting_completion_items_hash_0035",
        ),
        Index(
            "ix_stocktake_posting_completion_items_scope_0035",
            "completion_id",
            "scope_id",
        ),
    )

    completion_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    difference_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    source_round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    decision: Mapped[str] = mapped_column(String(32))
    posting_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    inventory_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("inventory_transactions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    inventory_movement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("inventory_movements.id", ondelete="RESTRICT"),
        nullable=True,
    )
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    item_manifest_sha256: Mapped[str] = mapped_column(String(64))


class StocktakePostingCommandOutcome(CreatedAtMixin, Base):
    """Immutable terminal outcome for one durable non-opening post command.

    ``posted`` binds to the existing posting completion.  ``sealed_not_executed``
    is the seal-first tombstone which makes a later replay fail closed.  The
    table intentionally records public coordinates and authorization evidence;
    request bodies, quantities and idempotency secrets never belong here.
    """

    __tablename__ = "stocktake_posting_command_outcomes"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_stocktake_posting_command_outcomes_0065"),
        UniqueConstraint(
            "task_id",
            "request_reference",
            name="uq_stocktake_posting_command_outcomes_request_0065",
        ),
        ForeignKeyConstraint(
            ["completion_id", "task_id"],
            [
                "stocktake_posting_completions.id",
                "stocktake_posting_completions.task_id",
            ],
            name="fk_stocktake_posting_command_outcomes_completion_0065",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "disposition IN ('posted', 'sealed_not_executed')",
            name="ck_stocktake_posting_command_outcomes_disposition_0065",
        ),
        CheckConstraint(
            "expected_task_version >= 0 AND authorization_version > 0",
            name="ck_stocktake_posting_command_outcomes_versions_0065",
        ),
        CheckConstraint(
            "length(request_reference) > 0",
            name="ck_stocktake_posting_command_outcomes_request_reference_0065",
        ),
        CheckConstraint(
            "length(request_sha256) = 64",
            name="ck_stocktake_posting_command_outcomes_request_hash_0065",
        ),
        CheckConstraint(
            "((disposition = 'posted' AND completion_id IS NOT NULL AND "
            "sealed_by_user_id IS NULL AND sealed_by_person_id IS NULL AND "
            "sealed_role_assignment_id IS NULL AND sealed_at IS NULL) OR "
            "(disposition = 'sealed_not_executed' AND completion_id IS NULL AND "
            "sealed_by_user_id IS NOT NULL AND sealed_by_person_id IS NOT NULL AND "
            "sealed_role_assignment_id IS NOT NULL AND sealed_at IS NOT NULL))",
            name="ck_stocktake_posting_command_outcomes_binding_0065",
        ),
        CheckConstraint(
            "sealed_at IS NULL OR sealed_at = created_at",
            name="ck_stocktake_posting_command_outcomes_chronology_0065",
        ),
        Index(
            "ix_stocktake_posting_command_outcomes_request_0065",
            "request_reference",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stocktake_tasks.id", ondelete="RESTRICT")
    )
    request_reference: Mapped[str] = mapped_column(String(160))
    disposition: Mapped[str] = mapped_column(String(24))
    completion_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    expected_task_version: Mapped[int] = mapped_column(BigInteger)
    request_sha256: Mapped[str] = mapped_column(String(64))
    sealed_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    sealed_by_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"), nullable=True
    )
    sealed_role_assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("role_assignments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    sealed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class StocktakeCloseTransitionAck(CreatedAtMixin, Base):
    """Internal commit acknowledgement written only by the task trigger.

    This is a technical atomicity fact, not a business state axis.  The
    deferred completion foreign keys make an immutable header impossible to
    commit unless its exact optimistic task transition also occurred.
    """

    __tablename__ = "stocktake_close_transition_acks"
    __table_args__ = (
        PrimaryKeyConstraint(
            "task_id",
            "target_task_version",
            name="pk_stocktake_close_transition_acks_0038",
        ),
        UniqueConstraint(
            "task_id",
            "target_task_version",
            "reconciliation_completion_id",
            name="uq_stocktake_close_transition_ack_reconciliation_0038",
        ),
        UniqueConstraint(
            "task_id",
            "target_task_version",
            "close_completion_id",
            name="uq_stocktake_close_transition_ack_close_0038",
        ),
        UniqueConstraint(
            "reconciliation_completion_id",
            name="uq_stocktake_close_transition_ack_reconciliation_id_0038",
        ),
        UniqueConstraint(
            "close_completion_id",
            name="uq_stocktake_close_transition_ack_close_id_0038",
        ),
        CheckConstraint(
            "(transition_kind = 'reconciliation' AND "
            "reconciliation_completion_id IS NOT NULL AND "
            "close_completion_id IS NULL) OR "
            "(transition_kind = 'close' AND close_completion_id IS NOT NULL "
            "AND reconciliation_completion_id IS NULL)",
            name="ck_stocktake_close_transition_ack_binding_0038",
        ),
        CheckConstraint(
            "target_task_version > 0 AND created_at = occurred_at",
            name="ck_stocktake_close_transition_ack_chronology_0038",
        ),
        Index(
            "ix_stocktake_close_transition_acks_kind_0038",
            "transition_kind",
            "occurred_at",
        ),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stocktake_tasks.id", ondelete="RESTRICT")
    )
    target_task_version: Mapped[int] = mapped_column(BigInteger)
    transition_kind: Mapped[str] = mapped_column(String(24))
    reconciliation_completion_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    close_completion_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StocktakeCloseReconciliationCompletion(CreatedAtMixin, Base):
    """One immutable, repeatable internal close-readiness reconciliation.

    A later legitimate inventory transaction makes an older completion stale;
    the next command appends another row linked through
    ``previous_reconciliation_id``.  The task remains ``posted`` throughout
    this chain and advances only its optimistic version.
    """

    __tablename__ = "stocktake_close_reconciliation_completions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "id", name="pk_stocktake_close_reconciliation_completions_0038"
        ),
        UniqueConstraint(
            "id",
            "task_id",
            name="uq_stocktake_close_reconciliation_id_task_0038",
        ),
        UniqueConstraint(
            "task_id",
            "reconciliation_no",
            name="uq_stocktake_close_reconciliation_task_no_0038",
        ),
        UniqueConstraint(
            "task_id",
            "reconciled_task_version",
            name="uq_stocktake_close_reconciliation_task_version_0038",
        ),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_close_reconciliation_idempotency_0038",
        ),
        ForeignKeyConstraint(
            ["posting_completion_id", "task_id"],
            [
                "stocktake_posting_completions.id",
                "stocktake_posting_completions.task_id",
            ],
            name="fk_stocktake_close_reconciliation_posting_0038",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["previous_reconciliation_id", "task_id"],
            [
                "stocktake_close_reconciliation_completions.id",
                "stocktake_close_reconciliation_completions.task_id",
            ],
            name="fk_stocktake_close_reconciliation_previous_0038",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_id", "reconciled_task_version", "id"],
            [
                "stocktake_close_transition_acks.task_id",
                "stocktake_close_transition_acks.target_task_version",
                "stocktake_close_transition_acks.reconciliation_completion_id",
            ],
            name="fk_stocktake_close_reconciliation_transition_ack_0038",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "(reconciliation_no = 1 AND previous_reconciliation_id IS NULL) OR "
            "(reconciliation_no > 1 AND previous_reconciliation_id IS NOT NULL)",
            name="ck_stocktake_close_reconciliation_chain_0038",
        ),
        CheckConstraint(
            "expected_task_version >= 0 AND reconciled_task_version = "
            "expected_task_version + 1",
            name="ck_stocktake_close_reconciliation_versions_0038",
        ),
        CheckConstraint(
            "reconciliation_ledger_cursor >= 0 AND scope_count > 0 AND "
            "account_count >= scoped_account_count AND scoped_account_count >= 0 "
            "AND serial_count >= 0 AND transaction_count >= 0 AND "
            "movement_count >= 0 AND book_total_qty >= 0 AND "
            "physical_total_qty >= 0 AND book_total_qty = physical_total_qty",
            name="ck_stocktake_close_reconciliation_totals_0038",
        ),
        CheckConstraint(
            "authorization_version > 0 AND role_code = 'admin' AND "
            "scope_type = 'national' AND scope_id_snapshot = '*'",
            name="ck_stocktake_close_reconciliation_authorization_0038",
        ),
        CheckConstraint(
            "length(posting_manifest_sha256) = 64 AND "
            "length(account_manifest_sha256) = 64 AND "
            "length(serial_manifest_sha256) = 64 AND "
            "length(reconciliation_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64 AND "
            "length(authorization_sha256) = 64",
            name="ck_stocktake_close_reconciliation_hashes_0038",
        ),
        CheckConstraint(
            "created_at = reconciled_at",
            name="ck_stocktake_close_reconciliation_chronology_0038",
        ),
        Index(
            "ix_stocktake_close_reconciliation_task_0038",
            "task_id",
            "reconciliation_no",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    posting_completion_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    previous_reconciliation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    reconciliation_no: Mapped[int] = mapped_column(Integer)
    expected_task_version: Mapped[int] = mapped_column(BigInteger)
    reconciled_task_version: Mapped[int] = mapped_column(BigInteger)
    reconciliation_ledger_cursor: Mapped[int] = mapped_column(BigInteger)
    scope_count: Mapped[int] = mapped_column(Integer)
    account_count: Mapped[int] = mapped_column(Integer)
    scoped_account_count: Mapped[int] = mapped_column(Integer)
    serial_count: Mapped[int] = mapped_column(Integer)
    transaction_count: Mapped[int] = mapped_column(Integer)
    movement_count: Mapped[int] = mapped_column(Integer)
    book_total_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    physical_total_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    posting_manifest_sha256: Mapped[str] = mapped_column(String(64))
    account_manifest_sha256: Mapped[str] = mapped_column(String(64))
    serial_manifest_sha256: Mapped[str] = mapped_column(String(64))
    reconciliation_manifest_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    reconciled_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    reconciled_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    reconciled_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    role_code: Mapped[str] = mapped_column(String(40))
    scope_type: Mapped[str] = mapped_column(String(24))
    scope_id_snapshot: Mapped[str] = mapped_column(String(80))
    authorization_sha256: Mapped[str] = mapped_column(String(64))
    reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StocktakeCloseReconciliationAccount(CreatedAtMixin, Base):
    """Immutable account-level book/physical/ledger reconciliation item."""

    __tablename__ = "stocktake_close_reconciliation_accounts"
    __table_args__ = (
        PrimaryKeyConstraint(
            "completion_id",
            "stock_account_id",
            name="pk_stocktake_close_reconciliation_accounts_0038",
        ),
        ForeignKeyConstraint(
            ["completion_id", "task_id"],
            [
                "stocktake_close_reconciliation_completions.id",
                "stocktake_close_reconciliation_completions.task_id",
            ],
            name="fk_stocktake_close_reconciliation_accounts_completion_0038",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_close_reconciliation_accounts_scope_0038",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["effective_round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_close_reconciliation_accounts_round_0038",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "account_role IN ('scope', 'posting_counterpart')",
            name="ck_stocktake_close_reconciliation_accounts_role_0038",
        ),
        CheckConstraint(
            "(account_role = 'scope' AND scope_id IS NOT NULL AND "
            "effective_round_id IS NOT NULL AND count_ledger_cursor >= 0 AND "
            "book_qty_at_count IS NOT NULL AND physical_qty_at_count IS NOT NULL "
            "AND ledger_delta_after_count IS NOT NULL AND "
            "physical_delta_after_count IS NOT NULL AND "
            "expected_physical_qty IS NOT NULL AND "
            "book_qty_at_count >= 0 AND physical_qty_at_count >= 0 AND "
            "expected_physical_qty >= 0 AND "
            "expected_physical_qty = physical_qty_at_count + physical_delta_after_count "
            "AND ledger_qty = book_qty_at_count + ledger_delta_after_count "
            "AND expected_physical_qty = ledger_qty) OR "
            "(account_role = 'posting_counterpart' AND scope_id IS NULL AND "
            "effective_round_id IS NULL AND count_ledger_cursor IS NULL AND "
            "book_qty_at_count IS NULL AND physical_qty_at_count IS NULL AND "
            "ledger_delta_after_count IS NULL AND "
            "physical_delta_after_count IS NULL AND "
            "expected_physical_qty IS NULL)",
            name="ck_stocktake_close_reconciliation_accounts_physical_0038",
        ),
        CheckConstraint(
            "ledger_qty >= 0 AND balance_qty >= 0 AND ledger_qty = balance_qty "
            "AND last_touch_ledger_cursor >= 0 AND "
            "((balance_ledger_cursor IS NULL AND balance_qty = 0 AND "
            "last_touch_ledger_cursor = 0) OR "
            "(balance_ledger_cursor = last_touch_ledger_cursor AND "
            "balance_ledger_cursor > 0))",
            name="ck_stocktake_close_reconciliation_accounts_ledger_0038",
        ),
        CheckConstraint(
            "length(account_dimension_sha256) = 64 AND "
            "length(item_manifest_sha256) = 64",
            name="ck_stocktake_close_reconciliation_accounts_hashes_0038",
        ),
        Index(
            "ix_stocktake_close_reconciliation_accounts_scope_0038",
            "task_id",
            "scope_id",
        ),
    )

    completion_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    stock_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_accounts.id", ondelete="RESTRICT")
    )
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    effective_round_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    account_role: Mapped[str] = mapped_column(String(24))
    count_ledger_cursor: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    book_qty_at_count: Mapped[Decimal | None] = mapped_column(
        QUANTITY, nullable=True
    )
    physical_qty_at_count: Mapped[Decimal | None] = mapped_column(
        QUANTITY, nullable=True
    )
    ledger_delta_after_count: Mapped[Decimal | None] = mapped_column(
        QUANTITY, nullable=True
    )
    physical_delta_after_count: Mapped[Decimal | None] = mapped_column(
        QUANTITY, nullable=True
    )
    expected_physical_qty: Mapped[Decimal | None] = mapped_column(
        QUANTITY, nullable=True
    )
    ledger_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    balance_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    last_touch_ledger_cursor: Mapped[int] = mapped_column(BigInteger)
    balance_ledger_cursor: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    account_dimension_sha256: Mapped[str] = mapped_column(String(64))
    item_manifest_sha256: Mapped[str] = mapped_column(String(64))


class StocktakeCloseReconciliationSerial(CreatedAtMixin, Base):
    """Immutable SN proof against the latest posted movement projection."""

    __tablename__ = "stocktake_close_reconciliation_serials"
    __table_args__ = (
        PrimaryKeyConstraint(
            "completion_id",
            "serial_id",
            name="pk_stocktake_close_reconciliation_serials_0038",
        ),
        ForeignKeyConstraint(
            ["completion_id", "task_id"],
            [
                "stocktake_close_reconciliation_completions.id",
                "stocktake_close_reconciliation_completions.task_id",
            ],
            name="fk_stocktake_close_reconciliation_serials_completion_0038",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["evidence_scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_close_reconciliation_serials_scope_0038",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["effective_round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_close_reconciliation_serials_round_0038",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(evidence_scope_id IS NULL AND effective_round_id IS NULL AND "
            "count_ledger_cursor IS NULL AND physical_present_at_count IS NULL "
            "AND physical_account_id_at_count IS NULL) OR "
            "(evidence_scope_id IS NOT NULL AND effective_round_id IS NOT NULL "
            "AND count_ledger_cursor >= 0 AND physical_present_at_count IS NOT NULL "
            "AND ((physical_present_at_count AND "
            "physical_account_id_at_count IS NOT NULL) OR "
            "(NOT physical_present_at_count AND "
            "physical_account_id_at_count IS NULL)))",
            name="ck_stocktake_close_reconciliation_serials_physical_0038",
        ),
        CheckConstraint(
            "ledger_last_movement_id = current_position_last_movement_id AND "
            "((expected_current_account_id IS NULL AND "
            "current_position_account_id IS NULL) OR "
            "expected_current_account_id = current_position_account_id)",
            name="ck_stocktake_close_reconciliation_serials_position_0038",
        ),
        CheckConstraint(
            "length(item_manifest_sha256) = 64",
            name="ck_stocktake_close_reconciliation_serials_hash_0038",
        ),
        Index(
            "ix_stocktake_close_reconciliation_serials_scope_0038",
            "task_id",
            "evidence_scope_id",
        ),
    )

    completion_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    serial_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_serials.id", ondelete="RESTRICT")
    )
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    evidence_scope_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    effective_round_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    count_ledger_cursor: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    physical_present_at_count: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    physical_account_id_at_count: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("stock_accounts.id", ondelete="RESTRICT"), nullable=True
    )
    expected_current_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("stock_accounts.id", ondelete="RESTRICT"), nullable=True
    )
    ledger_last_movement_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_movements.id", ondelete="RESTRICT")
    )
    current_position_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("stock_accounts.id", ondelete="RESTRICT"), nullable=True
    )
    current_position_last_movement_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_movements.id", ondelete="RESTRICT")
    )
    item_manifest_sha256: Mapped[str] = mapped_column(String(64))


class StocktakeCloseCompletion(CreatedAtMixin, Base):
    """Immutable independent ``posted -> closed`` completion."""

    __tablename__ = "stocktake_close_completions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_stocktake_close_completions_0038"),
        UniqueConstraint(
            "task_id", name="uq_stocktake_close_completions_task_0038"
        ),
        UniqueConstraint(
            "reconciliation_completion_id",
            name="uq_stocktake_close_completions_reconciliation_0038",
        ),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_close_completions_idempotency_0038",
        ),
        ForeignKeyConstraint(
            ["posting_completion_id", "task_id"],
            [
                "stocktake_posting_completions.id",
                "stocktake_posting_completions.task_id",
            ],
            name="fk_stocktake_close_completions_posting_0038",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["reconciliation_completion_id", "task_id"],
            [
                "stocktake_close_reconciliation_completions.id",
                "stocktake_close_reconciliation_completions.task_id",
            ],
            name="fk_stocktake_close_completions_reconciliation_0038",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_id", "closed_task_version", "id"],
            [
                "stocktake_close_transition_acks.task_id",
                "stocktake_close_transition_acks.target_task_version",
                "stocktake_close_transition_acks.close_completion_id",
            ],
            name="fk_stocktake_close_completion_transition_ack_0038",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "expected_task_version >= 0 AND closed_task_version = "
            "expected_task_version + 1 AND reconciliation_no > 0 AND "
            "reconciliation_ledger_cursor >= 0",
            name="ck_stocktake_close_completions_versions_0038",
        ),
        CheckConstraint(
            "authorization_version > 0 AND role_code = 'admin' AND "
            "scope_type = 'national' AND scope_id_snapshot = '*'",
            name="ck_stocktake_close_completions_authorization_0038",
        ),
        CheckConstraint(
            "length(posting_manifest_sha256) = 64 AND "
            "length(reconciliation_manifest_sha256) = 64 AND "
            "length(close_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64 AND "
            "length(authorization_sha256) = 64",
            name="ck_stocktake_close_completions_hashes_0038",
        ),
        CheckConstraint(
            "created_at = closed_at",
            name="ck_stocktake_close_completions_chronology_0038",
        ),
        Index(
            "ix_stocktake_close_completions_actor_0038",
            "closed_by_user_id",
            "closed_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    posting_completion_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    reconciliation_completion_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    reconciliation_no: Mapped[int] = mapped_column(Integer)
    reconciliation_ledger_cursor: Mapped[int] = mapped_column(BigInteger)
    expected_task_version: Mapped[int] = mapped_column(BigInteger)
    closed_task_version: Mapped[int] = mapped_column(BigInteger)
    posting_manifest_sha256: Mapped[str] = mapped_column(String(64))
    reconciliation_manifest_sha256: Mapped[str] = mapped_column(String(64))
    close_manifest_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    closed_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    closed_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    closed_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    role_code: Mapped[str] = mapped_column(String(40))
    scope_type: Mapped[str] = mapped_column(String(24))
    scope_id_snapshot: Mapped[str] = mapped_column(String(80))
    authorization_sha256: Mapped[str] = mapped_column(String(64))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InventoryOpeningEstablishment(CreatedAtMixin, Base):
    __tablename__ = "inventory_opening_establishments"
    __table_args__ = (
        PrimaryKeyConstraint(
            "id", name="pk_inventory_opening_establishments"
        ),
        UniqueConstraint(
            "owner_org_id",
            "location_id",
            name="uq_inventory_opening_establishments_scope",
        ),
        UniqueConstraint(
            "task_id",
            "scope_id",
            name="uq_inventory_opening_establishments_task_scope",
        ),
        ForeignKeyConstraint(
            ["scope_id", "task_id", "owner_org_id", "location_id"],
            [
                "stocktake_scopes.id",
                "stocktake_scopes.task_id",
                "stocktake_scopes.owner_org_id",
                "stocktake_scopes.location_id",
            ],
            name="fk_inventory_opening_establishments_scope_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_inventory_opening_establishments_round_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["posting_id", "task_id", "round_id"],
            ["stocktake_postings.id", "stocktake_postings.task_id", "stocktake_postings.round_id"],
            name="fk_inventory_opening_establishments_posting",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["regional_review_id", "task_id", "round_id"],
            ["stocktake_reviews.id", "stocktake_reviews.task_id", "stocktake_reviews.round_id"],
            name="fk_inventory_opening_establishments_region_review",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["headquarters_review_id", "task_id", "round_id"],
            ["stocktake_reviews.id", "stocktake_reviews.task_id", "stocktake_reviews.round_id"],
            name="fk_inventory_opening_establishments_hq_review",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "cutoff_ledger_cursor >= 0 AND "
            "established_ledger_cursor >= cutoff_ledger_cursor",
            name="ck_inventory_opening_establishments_cursor",
        ),
        CheckConstraint(
            "length(scope_manifest_sha256) = 64 AND "
            "length(snapshot_manifest_sha256) = 64 AND "
            "length(count_manifest_sha256) = 64 AND "
            "length(control_manifest_sha256) = 64",
            name="ck_inventory_opening_establishments_hashes",
        ),
        CheckConstraint(
            "established_at >= cutoff_at",
            name="ck_inventory_opening_establishments_time_order",
        ),
        Index(
            "ix_inventory_opening_establishments_task", "task_id", "established_at"
        ),
        Index(
            "ix_inventory_opening_establishments_location", "location_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, default=uuid4_value)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    owner_org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_locations.id", ondelete="RESTRICT")
    )
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    posting_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    regional_review_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    headquarters_review_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    cutoff_ledger_cursor: Mapped[int] = mapped_column(BigInteger)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    established_ledger_cursor: Mapped[int] = mapped_column(BigInteger)
    scope_manifest_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_manifest_sha256: Mapped[str] = mapped_column(String(64))
    count_manifest_sha256: Mapped[str] = mapped_column(String(64))
    control_manifest_sha256: Mapped[str] = mapped_column(String(64))
    has_pending_control_difference: Mapped[bool] = mapped_column(
        Boolean, default=False
    )
    established_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    established_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
