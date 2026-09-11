import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(ORMModel):
    id: str
    mobile: str
    name: str
    role: str
    province: str | None
    require_password_change: bool


class TokenSessionOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    session_id: str
    user: UserOut


class FormalSelfOut(BaseModel):
    """Minimum authenticated-person response for the production clients."""

    person_id: UUID
    name: str
    employee_no: str
    organization_code: str
    organization_name: str
    account_status: str
    employment_status: str
    access_mode: str
    authorization_version: int
    role_codes: list[str]


class FormalTokenSessionOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    session_id: str
    user: FormalSelfOut


class FormalAuthSessionOut(BaseModel):
    session_id: str
    person_id: UUID
    person_name: str
    employee_no: str
    organization_code: str
    organization_name: str
    account_status: str
    client_type: str
    device_name: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    is_current: bool = False


class FormalSessionRevokeOut(BaseModel):
    session_id: str
    status: Literal["revoked"] = "revoked"
    revoked_at: datetime
    replayed: bool
    audit_event_id: UUID
    state_transition_event_id: UUID


class UserCreateIn(BaseModel):
    mobile: str = Field(min_length=6, max_length=20)
    name: str = Field(min_length=1, max_length=80)
    role: str
    province: str | None = Field(default=None, max_length=40)
    temporary_password: str = Field(min_length=10, max_length=128)


class OamPersonnelEnableIn(BaseModel):
    role: Literal[
        "admin",
        "provincial_manager",
        "technician",
    ]
    province: str | None = Field(default=None, max_length=40)


class LoginIn(BaseModel):
    mobile: str = Field(min_length=6, max_length=20)
    password: str = Field(min_length=8, max_length=128)


class LoginOptionsOut(BaseModel):
    sms_enabled: bool
    password_enabled: bool
    wechat_enabled: bool
    sms_valid_seconds: int
    sms_interval_seconds: int
    session_ttl_days: int


class SmsCodeRequestIn(BaseModel):
    mobile: str = Field(pattern=r"^1[3-9]\d{9}$")


class SmsCodeLoginIn(BaseModel):
    mobile: str = Field(pattern=r"^1[3-9]\d{9}$")
    code: str = Field(pattern=r"^\d{4,8}$")


class MiniProgramClientIn(BaseModel):
    device_id: str = Field(min_length=8, max_length=128)
    device_name: str = Field(default="微信小程序", max_length=160)


class MiniProgramPasswordLoginIn(LoginIn, MiniProgramClientIn):
    pass


class MiniProgramSmsLoginIn(SmsCodeLoginIn, MiniProgramClientIn):
    pass


class MiniProgramWechatLoginIn(MiniProgramClientIn):
    login_code: str = Field(min_length=4, max_length=512)
    phone_code: str | None = Field(default=None, min_length=4, max_length=512)


class SessionRefreshIn(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)
    device_id: str = Field(min_length=8, max_length=128)


class SessionLogoutIn(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class AuthSessionOut(BaseModel):
    id: str
    user_id: str
    user_name: str
    mobile: str
    client_type: str
    device_name: str
    ip_address: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    is_current: bool = False


class AccessScopeOut(BaseModel):
    assignment_id: str
    role_code: str
    scope_type: str
    scope_id: str
    valid_from: datetime
    valid_to: datetime | None


class EffectivePermissionOut(BaseModel):
    resource: str
    action: str
    field_code: str


class AccessContextOut(BaseModel):
    person_id: str
    account_status: str
    employment_status: str
    authorization_version: int
    access_mode: str
    role_codes: list[str]
    assignments: list[AccessScopeOut]
    permissions: list[EffectivePermissionOut]


class ProvincialManagerCandidateOut(BaseModel):
    person_id: UUID
    person_name: str
    employee_no: str
    organization_id: UUID
    organization_code: str
    organization_name: str
    authorization_version: int


class ProvincialRegionOptionOut(BaseModel):
    organization_id: UUID
    organization_code: str
    organization_name: str
    province_code: str | None


class ProvincialManagerAssignmentOut(BaseModel):
    assignment_id: UUID
    person_id: UUID
    person_name: str
    employee_no: str
    organization_id: UUID
    organization_code: str
    organization_name: str
    valid_from: datetime
    valid_to: datetime | None
    status: str
    authorization_version: int


class ProvincialManagerGrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    organization_id: UUID
    expected_authorization_version: int = Field(ge=1)
    valid_to: AwareDatetime | None = None
    reason: str = Field(min_length=1, max_length=2000)


class ProvincialManagerRevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_authorization_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class ProvincialManagerMutationOut(BaseModel):
    assignment_id: UUID
    person_id: UUID | None
    organization_id: UUID
    role_code: Literal["provincial_manager"]
    scope_type: Literal["organization"]
    status: Literal["active", "revoked"]
    valid_from: datetime
    valid_to: datetime | None
    authorization_version: int
    audit_event_id: UUID
    state_transition_event_id: UUID
    replayed: bool


class SmsCodeRequestOut(BaseModel):
    ok: bool
    message: str
    retry_after: int
    expires_in: int


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


class MaterialOut(ORMModel):
    id: str
    code: str
    name: str
    specification: str
    category: str
    aliases: str
    unit: str


class MaterialCreateIn(BaseModel):
    code: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=1, max_length=180)
    specification: str = Field(default="", max_length=240)
    category: str = Field(default="其他", max_length=80)
    aliases: str = Field(default="", max_length=1000)
    unit: str = Field(default="个", min_length=1, max_length=20)


class WarehouseOut(ORMModel):
    id: str
    code: str
    name: str
    province: str
    city: str
    warehouse_type: str
    condition_scope: str
    warehouse_level: str
    ownership_type: str
    position_scope: str
    parent_warehouse_id: str | None


class WarehouseCreateIn(BaseModel):
    code: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=2, max_length=160)
    province: str = Field(min_length=2, max_length=40)
    city: str = Field(default="", max_length=40)
    warehouse_type: str = Field(default="service_backpack", max_length=32)
    condition_scope: str = Field(default="good", max_length=20)
    warehouse_level: str = Field(default="network", max_length=20)
    ownership_type: str = Field(default="regular", max_length=24)
    position_scope: str = Field(default="unrestricted", max_length=24)
    parent_warehouse_id: str | None = None


class TransferItemIn(BaseModel):
    material_id: str
    quantity: int = Field(gt=0, le=100000)
    condition: str = "good"
    remark: str = Field(default="", max_length=240)


class TransferCreateIn(BaseModel):
    transfer_type: str = "internal"
    source_warehouse_id: str | None = None
    source_holder_user_id: str | None = None
    target_warehouse_id: str
    recipient_user_id: str | None = None
    requester_user_id: str | None = None
    work_order_number: str = Field(default="", max_length=80)
    external_reference: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=2000)
    items: list[TransferItemIn] = Field(min_length=1, max_length=100)


class DispatchIn(BaseModel):
    logistics_company: str = Field(default="", max_length=80)
    tracking_number: str = Field(default="", max_length=100)


class TransferApproveIn(BaseModel):
    source_warehouse_id: str


class TransferRejectIn(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


class WorkOrderMaterialCreateIn(BaseModel):
    work_order_number: str = Field(min_length=3, max_length=80)
    warehouse_id: str
    user_id: str | None = None
    material_id: str
    condition: str = "good"
    quantity: int = Field(gt=0, le=100000)
    note: str = Field(default="", max_length=500)


class WorkOrderMaterialBatchItemIn(BaseModel):
    material_id: str
    condition: str = "good"
    quantity: int = Field(gt=0, le=100000)


class WorkOrderMaterialBatchCreateIn(BaseModel):
    work_order_number: str = Field(min_length=3, max_length=80)
    warehouse_id: str
    user_id: str | None = None
    note: str = Field(default="", max_length=500)
    items: list[WorkOrderMaterialBatchItemIn] = Field(min_length=1, max_length=100)


class WorkOrderMaterialRecoverIn(BaseModel):
    recovery_condition: str
    note: str = Field(default="", max_length=500)


class EdgeSyncRecordIn(BaseModel):
    business_key: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[^\r\n]+$",
    )
    source_updated_at: datetime | None = None
    data: dict[str, Any]

    @model_validator(mode="after")
    def validate_payload_size(self):
        encoded = json.dumps(
            self.data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 64 * 1024:
            raise ValueError("单条同步记录不得超过64KB")
        return self


class EdgeSyncBatchIn(BaseModel):
    source_system: Literal["starcharge_oam"] = "starcharge_oam"
    entity_type: str = Field(
        min_length=1,
        max_length=40,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    snapshot_at: datetime
    records: list[EdgeSyncRecordIn] = Field(max_length=1000)

    @model_validator(mode="after")
    def validate_business_keys(self):
        keys = [record.business_key for record in self.records]
        if len(keys) != len(set(keys)):
            raise ValueError("同一批次不得包含重复业务键")
        return self


class EdgeSyncDeltaRecordIn(EdgeSyncRecordIn):
    operation: Literal["upsert", "delete"] = "upsert"

    @model_validator(mode="after")
    def validate_operation_payload(self):
        if self.operation == "delete" and self.data:
            raise ValueError("删除记录不得携带数据")
        if self.operation == "upsert" and not self.data:
            raise ValueError("写入记录必须携带数据")
        return self


class EdgeSyncSnapshotBatchIn(BaseModel):
    source_system: Literal["starcharge_oam"] = "starcharge_oam"
    snapshot_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    scope_key: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    sync_mode: Literal["full", "incremental"]
    company_id: str = Field(min_length=1, max_length=80, pattern=r"^[^\r\n]+$")
    org_code: str = Field(min_length=1, max_length=80, pattern=r"^[^\r\n]+$")
    entity_type: Literal[
        "warehouse",
        "inventory",
        "employee",
        "material_application",
        "material_application_line",
        "work_order",
        "work_order_detail",
        "work_order_relation",
        "oam_receipt",
    ]
    snapshot_at: datetime
    sequence: int = Field(ge=1, le=10000)
    total_sequences: int = Field(ge=1, le=10000)
    records: list[EdgeSyncDeltaRecordIn] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_snapshot_batch(self):
        if self.sequence > self.total_sequences:
            raise ValueError("批次序号不得超过总批次数")
        keys = [record.business_key for record in self.records]
        if len(keys) != len(set(keys)):
            raise ValueError("同一批次不得包含重复业务键")
        return self


class EdgeSyncEntityManifestIn(BaseModel):
    entity_type: Literal[
        "warehouse",
        "inventory",
        "employee",
        "material_application",
        "material_application_line",
        "work_order",
        "work_order_detail",
        "work_order_relation",
        "oam_receipt",
    ]
    final_record_count: int = Field(ge=0)
    final_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    delta_record_count: int = Field(ge=0)
    delta_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    batch_count: int = Field(ge=0, le=10000)


class EdgeSyncSnapshotCompleteIn(BaseModel):
    source_system: Literal["starcharge_oam"] = "starcharge_oam"
    snapshot_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    scope_key: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    sync_mode: Literal["full", "incremental"]
    company_id: str = Field(min_length=1, max_length=80, pattern=r"^[^\r\n]+$")
    org_code: str = Field(min_length=1, max_length=80, pattern=r"^[^\r\n]+$")
    snapshot_at: datetime
    entities: list[EdgeSyncEntityManifestIn] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_manifest_entities(self):
        entity_types = [entity.entity_type for entity in self.entities]
        if len(entity_types) != len(set(entity_types)):
            raise ValueError("完成清单不得包含重复实体类型")
        return self


class StocktakeCreateIn(BaseModel):
    warehouse_id: str
    assignee_id: str
    deadline: datetime | None = None
    note: str = Field(default="", max_length=2000)


class StocktakeCountIn(BaseModel):
    counted_quantity: int = Field(ge=0, le=1000000)
    remark: str = Field(default="", max_length=240)


class InventoryAdjustIn(BaseModel):
    warehouse_id: str
    material_id: str
    condition: str = "good"
    delta: int = Field(ge=-100000, le=100000)
    reason: str = Field(min_length=2, max_length=240)
