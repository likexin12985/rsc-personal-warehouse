"""Add the formal V1 material-request and three-stage approval domain.

Revision ID: 20260831_0029
Revises: 20260831_0028
Create Date: 2026-08-31

This revision creates a clean production boundary beside, and never promotes,
the quarantined v0.9 ``transfers`` prototype.  It stops at approved demand,
confirmed substitution and shortage supply planning.  Allocation, reservation,
outbound, shipment, receipt and inventory posting remain independent domains.

Only fixed permission definitions and one versioned three-stage route are
seeded.  No person, role assignment, request, approval, OAM work order,
substitution or supply task is inferred.  In particular, an absent or
ambiguous provincial-manager assignment remains a submit-time fail-closed
condition for the application service.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Union
import uuid

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260831_0029"
down_revision: Union[str, Sequence[str], None] = "20260831_0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
QUANTITY = sa.Numeric(18, 3)
RATIO = sa.Numeric(18, 6)

ADMIN_ROLE_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")
PROVINCIAL_MANAGER_ROLE_ID = uuid.UUID(
    "10000000-0000-4000-8000-000000000002"
)
TECHNICIAN_ROLE_ID = uuid.UUID("10000000-0000-4000-8000-000000000003")
STAR_APPROVER_ROLE_ID = uuid.UUID("10000000-0000-4000-8000-000000000004")

PERMISSION_ROWS = (
    (uuid.UUID("20000000-0000-4000-8000-000000000040"), "material_request", "read", "", "Read material requests within the effective scope"),
    (uuid.UUID("20000000-0000-4000-8000-000000000041"), "material_request", "create", "", "Create a material-request draft for self"),
    (uuid.UUID("20000000-0000-4000-8000-000000000042"), "material_request", "update_draft", "", "Update an owned material-request draft"),
    (uuid.UUID("20000000-0000-4000-8000-000000000043"), "material_request", "submit", "", "Submit an owned material request"),
    (uuid.UUID("20000000-0000-4000-8000-000000000044"), "material_request", "withdraw", "", "Withdraw an owned material request at an allowed point"),
    (uuid.UUID("20000000-0000-4000-8000-000000000045"), "material_request", "cancel", "", "Request or complete an allowed material-request cancellation"),
    (uuid.UUID("20000000-0000-4000-8000-000000000046"), "material_request", "approve_region", "approval_decision", "Decide the regional first approval step"),
    (uuid.UUID("20000000-0000-4000-8000-000000000047"), "material_request", "approve_headquarters", "approval_decision", "Decide the NIO-headquarters second approval step"),
    (uuid.UUID("20000000-0000-4000-8000-000000000048"), "material_request", "register_external", "approval_evidence", "Register external Star-headquarters approval evidence"),
    (uuid.UUID("20000000-0000-4000-8000-000000000049"), "material_request", "verify_external", "approval_evidence", "Verify external approval evidence as a second administrator"),
    (uuid.UUID("20000000-0000-4000-8000-000000000050"), "material_request", "propose_substitution", "substitution", "Propose a governed substitute material"),
    (uuid.UUID("20000000-0000-4000-8000-000000000051"), "material_request", "confirm_substitution", "substitution", "Confirm or reject a proposed substitution as requester"),
    (uuid.UUID("20000000-0000-4000-8000-000000000052"), "supply_task", "read", "", "Read shortage supply-planning tasks within scope"),
    (uuid.UUID("20000000-0000-4000-8000-000000000053"), "supply_task", "manage", "", "Create and update bounded shortage supply-planning tasks"),
    (uuid.UUID("20000000-0000-4000-8000-000000000054"), "material_request", "read_star_approval", "approval_payload", "Read only a document-scoped Star approval payload"),
    (uuid.UUID("20000000-0000-4000-8000-000000000055"), "material_request", "approve_star", "approval_decision", "Decide only a document-scoped Star approval step"),
)

# IDs deliberately begin at the range reserved by the parent 0028 review.
ROLE_PERMISSION_ROWS = (
    (uuid.UUID("21000000-0000-4000-8000-000000000080"), TECHNICIAN_ROLE_ID, PERMISSION_ROWS[0][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000081"), TECHNICIAN_ROLE_ID, PERMISSION_ROWS[1][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000082"), TECHNICIAN_ROLE_ID, PERMISSION_ROWS[2][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000083"), TECHNICIAN_ROLE_ID, PERMISSION_ROWS[3][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000084"), TECHNICIAN_ROLE_ID, PERMISSION_ROWS[4][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000085"), TECHNICIAN_ROLE_ID, PERMISSION_ROWS[5][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000086"), TECHNICIAN_ROLE_ID, PERMISSION_ROWS[11][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000087"), PROVINCIAL_MANAGER_ROLE_ID, PERMISSION_ROWS[0][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000088"), PROVINCIAL_MANAGER_ROLE_ID, PERMISSION_ROWS[6][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000089"), PROVINCIAL_MANAGER_ROLE_ID, PERMISSION_ROWS[10][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000090"), PROVINCIAL_MANAGER_ROLE_ID, PERMISSION_ROWS[12][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000091"), ADMIN_ROLE_ID, PERMISSION_ROWS[0][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000092"), ADMIN_ROLE_ID, PERMISSION_ROWS[7][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000093"), ADMIN_ROLE_ID, PERMISSION_ROWS[8][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000094"), ADMIN_ROLE_ID, PERMISSION_ROWS[9][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000095"), ADMIN_ROLE_ID, PERMISSION_ROWS[10][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000096"), ADMIN_ROLE_ID, PERMISSION_ROWS[12][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000097"), ADMIN_ROLE_ID, PERMISSION_ROWS[13][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000098"), STAR_APPROVER_ROLE_ID, PERMISSION_ROWS[14][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000099"), STAR_APPROVER_ROLE_ID, PERMISSION_ROWS[15][0]),
)

ROUTE_VERSION_ID = uuid.UUID("22000000-0000-4000-8000-000000000001")
ROUTE_STEP_ROWS = (
    (uuid.UUID("22000000-0000-4000-8000-000000000011"), 1, "provincial_manager", "internal", "organization"),
    (uuid.UUID("22000000-0000-4000-8000-000000000012"), 2, "admin", "internal", "national"),
    (uuid.UUID("22000000-0000-4000-8000-000000000013"), 3, "star_headquarters_approver", "external_registration", "document"),
)

FACT_TABLES = (
    "approval_step_candidates",
    "material_request_commands",
    "approval_external_registration_lines",
    "approval_step_line_decisions",
    "approval_actions",
)
BUSINESS_TABLES = (
    "supply_tasks",
    "substitution_decisions",
    "approval_actions",
    "approval_step_line_decisions",
    "approval_external_registration_lines",
    "approval_external_registrations",
    "material_request_commands",
    "approval_step_candidates",
    "approval_steps",
    "approval_instances",
    "material_request_files",
    "material_request_lines",
    "material_request_revisions",
    "material_requests",
    "material_substitutions",
    "oam_work_orders",
)

GUARD_ERROR = "formal material-request invariant violated"
DOWNGRADE_BLOCKER = "cannot downgrade 0029 while material-request facts exist"
PG_APPEND_ONLY_FUNCTION = "rsc_guard_material_request_fact_immutable_0029"
PG_REQUEST_LINE_FUNCTION = "rsc_guard_material_request_original_line_0029"
PG_REQUEST_FUNCTION = "rsc_guard_material_request_identity_0029"
PG_REVISION_FUNCTION = "rsc_guard_material_request_revision_0029"
PG_INSTANCE_FUNCTION = "rsc_guard_material_request_approval_instance_0029"
PG_DECISION_FUNCTION = "rsc_guard_material_request_decision_quantity_0029"
PG_EXTERNAL_LINE_FUNCTION = "rsc_guard_external_registration_quantity_0029"
PG_EXTERNAL_REGISTRATION_FUNCTION = "rsc_guard_external_registration_core_0029"
PG_REQUEST_FILE_FUNCTION = "rsc_guard_material_request_file_0029"
PG_DELEGATION_FUNCTION = "rsc_guard_approval_delegation_0029"
PG_SUBSTITUTION_FUNCTION = "rsc_guard_substitution_decision_0029"
PG_SUPPLY_TASK_FUNCTION = "rsc_guard_supply_task_quantity_0029"

AUDIT_EVENT_TABLE = "audit_events"
AUDIT_HEAD_TABLE = "audit_chain_heads"
AUDIT_CHECK = "ck_audit_events_stream_key_0017"
MATERIAL_REQUEST_AUDIT_HEAD_ID = uuid.UUID(
    "30000000-0000-4000-8000-000000000004"
)
EXISTING_AUDIT_HEADS = {
    "authorization": uuid.UUID("30000000-0000-4000-8000-000000000001"),
    "authentication": uuid.UUID("30000000-0000-4000-8000-000000000002"),
    "inventory": uuid.UUID("30000000-0000-4000-8000-000000000003"),
}
AUDIT_UPGRADE_BLOCKER = (
    "0029 audit upgrade failed: existing fixed streams are not canonical"
)
AUDIT_DOWNGRADE_BLOCKER = (
    "cannot downgrade 0029 while material-request audit evidence exists"
)
SQLITE_AUDIT_EVENT_UPDATE = "trg_audit_events_immutable_update_0015"
SQLITE_AUDIT_EVENT_DELETE = "trg_audit_events_immutable_delete_0015"
SQLITE_AUDIT_EVENT_INSERT = "trg_audit_events_stream_binding_insert_0017"
SQLITE_AUDIT_HEAD_UPDATE = "trg_audit_chain_heads_forward_only_0015"
SQLITE_AUDIT_HEAD_DELETE = "trg_audit_chain_heads_no_delete_0015"
SQLITE_AUDIT_HEAD_BINDING = "trg_audit_chain_heads_stream_binding_0017"
PG_AUDIT_HEAD_ROW_TRIGGER = "trg_audit_chain_heads_forward_only_0015"
PG_AUDIT_HEAD_FUNCTION = "rsc_validate_audit_chain_head_mutation_0015"


def _postgresql_contact_envelope_invalid(
    document: str,
    request_id: str,
    requester_person_id: str,
) -> str:
    required_keys = (
        "'schema', 'provider', 'kms_key_id', 'key_version', "
        "'ciphertext_b64', 'nonce_b64', 'aad_sha256', 'mobile_hmac', "
        "'contact_hmac'"
    )
    aad = (
        "encode(sha256("
        "convert_to('cloud_oam.material_request.contact.envelope.v1', 'UTF8') "
        "|| decode('00', 'hex') "
        f"|| convert_to('request_id=' || ({request_id})::text, 'UTF8') "
        "|| decode('00', 'hex') "
        f"|| convert_to('requester_person_id=' || ({requester_person_id})::text, "
        "'UTF8')), 'hex')"
    )
    return f"""
jsonb_typeof({document}) IS DISTINCT FROM 'object'
OR (SELECT count(*) FROM jsonb_object_keys({document})) <> 9
OR NOT ({document} ?& ARRAY[{required_keys}])
OR jsonb_typeof({document}->'schema') IS DISTINCT FROM 'string'
OR {document}->>'schema' <> 'rsc.material_request_contact.v1'
OR jsonb_typeof({document}->'provider') IS DISTINCT FROM 'string'
OR {document}->>'provider' <> 'aliyun_kms'
OR jsonb_typeof({document}->'kms_key_id') IS DISTINCT FROM 'string'
OR {document}->>'kms_key_id' !~ '^[A-Za-z0-9][-A-Za-z0-9_./:@+]{{2,255}}$'
OR lower({document}->>'kms_key_id') LIKE ANY (
    ARRAY['%replace-with%', '%replace_me%', '%replace-me%',
          '%change-me%', '%changeme%']
)
OR jsonb_typeof({document}->'key_version') IS DISTINCT FROM 'number'
OR {document}->>'key_version' !~ '^[1-9][0-9]*$'
OR jsonb_typeof({document}->'ciphertext_b64') IS DISTINCT FROM 'string'
OR {document}->>'ciphertext_b64' !~ '^[A-Za-z0-9+/]+={{0,2}}$'
OR length({document}->>'ciphertext_b64') < 24
OR length({document}->>'ciphertext_b64') % 4 <> 0
OR octet_length(decode({document}->>'ciphertext_b64', 'base64')) < 17
OR jsonb_typeof({document}->'nonce_b64') IS DISTINCT FROM 'string'
OR {document}->>'nonce_b64' !~ '^[A-Za-z0-9+/]{{16}}$'
OR octet_length(decode({document}->>'nonce_b64', 'base64')) <> 12
OR jsonb_typeof({document}->'aad_sha256') IS DISTINCT FROM 'string'
OR {document}->>'aad_sha256' !~ '^[0-9a-f]{{64}}$'
OR {document}->>'aad_sha256' <> {aad}
OR jsonb_typeof({document}->'mobile_hmac') IS DISTINCT FROM 'string'
OR {document}->>'mobile_hmac' !~ '^hmac:[1-9][0-9]{{0,9}}:[0-9a-f]{{64}}$'
OR jsonb_typeof({document}->'contact_hmac') IS DISTINCT FROM 'string'
OR {document}->>'contact_hmac' !~ '^hmac:[1-9][0-9]{{0,9}}:[0-9a-f]{{64}}$'
""".strip()


def _postgresql_masked_projections_invalid(
    address_document: str,
    contact_document: str,
) -> str:
    return f"""
jsonb_typeof({address_document}) IS DISTINCT FROM 'object'
OR (SELECT count(*) FROM jsonb_object_keys({address_document})) <> 5
OR NOT ({address_document} ?& ARRAY[
    'province_code', 'province_name', 'city_name', 'district_name', 'detail_masked'
])
OR EXISTS (
    SELECT 1 FROM jsonb_each({address_document}) AS item
     WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'string'
       OR btrim(item.value #>> '{{}}') = ''
       OR item.value #>> '{{}}' <> btrim(item.value #>> '{{}}')
)
OR length({address_document}->>'province_code') > 12
OR length({address_document}->>'province_name') > 80
OR length({address_document}->>'city_name') > 80
OR length({address_document}->>'district_name') > 80
OR length({address_document}->>'detail_masked') > 500
OR {address_document}->>'detail_masked' !~ '[*＊•]'
OR jsonb_typeof({contact_document}) IS DISTINCT FROM 'object'
OR (SELECT count(*) FROM jsonb_object_keys({contact_document})) <> 2
OR NOT ({contact_document} ?& ARRAY['name_masked', 'mobile_masked'])
OR jsonb_typeof({contact_document}->'name_masked') IS DISTINCT FROM 'string'
OR jsonb_typeof({contact_document}->'mobile_masked') IS DISTINCT FROM 'string'
OR {contact_document}->>'name_masked' <> btrim({contact_document}->>'name_masked')
OR {contact_document}->>'mobile_masked' <> btrim({contact_document}->>'mobile_masked')
OR length({contact_document}->>'name_masked') NOT BETWEEN 1 AND 120
OR length({contact_document}->>'mobile_masked') NOT BETWEEN 1 AND 32
OR {contact_document}->>'name_masked' !~ '[*＊•]'
OR {contact_document}->>'mobile_masked' !~ '[*＊•]'
OR {contact_document}->>'mobile_masked' ~ '[0-9]{{7}}'
""".strip()


def _sqlite_contact_envelope_invalid(document: str) -> str:
    key_version = f"json_extract({document}, '$.key_version')"
    ciphertext = f"json_extract({document}, '$.ciphertext_b64')"
    nonce = f"json_extract({document}, '$.nonce_b64')"
    aad = f"json_extract({document}, '$.aad_sha256')"
    mobile_hmac = f"json_extract({document}, '$.mobile_hmac')"
    hmac_tail = f"substr({mobile_hmac}, 6)"
    separator = f"instr({hmac_tail}, ':')"
    hmac_version = f"substr({mobile_hmac}, 6, {separator} - 1)"
    hmac_digest = f"substr({mobile_hmac}, 6 + {separator})"
    return f"""
json_valid({document}) <> 1
OR json_type({document}) <> 'object'
OR (SELECT count(*) FROM json_each({document})) <> 9
OR json_type({document}, '$.schema') <> 'text'
OR json_extract({document}, '$.schema') <> 'rsc.material_request_contact.v1'
OR json_type({document}, '$.provider') <> 'text'
OR json_extract({document}, '$.provider') <> 'aliyun_kms'
OR json_type({document}, '$.kms_key_id') <> 'text'
OR length(json_extract({document}, '$.kms_key_id')) NOT BETWEEN 3 AND 256
OR substr(json_extract({document}, '$.kms_key_id'), 1, 1) GLOB '[^A-Za-z0-9]'
OR json_extract({document}, '$.kms_key_id') GLOB '*[^-A-Za-z0-9_./:@+]*'
OR lower(json_extract({document}, '$.kms_key_id')) LIKE '%replace-with%'
OR lower(json_extract({document}, '$.kms_key_id')) LIKE '%replace_me%'
OR lower(json_extract({document}, '$.kms_key_id')) LIKE '%replace-me%'
OR lower(json_extract({document}, '$.kms_key_id')) LIKE '%change-me%'
OR lower(json_extract({document}, '$.kms_key_id')) LIKE '%changeme%'
OR json_type({document}, '$.key_version') <> 'integer'
OR {key_version} <= 0
OR json_type({document}, '$.ciphertext_b64') <> 'text'
OR length({ciphertext}) < 24
OR length({ciphertext}) % 4 <> 0
OR {ciphertext} GLOB '*[^A-Za-z0-9+/=]*'
OR length({ciphertext}) - length(rtrim({ciphertext}, '=')) > 2
OR instr(rtrim({ciphertext}, '='), '=') > 0
OR json_type({document}, '$.nonce_b64') <> 'text'
OR length({nonce}) <> 16
OR {nonce} GLOB '*[^A-Za-z0-9+/]*'
OR json_type({document}, '$.aad_sha256') <> 'text'
OR length({aad}) <> 64
OR {aad} GLOB '*[^0-9a-f]*'
OR json_type({document}, '$.mobile_hmac') <> 'text'
OR substr({mobile_hmac}, 1, 5) <> 'hmac:'
OR {separator} NOT BETWEEN 2 AND 11
OR substr({hmac_version}, 1, 1) GLOB '[^1-9]'
OR {hmac_version} GLOB '*[^0-9]*'
OR length({hmac_digest}) <> 64
OR {hmac_digest} GLOB '*[^0-9a-f]*'
OR json_type({document}, '$.contact_hmac') <> 'text'
OR substr(json_extract({document}, '$.contact_hmac'), 1, 5) <> 'hmac:'
OR instr(substr(json_extract({document}, '$.contact_hmac'), 6), ':') NOT BETWEEN 2 AND 11
OR substr(
    json_extract({document}, '$.contact_hmac'),
    6,
    instr(substr(json_extract({document}, '$.contact_hmac'), 6), ':') - 1
) GLOB '*[^0-9]*'
OR substr(
    substr(
        json_extract({document}, '$.contact_hmac'),
        6,
        instr(substr(json_extract({document}, '$.contact_hmac'), 6), ':') - 1
    ), 1, 1
) GLOB '[^1-9]'
OR length(substr(
    json_extract({document}, '$.contact_hmac'),
    6 + instr(substr(json_extract({document}, '$.contact_hmac'), 6), ':')
)) <> 64
OR substr(
    json_extract({document}, '$.contact_hmac'),
    6 + instr(substr(json_extract({document}, '$.contact_hmac'), 6), ':')
) GLOB '*[^0-9a-f]*'
""".strip()


def _sqlite_masked_projections_invalid(
    address_document: str,
    contact_document: str,
) -> str:
    address_detail = f"json_extract({address_document}, '$.detail_masked')"
    name_masked = f"json_extract({contact_document}, '$.name_masked')"
    mobile_masked = f"json_extract({contact_document}, '$.mobile_masked')"
    return f"""
json_valid({address_document}) <> 1
OR json_type({address_document}) <> 'object'
OR (SELECT count(*) FROM json_each({address_document})) <> 5
OR json_type({address_document}, '$.province_code') <> 'text'
OR json_type({address_document}, '$.province_name') <> 'text'
OR json_type({address_document}, '$.city_name') <> 'text'
OR json_type({address_document}, '$.district_name') <> 'text'
OR json_type({address_document}, '$.detail_masked') <> 'text'
OR length(json_extract({address_document}, '$.province_code')) NOT BETWEEN 1 AND 12
OR length(json_extract({address_document}, '$.province_name')) NOT BETWEEN 1 AND 80
OR length(json_extract({address_document}, '$.city_name')) NOT BETWEEN 1 AND 80
OR length(json_extract({address_document}, '$.district_name')) NOT BETWEEN 1 AND 80
OR length({address_detail}) NOT BETWEEN 1 AND 500
OR json_extract({address_document}, '$.province_code') <> trim(json_extract({address_document}, '$.province_code'))
OR json_extract({address_document}, '$.province_name') <> trim(json_extract({address_document}, '$.province_name'))
OR json_extract({address_document}, '$.city_name') <> trim(json_extract({address_document}, '$.city_name'))
OR json_extract({address_document}, '$.district_name') <> trim(json_extract({address_document}, '$.district_name'))
OR {address_detail} <> trim({address_detail})
OR (instr({address_detail}, '*') = 0 AND instr({address_detail}, '＊') = 0 AND instr({address_detail}, '•') = 0)
OR json_valid({contact_document}) <> 1
OR json_type({contact_document}) <> 'object'
OR (SELECT count(*) FROM json_each({contact_document})) <> 2
OR json_type({contact_document}, '$.name_masked') <> 'text'
OR json_type({contact_document}, '$.mobile_masked') <> 'text'
OR length({name_masked}) NOT BETWEEN 1 AND 120
OR length({mobile_masked}) NOT BETWEEN 1 AND 32
OR {name_masked} <> trim({name_masked})
OR {mobile_masked} <> trim({mobile_masked})
OR (instr({name_masked}, '*') = 0 AND instr({name_masked}, '＊') = 0 AND instr({name_masked}, '•') = 0)
OR (instr({mobile_masked}, '*') = 0 AND instr({mobile_masked}, '＊') = 0 AND instr({mobile_masked}, '•') = 0)
OR {mobile_masked} GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'
""".strip()


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0029 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    _extend_audit_stream(dialect)
    _create_tables()
    _create_indexes()
    _seed_permissions_and_route()
    if dialect == "postgresql":
        _create_postgresql_guards()
    else:
        _create_sqlite_guards()


def downgrade() -> None:
    dialect = _dialect_name()
    _assert_downgrade_is_empty()
    if dialect == "postgresql":
        _drop_postgresql_guards()
    else:
        _drop_sqlite_existing_table_guards()
    _delete_static_rows()
    _drop_tables()
    _shrink_audit_stream(dialect)


def _extend_audit_stream(dialect: str) -> None:
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0029 SQLite audit upgrade requires an online connection")
        op.execute(
            f"""
LOCK TABLE public.{AUDIT_HEAD_TABLE} IN ACCESS EXCLUSIVE MODE;
LOCK TABLE public.{AUDIT_EVENT_TABLE} IN ACCESS EXCLUSIVE MODE;
DO $$
BEGIN
    IF (SELECT count(*) FROM public.{AUDIT_HEAD_TABLE}) <> 3
       OR EXISTS (
           SELECT 1
             FROM (VALUES
                 ('30000000-0000-4000-8000-000000000001'::uuid, 'authorization'),
                 ('30000000-0000-4000-8000-000000000002'::uuid, 'authentication'),
                 ('30000000-0000-4000-8000-000000000003'::uuid, 'inventory')
             ) AS expected(id, stream_key)
             LEFT JOIN public.{AUDIT_HEAD_TABLE} AS head
               ON head.id = expected.id AND head.stream_key = expected.stream_key
            WHERE head.id IS NULL
       ) THEN
        RAISE EXCEPTION '{AUDIT_UPGRADE_BLOCKER}';
    END IF;
END $$
"""
        )
        _replace_postgresql_audit_check(include_material_request=True)
        _insert_material_request_audit_head()
        return

    connection = op.get_bind()
    if dialect == "postgresql":
        op.execute(f"LOCK TABLE public.{AUDIT_HEAD_TABLE} IN ACCESS EXCLUSIVE MODE")
        op.execute(f"LOCK TABLE public.{AUDIT_EVENT_TABLE} IN ACCESS EXCLUSIVE MODE")
    _require_canonical_existing_audit_heads()
    if dialect == "postgresql":
        _replace_postgresql_audit_check(include_material_request=True)
    else:
        event_count = connection.execute(
            sa.text(f"SELECT COUNT(*) FROM {AUDIT_EVENT_TABLE}")
        ).scalar_one()
        foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        if event_count and foreign_keys:
            raise RuntimeError(AUDIT_UPGRADE_BLOCKER)
        _replace_sqlite_audit_check(include_material_request=True)
    _insert_material_request_audit_head()


def _shrink_audit_stream(dialect: str) -> None:
    if context.is_offline_mode():
        raise RuntimeError("0029 audit downgrade requires an online connection")
    connection = op.get_bind()
    if dialect == "postgresql":
        op.execute(f"LOCK TABLE public.{AUDIT_HEAD_TABLE} IN ACCESS EXCLUSIVE MODE")
        op.execute(f"LOCK TABLE public.{AUDIT_EVENT_TABLE} IN ACCESS EXCLUSIVE MODE")
    row = connection.execute(
        sa.text(
            f"SELECT id, last_event_id, last_hash, version FROM {AUDIT_HEAD_TABLE} "
            "WHERE stream_key = 'material_request'"
        )
    ).mappings().one_or_none()
    event_count = connection.execute(
        sa.text(
            f"SELECT COUNT(*) FROM {AUDIT_EVENT_TABLE} "
            "WHERE stream_key = 'material_request'"
        )
    ).scalar_one()
    if (
        row is None
        or str(row["id"]).replace("-", "").lower()
        != MATERIAL_REQUEST_AUDIT_HEAD_ID.hex
        or row["last_event_id"] is not None
        or row["last_hash"] is not None
        or row["version"] != 0
        or event_count
    ):
        raise RuntimeError(AUDIT_DOWNGRADE_BLOCKER)
    if dialect == "sqlite":
        total_events = connection.execute(
            sa.text(f"SELECT COUNT(*) FROM {AUDIT_EVENT_TABLE}")
        ).scalar_one()
        foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        if total_events and foreign_keys:
            raise RuntimeError(AUDIT_DOWNGRADE_BLOCKER)
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_AUDIT_HEAD_DELETE}")
        op.execute(
            sa.text(
                f"DELETE FROM {AUDIT_HEAD_TABLE} WHERE stream_key = 'material_request'"
            )
        )
        _replace_sqlite_audit_check(include_material_request=False)
        return
    op.execute(
        f"ALTER TABLE public.{AUDIT_HEAD_TABLE} DISABLE TRIGGER "
        f"{PG_AUDIT_HEAD_ROW_TRIGGER}"
    )
    op.execute(
        sa.text(
            f"DELETE FROM {AUDIT_HEAD_TABLE} WHERE stream_key = 'material_request'"
        )
    )
    op.execute(
        f"ALTER TABLE public.{AUDIT_HEAD_TABLE} ENABLE ALWAYS TRIGGER "
        f"{PG_AUDIT_HEAD_ROW_TRIGGER}"
    )
    _replace_postgresql_audit_check(include_material_request=False)


def _require_canonical_existing_audit_heads() -> None:
    rows = op.get_bind().execute(
        sa.text(
            f"SELECT id, stream_key FROM {AUDIT_HEAD_TABLE} ORDER BY stream_key"
        )
    ).mappings().all()
    actual = {
        row["stream_key"]: str(row["id"]).replace("-", "").lower()
        for row in rows
    }
    expected = {key: value.hex for key, value in EXISTING_AUDIT_HEADS.items()}
    if actual != expected:
        raise RuntimeError(AUDIT_UPGRADE_BLOCKER)


def _replace_postgresql_audit_check(*, include_material_request: bool) -> None:
    values = "'authorization', 'authentication', 'inventory'"
    if include_material_request:
        values += ", 'material_request'"
    op.drop_constraint(AUDIT_CHECK, AUDIT_EVENT_TABLE, type_="check")
    op.create_check_constraint(
        AUDIT_CHECK,
        AUDIT_EVENT_TABLE,
        f"stream_key IN ({values})",
    )


def _replace_sqlite_audit_check(*, include_material_request: bool) -> None:
    dependent_triggers = _capture_sqlite_audit_dependent_triggers()
    for trigger_name, _ in dependent_triggers:
        op.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')
    _drop_sqlite_audit_event_guards()
    _drop_sqlite_audit_head_guards()
    values = "'authorization', 'authentication', 'inventory'"
    if include_material_request:
        values += ", 'material_request'"
    with op.batch_alter_table(AUDIT_EVENT_TABLE, recreate="always") as batch_op:
        batch_op.drop_constraint(AUDIT_CHECK, type_="check")
        batch_op.create_check_constraint(
            AUDIT_CHECK,
            f"stream_key IN ({values})",
        )
    _create_sqlite_audit_event_guards(include_material_request=include_material_request)
    _create_sqlite_audit_head_guards()
    for _, trigger_sql in dependent_triggers:
        op.execute(trigger_sql)
    if op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").first():
        raise RuntimeError(
            AUDIT_UPGRADE_BLOCKER if include_material_request else AUDIT_DOWNGRADE_BLOCKER
        )


def _capture_sqlite_audit_dependent_triggers() -> list[tuple[str, str]]:
    canonical = {
        SQLITE_AUDIT_EVENT_UPDATE,
        SQLITE_AUDIT_EVENT_DELETE,
        SQLITE_AUDIT_EVENT_INSERT,
        SQLITE_AUDIT_HEAD_UPDATE,
        SQLITE_AUDIT_HEAD_DELETE,
        SQLITE_AUDIT_HEAD_BINDING,
    }
    rows = op.get_bind().exec_driver_sql(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'trigger' AND sql IS NOT NULL "
        "AND lower(sql) LIKE '%audit_events%' ORDER BY name"
    ).all()
    return [
        (str(name), str(sql))
        for name, sql in rows
        if str(name) not in canonical
    ]


def _insert_material_request_audit_head() -> None:
    seeded_at = datetime(2026, 8, 31, tzinfo=timezone.utc)
    head_table = sa.table(
        AUDIT_HEAD_TABLE,
        sa.column("id", sa.Uuid()),
        sa.column("stream_key", sa.String(length=160)),
        sa.column("last_event_id", sa.Uuid()),
        sa.column("last_hash", sa.String(length=64)),
        sa.column("version", sa.BigInteger()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        head_table,
        [
            {
                "id": MATERIAL_REQUEST_AUDIT_HEAD_ID,
                "stream_key": "material_request",
                "last_event_id": None,
                "last_hash": None,
                "version": 0,
                "created_at": seeded_at,
                "updated_at": seeded_at,
            }
        ],
    )


def _drop_sqlite_audit_event_guards() -> None:
    for trigger_name in (
        SQLITE_AUDIT_EVENT_UPDATE,
        SQLITE_AUDIT_EVENT_DELETE,
        SQLITE_AUDIT_EVENT_INSERT,
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _create_sqlite_audit_event_guards(*, include_material_request: bool) -> None:
    values = "'authorization', 'authentication', 'inventory'"
    if include_material_request:
        values += ", 'material_request'"
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_AUDIT_EVENT_UPDATE}
BEFORE UPDATE ON {AUDIT_EVENT_TABLE}
BEGIN
    SELECT RAISE(ABORT, 'audit events are immutable');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_AUDIT_EVENT_DELETE}
BEFORE DELETE ON {AUDIT_EVENT_TABLE}
BEGIN
    SELECT RAISE(ABORT, 'audit events are immutable');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_AUDIT_EVENT_INSERT}
BEFORE INSERT ON {AUDIT_EVENT_TABLE}
WHEN NEW.stream_key NOT IN ({values})
  OR NEW.stream_version <= 0
  OR NOT EXISTS (
      SELECT 1
        FROM {AUDIT_HEAD_TABLE} AS head
       WHERE head.stream_key = NEW.stream_key
         AND NEW.stream_version = head.version + 1
         AND NEW.previous_hash IS head.last_hash
  )
BEGIN
    SELECT RAISE(ABORT, 'audit event stream binding is invalid');
END
"""
    )


def _drop_sqlite_audit_head_guards() -> None:
    for trigger_name in (
        SQLITE_AUDIT_HEAD_UPDATE,
        SQLITE_AUDIT_HEAD_DELETE,
        SQLITE_AUDIT_HEAD_BINDING,
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _create_sqlite_audit_head_guards() -> None:
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_AUDIT_HEAD_UPDATE}
BEFORE UPDATE ON {AUDIT_HEAD_TABLE}
WHEN NEW.id IS NOT OLD.id
  OR NEW.stream_key IS NOT OLD.stream_key
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.version <> OLD.version + 1
  OR NEW.last_event_id IS NULL
  OR NEW.last_hash IS NULL
  OR NOT EXISTS (
      SELECT 1
        FROM {AUDIT_EVENT_TABLE} AS event
       WHERE event.id = NEW.last_event_id
         AND event.event_hash = NEW.last_hash
         AND event.previous_hash IS OLD.last_hash
  )
BEGIN
    SELECT RAISE(ABORT, 'audit chain head must advance by one immutable event');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_AUDIT_HEAD_DELETE}
BEFORE DELETE ON {AUDIT_HEAD_TABLE}
BEGIN
    SELECT RAISE(ABORT, 'audit chain heads cannot be removed');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_AUDIT_HEAD_BINDING}
BEFORE UPDATE ON {AUDIT_HEAD_TABLE}
WHEN NOT EXISTS (
    SELECT 1
      FROM {AUDIT_EVENT_TABLE} AS event
     WHERE event.id = NEW.last_event_id
       AND event.event_hash = NEW.last_hash
       AND event.stream_key = NEW.stream_key
       AND event.stream_version = NEW.version
)
BEGIN
    SELECT RAISE(ABORT, 'audit head/event stream binding is invalid');
END
"""
    )


def _create_tables() -> None:
    op.create_table(
        "oam_work_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("external_object_id", sa.Uuid(), nullable=False),
        sa.Column("work_order_no", sa.String(length=100), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("engineer_person_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'completed', 'closed', "
            "'cancelled', 'inactive')",
            name="ck_oam_work_orders_status",
        ),
        sa.ForeignKeyConstraint(["engineer_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["external_object_id"], ["external_objects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_object_id", name="uq_oam_work_orders_external_object"),
        sa.UniqueConstraint("work_order_no", name="uq_oam_work_orders_number"),
    )
    op.create_table(
        "material_substitutions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("substitute_material_id", sa.Uuid(), nullable=False),
        sa.Column("ratio", RATIO, nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("material_id <> substitute_material_id", name="ck_material_substitutions_distinct_materials"),
        sa.CheckConstraint("ratio > 0", name="ck_material_substitutions_ratio"),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_material_substitutions_validity"),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_material_substitutions_status"),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["substitute_material_id"], ["materials.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("material_id", "substitute_material_id", "valid_from", name="uq_material_substitutions_pair_start"),
    )
    op.create_table(
        "approval_route_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("route_code", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("approval_mode", sa.String(length=32), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_approval_route_versions_version"),
        sa.CheckConstraint("approval_mode IN ('external_registration', 'direct_star')", name="ck_approval_route_versions_mode"),
        sa.CheckConstraint("status IN ('scheduled', 'active', 'inactive')", name="ck_approval_route_versions_status"),
        sa.CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="ck_approval_route_versions_validity"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("route_code", "version", name="uq_approval_route_versions_code_version"),
    )
    op.create_table(
        "approval_route_step_defs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("route_version_id", sa.Uuid(), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("role_code", sa.String(length=80), nullable=False),
        sa.Column("source_mode", sa.String(length=32), nullable=False),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("step_no BETWEEN 1 AND 3", name="ck_approval_route_step_defs_number"),
        sa.CheckConstraint("source_mode IN ('internal', 'external_registration', 'direct_star')", name="ck_approval_route_step_defs_source_mode"),
        sa.CheckConstraint("scope_type IN ('organization', 'national', 'document')", name="ck_approval_route_step_defs_scope_type"),
        sa.ForeignKeyConstraint(["role_code"], ["roles.code"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["route_version_id"], ["approval_route_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("route_version_id", "step_no", name="uq_approval_route_step_defs_step"),
    )
    op.create_table(
        "material_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_no", sa.String(length=100), nullable=False),
        sa.Column("requester_user_id", sa.String(length=36), nullable=False),
        sa.Column("requester_person_id", sa.Uuid(), nullable=False),
        sa.Column("requester_org_id", sa.Uuid(), nullable=False),
        sa.Column("work_order_id", sa.Uuid(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("urgency", sa.String(length=20), nullable=False),
        sa.Column("expected_date", sa.Date(), nullable=True),
        sa.Column("address_snapshot_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column("address_masked_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column(
            "contact_snapshot_jsonb",
            JSON_DOCUMENT,
            nullable=False,
            comment="encrypted contact envelope and hashes only; no plaintext audit/outbox copy",
        ),
        sa.Column("contact_masked_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column("approval_mode", sa.String(length=32), server_default="external_registration", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("revision_no", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("allocation_status", sa.String(length=32), server_default="not_allocated", nullable=False),
        sa.Column("reservation_status", sa.String(length=32), server_default="not_reserved", nullable=False),
        sa.Column("outbound_status", sa.String(length=32), server_default="not_started", nullable=False),
        sa.Column("shipment_status", sa.String(length=32), server_default="not_started", nullable=False),
        sa.Column("logistics_signature_status", sa.String(length=32), server_default="not_signed", nullable=False),
        sa.Column("oam_receipt_status", sa.String(length=32), server_default="not_occurred", nullable=False),
        sa.Column("personal_inbound_status", sa.String(length=32), server_default="not_started", nullable=False),
        sa.Column("notification_status", sa.String(length=32), server_default="not_started", nullable=False),
        sa.Column("reconciliation_status", sa.String(length=32), server_default="not_started", nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("urgency IN ('normal', 'urgent', 'emergency')", name="ck_material_requests_urgency"),
        sa.CheckConstraint("approval_mode IN ('external_registration', 'direct_star')", name="ck_material_requests_approval_mode"),
        sa.CheckConstraint("status IN ('draft', 'submitted', 'approval_in_progress', 'returned', 'approved', 'partially_approved', 'rejected', 'withdrawn', 'cancellation_pending', 'cancelled')", name="ck_material_requests_status"),
        sa.CheckConstraint("version >= 0", name="ck_material_requests_version"),
        sa.CheckConstraint("revision_no > 0", name="ck_material_requests_revision"),
        sa.CheckConstraint("(status = 'draft' AND submitted_at IS NULL) OR (status <> 'draft' AND submitted_at IS NOT NULL)", name="ck_material_requests_submission_state"),
        sa.CheckConstraint("(status IN ('approved', 'partially_approved', 'rejected', 'cancellation_pending') AND decided_at IS NOT NULL) OR (status NOT IN ('approved', 'partially_approved', 'rejected', 'cancellation_pending', 'cancelled') AND decided_at IS NULL) OR status = 'cancelled'", name="ck_material_requests_decision_state"),
        sa.CheckConstraint("(status = 'withdrawn' AND withdrawn_at IS NOT NULL) OR (status <> 'withdrawn' AND withdrawn_at IS NULL)", name="ck_material_requests_withdrawal_state"),
        sa.CheckConstraint("(status = 'cancelled' AND cancelled_at IS NOT NULL) OR (status <> 'cancelled' AND cancelled_at IS NULL)", name="ck_material_requests_cancellation_state"),
        sa.CheckConstraint("allocation_status IN ('not_allocated', 'partially_allocated', 'allocated', 'shortage')", name="ck_material_requests_allocation_status"),
        sa.CheckConstraint("reservation_status IN ('not_reserved', 'pending', 'reserved', 'partially_released', 'released', 'fulfilled')", name="ck_material_requests_reservation_status"),
        sa.CheckConstraint("outbound_status IN ('not_started', 'pending_pick', 'picked', 'outbound')", name="ck_material_requests_outbound_status"),
        sa.CheckConstraint("shipment_status IN ('not_started', 'pending_handover', 'shipped', 'in_transit', 'exception')", name="ck_material_requests_shipment_status"),
        sa.CheckConstraint("logistics_signature_status IN ('not_signed', 'signed', 'refused', 'exception')", name="ck_material_requests_signature_status"),
        sa.CheckConstraint("oam_receipt_status IN ('not_occurred', 'synced', 'exception')", name="ck_material_requests_oam_receipt_status"),
        sa.CheckConstraint("personal_inbound_status IN ('not_started', 'pending_acceptance', 'partially_accepted', 'accepted', 'posted')", name="ck_material_requests_personal_inbound_status"),
        sa.CheckConstraint("notification_status IN ('not_started', 'queued', 'sent', 'delivered', 'read', 'failed')", name="ck_material_requests_notification_status"),
        sa.CheckConstraint("reconciliation_status IN ('not_started', 'pending', 'staged', 'validated', 'reconciled', 'conflict', 'failed')", name="ck_material_requests_reconciliation_status"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requester_org_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requester_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["work_order_id"], ["oam_work_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_no", name="uq_material_requests_number"),
    )
    op.create_table(
        "material_request_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("previous_revision_id", sa.Uuid(), nullable=True),
        sa.Column("work_order_id", sa.Uuid(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("urgency", sa.String(length=20), nullable=False),
        sa.Column("expected_date", sa.Date(), nullable=True),
        sa.Column("address_snapshot_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column("address_masked_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column(
            "contact_snapshot_jsonb",
            JSON_DOCUMENT,
            nullable=False,
            comment="sealed aliyun_kms envelope only; never plaintext contact data",
        ),
        sa.Column("contact_masked_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column("approval_mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("content_manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sealed_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision_no > 0", name="ck_material_request_revisions_number"),
        sa.CheckConstraint(
            "(revision_no = 1 AND previous_revision_id IS NULL) OR "
            "(revision_no > 1 AND previous_revision_id IS NOT NULL)",
            name="ck_material_request_revisions_chain",
        ),
        sa.CheckConstraint(
            "urgency IN ('normal', 'urgent', 'emergency')",
            name="ck_material_request_revisions_urgency",
        ),
        sa.CheckConstraint(
            "approval_mode IN ('external_registration', 'direct_star')",
            name="ck_material_request_revisions_approval_mode",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'sealed')",
            name="ck_material_request_revisions_status",
        ),
        sa.CheckConstraint(
            "(status = 'draft' AND content_manifest_sha256 IS NULL "
            "AND sealed_at IS NULL AND sealed_by_user_id IS NULL) OR "
            "(status = 'sealed' AND length(content_manifest_sha256) = 64 "
            "AND sealed_at IS NOT NULL AND sealed_by_user_id IS NOT NULL)",
            name="ck_material_request_revisions_seal_state",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["material_requests.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["sealed_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["work_order_id"], ["oam_work_orders.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["previous_revision_id", "request_id"],
            ["material_request_revisions.id", "material_request_revisions.request_id"],
            name="fk_material_request_revisions_previous_request",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "request_id", name="uq_material_request_revisions_id_request"
        ),
        sa.UniqueConstraint(
            "id",
            "request_id",
            "revision_no",
            name="uq_material_request_revisions_identity",
        ),
        sa.UniqueConstraint(
            "previous_revision_id", name="uq_material_request_revisions_previous"
        ),
        sa.UniqueConstraint(
            "request_id", "revision_no", name="uq_material_request_revisions_number"
        ),
    )
    op.create_table(
        "material_request_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("client_line_key", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("suggested_substitute_material_id", sa.Uuid(), nullable=True),
        sa.Column("requested_qty", QUANTITY, nullable=False),
        sa.Column("required_date", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="draft", nullable=False),
        sa.Column("final_approved_qty", QUANTITY, server_default=sa.text("0"), nullable=False, comment="derived projection only; immutable approval decisions are the fact source"),
        sa.Column("cancelled_qty", QUANTITY, server_default=sa.text("0"), nullable=False, comment="derived projection only; accepted cancellation commands are the fact source"),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("line_no > 0", name="ck_material_request_lines_number"),
        sa.CheckConstraint("revision_no > 0", name="ck_material_request_lines_revision"),
        sa.CheckConstraint("requested_qty > 0", name="ck_material_request_lines_requested_qty"),
        sa.CheckConstraint("suggested_substitute_material_id IS NULL OR suggested_substitute_material_id <> material_id", name="ck_material_request_lines_suggested_substitute"),
        sa.CheckConstraint("final_approved_qty >= 0 AND final_approved_qty <= requested_qty", name="ck_material_request_lines_approved_qty"),
        sa.CheckConstraint("cancelled_qty >= 0 AND cancelled_qty <= final_approved_qty", name="ck_material_request_lines_cancelled_qty"),
        sa.CheckConstraint("status IN ('draft', 'approval_pending', 'approved', 'partially_approved', 'rejected', 'cancelled')", name="ck_material_request_lines_status"),
        sa.CheckConstraint("version >= 0", name="ck_material_request_lines_version"),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["suggested_substitute_material_id"], ["materials.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["request_id"], ["material_requests.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["revision_id", "request_id", "revision_no"],
            [
                "material_request_revisions.id",
                "material_request_revisions.request_id",
                "material_request_revisions.revision_no",
            ],
            name="fk_material_request_lines_revision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "request_id",
            "revision_id",
            name="uq_material_request_lines_identity",
        ),
        sa.UniqueConstraint("revision_id", "client_line_key", name="uq_material_request_lines_client"),
        sa.UniqueConstraint("revision_id", "line_no", name="uq_material_request_lines_number"),
    )
    op.create_table(
        "material_request_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("request_line_id", sa.Uuid(), nullable=True),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('request_attachment', 'request_line_attachment')",
            name="ck_material_request_files_purpose",
        ),
        sa.CheckConstraint(
            "(purpose = 'request_attachment' AND request_line_id IS NULL) OR "
            "(purpose = 'request_line_attachment' AND request_line_id IS NOT NULL)",
            name="ck_material_request_files_scope",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["request_id"], ["material_requests.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["revision_id", "request_id", "revision_no"],
            [
                "material_request_revisions.id",
                "material_request_revisions.request_id",
                "material_request_revisions.revision_no",
            ],
            name="fk_material_request_files_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["request_line_id", "request_id", "revision_id"],
            [
                "material_request_lines.id",
                "material_request_lines.request_id",
                "material_request_lines.revision_id",
            ],
            name="fk_material_request_files_line_request",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "approval_instances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_revision_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("route_version_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("current_step_no", sa.Integer(), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_no > 0", name="ck_approval_instances_attempt"),
        sa.CheckConstraint("revision_no > 0", name="ck_approval_instances_revision"),
        sa.CheckConstraint("status IN ('active', 'returned', 'completed', 'rejected', 'withdrawn', 'cancelled', 'superseded')", name="ck_approval_instances_status"),
        sa.CheckConstraint("current_step_no IS NULL OR current_step_no BETWEEN 1 AND 3", name="ck_approval_instances_current_step"),
        sa.CheckConstraint("version >= 0", name="ck_approval_instances_version"),
        sa.ForeignKeyConstraint(["request_id"], ["material_requests.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["request_revision_id", "request_id", "revision_no"],
            [
                "material_request_revisions.id",
                "material_request_revisions.request_id",
                "material_request_revisions.revision_no",
            ],
            name="fk_approval_instances_request_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["route_version_id"], ["approval_route_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", "attempt_no", name="uq_approval_instances_attempt"),
        sa.UniqueConstraint(
            "request_revision_id", name="uq_approval_instances_request_revision"
        ),
    )
    op.create_table(
        "approval_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("predecessor_step_id", sa.Uuid(), nullable=True),
        sa.Column("source_mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("assignee_user_id", sa.String(length=36), nullable=True),
        sa.Column("assignee_snapshot_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column("decision_manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("step_no BETWEEN 1 AND 3", name="ck_approval_steps_number"),
        sa.CheckConstraint("attempt_no > 0", name="ck_approval_steps_attempt"),
        sa.CheckConstraint("(step_no = 1 AND predecessor_step_id IS NULL) OR (step_no > 1 AND predecessor_step_id IS NOT NULL)", name="ck_approval_steps_predecessor"),
        sa.CheckConstraint("source_mode IN ('internal', 'external_registration', 'direct_star')", name="ck_approval_steps_source_mode"),
        sa.CheckConstraint("status IN ('pending', 'open', 'awaiting_external_evidence', 'evidence_pending_verification', 'approved', 'partially_approved', 'rejected', 'returned', 'cancelled', 'superseded')", name="ck_approval_steps_status"),
        sa.CheckConstraint("(status IN ('approved', 'partially_approved', 'rejected') AND decided_at IS NOT NULL AND length(decision_manifest_sha256) = 64) OR (status = 'returned' AND decided_at IS NOT NULL AND decision_manifest_sha256 IS NULL) OR (status NOT IN ('approved', 'partially_approved', 'rejected', 'returned') AND decided_at IS NULL AND decision_manifest_sha256 IS NULL)", name="ck_approval_steps_decision_state"),
        sa.CheckConstraint("version >= 0", name="ck_approval_steps_version"),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["instance_id"], ["approval_instances.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["predecessor_step_id", "instance_id"],
            ["approval_steps.id", "approval_steps.instance_id"],
            name="fk_approval_steps_predecessor_instance",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "instance_id", name="uq_approval_steps_id_instance"),
        sa.UniqueConstraint("instance_id", "step_no", "attempt_no", name="uq_approval_steps_attempt"),
    )
    op.create_table(
        "approval_step_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("candidate_kind", sa.String(length=20), nullable=False),
        sa.Column("snapshot_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("candidate_kind IN ('assignee', 'registrar', 'verifier')", name="ck_approval_step_candidates_kind"),
        sa.CheckConstraint("authorization_version > 0", name="ck_approval_step_candidates_authorization_version"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["role_assignment_id"], ["role_assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["step_id"], ["approval_steps.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("step_id", "user_id", "candidate_kind", name="uq_approval_step_candidates_user_kind"),
    )
    op.create_table(
        "material_request_commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("target_version", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_reference", sa.String(length=160), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("request_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column("result_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("actor_person_id", sa.Uuid(), nullable=False),
        sa.Column("actor_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("operation IN ('create', 'update_draft', 'submit', 'region_decide', 'headquarters_decide', 'register_external', 'verify_external', 'withdraw', 'cancel', 'propose_substitution', 'confirm_substitution', 'reject_substitution', 'create_supply_task', 'update_supply_task', 'cancel_supply_task')", name="ck_material_request_commands_operation"),
        sa.CheckConstraint("length(idempotency_key_hash) = 64 AND length(request_hash) = 64 AND length(result_hash) = 64", name="ck_material_request_commands_hashes"),
        sa.CheckConstraint("authorization_version > 0", name="ck_material_request_commands_authorization_version"),
        sa.CheckConstraint("target_version >= 0", name="ck_material_request_commands_target_version"),
        sa.CheckConstraint("occurred_at = created_at", name="ck_material_request_commands_time"),
        sa.ForeignKeyConstraint(["actor_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_role_assignment_id"], ["role_assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["request_id"], ["material_requests.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key_hash", name="uq_material_request_commands_idempotency"),
        sa.UniqueConstraint("request_id", "target_version", name="uq_material_request_commands_target_version"),
    )
    op.create_table(
        "approval_external_registrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=False),
        sa.Column("registration_no", sa.String(length=100), nullable=False),
        sa.Column("external_action", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("evidence_file_id", sa.Uuid(), nullable=False),
        sa.Column("external_approver_snapshot_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column("external_decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("registered_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("registered_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("registered_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("verified_by_person_id", sa.Uuid(), nullable=True),
        sa.Column("verified_role_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("verified_authorization_version", sa.BigInteger(), nullable=True),
        sa.Column("verification_comment", sa.Text(), server_default="", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending_verification', 'accepted', 'rejected', 'superseded')", name="ck_approval_external_registrations_status"),
        sa.CheckConstraint("external_action IN ('approve', 'partial_approve', 'reject', 'return')", name="ck_approval_external_registrations_action"),
        sa.CheckConstraint("authorization_version > 0", name="ck_approval_external_registrations_authorization_version"),
        sa.CheckConstraint("length(decision_manifest_sha256) = 64", name="ck_approval_external_registrations_manifest"),
        sa.CheckConstraint("(status = 'pending_verification' AND verified_by_user_id IS NULL AND verified_by_person_id IS NULL AND verified_role_assignment_id IS NULL AND verified_authorization_version IS NULL AND verified_at IS NULL) OR (status <> 'pending_verification' AND verified_by_user_id IS NOT NULL AND verified_by_person_id IS NOT NULL AND verified_role_assignment_id IS NOT NULL AND verified_authorization_version > 0 AND verified_at IS NOT NULL)", name="ck_approval_external_registrations_verification_state"),
        sa.CheckConstraint("verified_by_user_id IS NULL OR (verified_by_user_id <> registered_by_user_id AND verified_by_person_id <> registered_by_person_id)", name="ck_approval_external_registrations_two_person"),
        sa.CheckConstraint("verified_at IS NULL OR verified_at >= registered_at", name="ck_approval_external_registrations_time_order"),
        sa.CheckConstraint("version >= 0", name="ck_approval_external_registrations_version"),
        sa.ForeignKeyConstraint(["evidence_file_id"], ["files.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["registered_by_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["registered_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["registered_role_assignment_id"], ["role_assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["step_id"], ["approval_steps.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["verified_by_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["verified_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["verified_role_assignment_id"], ["role_assignments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("registration_no", name="uq_approval_external_registrations_number"),
    )
    op.create_table(
        "approval_external_registration_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("registration_id", sa.Uuid(), nullable=False),
        sa.Column("request_line_id", sa.Uuid(), nullable=False),
        sa.Column("input_qty", QUANTITY, nullable=False),
        sa.Column("approved_qty", QUANTITY, nullable=False),
        sa.Column("rejected_qty", QUANTITY, nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("input_qty > 0 AND approved_qty >= 0 AND rejected_qty >= 0 AND approved_qty + rejected_qty = input_qty", name="ck_approval_external_registration_lines_quantities"),
        sa.ForeignKeyConstraint(["registration_id"], ["approval_external_registrations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["request_line_id"], ["material_request_lines.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("registration_id", "request_line_id", name="uq_approval_external_registration_lines_line"),
    )
    op.create_table(
        "approval_step_line_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=False),
        sa.Column("request_line_id", sa.Uuid(), nullable=False),
        sa.Column("input_qty", QUANTITY, nullable=False),
        sa.Column("approved_qty", QUANTITY, nullable=False),
        sa.Column("rejected_qty", QUANTITY, nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("decision_source", sa.String(length=32), nullable=False),
        sa.Column("external_registration_id", sa.Uuid(), nullable=True),
        sa.Column("decided_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("decided_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("decided_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("input_qty > 0 AND approved_qty >= 0 AND rejected_qty >= 0 AND approved_qty + rejected_qty = input_qty", name="ck_approval_step_line_decisions_quantities"),
        sa.CheckConstraint("decision_source IN ('internal', 'external_registration', 'direct_star')", name="ck_approval_step_line_decisions_source"),
        sa.CheckConstraint("(decision_source = 'external_registration' AND external_registration_id IS NOT NULL) OR (decision_source <> 'external_registration' AND external_registration_id IS NULL)", name="ck_approval_step_line_decisions_external_binding"),
        sa.CheckConstraint("authorization_version > 0", name="ck_approval_step_line_decisions_authorization_version"),
        sa.ForeignKeyConstraint(["decided_by_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_role_assignment_id"], ["role_assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["external_registration_id"], ["approval_external_registrations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["request_line_id"], ["material_request_lines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["step_id"], ["approval_steps.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("step_id", "request_line_id", name="uq_approval_step_line_decisions_line"),
    )
    op.create_table(
        "approval_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=True),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("actor_person_id", sa.Uuid(), nullable=False),
        sa.Column("actor_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("source_mode", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), server_default="", nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action IN ('submit', 'approve', 'partial_approve', 'reject', 'return', 'withdraw', 'cancel', 'register_external_evidence', 'verify_external_accept', 'verify_external_reject')", name="ck_approval_actions_action"),
        sa.CheckConstraint("source_mode IN ('internal', 'external_registration', 'direct_star')", name="ck_approval_actions_source_mode"),
        sa.CheckConstraint("authorization_version > 0", name="ck_approval_actions_authorization_version"),
        sa.ForeignKeyConstraint(["actor_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_role_assignment_id"], ["role_assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["command_id"], ["material_request_commands.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["instance_id"], ["approval_instances.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["step_id"], ["approval_steps.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_id", name="uq_approval_actions_command"),
    )
    op.create_table(
        "substitution_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_line_id", sa.Uuid(), nullable=False),
        sa.Column("substitution_id", sa.Uuid(), nullable=False),
        sa.Column("original_approved_qty", QUANTITY, nullable=False),
        sa.Column("ratio", RATIO, nullable=False),
        sa.Column("substitute_qty", QUANTITY, nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("proposed_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("proposed_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("proposed_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("decided_by_person_id", sa.Uuid(), nullable=True),
        sa.Column("decided_role_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("decided_authorization_version", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('proposed', 'confirmed', 'rejected', 'cancelled', 'superseded')", name="ck_substitution_decisions_status"),
        sa.CheckConstraint("original_approved_qty > 0 AND ratio > 0 AND substitute_qty > 0", name="ck_substitution_decisions_quantities"),
        sa.CheckConstraint("authorization_version > 0", name="ck_substitution_decisions_authorization_version"),
        sa.CheckConstraint("(status = 'proposed' AND decided_by_user_id IS NULL AND decided_by_person_id IS NULL AND decided_role_assignment_id IS NULL AND decided_authorization_version IS NULL AND decided_at IS NULL) OR (status <> 'proposed' AND decided_by_user_id IS NOT NULL AND decided_by_person_id IS NOT NULL AND decided_role_assignment_id IS NOT NULL AND decided_authorization_version > 0 AND decided_at IS NOT NULL)", name="ck_substitution_decisions_decision_state"),
        sa.CheckConstraint("version >= 0", name="ck_substitution_decisions_version"),
        sa.ForeignKeyConstraint(["decided_by_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_role_assignment_id"], ["role_assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["proposed_by_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["proposed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["proposed_role_assignment_id"], ["role_assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["request_line_id"], ["material_request_lines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["substitution_id"], ["material_substitutions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "supply_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_no", sa.String(length=100), nullable=False),
        sa.Column("request_line_id", sa.Uuid(), nullable=False),
        sa.Column("substitution_decision_id", sa.Uuid(), nullable=True),
        sa.Column("supply_type", sa.String(length=40), nullable=False),
        sa.Column("reference_no", sa.String(length=160), nullable=True),
        sa.Column("expected_qty", QUANTITY, nullable=False),
        sa.Column("original_equivalent_qty", QUANTITY, nullable=False),
        sa.Column("expected_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("created_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("cancelled_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("supply_type IN ('cross_region_transfer', 'headquarters_replenishment', 'star_replenishment', 'external_procurement_reference')", name="ck_supply_tasks_type"),
        sa.CheckConstraint("status IN ('open', 'reference_registered', 'awaiting_supply', 'cancelled', 'closed_no_supply')", name="ck_supply_tasks_status"),
        sa.CheckConstraint("expected_qty > 0 AND original_equivalent_qty > 0", name="ck_supply_tasks_quantities"),
        sa.CheckConstraint("authorization_version > 0", name="ck_supply_tasks_authorization_version"),
        sa.CheckConstraint("status <> 'reference_registered' OR reference_no IS NOT NULL", name="ck_supply_tasks_reference_state"),
        sa.CheckConstraint("(status = 'cancelled' AND cancelled_at IS NOT NULL AND cancelled_by_user_id IS NOT NULL) OR (status <> 'cancelled' AND cancelled_at IS NULL AND cancelled_by_user_id IS NULL)", name="ck_supply_tasks_cancellation_state"),
        sa.CheckConstraint("version >= 0", name="ck_supply_tasks_version"),
        sa.ForeignKeyConstraint(["cancelled_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_role_assignment_id"], ["role_assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["request_line_id"], ["material_request_lines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["substitution_decision_id"], ["substitution_decisions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_no", name="uq_supply_tasks_number"),
    )


def _create_indexes() -> None:
    def index(name: str, table: str, columns: list[str], *, unique: bool = False) -> None:
        op.create_index(name, table, columns, unique=unique)

    index("ix_oam_work_orders_status", "oam_work_orders", ["status"])
    index("ix_oam_work_orders_org_status", "oam_work_orders", ["organization_id", "status"])
    index("ix_oam_work_orders_engineer_status", "oam_work_orders", ["engineer_person_id", "status"])
    index("ix_material_substitutions_material_id", "material_substitutions", ["material_id"])
    index("ix_material_substitutions_substitute_material_id", "material_substitutions", ["substitute_material_id"])
    index("ix_material_substitutions_status", "material_substitutions", ["status"])
    op.create_index(
        "uq_material_substitutions_current_pair",
        "material_substitutions",
        ["material_id", "substitute_material_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND valid_to IS NULL"),
        sqlite_where=sa.text("status = 'active' AND valid_to IS NULL"),
    )
    index("ix_approval_route_versions_route_code", "approval_route_versions", ["route_code"])
    index("ix_approval_route_versions_status", "approval_route_versions", ["status"])
    op.create_index(
        "uq_approval_route_versions_current",
        "approval_route_versions",
        ["route_code"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND effective_to IS NULL"),
        sqlite_where=sa.text("status = 'active' AND effective_to IS NULL"),
    )
    index("ix_approval_route_step_defs_route_version_id", "approval_route_step_defs", ["route_version_id"])
    index("ix_material_requests_request_no", "material_requests", ["request_no"])
    index("ix_material_requests_requester_user_id", "material_requests", ["requester_user_id"])
    index("ix_material_requests_requester_person_id", "material_requests", ["requester_person_id"])
    index("ix_material_requests_requester_org_id", "material_requests", ["requester_org_id"])
    index("ix_material_requests_work_order_id", "material_requests", ["work_order_id"])
    index("ix_material_requests_status", "material_requests", ["status"])
    index("ix_material_requests_requester_status", "material_requests", ["requester_person_id", "status"])
    index("ix_material_requests_org_status", "material_requests", ["requester_org_id", "status"])
    index("ix_material_request_revisions_request_id", "material_request_revisions", ["request_id"])
    index("ix_material_request_revisions_status", "material_request_revisions", ["status"])
    op.create_index(
        "uq_material_request_revisions_current_draft",
        "material_request_revisions",
        ["request_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
        sqlite_where=sa.text("status = 'draft'"),
    )
    index("ix_material_request_lines_request_id", "material_request_lines", ["request_id"])
    index("ix_material_request_lines_revision_id", "material_request_lines", ["revision_id"])
    index("ix_material_request_lines_status", "material_request_lines", ["status"])
    index("ix_material_request_lines_material", "material_request_lines", ["material_id"])
    index("ix_material_request_lines_suggested_substitute", "material_request_lines", ["suggested_substitute_material_id"])
    index("ix_material_request_files_request_id", "material_request_files", ["request_id"])
    index("ix_material_request_files_revision_id", "material_request_files", ["revision_id"])
    index("ix_material_request_files_file_id", "material_request_files", ["file_id"])
    index("ix_material_request_files_request", "material_request_files", ["request_id", "created_at"])
    op.create_index(
        "uq_material_request_files_header",
        "material_request_files",
        ["revision_id", "file_id"],
        unique=True,
        postgresql_where=sa.text("request_line_id IS NULL"),
        sqlite_where=sa.text("request_line_id IS NULL"),
    )
    op.create_index(
        "uq_material_request_files_line",
        "material_request_files",
        ["request_line_id", "file_id"],
        unique=True,
        postgresql_where=sa.text("request_line_id IS NOT NULL"),
        sqlite_where=sa.text("request_line_id IS NOT NULL"),
    )
    index("ix_approval_instances_request_id", "approval_instances", ["request_id"])
    index("ix_approval_instances_request_revision_id", "approval_instances", ["request_revision_id"])
    index("ix_approval_instances_status", "approval_instances", ["status"])
    op.create_index(
        "uq_approval_instances_current_request",
        "approval_instances",
        ["request_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    index("ix_approval_steps_instance_id", "approval_steps", ["instance_id"])
    index("ix_approval_steps_status", "approval_steps", ["status"])
    index("ix_approval_steps_instance_status", "approval_steps", ["instance_id", "status"])
    index("ix_approval_step_candidates_step_id", "approval_step_candidates", ["step_id"])
    index("ix_material_request_commands_request_id", "material_request_commands", ["request_id"])
    index("ix_material_request_commands_request_time", "material_request_commands", ["request_id", "occurred_at"])
    index("ix_approval_external_registrations_step_id", "approval_external_registrations", ["step_id"])
    index("ix_approval_external_registrations_registration_no", "approval_external_registrations", ["registration_no"])
    index("ix_approval_external_registrations_status", "approval_external_registrations", ["status"])
    op.create_index(
        "uq_approval_external_registrations_pending_step",
        "approval_external_registrations",
        ["step_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending_verification'"),
        sqlite_where=sa.text("status = 'pending_verification'"),
    )
    op.create_index(
        "uq_approval_external_registrations_accepted_step",
        "approval_external_registrations",
        ["step_id"],
        unique=True,
        postgresql_where=sa.text("status = 'accepted'"),
        sqlite_where=sa.text("status = 'accepted'"),
    )
    index("ix_approval_external_registration_lines_registration_id", "approval_external_registration_lines", ["registration_id"])
    index("ix_approval_external_registration_lines_request_line_id", "approval_external_registration_lines", ["request_line_id"])
    index("ix_approval_step_line_decisions_step_id", "approval_step_line_decisions", ["step_id"])
    index("ix_approval_step_line_decisions_request_line_id", "approval_step_line_decisions", ["request_line_id"])
    index("ix_approval_actions_instance_id", "approval_actions", ["instance_id"])
    index("ix_approval_actions_action", "approval_actions", ["action"])
    index("ix_substitution_decisions_request_line_id", "substitution_decisions", ["request_line_id"])
    index("ix_substitution_decisions_status", "substitution_decisions", ["status"])
    op.create_index(
        "uq_substitution_decisions_current_line",
        "substitution_decisions",
        ["request_line_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('proposed', 'confirmed')"),
        sqlite_where=sa.text("status IN ('proposed', 'confirmed')"),
    )
    index("ix_supply_tasks_task_no", "supply_tasks", ["task_no"])
    index("ix_supply_tasks_request_line_id", "supply_tasks", ["request_line_id"])
    index("ix_supply_tasks_supply_type", "supply_tasks", ["supply_type"])
    index("ix_supply_tasks_status", "supply_tasks", ["status"])
    index("ix_supply_tasks_line_status", "supply_tasks", ["request_line_id", "status"])


def _seed_permissions_and_route() -> None:
    seeded_at = datetime(2026, 8, 31, tzinfo=timezone.utc)
    permission_table = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("resource", sa.String(length=100)),
        sa.column("action", sa.String(length=80)),
        sa.column("field_code", sa.String(length=100)),
        sa.column("description", sa.String(length=300)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        permission_table,
        [
            {
                "id": permission_id,
                "resource": resource,
                "action": action,
                "field_code": field_code,
                "description": description,
                "created_at": seeded_at,
                "updated_at": seeded_at,
            }
            for permission_id, resource, action, field_code, description in PERMISSION_ROWS
        ],
    )
    role_permission_table = sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("effect", sa.String(length=12)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        role_permission_table,
        [
            {
                "id": row_id,
                "role_id": role_id,
                "permission_id": permission_id,
                "effect": "allow",
                "created_at": seeded_at,
            }
            for row_id, role_id, permission_id in ROLE_PERMISSION_ROWS
        ],
    )
    route_table = sa.table(
        "approval_route_versions",
        sa.column("id", sa.Uuid()),
        sa.column("route_code", sa.String(length=80)),
        sa.column("version", sa.Integer()),
        sa.column("approval_mode", sa.String(length=32)),
        sa.column("effective_from", sa.DateTime(timezone=True)),
        sa.column("effective_to", sa.DateTime(timezone=True)),
        sa.column("status", sa.String(length=20)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        route_table,
        [
            {
                "id": ROUTE_VERSION_ID,
                "route_code": "material_request_three_stage",
                "version": 1,
                "approval_mode": "external_registration",
                "effective_from": seeded_at,
                "effective_to": None,
                "status": "active",
                "created_at": seeded_at,
                "updated_at": seeded_at,
            }
        ],
    )
    route_step_table = sa.table(
        "approval_route_step_defs",
        sa.column("id", sa.Uuid()),
        sa.column("route_version_id", sa.Uuid()),
        sa.column("step_no", sa.Integer()),
        sa.column("role_code", sa.String(length=80)),
        sa.column("source_mode", sa.String(length=32)),
        sa.column("scope_type", sa.String(length=24)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        route_step_table,
        [
            {
                "id": step_id,
                "route_version_id": ROUTE_VERSION_ID,
                "step_no": step_no,
                "role_code": role_code,
                "source_mode": source_mode,
                "scope_type": scope_type,
                "created_at": seeded_at,
            }
            for step_id, step_no, role_code, source_mode, scope_type in ROUTE_STEP_ROWS
        ],
    )


def _assert_downgrade_is_empty() -> None:
    connection = op.get_bind()
    for table_name in BUSINESS_TABLES:
        count = connection.execute(
            sa.text(f"SELECT COUNT(*) FROM {table_name}")
        ).scalar_one()
        if count:
            raise RuntimeError(DOWNGRADE_BLOCKER)


def _delete_static_rows() -> None:
    route_step_table = sa.table(
        "approval_route_step_defs", sa.column("id", sa.Uuid())
    )
    route_table = sa.table("approval_route_versions", sa.column("id", sa.Uuid()))
    role_permission_table = sa.table(
        "role_permissions", sa.column("id", sa.Uuid())
    )
    permission_table = sa.table("permissions", sa.column("id", sa.Uuid()))
    op.execute(
        route_step_table.delete().where(
            route_step_table.c.id.in_(tuple(row[0] for row in ROUTE_STEP_ROWS))
        )
    )
    op.execute(route_table.delete().where(route_table.c.id == ROUTE_VERSION_ID))
    op.execute(
        role_permission_table.delete().where(
            role_permission_table.c.id.in_(
                tuple(row[0] for row in ROLE_PERMISSION_ROWS)
            )
        )
    )
    op.execute(
        permission_table.delete().where(
            permission_table.c.id.in_(tuple(row[0] for row in PERMISSION_ROWS))
        )
    )


def _drop_tables() -> None:
    for table_name in (
        "supply_tasks",
        "substitution_decisions",
        "approval_actions",
        "approval_step_line_decisions",
        "approval_external_registration_lines",
        "approval_external_registrations",
        "material_request_commands",
        "approval_step_candidates",
        "approval_steps",
        "approval_instances",
        "material_request_files",
        "material_request_lines",
        "material_request_revisions",
        "material_requests",
        "approval_route_step_defs",
        "approval_route_versions",
        "material_substitutions",
        "oam_work_orders",
    ):
        op.drop_table(table_name)


def _create_postgresql_guards() -> None:
    request_contact_invalid = _postgresql_contact_envelope_invalid(
        "NEW.contact_snapshot_jsonb", "NEW.id", "NEW.requester_person_id"
    )
    request_masked_invalid = _postgresql_masked_projections_invalid(
        "NEW.address_masked_jsonb", "NEW.contact_masked_jsonb"
    )
    revision_contact_invalid = _postgresql_contact_envelope_invalid(
        "NEW.contact_snapshot_jsonb", "NEW.request_id", "requester_person"
    )
    revision_masked_invalid = _postgresql_masked_projections_invalid(
        "NEW.address_masked_jsonb", "NEW.contact_masked_jsonb"
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_APPEND_ONLY_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION '{GUARD_ERROR}';
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_REQUEST_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF {request_contact_invalid}
       OR {request_masked_invalid} THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.allocation_status <> 'not_allocated'
           OR NEW.reservation_status <> 'not_reserved'
           OR NEW.outbound_status <> 'not_started'
           OR NEW.shipment_status <> 'not_started'
           OR NEW.logistics_signature_status <> 'not_signed'
           OR NEW.oam_receipt_status <> 'not_occurred'
           OR NEW.personal_inbound_status <> 'not_started'
           OR NEW.notification_status <> 'not_started'
           OR NEW.reconciliation_status <> 'not_started' THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.request_no IS DISTINCT FROM OLD.request_no
       OR NEW.requester_user_id IS DISTINCT FROM OLD.requester_user_id
       OR NEW.requester_person_id IS DISTINCT FROM OLD.requester_person_id
       OR NEW.requester_org_id IS DISTINCT FROM OLD.requester_org_id
       OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR (OLD.submitted_at IS NOT NULL
           AND NEW.submitted_at IS DISTINCT FROM OLD.submitted_at)
       OR (OLD.submitted_at IS NOT NULL
           AND NEW.approval_mode IS DISTINCT FROM OLD.approval_mode) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF NEW.revision_no IS DISTINCT FROM OLD.revision_no THEN
        IF OLD.status <> 'returned'
           OR NEW.status <> 'returned'
           OR NEW.revision_no <> OLD.revision_no + 1
           OR NOT EXISTS (
               SELECT 1
                 FROM public.material_request_revisions AS revision
                WHERE revision.request_id = NEW.id
                  AND revision.revision_no = NEW.revision_no
                  AND revision.status = 'draft'
                  AND revision.work_order_id IS NOT DISTINCT FROM NEW.work_order_id
                  AND revision.purpose = NEW.purpose
                  AND revision.urgency = NEW.urgency
                  AND revision.expected_date IS NOT DISTINCT FROM NEW.expected_date
                  AND revision.address_snapshot_jsonb = NEW.address_snapshot_jsonb
                  AND revision.address_masked_jsonb = NEW.address_masked_jsonb
                  AND revision.contact_snapshot_jsonb = NEW.contact_snapshot_jsonb
                  AND revision.contact_masked_jsonb = NEW.contact_masked_jsonb
                  AND revision.note = NEW.note
                  AND revision.approval_mode = NEW.approval_mode
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;
    IF NEW.work_order_id IS DISTINCT FROM OLD.work_order_id
       OR NEW.purpose IS DISTINCT FROM OLD.purpose
       OR NEW.urgency IS DISTINCT FROM OLD.urgency
       OR NEW.expected_date IS DISTINCT FROM OLD.expected_date
       OR NEW.address_snapshot_jsonb IS DISTINCT FROM OLD.address_snapshot_jsonb
       OR NEW.address_masked_jsonb IS DISTINCT FROM OLD.address_masked_jsonb
       OR NEW.contact_snapshot_jsonb IS DISTINCT FROM OLD.contact_snapshot_jsonb
       OR NEW.contact_masked_jsonb IS DISTINCT FROM OLD.contact_masked_jsonb
       OR NEW.note IS DISTINCT FROM OLD.note THEN
        IF NOT EXISTS (
            SELECT 1
              FROM public.material_request_revisions AS revision
             WHERE revision.request_id = NEW.id
               AND revision.revision_no = NEW.revision_no
               AND revision.status = 'draft'
               AND revision.work_order_id IS NOT DISTINCT FROM NEW.work_order_id
               AND revision.purpose = NEW.purpose
               AND revision.urgency = NEW.urgency
               AND revision.expected_date IS NOT DISTINCT FROM NEW.expected_date
               AND revision.address_snapshot_jsonb = NEW.address_snapshot_jsonb
               AND revision.address_masked_jsonb = NEW.address_masked_jsonb
               AND revision.contact_snapshot_jsonb = NEW.contact_snapshot_jsonb
               AND revision.contact_masked_jsonb = NEW.contact_masked_jsonb
               AND revision.note = NEW.note
               AND revision.approval_mode = NEW.approval_mode
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;
    IF OLD.status IN ('draft', 'returned')
       AND NEW.status IN ('submitted', 'approval_in_progress')
       AND NOT EXISTS (
           SELECT 1 FROM public.material_request_revisions AS revision
            WHERE revision.request_id = NEW.id
              AND revision.revision_no = NEW.revision_no
              AND revision.status = 'sealed'
       ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF NEW.allocation_status <> 'not_allocated'
       OR NEW.reservation_status <> 'not_reserved'
       OR NEW.outbound_status <> 'not_started'
       OR NEW.shipment_status <> 'not_started'
       OR NEW.logistics_signature_status <> 'not_signed'
       OR NEW.oam_receipt_status <> 'not_occurred'
       OR NEW.personal_inbound_status <> 'not_started'
       OR NEW.notification_status <> 'not_started'
       OR NEW.reconciliation_status <> 'not_started' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_REVISION_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    requester_person uuid;
    current_revision_no integer;
    request_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT request.requester_person_id, request.revision_no, request.status
      INTO requester_person, current_revision_no, request_status
      FROM public.material_requests AS request
     WHERE request.id = NEW.request_id;
    IF NOT FOUND OR {revision_contact_invalid}
       OR {revision_masked_invalid} THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft'
           OR (NEW.revision_no = 1 AND (
               NEW.previous_revision_id IS NOT NULL
               OR current_revision_no <> 1
               OR request_status <> 'draft'
               OR EXISTS (
                   SELECT 1 FROM public.material_request_revisions AS existing
                    WHERE existing.request_id = NEW.request_id
               )
           ))
           OR (NEW.revision_no > 1 AND (
               NEW.revision_no <> current_revision_no + 1
               OR request_status <> 'returned'
               OR NOT EXISTS (
                   SELECT 1
                     FROM public.material_request_revisions AS previous
                     JOIN public.approval_instances AS instance
                       ON instance.request_revision_id = previous.id
                    WHERE previous.id = NEW.previous_revision_id
                      AND previous.request_id = NEW.request_id
                      AND previous.revision_no = NEW.revision_no - 1
                      AND previous.status = 'sealed'
                      AND instance.status IN ('returned', 'superseded')
               )
           )) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.request_id IS DISTINCT FROM OLD.request_id
       OR NEW.revision_no IS DISTINCT FROM OLD.revision_no
       OR NEW.previous_revision_id IS DISTINCT FROM OLD.previous_revision_id
       OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR OLD.status = 'sealed'
       OR NEW.status NOT IN ('draft', 'sealed')
       OR current_revision_no <> NEW.revision_no
       OR request_status NOT IN ('draft', 'returned', 'approval_in_progress') THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF NEW.status = 'sealed' THEN
        IF NEW.content_manifest_sha256 !~ '^[0-9a-f]{{64}}$'
           OR NEW.sealed_at IS NULL
           OR NEW.sealed_by_user_id IS NULL
           OR NOT EXISTS (
               SELECT 1 FROM public.material_request_lines AS line
                WHERE line.revision_id = NEW.id
                  AND line.request_id = NEW.request_id
                  AND line.revision_no = NEW.revision_no
           )
           OR NOT EXISTS (
               SELECT 1 FROM public.material_requests AS request
                WHERE request.id = NEW.request_id
                  AND request.revision_no = NEW.revision_no
                  AND request.work_order_id IS NOT DISTINCT FROM NEW.work_order_id
                  AND request.purpose = NEW.purpose
                  AND request.urgency = NEW.urgency
                  AND request.expected_date IS NOT DISTINCT FROM NEW.expected_date
                  AND request.address_snapshot_jsonb = NEW.address_snapshot_jsonb
                  AND request.address_masked_jsonb = NEW.address_masked_jsonb
                  AND request.contact_snapshot_jsonb = NEW.contact_snapshot_jsonb
                  AND request.contact_masked_jsonb = NEW.contact_masked_jsonb
                  AND request.note = NEW.note
                  AND request.approval_mode = NEW.approval_mode
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSIF NEW.content_manifest_sha256 IS NOT NULL
       OR NEW.sealed_at IS NOT NULL
       OR NEW.sealed_by_user_id IS NOT NULL THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_INSTANCE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (
            SELECT 1
              FROM public.material_request_revisions AS revision
              JOIN public.material_requests AS request
                ON request.id = revision.request_id
             WHERE revision.id = NEW.request_revision_id
               AND revision.request_id = NEW.request_id
               AND revision.revision_no = NEW.revision_no
               AND revision.status = 'sealed'
               AND request.revision_no = NEW.revision_no
               AND request.status IN ('draft', 'returned', 'submitted',
                                      'approval_in_progress')
        )
        OR (NEW.attempt_no > 1 AND NOT EXISTS (
            SELECT 1
              FROM public.approval_instances AS previous
             WHERE previous.request_id = NEW.request_id
               AND previous.attempt_no = NEW.attempt_no - 1
               AND previous.status IN ('returned', 'superseded')
        )) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.request_id IS DISTINCT FROM OLD.request_id
       OR NEW.request_revision_id IS DISTINCT FROM OLD.request_revision_id
       OR NEW.revision_no IS DISTINCT FROM OLD.revision_no
       OR NEW.route_version_id IS DISTINCT FROM OLD.route_version_id
       OR NEW.attempt_no IS DISTINCT FROM OLD.attempt_no
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR OLD.status IN ('completed', 'rejected', 'withdrawn', 'cancelled', 'superseded')
       OR (OLD.status = 'returned' AND NEW.status <> 'superseded') THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_REQUEST_LINE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF NOT EXISTS (
            SELECT 1
              FROM public.material_request_revisions AS revision
              JOIN public.material_requests AS request
                ON request.id = revision.request_id
             WHERE revision.id = OLD.revision_id
               AND revision.request_id = OLD.request_id
               AND revision.revision_no = OLD.revision_no
               AND revision.status = 'draft'
               AND request.revision_no = OLD.revision_no
               AND request.status IN ('draft', 'returned')
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (
            SELECT 1
              FROM public.material_request_revisions AS revision
              JOIN public.material_requests AS request
                ON request.id = revision.request_id
             WHERE revision.id = NEW.revision_id
               AND revision.request_id = NEW.request_id
               AND revision.revision_no = NEW.revision_no
               AND revision.status = 'draft'
               AND request.revision_no = NEW.revision_no
               AND request.status IN ('draft', 'returned')
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.id IS DISTINCT FROM NEW.id
       OR OLD.request_id IS DISTINCT FROM NEW.request_id
       OR OLD.revision_id IS DISTINCT FROM NEW.revision_id
       OR OLD.revision_no IS DISTINCT FROM NEW.revision_no
       OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF OLD.line_no IS DISTINCT FROM NEW.line_no
       OR OLD.client_line_key IS DISTINCT FROM NEW.client_line_key
       OR OLD.material_id IS DISTINCT FROM NEW.material_id
       OR OLD.suggested_substitute_material_id IS DISTINCT FROM NEW.suggested_substitute_material_id
       OR OLD.requested_qty IS DISTINCT FROM NEW.requested_qty
       OR OLD.required_date IS DISTINCT FROM NEW.required_date
       OR OLD.note IS DISTINCT FROM NEW.note THEN
        IF NOT EXISTS (
            SELECT 1
              FROM public.material_request_revisions AS revision
              JOIN public.material_requests AS request
                ON request.id = revision.request_id
             WHERE revision.id = NEW.revision_id
               AND revision.request_id = NEW.request_id
               AND revision.revision_no = NEW.revision_no
               AND revision.status = 'draft'
               AND request.revision_no = NEW.revision_no
               AND request.status IN ('draft', 'returned')
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;
    RETURN NEW;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_REQUEST_FILE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF NOT EXISTS (
            SELECT 1
              FROM public.material_request_revisions AS revision
              JOIN public.material_requests AS request
                ON request.id = revision.request_id
             WHERE revision.id = OLD.revision_id
               AND revision.request_id = OLD.request_id
               AND revision.revision_no = OLD.revision_no
               AND revision.status = 'draft'
               AND request.revision_no = OLD.revision_no
               AND request.status IN ('draft', 'returned')
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN OLD;
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM public.material_request_revisions AS revision
          JOIN public.material_requests AS request
            ON request.id = revision.request_id
         WHERE revision.id = NEW.revision_id
           AND revision.request_id = NEW.request_id
           AND revision.revision_no = NEW.revision_no
           AND revision.status = 'draft'
           AND request.revision_no = NEW.revision_no
           AND request.status IN ('draft', 'returned')
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.files AS file
         WHERE file.id = NEW.file_id AND file.status = 'available'
    ) OR EXISTS (
        SELECT 1 FROM public.approval_external_registrations AS registration
         WHERE registration.evidence_file_id = NEW.file_id
    ) OR EXISTS (
        SELECT 1 FROM public.approval_delegations AS delegation
         WHERE delegation.evidence_file_id = NEW.file_id
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_DELEGATION_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.evidence_file_id IS NULL
           OR btrim(NEW.reason) = ''
           OR length(NEW.scope_hash) <> 64
           OR NEW.status NOT IN ('scheduled', 'active')
           OR NEW.revoked_by IS NOT NULL
           OR NEW.revoked_at IS NOT NULL
           OR NOT EXISTS (
               SELECT 1 FROM public.files AS file
                WHERE file.id = NEW.evidence_file_id
                  AND file.status = 'available'
           )
           OR EXISTS (
               SELECT 1 FROM public.material_request_files AS binding
                WHERE binding.file_id = NEW.evidence_file_id
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.from_user_id IS DISTINCT FROM OLD.from_user_id
       OR NEW.to_user_id IS DISTINCT FROM OLD.to_user_id
       OR NEW.scope_jsonb IS DISTINCT FROM OLD.scope_jsonb
       OR NEW.scope_hash IS DISTINCT FROM OLD.scope_hash
       OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
       OR NEW.valid_to IS DISTINCT FROM OLD.valid_to
       OR NEW.evidence_file_id IS DISTINCT FROM OLD.evidence_file_id
       OR NEW.reason IS DISTINCT FROM OLD.reason
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR OLD.status IN ('revoked', 'expired')
       OR (OLD.status = 'scheduled' AND NEW.status NOT IN ('active', 'revoked', 'expired'))
       OR (OLD.status = 'active' AND NEW.status NOT IN ('revoked', 'expired'))
       OR (NEW.status = 'revoked' AND
           (NEW.revoked_by IS NULL OR NEW.revoked_at IS NULL))
       OR (NEW.status <> 'revoked' AND
           (NEW.revoked_by IS NOT NULL OR NEW.revoked_at IS NOT NULL)) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_EXTERNAL_REGISTRATION_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    predecessor_id uuid;
    requester_user text;
    requester_person uuid;
    expected_count bigint;
    actual_count bigint;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT step.predecessor_step_id,
           request.requester_user_id,
           request.requester_person_id
      INTO predecessor_id, requester_user, requester_person
      FROM public.approval_steps AS step
      JOIN public.approval_instances AS instance ON instance.id = step.instance_id
      JOIN public.material_requests AS request ON request.id = instance.request_id
     WHERE step.id = COALESCE(NEW.step_id, OLD.step_id)
       AND step.step_no = 3
       AND step.source_mode = 'external_registration';
    IF NOT FOUND THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'pending_verification'
           OR NEW.registered_by_user_id = requester_user
           OR NEW.registered_by_person_id = requester_person
           OR NOT EXISTS (
               SELECT 1 FROM public.files AS file
                WHERE file.id = NEW.evidence_file_id
                  AND file.status = 'available'
           )
           OR EXISTS (
               SELECT 1 FROM public.material_request_files AS binding
                WHERE binding.file_id = NEW.evidence_file_id
           )
           OR NOT EXISTS (
               SELECT 1 FROM public.approval_step_candidates AS candidate
                WHERE candidate.step_id = NEW.step_id
                  AND candidate.user_id = NEW.registered_by_user_id
                  AND candidate.person_id = NEW.registered_by_person_id
                  AND candidate.role_assignment_id = NEW.registered_role_assignment_id
                  AND candidate.authorization_version = NEW.authorization_version
                  AND candidate.candidate_kind = 'registrar'
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.step_id IS DISTINCT FROM OLD.step_id
       OR NEW.registration_no IS DISTINCT FROM OLD.registration_no
       OR NEW.external_action IS DISTINCT FROM OLD.external_action
       OR NEW.evidence_file_id IS DISTINCT FROM OLD.evidence_file_id
       OR NEW.external_approver_snapshot_jsonb IS DISTINCT FROM OLD.external_approver_snapshot_jsonb
       OR NEW.external_decided_at IS DISTINCT FROM OLD.external_decided_at
       OR NEW.decision_manifest_sha256 IS DISTINCT FROM OLD.decision_manifest_sha256
       OR NEW.registered_by_user_id IS DISTINCT FROM OLD.registered_by_user_id
       OR NEW.registered_by_person_id IS DISTINCT FROM OLD.registered_by_person_id
       OR NEW.registered_role_assignment_id IS DISTINCT FROM OLD.registered_role_assignment_id
       OR NEW.authorization_version IS DISTINCT FROM OLD.authorization_version
       OR NEW.registered_at IS DISTINCT FROM OLD.registered_at
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR OLD.status <> 'pending_verification'
       OR NEW.status NOT IN ('accepted', 'rejected', 'superseded')
       OR NEW.verified_by_user_id = requester_user
       OR NEW.verified_by_person_id = requester_person
       OR NOT EXISTS (
           SELECT 1 FROM public.approval_step_candidates AS candidate
            WHERE candidate.step_id = NEW.step_id
              AND candidate.user_id = NEW.verified_by_user_id
              AND candidate.person_id = NEW.verified_by_person_id
              AND candidate.role_assignment_id = NEW.verified_role_assignment_id
              AND candidate.authorization_version = NEW.verified_authorization_version
              AND candidate.candidate_kind = 'verifier'
       ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT count(*) INTO expected_count
      FROM public.approval_step_line_decisions AS decision
     WHERE decision.step_id = predecessor_id
       AND decision.approved_qty > 0;
    SELECT count(*) INTO actual_count
      FROM public.approval_external_registration_lines AS line
     WHERE line.registration_id = NEW.id;
    IF NEW.external_action IN ('approve', 'partial_approve') THEN
        IF expected_count = 0 OR actual_count <> expected_count
           OR EXISTS (
               SELECT 1
                 FROM public.approval_step_line_decisions AS predecessor
                WHERE predecessor.step_id = predecessor_id
                  AND predecessor.approved_qty > 0
                  AND NOT EXISTS (
                      SELECT 1
                        FROM public.approval_external_registration_lines AS line
                       WHERE line.registration_id = NEW.id
                         AND line.request_line_id = predecessor.request_line_id
                  )
           )
           OR (NEW.external_action = 'approve' AND EXISTS (
               SELECT 1 FROM public.approval_external_registration_lines AS line
                WHERE line.registration_id = NEW.id AND line.rejected_qty > 0
           ))
           OR (NEW.external_action = 'partial_approve' AND NOT EXISTS (
               SELECT 1 FROM public.approval_external_registration_lines AS line
                WHERE line.registration_id = NEW.id AND line.rejected_qty > 0
           )) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSIF actual_count <> 0 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    _create_postgresql_quantity_guards()
    _create_postgresql_guard_triggers()


def _create_postgresql_quantity_guards() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{PG_DECISION_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    step_number integer;
    predecessor_id uuid;
    step_source text;
    request_id uuid;
    request_revision_id uuid;
    requester_user text;
    requester_person uuid;
    expected_input numeric(18,3);
    predecessor_status text;
BEGIN
    SELECT step.step_no, step.predecessor_step_id, step.source_mode,
           instance.request_id, instance.request_revision_id,
           request.requester_user_id,
           request.requester_person_id
      INTO step_number, predecessor_id, step_source, request_id,
           request_revision_id,
           requester_user, requester_person
      FROM public.approval_steps AS step
      JOIN public.approval_instances AS instance ON instance.id = step.instance_id
      JOIN public.material_requests AS request ON request.id = instance.request_id
     WHERE step.id = NEW.step_id;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1 FROM public.material_request_lines AS line
         WHERE line.id = NEW.request_line_id
           AND line.request_id = request_id
           AND line.revision_id = request_revision_id
    ) OR NEW.decision_source <> step_source
       OR NEW.decided_by_user_id = requester_user
       OR NEW.decided_by_person_id = requester_person
       OR (NEW.rejected_qty > 0 AND btrim(NEW.reason) = '') THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF step_number = 1 THEN
        SELECT line.requested_qty INTO expected_input
          FROM public.material_request_lines AS line
         WHERE line.id = NEW.request_line_id;
    ELSE
        SELECT step.status, decision.approved_qty
          INTO predecessor_status, expected_input
          FROM public.approval_steps AS step
          JOIN public.approval_step_line_decisions AS decision
            ON decision.step_id = step.id
           AND decision.request_line_id = NEW.request_line_id
         WHERE step.id = predecessor_id;
        IF NOT FOUND OR predecessor_status NOT IN ('approved', 'partially_approved')
           OR expected_input <= 0 THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;
    IF NEW.input_qty <> expected_input THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF NEW.decision_source = 'external_registration' THEN
        IF NOT EXISTS (
            SELECT 1
              FROM public.approval_external_registrations AS registration
              JOIN public.approval_external_registration_lines AS line
                ON line.registration_id = registration.id
             WHERE registration.id = NEW.external_registration_id
               AND registration.step_id = NEW.step_id
               AND registration.status = 'accepted'
               AND line.request_line_id = NEW.request_line_id
               AND line.input_qty = NEW.input_qty
               AND line.approved_qty = NEW.approved_qty
               AND line.rejected_qty = NEW.rejected_qty
               AND line.reason = NEW.reason
        ) OR NOT EXISTS (
            SELECT 1 FROM public.approval_step_candidates AS candidate
             WHERE candidate.step_id = NEW.step_id
               AND candidate.user_id = NEW.decided_by_user_id
               AND candidate.person_id = NEW.decided_by_person_id
               AND candidate.role_assignment_id = NEW.decided_role_assignment_id
               AND candidate.authorization_version = NEW.authorization_version
               AND candidate.candidate_kind = 'verifier'
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSIF NOT EXISTS (
        SELECT 1 FROM public.approval_step_candidates AS candidate
         WHERE candidate.step_id = NEW.step_id
           AND candidate.user_id = NEW.decided_by_user_id
           AND candidate.person_id = NEW.decided_by_person_id
           AND candidate.role_assignment_id = NEW.decided_role_assignment_id
           AND candidate.authorization_version = NEW.authorization_version
           AND candidate.candidate_kind = 'assignee'
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_EXTERNAL_LINE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    predecessor_id uuid;
    expected_input numeric(18,3);
    registration_action text;
BEGIN
    SELECT step.predecessor_step_id, registration.external_action
      INTO predecessor_id, registration_action
      FROM public.approval_external_registrations AS registration
      JOIN public.approval_steps AS step ON step.id = registration.step_id
     WHERE registration.id = NEW.registration_id
       AND registration.status = 'pending_verification'
       AND step.step_no = 3
       AND step.source_mode = 'external_registration';
    IF NOT FOUND OR registration_action NOT IN ('approve', 'partial_approve') THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT decision.approved_qty INTO expected_input
      FROM public.approval_step_line_decisions AS decision
      JOIN public.approval_steps AS step ON step.id = decision.step_id
     WHERE decision.step_id = predecessor_id
       AND decision.request_line_id = NEW.request_line_id
       AND step.status IN ('approved', 'partially_approved');
    IF NOT FOUND OR expected_input <= 0 OR NEW.input_qty <> expected_input
       OR (NEW.rejected_qty > 0 AND btrim(NEW.reason) = '') THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_SUBSTITUTION_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    original_material uuid;
    requester_user text;
    requester_person uuid;
    remaining_qty numeric(18,3);
    governed_ratio numeric(18,6);
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT line.material_id, request.requester_user_id,
           request.requester_person_id,
           line.final_approved_qty - line.cancelled_qty
      INTO original_material, requester_user, requester_person, remaining_qty
      FROM public.material_request_lines AS line
      JOIN public.material_requests AS request ON request.id = line.request_id
     WHERE line.id = COALESCE(NEW.request_line_id, OLD.request_line_id)
       AND line.revision_no = request.revision_no
       AND line.status IN ('approved', 'partially_approved')
       AND request.status IN ('approved', 'partially_approved');
    IF NOT FOUND OR remaining_qty <= 0 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT substitution.ratio INTO governed_ratio
          FROM public.material_substitutions AS substitution
         WHERE substitution.id = NEW.substitution_id
           AND substitution.material_id = original_material
           AND substitution.status = 'active'
           AND substitution.valid_from <= NEW.proposed_at
           AND (substitution.valid_to IS NULL OR substitution.valid_to > NEW.proposed_at);
        IF NOT FOUND OR NEW.status <> 'proposed'
           OR NEW.original_approved_qty <> remaining_qty
           OR NEW.ratio <> governed_ratio
           OR NEW.substitute_qty <> round(NEW.original_approved_qty * NEW.ratio, 3) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.request_line_id IS DISTINCT FROM OLD.request_line_id
       OR NEW.substitution_id IS DISTINCT FROM OLD.substitution_id
       OR NEW.original_approved_qty IS DISTINCT FROM OLD.original_approved_qty
       OR NEW.ratio IS DISTINCT FROM OLD.ratio
       OR NEW.substitute_qty IS DISTINCT FROM OLD.substitute_qty
       OR NEW.proposed_by_user_id IS DISTINCT FROM OLD.proposed_by_user_id
       OR NEW.proposed_by_person_id IS DISTINCT FROM OLD.proposed_by_person_id
       OR NEW.proposed_role_assignment_id IS DISTINCT FROM OLD.proposed_role_assignment_id
       OR NEW.authorization_version IS DISTINCT FROM OLD.authorization_version
       OR NEW.proposed_at IS DISTINCT FROM OLD.proposed_at
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR OLD.status <> 'proposed'
       OR NEW.status NOT IN ('confirmed', 'rejected', 'cancelled', 'superseded')
       OR (NEW.status IN ('confirmed', 'rejected') AND
           (NEW.decided_by_user_id <> requester_user
            OR NEW.decided_by_person_id <> requester_person)) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_SUPPLY_TASK_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    remaining_qty numeric(18,3);
    active_total numeric(18,3);
    substitution_ratio numeric(18,6);
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT line.final_approved_qty - line.cancelled_qty
      INTO remaining_qty
      FROM public.material_request_lines AS line
      JOIN public.material_requests AS request ON request.id = line.request_id
     WHERE line.id = NEW.request_line_id
       AND line.revision_no = request.revision_no
       AND line.status IN ('approved', 'partially_approved')
       AND request.status IN ('approved', 'partially_approved');
    IF NOT FOUND OR remaining_qty <= 0 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF NEW.substitution_decision_id IS NULL THEN
        IF NEW.expected_qty <> NEW.original_equivalent_qty THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSE
        SELECT decision.ratio INTO substitution_ratio
          FROM public.substitution_decisions AS decision
         WHERE decision.id = NEW.substitution_decision_id
           AND decision.request_line_id = NEW.request_line_id
           AND decision.status = 'confirmed';
        IF NOT FOUND
           OR NEW.expected_qty <> round(NEW.original_equivalent_qty * substitution_ratio, 3) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;
    SELECT COALESCE(sum(task.original_equivalent_qty), 0)
      INTO active_total
      FROM public.supply_tasks AS task
     WHERE task.request_line_id = NEW.request_line_id
       AND task.status NOT IN ('cancelled', 'closed_no_supply')
       AND (TG_OP = 'INSERT' OR task.id <> NEW.id);
    IF NEW.status NOT IN ('cancelled', 'closed_no_supply') THEN
        active_total := active_total + NEW.original_equivalent_qty;
    END IF;
    IF active_total > remaining_qty THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.id IS DISTINCT FROM OLD.id
        OR NEW.request_line_id IS DISTINCT FROM OLD.request_line_id
        OR NEW.substitution_decision_id IS DISTINCT FROM OLD.substitution_decision_id
        OR NEW.supply_type IS DISTINCT FROM OLD.supply_type
        OR NEW.expected_qty IS DISTINCT FROM OLD.expected_qty
        OR NEW.original_equivalent_qty IS DISTINCT FROM OLD.original_equivalent_qty
        OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
        OR NEW.created_by_person_id IS DISTINCT FROM OLD.created_by_person_id
        OR NEW.created_role_assignment_id IS DISTINCT FROM OLD.created_role_assignment_id
        OR NEW.authorization_version IS DISTINCT FROM OLD.authorization_version
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR OLD.status IN ('cancelled', 'closed_no_supply')
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )


def _create_postgresql_guard_triggers() -> None:
    for table_name in FACT_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_immutable_0029 "
            f"BEFORE UPDATE OR DELETE ON public.{table_name} FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_APPEND_ONLY_FUNCTION}()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_no_truncate_0029 "
            f"BEFORE TRUNCATE ON public.{table_name} FOR EACH STATEMENT "
            f"EXECUTE FUNCTION public.{PG_APPEND_ONLY_FUNCTION}()"
        )
    op.execute(
        f"CREATE TRIGGER trg_material_requests_guard_0029 "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.material_requests "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_REQUEST_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_material_request_revisions_guard_0029 "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.material_request_revisions "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_REVISION_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_material_request_lines_guard_0029 "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.material_request_lines "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_REQUEST_LINE_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_approval_instances_guard_0029 "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.approval_instances "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_INSTANCE_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_material_request_files_guard_0029 "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.material_request_files "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_REQUEST_FILE_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_approval_delegations_guard_0029 "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.approval_delegations "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_DELEGATION_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_approval_external_registrations_guard_0029 "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.approval_external_registrations "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_EXTERNAL_REGISTRATION_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_approval_external_registration_lines_quantity_0029 "
        f"BEFORE INSERT ON public.approval_external_registration_lines "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_EXTERNAL_LINE_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_approval_step_line_decisions_quantity_0029 "
        f"BEFORE INSERT ON public.approval_step_line_decisions "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_DECISION_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_substitution_decisions_guard_0029 "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.substitution_decisions "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_SUBSTITUTION_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_supply_tasks_guard_0029 "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.supply_tasks "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_SUPPLY_TASK_FUNCTION}()"
    )
    for table_name in (
        "material_requests",
        "material_request_revisions",
        "material_request_lines",
        "material_request_files",
        "approval_instances",
        "approval_steps",
        "approval_external_registrations",
        "substitution_decisions",
        "supply_tasks",
        "approval_delegations",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_no_truncate_0029 "
            f"BEFORE TRUNCATE ON public.{table_name} FOR EACH STATEMENT "
            f"EXECUTE FUNCTION public.{PG_APPEND_ONLY_FUNCTION}()"
        )
    for table_name in ("approval_steps",):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_no_delete_0029 "
            f"BEFORE DELETE ON public.{table_name} FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_APPEND_ONLY_FUNCTION}()"
        )
    for function_name in (
        PG_APPEND_ONLY_FUNCTION,
        PG_REQUEST_FUNCTION,
        PG_REVISION_FUNCTION,
        PG_INSTANCE_FUNCTION,
        PG_REQUEST_LINE_FUNCTION,
        PG_REQUEST_FILE_FUNCTION,
        PG_DELEGATION_FUNCTION,
        PG_EXTERNAL_REGISTRATION_FUNCTION,
        PG_DECISION_FUNCTION,
        PG_EXTERNAL_LINE_FUNCTION,
        PG_SUBSTITUTION_FUNCTION,
        PG_SUPPLY_TASK_FUNCTION,
    ):
        op.execute(
            f"REVOKE EXECUTE ON FUNCTION public.{function_name}() FROM PUBLIC"
        )


def _drop_postgresql_guards() -> None:
    for table_name in FACT_TABLES:
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable_0029 ON public.{table_name}"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_no_truncate_0029 ON public.{table_name}"
        )
    for trigger_name, table_name in (
        ("trg_material_requests_guard_0029", "material_requests"),
        ("trg_material_request_revisions_guard_0029", "material_request_revisions"),
        ("trg_material_request_lines_guard_0029", "material_request_lines"),
        ("trg_approval_instances_guard_0029", "approval_instances"),
        ("trg_material_request_files_guard_0029", "material_request_files"),
        ("trg_approval_delegations_guard_0029", "approval_delegations"),
        ("trg_approval_external_registrations_guard_0029", "approval_external_registrations"),
        ("trg_approval_external_registration_lines_quantity_0029", "approval_external_registration_lines"),
        ("trg_approval_step_line_decisions_quantity_0029", "approval_step_line_decisions"),
        ("trg_substitution_decisions_guard_0029", "substitution_decisions"),
        ("trg_supply_tasks_guard_0029", "supply_tasks"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name}")
    for table_name in (
        "material_requests",
        "material_request_revisions",
        "material_request_lines",
        "material_request_files",
        "approval_instances",
        "approval_steps",
        "approval_external_registrations",
        "substitution_decisions",
        "supply_tasks",
        "approval_delegations",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_no_truncate_0029 ON public.{table_name}"
        )
    for table_name in ("approval_steps",):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_no_delete_0029 ON public.{table_name}"
        )
    for function_name in (
        PG_SUPPLY_TASK_FUNCTION,
        PG_SUBSTITUTION_FUNCTION,
        PG_EXTERNAL_LINE_FUNCTION,
        PG_DECISION_FUNCTION,
        PG_EXTERNAL_REGISTRATION_FUNCTION,
        PG_DELEGATION_FUNCTION,
        PG_REQUEST_FILE_FUNCTION,
        PG_REQUEST_LINE_FUNCTION,
        PG_INSTANCE_FUNCTION,
        PG_REVISION_FUNCTION,
        PG_REQUEST_FUNCTION,
        PG_APPEND_ONLY_FUNCTION,
    ):
        op.execute(f"DROP FUNCTION public.{function_name}()")


def _create_sqlite_guards() -> None:
    request_contact_invalid = _sqlite_contact_envelope_invalid(
        "NEW.contact_snapshot_jsonb"
    )
    request_masked_invalid = _sqlite_masked_projections_invalid(
        "NEW.address_masked_jsonb", "NEW.contact_masked_jsonb"
    )
    revision_contact_invalid = _sqlite_contact_envelope_invalid(
        "NEW.contact_snapshot_jsonb"
    )
    revision_masked_invalid = _sqlite_masked_projections_invalid(
        "NEW.address_masked_jsonb", "NEW.contact_masked_jsonb"
    )
    for table_name in FACT_TABLES:
        op.execute(
            f"""
CREATE TRIGGER trg_{table_name}_immutable_update_0029
BEFORE UPDATE ON {table_name}
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
        )
        op.execute(
            f"""
CREATE TRIGGER trg_{table_name}_immutable_delete_0029
BEFORE DELETE ON {table_name}
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
        )
    op.execute(
        f"""
CREATE TRIGGER trg_material_requests_insert_guard_0029
BEFORE INSERT ON material_requests
WHEN {request_contact_invalid}
  OR {request_masked_invalid}
  OR NEW.allocation_status <> 'not_allocated'
  OR NEW.reservation_status <> 'not_reserved'
  OR NEW.outbound_status <> 'not_started'
  OR NEW.shipment_status <> 'not_started'
  OR NEW.logistics_signature_status <> 'not_signed'
  OR NEW.oam_receipt_status <> 'not_occurred'
  OR NEW.personal_inbound_status <> 'not_started'
  OR NEW.notification_status <> 'not_started'
  OR NEW.reconciliation_status <> 'not_started'
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_material_requests_update_guard_0029
BEFORE UPDATE ON material_requests
WHEN {request_contact_invalid}
  OR {request_masked_invalid}
  OR NEW.id IS NOT OLD.id
  OR NEW.request_no IS NOT OLD.request_no
  OR NEW.requester_user_id IS NOT OLD.requester_user_id
  OR NEW.requester_person_id IS NOT OLD.requester_person_id
  OR NEW.requester_org_id IS NOT OLD.requester_org_id
  OR NEW.created_by_user_id IS NOT OLD.created_by_user_id
  OR NEW.created_at IS NOT OLD.created_at
  OR (OLD.submitted_at IS NOT NULL AND NEW.submitted_at IS NOT OLD.submitted_at)
  OR (OLD.submitted_at IS NOT NULL AND NEW.approval_mode IS NOT OLD.approval_mode)
  OR (NEW.revision_no IS NOT OLD.revision_no AND NOT (
      OLD.status = 'returned'
      AND NEW.status = 'returned'
      AND NEW.revision_no = OLD.revision_no + 1
      AND EXISTS (
          SELECT 1 FROM material_request_revisions AS revision
           WHERE revision.request_id = NEW.id
             AND revision.revision_no = NEW.revision_no
             AND revision.status = 'draft'
             AND revision.work_order_id IS NEW.work_order_id
             AND revision.purpose = NEW.purpose
             AND revision.urgency = NEW.urgency
             AND revision.expected_date IS NEW.expected_date
             AND revision.address_snapshot_jsonb = NEW.address_snapshot_jsonb
             AND revision.address_masked_jsonb = NEW.address_masked_jsonb
             AND revision.contact_snapshot_jsonb = NEW.contact_snapshot_jsonb
             AND revision.contact_masked_jsonb = NEW.contact_masked_jsonb
             AND revision.note = NEW.note
             AND revision.approval_mode = NEW.approval_mode
      )
  ))
  OR ((NEW.work_order_id IS NOT OLD.work_order_id
       OR NEW.purpose IS NOT OLD.purpose
       OR NEW.urgency IS NOT OLD.urgency
       OR NEW.expected_date IS NOT OLD.expected_date
       OR NEW.address_snapshot_jsonb IS NOT OLD.address_snapshot_jsonb
       OR NEW.address_masked_jsonb IS NOT OLD.address_masked_jsonb
       OR NEW.contact_snapshot_jsonb IS NOT OLD.contact_snapshot_jsonb
       OR NEW.contact_masked_jsonb IS NOT OLD.contact_masked_jsonb
       OR NEW.note IS NOT OLD.note)
      AND NOT EXISTS (
          SELECT 1 FROM material_request_revisions AS revision
           WHERE revision.request_id = NEW.id
             AND revision.revision_no = NEW.revision_no
             AND revision.status = 'draft'
             AND revision.work_order_id IS NEW.work_order_id
             AND revision.purpose = NEW.purpose
             AND revision.urgency = NEW.urgency
             AND revision.expected_date IS NEW.expected_date
             AND revision.address_snapshot_jsonb = NEW.address_snapshot_jsonb
             AND revision.address_masked_jsonb = NEW.address_masked_jsonb
             AND revision.contact_snapshot_jsonb = NEW.contact_snapshot_jsonb
             AND revision.contact_masked_jsonb = NEW.contact_masked_jsonb
             AND revision.note = NEW.note
             AND revision.approval_mode = NEW.approval_mode
      ))
  OR (OLD.status IN ('draft', 'returned')
      AND NEW.status IN ('submitted', 'approval_in_progress')
      AND NOT EXISTS (
          SELECT 1 FROM material_request_revisions AS revision
           WHERE revision.request_id = NEW.id
             AND revision.revision_no = NEW.revision_no
             AND revision.status = 'sealed'
      ))
  OR NEW.allocation_status <> 'not_allocated'
  OR NEW.reservation_status <> 'not_reserved'
  OR NEW.outbound_status <> 'not_started'
  OR NEW.shipment_status <> 'not_started'
  OR NEW.logistics_signature_status <> 'not_signed'
  OR NEW.oam_receipt_status <> 'not_occurred'
  OR NEW.personal_inbound_status <> 'not_started'
  OR NEW.notification_status <> 'not_started'
  OR NEW.reconciliation_status <> 'not_started'
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_material_requests_delete_guard_0029
BEFORE DELETE ON material_requests
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_material_request_revisions_insert_guard_0029
BEFORE INSERT ON material_request_revisions
WHEN {revision_contact_invalid}
  OR {revision_masked_invalid}
  OR NEW.status <> 'draft'
  OR (NEW.revision_no = 1 AND (
      NEW.previous_revision_id IS NOT NULL
      OR NOT EXISTS (
          SELECT 1 FROM material_requests AS request
           WHERE request.id = NEW.request_id
             AND request.revision_no = 1
             AND request.status = 'draft'
      )
      OR EXISTS (
          SELECT 1 FROM material_request_revisions AS existing
           WHERE existing.request_id = NEW.request_id
      )
  ))
  OR (NEW.revision_no > 1 AND NOT EXISTS (
      SELECT 1
        FROM material_requests AS request
        JOIN material_request_revisions AS previous
          ON previous.id = NEW.previous_revision_id
         AND previous.request_id = request.id
         AND previous.revision_no = NEW.revision_no - 1
        JOIN approval_instances AS instance
          ON instance.request_revision_id = previous.id
       WHERE request.id = NEW.request_id
         AND request.status = 'returned'
         AND NEW.revision_no = request.revision_no + 1
         AND previous.status = 'sealed'
         AND instance.status IN ('returned', 'superseded')
  ))
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_material_request_revisions_update_guard_0029
BEFORE UPDATE ON material_request_revisions
WHEN {revision_contact_invalid}
  OR {revision_masked_invalid}
  OR NEW.id IS NOT OLD.id
  OR NEW.request_id IS NOT OLD.request_id
  OR NEW.revision_no IS NOT OLD.revision_no
  OR NEW.previous_revision_id IS NOT OLD.previous_revision_id
  OR NEW.created_by_user_id IS NOT OLD.created_by_user_id
  OR NEW.created_at IS NOT OLD.created_at
  OR OLD.status = 'sealed'
  OR NEW.status NOT IN ('draft', 'sealed')
  OR NOT EXISTS (
      SELECT 1 FROM material_requests AS request
       WHERE request.id = NEW.request_id
         AND request.revision_no = NEW.revision_no
         AND request.status IN ('draft', 'returned', 'approval_in_progress')
  )
  OR (NEW.status = 'sealed' AND (
      NEW.content_manifest_sha256 NOT GLOB '[0-9a-f]*'
      OR NEW.content_manifest_sha256 GLOB '*[^0-9a-f]*'
      OR length(NEW.content_manifest_sha256) <> 64
      OR NEW.sealed_at IS NULL
      OR NEW.sealed_by_user_id IS NULL
      OR NOT EXISTS (
          SELECT 1 FROM material_request_lines AS line
           WHERE line.revision_id = NEW.id
             AND line.request_id = NEW.request_id
             AND line.revision_no = NEW.revision_no
      )
      OR NOT EXISTS (
          SELECT 1 FROM material_requests AS request
           WHERE request.id = NEW.request_id
             AND request.revision_no = NEW.revision_no
             AND request.work_order_id IS NEW.work_order_id
             AND request.purpose = NEW.purpose
             AND request.urgency = NEW.urgency
             AND request.expected_date IS NEW.expected_date
             AND request.address_snapshot_jsonb = NEW.address_snapshot_jsonb
             AND request.address_masked_jsonb = NEW.address_masked_jsonb
             AND request.contact_snapshot_jsonb = NEW.contact_snapshot_jsonb
             AND request.contact_masked_jsonb = NEW.contact_masked_jsonb
             AND request.note = NEW.note
             AND request.approval_mode = NEW.approval_mode
      )
  ))
  OR (NEW.status = 'draft' AND (
      NEW.content_manifest_sha256 IS NOT NULL
      OR NEW.sealed_at IS NOT NULL
      OR NEW.sealed_by_user_id IS NOT NULL
  ))
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_material_request_revisions_delete_guard_0029
BEFORE DELETE ON material_request_revisions
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_instances_insert_guard_0029
BEFORE INSERT ON approval_instances
WHEN NOT EXISTS (
    SELECT 1
      FROM material_request_revisions AS revision
      JOIN material_requests AS request ON request.id = revision.request_id
     WHERE revision.id = NEW.request_revision_id
       AND revision.request_id = NEW.request_id
       AND revision.revision_no = NEW.revision_no
       AND revision.status = 'sealed'
       AND request.revision_no = NEW.revision_no
       AND request.status IN ('draft', 'returned', 'submitted', 'approval_in_progress')
)
  OR (NEW.attempt_no > 1 AND NOT EXISTS (
      SELECT 1 FROM approval_instances AS previous
       WHERE previous.request_id = NEW.request_id
         AND previous.attempt_no = NEW.attempt_no - 1
         AND previous.status IN ('returned', 'superseded')
  ))
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_instances_update_guard_0029
BEFORE UPDATE ON approval_instances
WHEN NEW.id IS NOT OLD.id
  OR NEW.request_id IS NOT OLD.request_id
  OR NEW.request_revision_id IS NOT OLD.request_revision_id
  OR NEW.revision_no IS NOT OLD.revision_no
  OR NEW.route_version_id IS NOT OLD.route_version_id
  OR NEW.attempt_no IS NOT OLD.attempt_no
  OR NEW.created_at IS NOT OLD.created_at
  OR OLD.status IN ('completed', 'rejected', 'withdrawn', 'cancelled', 'superseded')
  OR (OLD.status = 'returned' AND NEW.status <> 'superseded')
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_instances_delete_guard_0029
BEFORE DELETE ON approval_instances
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_material_request_lines_insert_guard_0029
BEFORE INSERT ON material_request_lines
WHEN NOT EXISTS (
    SELECT 1
      FROM material_request_revisions AS revision
      JOIN material_requests AS request ON request.id = revision.request_id
     WHERE revision.id = NEW.revision_id
       AND revision.request_id = NEW.request_id
       AND revision.revision_no = NEW.revision_no
       AND revision.status = 'draft'
       AND request.revision_no = NEW.revision_no
       AND request.status IN ('draft', 'returned')
)
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_material_request_lines_update_guard_0029
BEFORE UPDATE ON material_request_lines
WHEN OLD.created_at IS NOT NEW.created_at
  OR OLD.id IS NOT NEW.id
  OR OLD.request_id IS NOT NEW.request_id
  OR OLD.revision_id IS NOT NEW.revision_id
  OR OLD.revision_no IS NOT NEW.revision_no
  OR ((OLD.line_no IS NOT NEW.line_no
       OR OLD.client_line_key IS NOT NEW.client_line_key
       OR OLD.material_id IS NOT NEW.material_id
       OR OLD.suggested_substitute_material_id IS NOT NEW.suggested_substitute_material_id
       OR OLD.requested_qty IS NOT NEW.requested_qty
       OR OLD.required_date IS NOT NEW.required_date
       OR OLD.note IS NOT NEW.note)
      AND NOT EXISTS (
          SELECT 1
            FROM material_request_revisions AS revision
            JOIN material_requests AS request ON request.id = revision.request_id
           WHERE revision.id = NEW.revision_id
             AND revision.request_id = NEW.request_id
             AND revision.revision_no = NEW.revision_no
             AND revision.status = 'draft'
             AND request.revision_no = NEW.revision_no
             AND request.status IN ('draft', 'returned')
      ))
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_material_request_lines_delete_guard_0029
BEFORE DELETE ON material_request_lines
WHEN NOT EXISTS (
    SELECT 1
      FROM material_request_revisions AS revision
      JOIN material_requests AS request ON request.id = revision.request_id
     WHERE revision.id = OLD.revision_id
       AND revision.request_id = OLD.request_id
       AND revision.revision_no = OLD.revision_no
       AND revision.status = 'draft'
       AND request.revision_no = OLD.revision_no
       AND request.status IN ('draft', 'returned')
)
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_material_request_files_insert_guard_0029
BEFORE INSERT ON material_request_files
WHEN NOT EXISTS (
    SELECT 1
      FROM material_request_revisions AS revision
      JOIN material_requests AS request ON request.id = revision.request_id
     WHERE revision.id = NEW.revision_id
       AND revision.request_id = NEW.request_id
       AND revision.revision_no = NEW.revision_no
       AND revision.status = 'draft'
       AND request.revision_no = NEW.revision_no
       AND request.status IN ('draft', 'returned')
)
  OR NOT EXISTS (
    SELECT 1 FROM files AS file
     WHERE file.id = NEW.file_id AND file.status = 'available'
)
  OR EXISTS (
    SELECT 1 FROM approval_external_registrations AS registration
     WHERE registration.evidence_file_id = NEW.file_id
)
  OR EXISTS (
    SELECT 1 FROM approval_delegations AS delegation
     WHERE delegation.evidence_file_id = NEW.file_id
)
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_material_request_files_update_guard_0029
BEFORE UPDATE ON material_request_files
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_material_request_files_delete_guard_0029
BEFORE DELETE ON material_request_files
WHEN NOT EXISTS (
    SELECT 1
      FROM material_request_revisions AS revision
      JOIN material_requests AS request ON request.id = revision.request_id
     WHERE revision.id = OLD.revision_id
       AND revision.request_id = OLD.request_id
       AND revision.revision_no = OLD.revision_no
       AND revision.status = 'draft'
       AND request.revision_no = OLD.revision_no
       AND request.status IN ('draft', 'returned')
)
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    _create_sqlite_delegation_and_external_guards()
    _create_sqlite_quantity_guards()
    for table_name in ("approval_steps",):
        op.execute(
            f"""
CREATE TRIGGER trg_{table_name}_no_delete_0029
BEFORE DELETE ON {table_name}
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
        )


def _create_sqlite_delegation_and_external_guards() -> None:
    op.execute(
        f"""
CREATE TRIGGER trg_approval_delegations_insert_guard_0029
BEFORE INSERT ON approval_delegations
WHEN NEW.evidence_file_id IS NULL
  OR trim(NEW.reason) = ''
  OR length(NEW.scope_hash) <> 64
  OR NEW.status NOT IN ('scheduled', 'active')
  OR NEW.revoked_by IS NOT NULL
  OR NEW.revoked_at IS NOT NULL
  OR NOT EXISTS (
      SELECT 1 FROM files AS file
       WHERE file.id = NEW.evidence_file_id AND file.status = 'available'
  )
  OR EXISTS (
      SELECT 1 FROM material_request_files AS binding
       WHERE binding.file_id = NEW.evidence_file_id
  )
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_delegations_update_guard_0029
BEFORE UPDATE ON approval_delegations
WHEN NEW.id IS NOT OLD.id
  OR NEW.from_user_id IS NOT OLD.from_user_id
  OR NEW.to_user_id IS NOT OLD.to_user_id
  OR NEW.scope_jsonb IS NOT OLD.scope_jsonb
  OR NEW.scope_hash IS NOT OLD.scope_hash
  OR NEW.valid_from IS NOT OLD.valid_from
  OR NEW.valid_to IS NOT OLD.valid_to
  OR NEW.evidence_file_id IS NOT OLD.evidence_file_id
  OR NEW.reason IS NOT OLD.reason
  OR NEW.created_by IS NOT OLD.created_by
  OR NEW.created_at IS NOT OLD.created_at
  OR OLD.status IN ('revoked', 'expired')
  OR (OLD.status = 'scheduled' AND NEW.status NOT IN ('active', 'revoked', 'expired'))
  OR (OLD.status = 'active' AND NEW.status NOT IN ('revoked', 'expired'))
  OR (NEW.status = 'revoked' AND (NEW.revoked_by IS NULL OR NEW.revoked_at IS NULL))
  OR (NEW.status <> 'revoked' AND (NEW.revoked_by IS NOT NULL OR NEW.revoked_at IS NOT NULL))
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_delegations_delete_guard_0029
BEFORE DELETE ON approval_delegations
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_external_registrations_insert_guard_0029
BEFORE INSERT ON approval_external_registrations
WHEN NEW.status <> 'pending_verification'
  OR NOT EXISTS (
      SELECT 1
        FROM approval_steps AS step
        JOIN approval_instances AS instance ON instance.id = step.instance_id
        JOIN material_requests AS request ON request.id = instance.request_id
       WHERE step.id = NEW.step_id
         AND step.step_no = 3
         AND step.source_mode = 'external_registration'
         AND request.requester_user_id <> NEW.registered_by_user_id
         AND request.requester_person_id <> NEW.registered_by_person_id
  )
  OR NOT EXISTS (
      SELECT 1 FROM files AS file
       WHERE file.id = NEW.evidence_file_id AND file.status = 'available'
  )
  OR EXISTS (
      SELECT 1 FROM material_request_files AS binding
       WHERE binding.file_id = NEW.evidence_file_id
  )
  OR NOT EXISTS (
      SELECT 1 FROM approval_step_candidates AS candidate
       WHERE candidate.step_id = NEW.step_id
         AND candidate.user_id = NEW.registered_by_user_id
         AND candidate.person_id = NEW.registered_by_person_id
         AND candidate.role_assignment_id = NEW.registered_role_assignment_id
         AND candidate.authorization_version = NEW.authorization_version
         AND candidate.candidate_kind = 'registrar'
  )
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_external_registrations_update_guard_0029
BEFORE UPDATE ON approval_external_registrations
WHEN NEW.id IS NOT OLD.id
  OR NEW.step_id IS NOT OLD.step_id
  OR NEW.registration_no IS NOT OLD.registration_no
  OR NEW.external_action IS NOT OLD.external_action
  OR NEW.evidence_file_id IS NOT OLD.evidence_file_id
  OR NEW.external_approver_snapshot_jsonb IS NOT OLD.external_approver_snapshot_jsonb
  OR NEW.external_decided_at IS NOT OLD.external_decided_at
  OR NEW.decision_manifest_sha256 IS NOT OLD.decision_manifest_sha256
  OR NEW.registered_by_user_id IS NOT OLD.registered_by_user_id
  OR NEW.registered_by_person_id IS NOT OLD.registered_by_person_id
  OR NEW.registered_role_assignment_id IS NOT OLD.registered_role_assignment_id
  OR NEW.authorization_version IS NOT OLD.authorization_version
  OR NEW.registered_at IS NOT OLD.registered_at
  OR NEW.created_at IS NOT OLD.created_at
  OR OLD.status <> 'pending_verification'
  OR NEW.status NOT IN ('accepted', 'rejected', 'superseded')
  OR NOT EXISTS (
      SELECT 1
        FROM approval_steps AS step
        JOIN approval_instances AS instance ON instance.id = step.instance_id
        JOIN material_requests AS request ON request.id = instance.request_id
       WHERE step.id = NEW.step_id
         AND request.requester_user_id <> NEW.verified_by_user_id
         AND request.requester_person_id <> NEW.verified_by_person_id
  )
  OR NOT EXISTS (
      SELECT 1 FROM approval_step_candidates AS candidate
       WHERE candidate.step_id = NEW.step_id
         AND candidate.user_id = NEW.verified_by_user_id
         AND candidate.person_id = NEW.verified_by_person_id
         AND candidate.role_assignment_id = NEW.verified_role_assignment_id
         AND candidate.authorization_version = NEW.verified_authorization_version
         AND candidate.candidate_kind = 'verifier'
  )
  OR (NEW.external_action IN ('approve', 'partial_approve') AND (
      (SELECT count(*) FROM approval_external_registration_lines AS line
        WHERE line.registration_id = NEW.id)
      <>
      (SELECT count(*)
         FROM approval_step_line_decisions AS decision
         JOIN approval_steps AS step ON step.id = NEW.step_id
        WHERE decision.step_id = step.predecessor_step_id
          AND decision.approved_qty > 0)
      OR EXISTS (
          SELECT 1
            FROM approval_step_line_decisions AS predecessor
            JOIN approval_steps AS step ON step.id = NEW.step_id
           WHERE predecessor.step_id = step.predecessor_step_id
             AND predecessor.approved_qty > 0
             AND NOT EXISTS (
                 SELECT 1 FROM approval_external_registration_lines AS line
                  WHERE line.registration_id = NEW.id
                    AND line.request_line_id = predecessor.request_line_id
             )
      )
      OR (NEW.external_action = 'approve' AND EXISTS (
          SELECT 1 FROM approval_external_registration_lines AS line
           WHERE line.registration_id = NEW.id AND line.rejected_qty > 0
      ))
      OR (NEW.external_action = 'partial_approve' AND NOT EXISTS (
          SELECT 1 FROM approval_external_registration_lines AS line
           WHERE line.registration_id = NEW.id AND line.rejected_qty > 0
      ))
  ))
  OR (NEW.external_action IN ('reject', 'return') AND EXISTS (
      SELECT 1 FROM approval_external_registration_lines AS line
       WHERE line.registration_id = NEW.id
  ))
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_external_registrations_delete_guard_0029
BEFORE DELETE ON approval_external_registrations
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )


def _create_sqlite_quantity_guards() -> None:
    op.execute(
        f"""
CREATE TRIGGER trg_approval_external_registration_lines_quantity_0029
BEFORE INSERT ON approval_external_registration_lines
WHEN NOT EXISTS (
    SELECT 1
      FROM approval_external_registrations AS registration
      JOIN approval_steps AS step ON step.id = registration.step_id
      JOIN approval_steps AS predecessor_step
        ON predecessor_step.id = step.predecessor_step_id
      JOIN approval_step_line_decisions AS predecessor
        ON predecessor.step_id = predecessor_step.id
       AND predecessor.request_line_id = NEW.request_line_id
     WHERE registration.id = NEW.registration_id
       AND registration.status = 'pending_verification'
       AND registration.external_action IN ('approve', 'partial_approve')
       AND step.step_no = 3
       AND step.source_mode = 'external_registration'
       AND predecessor_step.status IN ('approved', 'partially_approved')
       AND predecessor.approved_qty > 0
       AND predecessor.approved_qty = NEW.input_qty
)
  OR (NEW.rejected_qty > 0 AND trim(NEW.reason) = '')
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_step_line_decisions_quantity_0029
BEFORE INSERT ON approval_step_line_decisions
WHEN NOT EXISTS (
    SELECT 1
      FROM approval_steps AS step
      JOIN approval_instances AS instance ON instance.id = step.instance_id
      JOIN material_requests AS request ON request.id = instance.request_id
      JOIN material_request_lines AS line
        ON line.request_id = request.id AND line.id = NEW.request_line_id
     WHERE step.id = NEW.step_id
       AND step.source_mode = NEW.decision_source
       AND line.revision_id = instance.request_revision_id
       AND request.requester_user_id <> NEW.decided_by_user_id
       AND request.requester_person_id <> NEW.decided_by_person_id
       AND (
           (step.step_no = 1 AND NEW.input_qty = line.requested_qty)
           OR
           (step.step_no > 1 AND EXISTS (
               SELECT 1
                 FROM approval_steps AS predecessor_step
                 JOIN approval_step_line_decisions AS predecessor
                   ON predecessor.step_id = predecessor_step.id
                  AND predecessor.request_line_id = NEW.request_line_id
                WHERE predecessor_step.id = step.predecessor_step_id
                  AND predecessor_step.status IN ('approved', 'partially_approved')
                  AND predecessor.approved_qty > 0
                  AND NEW.input_qty = predecessor.approved_qty
           ))
       )
       AND EXISTS (
           SELECT 1 FROM approval_step_candidates AS candidate
            WHERE candidate.step_id = NEW.step_id
              AND candidate.user_id = NEW.decided_by_user_id
              AND candidate.person_id = NEW.decided_by_person_id
              AND candidate.role_assignment_id = NEW.decided_role_assignment_id
              AND candidate.authorization_version = NEW.authorization_version
              AND candidate.candidate_kind = CASE
                  WHEN NEW.decision_source = 'external_registration'
                  THEN 'verifier' ELSE 'assignee' END
       )
       AND (
           NEW.decision_source <> 'external_registration'
           OR EXISTS (
               SELECT 1
                 FROM approval_external_registrations AS registration
                 JOIN approval_external_registration_lines AS evidence_line
                   ON evidence_line.registration_id = registration.id
                WHERE registration.id = NEW.external_registration_id
                  AND registration.step_id = NEW.step_id
                  AND registration.status = 'accepted'
                  AND evidence_line.request_line_id = NEW.request_line_id
                  AND evidence_line.input_qty = NEW.input_qty
                  AND evidence_line.approved_qty = NEW.approved_qty
                  AND evidence_line.rejected_qty = NEW.rejected_qty
                  AND evidence_line.reason = NEW.reason
           )
       )
)
  OR (NEW.rejected_qty > 0 AND trim(NEW.reason) = '')
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_substitution_decisions_insert_guard_0029
BEFORE INSERT ON substitution_decisions
WHEN NEW.status <> 'proposed'
  OR NOT EXISTS (
      SELECT 1
        FROM material_request_lines AS line
        JOIN material_requests AS request ON request.id = line.request_id
        JOIN material_substitutions AS substitution
          ON substitution.id = NEW.substitution_id
         AND substitution.material_id = line.material_id
       WHERE line.id = NEW.request_line_id
         AND line.revision_no = request.revision_no
         AND line.status IN ('approved', 'partially_approved')
         AND request.status IN ('approved', 'partially_approved')
         AND line.final_approved_qty - line.cancelled_qty > 0
         AND NEW.original_approved_qty = line.final_approved_qty - line.cancelled_qty
         AND substitution.status = 'active'
         AND substitution.valid_from <= NEW.proposed_at
         AND (substitution.valid_to IS NULL OR substitution.valid_to > NEW.proposed_at)
         AND NEW.ratio = substitution.ratio
         AND NEW.substitute_qty = round(NEW.original_approved_qty * NEW.ratio, 3)
  )
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_substitution_decisions_update_guard_0029
BEFORE UPDATE ON substitution_decisions
WHEN NEW.id IS NOT OLD.id
  OR NEW.request_line_id IS NOT OLD.request_line_id
  OR NEW.substitution_id IS NOT OLD.substitution_id
  OR NEW.original_approved_qty IS NOT OLD.original_approved_qty
  OR NEW.ratio IS NOT OLD.ratio
  OR NEW.substitute_qty IS NOT OLD.substitute_qty
  OR NEW.proposed_by_user_id IS NOT OLD.proposed_by_user_id
  OR NEW.proposed_by_person_id IS NOT OLD.proposed_by_person_id
  OR NEW.proposed_role_assignment_id IS NOT OLD.proposed_role_assignment_id
  OR NEW.authorization_version IS NOT OLD.authorization_version
  OR NEW.proposed_at IS NOT OLD.proposed_at
  OR NEW.created_at IS NOT OLD.created_at
  OR OLD.status <> 'proposed'
  OR NEW.status NOT IN ('confirmed', 'rejected', 'cancelled', 'superseded')
  OR (NEW.status IN ('confirmed', 'rejected') AND NOT EXISTS (
      SELECT 1
        FROM material_request_lines AS line
        JOIN material_requests AS request ON request.id = line.request_id
       WHERE line.id = NEW.request_line_id
         AND request.requester_user_id = NEW.decided_by_user_id
         AND request.requester_person_id = NEW.decided_by_person_id
  ))
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_substitution_decisions_delete_guard_0029
BEFORE DELETE ON substitution_decisions
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_supply_tasks_insert_guard_0029
BEFORE INSERT ON supply_tasks
WHEN NOT EXISTS (
    SELECT 1
      FROM material_request_lines AS line
      JOIN material_requests AS request ON request.id = line.request_id
     WHERE line.id = NEW.request_line_id
       AND line.revision_no = request.revision_no
       AND line.status IN ('approved', 'partially_approved')
       AND request.status IN ('approved', 'partially_approved')
       AND line.final_approved_qty - line.cancelled_qty > 0
       AND COALESCE((
           SELECT sum(task.original_equivalent_qty)
             FROM supply_tasks AS task
            WHERE task.request_line_id = NEW.request_line_id
              AND task.status NOT IN ('cancelled', 'closed_no_supply')
       ), 0) + CASE WHEN NEW.status IN ('cancelled', 'closed_no_supply')
                    THEN 0 ELSE NEW.original_equivalent_qty END
           <= line.final_approved_qty - line.cancelled_qty
)
  OR (NEW.substitution_decision_id IS NULL
      AND NEW.expected_qty <> NEW.original_equivalent_qty)
  OR (NEW.substitution_decision_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM substitution_decisions AS decision
       WHERE decision.id = NEW.substitution_decision_id
         AND decision.request_line_id = NEW.request_line_id
         AND decision.status = 'confirmed'
         AND NEW.expected_qty = round(
             NEW.original_equivalent_qty * decision.ratio, 3
         )
  ))
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_supply_tasks_update_guard_0029
BEFORE UPDATE ON supply_tasks
WHEN NEW.id IS NOT OLD.id
  OR NEW.request_line_id IS NOT OLD.request_line_id
  OR NEW.substitution_decision_id IS NOT OLD.substitution_decision_id
  OR NEW.supply_type IS NOT OLD.supply_type
  OR NEW.expected_qty IS NOT OLD.expected_qty
  OR NEW.original_equivalent_qty IS NOT OLD.original_equivalent_qty
  OR NEW.created_by_user_id IS NOT OLD.created_by_user_id
  OR NEW.created_by_person_id IS NOT OLD.created_by_person_id
  OR NEW.created_role_assignment_id IS NOT OLD.created_role_assignment_id
  OR NEW.authorization_version IS NOT OLD.authorization_version
  OR NEW.created_at IS NOT OLD.created_at
  OR OLD.status IN ('cancelled', 'closed_no_supply')
  OR NOT EXISTS (
      SELECT 1
        FROM material_request_lines AS line
        JOIN material_requests AS request ON request.id = line.request_id
       WHERE line.id = NEW.request_line_id
         AND line.revision_no = request.revision_no
         AND line.status IN ('approved', 'partially_approved')
         AND request.status IN ('approved', 'partially_approved')
         AND COALESCE((
             SELECT sum(task.original_equivalent_qty)
               FROM supply_tasks AS task
              WHERE task.request_line_id = NEW.request_line_id
                AND task.id <> NEW.id
                AND task.status NOT IN ('cancelled', 'closed_no_supply')
         ), 0) + CASE WHEN NEW.status IN ('cancelled', 'closed_no_supply')
                      THEN 0 ELSE NEW.original_equivalent_qty END
             <= line.final_approved_qty - line.cancelled_qty
  )
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_supply_tasks_delete_guard_0029
BEFORE DELETE ON supply_tasks
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )


def _drop_sqlite_existing_table_guards() -> None:
    for trigger_name in (
        "trg_approval_delegations_insert_guard_0029",
        "trg_approval_delegations_update_guard_0029",
        "trg_approval_delegations_delete_guard_0029",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
