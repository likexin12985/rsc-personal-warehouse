from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def uuid4_str() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("person_id", name="uq_users_person_id"),
        CheckConstraint(
            "account_status IN ('pending_identity', 'active', "
            "'restricted_handover', 'suspended', 'disabled')",
            name="ck_users_account_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    # The fields below are the formal V1.0 identity boundary.  The legacy
    # mobile/password/role/province columns are intentionally retained until a
    # separately reviewed compatibility cutover; no automatic person mapping
    # is inferred by the schema migration.
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
        index=True,
    )
    account_status: Mapped[str] = mapped_column(
        String(32),
        default="pending_identity",
        server_default="pending_identity",
        index=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    authorization_version: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )
    mobile: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="technician", index=True)
    province: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    require_password_change: Mapped[bool] = mapped_column(Boolean, default=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_session_user_active", "user_id", "revoked_at", "expires_at"),
        Index(
            "uq_auth_sessions_active_device_family",
            "user_id",
            "client_type",
            "device_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    client_type: Mapped[str] = mapped_column(String(24), index=True)
    device_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    device_name: Mapped[str] = mapped_column(String(160), default="")
    ip_address: Mapped[str] = mapped_column(String(80), default="")
    user_agent: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    revoked_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    revoked_by: Mapped[User | None] = relationship(foreign_keys=[revoked_by_id])


class WechatIdentity(TimestampMixin, Base):
    __tablename__ = "wechat_identities"
    __table_args__ = (
        UniqueConstraint("app_id", "openid", name="uq_wechat_app_openid"),
        UniqueConstraint("app_id", "user_id", name="uq_wechat_app_user"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    app_id: Mapped[str] = mapped_column(String(80), index=True)
    openid: Mapped[str] = mapped_column(String(160), index=True)
    unionid: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    user: Mapped[User] = relationship()


class SmsLoginChallenge(Base):
    __tablename__ = "sms_login_challenges"
    __table_args__ = (
        Index("ix_sms_challenge_mobile_created", "mobile", "created_at"),
        Index("ix_sms_challenge_ip_created", "requested_ip", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    mobile: Mapped[str] = mapped_column(String(20), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_biz_id: Mapped[str] = mapped_column(String(160), default="")
    requested_ip: Mapped[str] = mapped_column(String(80), default="", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    user: Mapped[User | None] = relationship()


class Warehouse(TimestampMixin, Base):
    __tablename__ = "warehouses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    code: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    province: Mapped[str] = mapped_column(String(40), index=True)
    city: Mapped[str] = mapped_column(String(40), default="")
    warehouse_type: Mapped[str] = mapped_column(String(32), default="service_backpack")
    condition_scope: Mapped[str] = mapped_column(String(20), default="good")
    warehouse_level: Mapped[str] = mapped_column(String(20), default="network")
    ownership_type: Mapped[str] = mapped_column(String(24), default="regular")
    position_scope: Mapped[str] = mapped_column(String(24), default="unrestricted")
    parent_warehouse_id: Mapped[str | None] = mapped_column(
        ForeignKey("warehouses.id"), nullable=True
    )
    manager_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    manager: Mapped[User | None] = relationship()


class LegacyMaterial(TimestampMixin, Base):
    __tablename__ = "legacy_v09_materials"
    __table_args__ = (
        Index("ix_materials_code", "code", unique=True),
        Index("ix_materials_name", "name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(180))
    specification: Mapped[str] = mapped_column(String(240), default="")
    category: Mapped[str] = mapped_column(String(80), default="其他")
    aliases: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[str] = mapped_column(String(20), default="个")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# The v0.9 routers remain development-only compatibility surfaces.  Preserve
# their existing import name while keeping the formal V1.0 ``materials`` table
# owned by ``inventory_models.FormalMaterial``.
Material = LegacyMaterial


class InventoryBalance(TimestampMixin, Base):
    __tablename__ = "inventory_balances"
    __table_args__ = (
        UniqueConstraint(
            "warehouse_id",
            "holder_key",
            "material_id",
            "condition",
            name="uq_inventory_owner_material_condition",
        ),
        CheckConstraint("quantity_on_hand >= 0", name="ck_inventory_on_hand_nonnegative"),
        CheckConstraint("quantity_occupied >= 0", name="ck_inventory_occupied_nonnegative"),
        CheckConstraint("quantity_in_transit >= 0", name="ck_inventory_transit_nonnegative"),
        Index("ix_inventory_warehouse_material", "warehouse_id", "material_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"), index=True)
    holder_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    holder_key: Mapped[str] = mapped_column(String(36), default="WAREHOUSE")
    material_id: Mapped[str] = mapped_column(
        ForeignKey("legacy_v09_materials.id"), index=True
    )
    condition: Mapped[str] = mapped_column(String(20), default="good", index=True)
    quantity_on_hand: Mapped[int] = mapped_column(Integer, default=0)
    quantity_occupied: Mapped[int] = mapped_column(Integer, default=0)
    quantity_in_transit: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)

    warehouse: Mapped[Warehouse] = relationship()
    holder: Mapped[User | None] = relationship()
    material: Mapped[Material] = relationship()


class Transfer(TimestampMixin, Base):
    __tablename__ = "transfers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    transfer_type: Mapped[str] = mapped_column(String(30), default="internal")
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    source_warehouse_id: Mapped[str | None] = mapped_column(
        ForeignKey("warehouses.id"), nullable=True
    )
    source_holder_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    target_warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    recipient_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    requester_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    approved_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    work_order_number: Mapped[str] = mapped_column(String(80), default="", index=True)
    logistics_company: Mapped[str] = mapped_column(String(80), default="")
    tracking_number: Mapped[str] = mapped_column(String(100), default="", index=True)
    external_reference: Mapped[str] = mapped_column(String(100), default="", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source_warehouse: Mapped[Warehouse | None] = relationship(
        foreign_keys=[source_warehouse_id]
    )
    target_warehouse: Mapped[Warehouse] = relationship(foreign_keys=[target_warehouse_id])
    source_holder: Mapped[User | None] = relationship(
        foreign_keys=[source_holder_user_id]
    )
    recipient: Mapped[User | None] = relationship(foreign_keys=[recipient_user_id])
    requester: Mapped[User | None] = relationship(foreign_keys=[requester_user_id])
    approved_by: Mapped[User | None] = relationship(foreign_keys=[approved_by_id])
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    items: Mapped[list[TransferItem]] = relationship(
        back_populates="transfer", cascade="all, delete-orphan", lazy="selectin"
    )


class TransferItem(Base):
    __tablename__ = "transfer_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_transfer_item_positive"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    transfer_id: Mapped[str] = mapped_column(ForeignKey("transfers.id", ondelete="CASCADE"))
    material_id: Mapped[str] = mapped_column(ForeignKey("legacy_v09_materials.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    condition: Mapped[str] = mapped_column(String(20), default="good")
    remark: Mapped[str] = mapped_column(String(240), default="")

    transfer: Mapped[Transfer] = relationship(back_populates="items")
    material: Mapped[Material] = relationship(lazy="joined")


class WorkOrderMaterial(TimestampMixin, Base):
    __tablename__ = "work_order_materials"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_work_order_material_positive"),
        Index("ix_work_order_material_order_status", "work_order_number", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    work_order_number: Mapped[str] = mapped_column(String(80), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    material_id: Mapped[str] = mapped_column(
        ForeignKey("legacy_v09_materials.id"), index=True
    )
    condition: Mapped[str] = mapped_column(String(20), default="good")
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="occupied", index=True)
    recovery_condition: Mapped[str] = mapped_column(String(20), default="")
    note: Mapped[str] = mapped_column(String(500), default="")
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    warehouse: Mapped[Warehouse] = relationship()
    user: Mapped[User] = relationship(foreign_keys=[user_id])
    material: Mapped[Material] = relationship()
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])


class StocktakeTask(TimestampMixin, Base):
    # Quarantined v0.9 prototype.  Revision 0010 renames the original table in
    # place; these classes remain only for non-production compatibility routes.
    __tablename__ = "legacy_v09_stocktake_tasks"
    __table_args__ = (
        # Table renames preserve the original physical index names on both
        # PostgreSQL and SQLite.  Keep metadata identical to that rename-only
        # migration instead of generating legacy-table-derived names.
        Index("ix_stocktake_tasks_number", "number", unique=True),
        Index("ix_stocktake_tasks_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    number: Mapped[str] = mapped_column(String(40))
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    assignee_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    warehouse: Mapped[Warehouse] = relationship()
    assignee: Mapped[User] = relationship(foreign_keys=[assignee_id])
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    items: Mapped[list[StocktakeItem]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="selectin"
    )


class StocktakeItem(Base):
    __tablename__ = "legacy_v09_stocktake_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("legacy_v09_stocktake_tasks.id", ondelete="CASCADE")
    )
    material_id: Mapped[str] = mapped_column(ForeignKey("legacy_v09_materials.id"))
    condition: Mapped[str] = mapped_column(String(20), default="good")
    expected_quantity: Mapped[int] = mapped_column(Integer, default=0)
    counted_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remark: Mapped[str] = mapped_column(String(240), default="")

    task: Mapped[StocktakeTask] = relationship(back_populates="items")
    material: Mapped[Material] = relationship(lazy="joined")


class MediaAttachment(TimestampMixin, Base):
    __tablename__ = "media_attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500), unique=True)
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    uploaded_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"))

    uploaded_by: Mapped[User] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[str] = mapped_column(String(80), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    ip_address: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    actor: Mapped[User | None] = relationship()


class ExternalSyncBatch(Base):
    __tablename__ = "external_sync_batches"
    __table_args__ = (
        UniqueConstraint(
            "source_instance",
            "batch_id",
            name="uq_external_sync_source_batch",
        ),
        Index("ix_external_sync_batch_received", "received_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    source_system: Mapped[str] = mapped_column(String(40), index=True)
    source_instance: Mapped[str] = mapped_column(String(128), index=True)
    batch_id: Mapped[str] = mapped_column(String(128), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    record_count: Mapped[int] = mapped_column(Integer)
    body_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="accepted", index=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class ExternalSyncRecord(TimestampMixin, Base):
    __tablename__ = "external_sync_records"
    __table_args__ = (
        UniqueConstraint(
            "source_instance",
            "entity_type",
            "business_key",
            name="uq_external_sync_record_business_key",
        ),
        Index(
            "ix_external_sync_record_source_entity",
            "source_instance",
            "entity_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    source_system: Mapped[str] = mapped_column(String(40), index=True)
    source_instance: Mapped[str] = mapped_column(String(128), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    business_key: Mapped[str] = mapped_column(String(200))
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload_json: Mapped[str] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(String(64), index=True)
    last_batch_id: Mapped[str] = mapped_column(
        ForeignKey("external_sync_batches.id"), index=True
    )

    last_batch: Mapped[ExternalSyncBatch] = relationship()


class ExternalSyncSnapshot(Base):
    __tablename__ = "external_sync_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "source_instance",
            "snapshot_id",
            name="uq_external_sync_snapshot_source_id",
        ),
        Index(
            "ix_external_sync_snapshot_scope_status",
            "source_instance",
            "scope_key",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    source_system: Mapped[str] = mapped_column(String(40), index=True)
    source_instance: Mapped[str] = mapped_column(String(128), index=True)
    snapshot_id: Mapped[str] = mapped_column(String(64), index=True)
    scope_key: Mapped[str] = mapped_column(String(160), index=True)
    sync_mode: Mapped[str] = mapped_column(String(20), index=True)
    company_id: Mapped[str] = mapped_column(String(80), index=True)
    org_code: Mapped[str] = mapped_column(String(80), index=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(24), default="receiving", index=True)
    manifest_json: Mapped[str] = mapped_column(Text, default="")
    manifest_sha256: Mapped[str] = mapped_column(String(64), default="")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class ExternalSyncSnapshotBatch(Base):
    __tablename__ = "external_sync_snapshot_batches"
    __table_args__ = (
        UniqueConstraint(
            "source_instance",
            "batch_id",
            name="uq_external_sync_snapshot_batch_source_id",
        ),
        UniqueConstraint(
            "snapshot_ref_id",
            "entity_type",
            "sequence",
            name="uq_external_sync_snapshot_batch_sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    snapshot_ref_id: Mapped[str] = mapped_column(
        ForeignKey("external_sync_snapshots.id"), index=True
    )
    source_instance: Mapped[str] = mapped_column(String(128), index=True)
    batch_id: Mapped[str] = mapped_column(String(128), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    total_sequences: Mapped[int] = mapped_column(Integer)
    record_count: Mapped[int] = mapped_column(Integer)
    body_sha256: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    snapshot: Mapped[ExternalSyncSnapshot] = relationship()


class ExternalSyncSnapshotRecord(Base):
    __tablename__ = "external_sync_snapshot_records"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_ref_id",
            "entity_type",
            "business_key",
            name="uq_external_sync_snapshot_record_key",
        ),
        Index(
            "ix_external_sync_snapshot_record_entity",
            "snapshot_ref_id",
            "entity_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    snapshot_ref_id: Mapped[str] = mapped_column(
        ForeignKey("external_sync_snapshots.id"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    business_key: Mapped[str] = mapped_column(String(200))
    operation: Mapped[str] = mapped_column(String(16))
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload_json: Mapped[str] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(String(64), index=True)

    snapshot: Mapped[ExternalSyncSnapshot] = relationship()


class ExternalSyncCurrentRecord(TimestampMixin, Base):
    __tablename__ = "external_sync_current_records"
    __table_args__ = (
        UniqueConstraint(
            "source_instance",
            "scope_key",
            "entity_type",
            "business_key",
            name="uq_external_sync_current_record_key",
        ),
        Index(
            "ix_external_sync_current_scope_entity",
            "source_instance",
            "scope_key",
            "entity_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    source_system: Mapped[str] = mapped_column(String(40), index=True)
    source_instance: Mapped[str] = mapped_column(String(128), index=True)
    scope_key: Mapped[str] = mapped_column(String(160), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    business_key: Mapped[str] = mapped_column(String(200))
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload_json: Mapped[str] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(String(64), index=True)
    last_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("external_sync_snapshots.id"), index=True
    )

    last_snapshot: Mapped[ExternalSyncSnapshot] = relationship()


class OamPersonnelBinding(TimestampMixin, Base):
    __tablename__ = "oam_personnel_bindings"
    __table_args__ = (
        UniqueConstraint(
            "source_instance",
            "oam_account_id",
            name="uq_oam_personnel_source_account",
        ),
        UniqueConstraint("user_id", name="uq_oam_personnel_user"),
        Index("ix_oam_personnel_mobile", "mobile"),
        Index(
            "ix_oam_personnel_login_state",
            "source_present",
            "source_active",
            "login_enabled",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    source_instance: Mapped[str] = mapped_column(String(128), index=True)
    oam_account_id: Mapped[str] = mapped_column(String(100), index=True)
    oam_employee_id: Mapped[str] = mapped_column(String(100), default="")
    account: Mapped[str] = mapped_column(String(100), default="")
    job_no: Mapped[str] = mapped_column(String(100), default="")
    name: Mapped[str] = mapped_column(String(80), index=True)
    mobile: Mapped[str] = mapped_column(String(20), default="")
    oam_status: Mapped[str] = mapped_column(String(24), default="")
    source_present: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    source_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    login_eligible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    eligibility_reason: Mapped[str] = mapped_column(String(160), default="")
    login_enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    last_seen_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("external_sync_snapshots.id"), index=True
    )

    user: Mapped[User | None] = relationship()
    last_seen_snapshot: Mapped[ExternalSyncSnapshot] = relationship()


# Register the additive V1.0 stage-one tables on the shared metadata.  The
# module intentionally has no dependency on these classes at runtime; this
# import exists only so Alembic autogenerate and test metadata see the complete
# schema while preserving every v0.9 model above unchanged.
from . import foundation_models as _foundation_models  # noqa: E402,F401
from . import inventory_models as _inventory_models  # noqa: E402,F401
from . import stocktake_models as _stocktake_models  # noqa: E402,F401
from . import demand_models as _demand_models  # noqa: E402,F401
