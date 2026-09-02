"""Constrain formal file completion and single-purpose business bindings.

Revision ID: 20260901_0036
Revises: 20260901_0035
Create Date: 2026-09-01

This revision is deliberately local to file evidence.  It does not advance a
material request, a stocktake, notification delivery or reconciliation.  The
runtime may create one exact pending upload intent, complete it once, and
append one purpose-matched binding; all other mutation remains migration-only.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260901_0036"
down_revision: Union[str, Sequence[str], None] = "20260901_0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
FORMAL_SCHEMA = "cloud_oam.formal_file_upload_intent.v1"
FORMAL_PROVIDER = "aliyun_oss_v2"
FORMAL_PURPOSES_SQL = (
    "('request_attachment', 'external_approval_evidence', "
    "'stocktake_evidence')"
)
MAXIMUM_SIZE_BYTES = 120 * 1024 * 1024

EXTERNAL_EVIDENCE_INDEX = (
    "uq_approval_external_registrations_evidence_file_0036"
)
STOCKTAKE_EVIDENCE_INDEX = (
    "uq_document_attachments_stocktake_evidence_file_0036"
)
PG_FILE_FUNCTION = "rsc_guard_formal_file_object_0036"
PG_BINDING_FUNCTION = "rsc_guard_formal_file_binding_0036"
PG_TRIGGERS = {
    "trg_files_formal_runtime_guard_0036": ("files", "row"),
    "trg_files_formal_no_truncate_0036": ("files", "truncate"),
    "trg_material_request_files_formal_guard_0036": (
        "material_request_files",
        "insert",
    ),
    "trg_approval_external_registrations_evidence_guard_0036": (
        "approval_external_registrations",
        "row",
    ),
    "trg_approval_external_registrations_evidence_no_truncate_0036": (
        "approval_external_registrations",
        "truncate",
    ),
    "trg_document_attachments_stocktake_evidence_guard_0036": (
        "document_attachments",
        "row",
    ),
    "trg_document_attachments_stocktake_evidence_no_truncate_0036": (
        "document_attachments",
        "truncate",
    ),
}

UPGRADE_BLOCKER = (
    "0036 preflight failed: formal file or evidence binding requires quarantine"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0036 while formal files or protected evidence bindings exist"
)


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0036 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0036 SQLite upgrade requires an online connection")
        _postgresql_lock_and_preflight()
    else:
        if dialect == "sqlite":
            _ensure_sqlite_migration_transaction()
        else:
            _lock_postgresql_graph()
        _online_preflight(dialect)

    op.create_index(
        EXTERNAL_EVIDENCE_INDEX,
        "approval_external_registrations",
        ["evidence_file_id"],
        unique=True,
    )
    op.create_index(
        STOCKTAKE_EVIDENCE_INDEX,
        "document_attachments",
        ["file_id"],
        unique=True,
        postgresql_where=sa.text(
            "document_type = 'stocktake_scope_count_completion' AND "
            "attachment_type = 'stocktake_evidence'"
        ),
        sqlite_where=sa.text(
            "document_type = 'stocktake_scope_count_completion' AND "
            "attachment_type = 'stocktake_evidence'"
        ),
    )

    if dialect == "postgresql":
        op.execute(_postgresql_file_function_sql())
        op.execute(_postgresql_binding_function_sql())
        _create_postgresql_triggers()
        _apply_postgresql_acl()
        return
    _create_sqlite_triggers()


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0036 downgrade requires an online evidence check")
    dialect = _dialect_name()
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
    else:
        _lock_postgresql_graph()
    _require_safe_downgrade(dialect)

    if dialect == "postgresql":
        for trigger_name, (table_name, _kind) in PG_TRIGGERS.items():
            op.execute(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name}"
            )
        op.execute(f"DROP FUNCTION IF EXISTS public.{PG_BINDING_FUNCTION}()")
        op.execute(f"DROP FUNCTION IF EXISTS public.{PG_FILE_FUNCTION}()")
        _restore_postgresql_acl()
    else:
        for trigger_name in _sqlite_trigger_names():
            op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

    op.drop_index(STOCKTAKE_EVIDENCE_INDEX, table_name="document_attachments")
    op.drop_index(
        EXTERNAL_EVIDENCE_INDEX,
        table_name="approval_external_registrations",
    )


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _lock_postgresql_graph() -> None:
    bind = op.get_bind()
    # The order is part of the deployment contract and mirrors runtime parent
    # locking: files first, then each child/fact graph and identity rows.
    for table_name in (
        "files",
        "material_request_files",
        "approval_external_registrations",
        "document_attachments",
        "stocktake_scope_count_completions",
        "users",
        "people",
    ):
        bind.exec_driver_sql(
            f"LOCK TABLE public.{table_name} IN ACCESS EXCLUSIVE MODE"
        )


def _postgresql_lock_and_preflight() -> None:
    for table_name in (
        "files",
        "material_request_files",
        "approval_external_registrations",
        "document_attachments",
        "stocktake_scope_count_completions",
        "users",
        "people",
    ):
        op.execute(f"LOCK TABLE public.{table_name} IN ACCESS EXCLUSIVE MODE")
    op.execute(
        f"""
DO $$
BEGIN
    IF {_upgrade_blocker_sql('postgresql', 'public.')} THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _online_preflight(dialect: str) -> None:
    prefix = "public." if dialect == "postgresql" else ""
    sql = f"SELECT 1 WHERE {_upgrade_blocker_sql(dialect, prefix)}"
    if op.get_bind().execute(sa.text(sql)).first() is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _require_safe_downgrade(dialect: str) -> None:
    prefix = "public." if dialect == "postgresql" else ""
    metadata_schema = _json_text(dialect, "file_row.metadata_jsonb", "schema")
    purpose = _json_text(dialect, "file_row.metadata_jsonb", "purpose")
    predicates = f"""
EXISTS (
    SELECT 1 FROM {prefix}files AS file_row
     WHERE file_row.storage_key LIKE 'formal-files/v1/%'
        OR {metadata_schema} = '{FORMAL_SCHEMA}'
        OR {purpose} IN {FORMAL_PURPOSES_SQL}
)
OR EXISTS (
    SELECT 1 FROM {prefix}material_request_files
     WHERE purpose = 'request_attachment'
)
OR EXISTS (SELECT 1 FROM {prefix}approval_external_registrations)
OR EXISTS (
    SELECT 1 FROM {prefix}document_attachments
     WHERE document_type = 'stocktake_scope_count_completion'
        OR attachment_type = 'stocktake_evidence'
)
"""
    if op.get_bind().execute(
        sa.text(f"SELECT 1 WHERE {predicates}")
    ).first():
        raise RuntimeError(DOWNGRADE_BLOCKER)


def _json_text(dialect: str, document: str, key: str) -> str:
    if dialect == "postgresql":
        return f"({document} ->> '{key}')"
    return f"json_extract({document}, '$.{key}')"


def _formal_candidate_sql(dialect: str, alias: str) -> str:
    document = f"{alias}.metadata_jsonb"
    schema = _json_text(dialect, document, "schema")
    purpose = _json_text(dialect, document, "purpose")
    json_guard = "TRUE" if dialect == "postgresql" else f"json_valid({document})"
    return (
        f"({alias}.storage_key LIKE 'formal-files/v1/%' OR "
        f"({json_guard} AND ({schema} = '{FORMAL_SCHEMA}' OR "
        f"{purpose} IN {FORMAL_PURPOSES_SQL})))"
    )


def _postgresql_file_shape_sql(
    alias: str,
    *,
    status: str,
    require_current_identity: bool,
) -> str:
    document = f"{alias}.metadata_jsonb"
    purpose = f"({document}->>'purpose')"
    compact_id = f"replace({alias}.id::text, '-', '')"
    expected_key = (
        f"'formal-files/v1/' || {purpose} || '/' || left({compact_id}, 2) || "
        f"'/' || {compact_id}"
    )
    top_count = 11 if status == "available" else 10
    completion = f"({document}->'completion')"
    completion_sql = (
        f"AND jsonb_typeof({completion}) = 'object' "
        f"AND (SELECT count(*) FROM jsonb_object_keys({completion})) = 3 "
        f"AND {completion} ?& ARRAY['etag_sha256','head_manifest_sha256','verified_at'] "
        f"AND jsonb_typeof({completion}->'etag_sha256') = 'string' "
        f"AND jsonb_typeof({completion}->'head_manifest_sha256') = 'string' "
        f"AND jsonb_typeof({completion}->'verified_at') = 'string' "
        f"AND ({completion}->>'etag_sha256') ~ '^[0-9a-f]{{64}}$' "
        f"AND ({completion}->>'head_manifest_sha256') ~ '^[0-9a-f]{{64}}$' "
        f"AND ({completion}->>'verified_at') ~ "
        "'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}' "
        f"AND ({completion}->>'verified_at')::timestamptz >= {alias}.created_at "
        f"AND ({completion}->>'verified_at')::timestamptz <= transaction_timestamp()"
    ) if status == "available" else f"AND NOT ({document} ? 'completion')"
    identity_sql = ""
    if require_current_identity:
        identity_sql = f"""
AND EXISTS (
    SELECT 1
      FROM public.users AS uploader
      JOIN public.people AS person ON person.id = uploader.person_id
     WHERE uploader.id = {alias}.uploaded_by
       AND uploader.account_status = 'active'
       AND person.employment_status = 'active'
       AND ({document}->>'uploader_person_id') = person.id::text
       AND ({document}->>'authorization_version') = uploader.authorization_version::text
)
"""
    return f"""
{alias}.status = '{status}'
AND jsonb_typeof({document}) = 'object'
AND (SELECT count(*) FROM jsonb_object_keys({document})) = {top_count}
AND {document} ?& ARRAY[
    'authorization_version','file_id','idempotency_key_hash','provider',
    'purpose','request_sha256','schema','storage_key',
    'uploader_person_id','uploader_user_id'
]
AND jsonb_typeof({document}->'authorization_version') = 'number'
AND jsonb_typeof({document}->'file_id') = 'string'
AND jsonb_typeof({document}->'idempotency_key_hash') = 'string'
AND jsonb_typeof({document}->'provider') = 'string'
AND jsonb_typeof({document}->'purpose') = 'string'
AND jsonb_typeof({document}->'request_sha256') = 'string'
AND jsonb_typeof({document}->'schema') = 'string'
AND jsonb_typeof({document}->'storage_key') = 'string'
AND jsonb_typeof({document}->'uploader_person_id') = 'string'
AND jsonb_typeof({document}->'uploader_user_id') = 'string'
AND ({document}->>'schema') = '{FORMAL_SCHEMA}'
AND ({document}->>'provider') = '{FORMAL_PROVIDER}'
AND {purpose} IN {FORMAL_PURPOSES_SQL}
AND ({document}->>'file_id') = {alias}.id::text
AND ({document}->>'storage_key') = {alias}.storage_key
AND {alias}.storage_key = {expected_key}
AND ({document}->>'uploader_user_id') = {alias}.uploaded_by
AND ({document}->>'authorization_version') ~ '^[1-9][0-9]*$'
AND ({document}->>'uploader_person_id') ~
    '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[1-5][0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$'
AND ({document}->>'idempotency_key_hash') ~ '^[0-9a-f]{{64}}$'
AND ({document}->>'request_sha256') ~ '^[0-9a-f]{{64}}$'
AND {alias}.sha256 ~ '^[0-9a-f]{{64}}$'
AND {alias}.size_bytes BETWEEN 1 AND {MAXIMUM_SIZE_BYTES}
AND {alias}.mime_type IN (
    'application/pdf','image/jpeg','image/png','image/webp',
    'image/heic','image/heif','video/mp4','video/quicktime'
)
AND (
    ({alias}.mime_type = 'application/pdf'
     AND lower({alias}.original_filename) LIKE '%.pdf')
 OR ({alias}.mime_type = 'image/jpeg'
     AND (lower({alias}.original_filename) LIKE '%.jpg'
          OR lower({alias}.original_filename) LIKE '%.jpeg'))
 OR ({alias}.mime_type = 'image/png'
     AND lower({alias}.original_filename) LIKE '%.png')
 OR ({alias}.mime_type = 'image/webp'
     AND lower({alias}.original_filename) LIKE '%.webp')
 OR ({alias}.mime_type = 'image/heic'
     AND lower({alias}.original_filename) LIKE '%.heic')
 OR ({alias}.mime_type = 'image/heif'
     AND lower({alias}.original_filename) LIKE '%.heif')
 OR ({alias}.mime_type = 'video/mp4'
     AND lower({alias}.original_filename) LIKE '%.mp4')
 OR ({alias}.mime_type = 'video/quicktime'
     AND lower({alias}.original_filename) LIKE '%.mov')
)
AND {alias}.original_filename IS NOT NULL
AND length({alias}.original_filename) BETWEEN 1 AND 200
AND octet_length({alias}.original_filename) <= 255
AND {alias}.original_filename = btrim({alias}.original_filename)
AND position('/' IN {alias}.original_filename) = 0
AND position(chr(92) IN {alias}.original_filename) = 0
AND {alias}.uploaded_by IS NOT NULL
AND {alias}.created_at <= transaction_timestamp()
{completion_sql}
{identity_sql}
""".strip()


def _sqlite_file_shape_sql(
    alias: str,
    *,
    status: str,
    require_current_identity: bool,
) -> str:
    document = f"{alias}.metadata_jsonb"
    purpose = f"json_extract({document}, '$.purpose')"
    compact_id = f"replace({alias}.id, '-', '')"
    expected_key = (
        f"'formal-files/v1/' || {purpose} || '/' || substr({compact_id}, 1, 2) || "
        f"'/' || {compact_id}"
    )
    top_count = 11 if status == "available" else 10
    completion_sql = f"""
AND json_type({document}, '$.completion') = 'object'
AND (SELECT count(*) FROM json_each(json_extract({document}, '$.completion'))) = 3
AND json_type({document}, '$.completion.etag_sha256') = 'text'
AND length(json_extract({document}, '$.completion.etag_sha256')) = 64
AND json_extract({document}, '$.completion.etag_sha256') NOT GLOB '*[^0-9a-f]*'
AND json_type({document}, '$.completion.head_manifest_sha256') = 'text'
AND length(json_extract({document}, '$.completion.head_manifest_sha256')) = 64
AND json_extract({document}, '$.completion.head_manifest_sha256') NOT GLOB '*[^0-9a-f]*'
AND json_type({document}, '$.completion.verified_at') = 'text'
AND julianday(json_extract({document}, '$.completion.verified_at')) IS NOT NULL
AND julianday(json_extract({document}, '$.completion.verified_at')) >= julianday({alias}.created_at)
AND julianday(json_extract({document}, '$.completion.verified_at')) <= julianday(CURRENT_TIMESTAMP)
""" if status == "available" else f"AND json_type({document}, '$.completion') IS NULL"
    identity_sql = ""
    if require_current_identity:
        identity_sql = f"""
AND EXISTS (
    SELECT 1 FROM users AS uploader
      JOIN people AS person ON person.id = uploader.person_id
     WHERE uploader.id = {alias}.uploaded_by
       AND uploader.account_status = 'active'
       AND person.employment_status = 'active'
       AND replace(json_extract({document}, '$.uploader_person_id'), '-', '') =
           replace(person.id, '-', '')
       AND json_extract({document}, '$.authorization_version') =
           uploader.authorization_version
)
"""
    return f"""
{alias}.status = '{status}'
AND json_valid({document})
AND json_type({document}) = 'object'
AND (SELECT count(*) FROM json_each({document})) = {top_count}
AND json_type({document}, '$.authorization_version') = 'integer'
AND json_type({document}, '$.file_id') = 'text'
AND json_type({document}, '$.idempotency_key_hash') = 'text'
AND json_type({document}, '$.provider') = 'text'
AND json_type({document}, '$.purpose') = 'text'
AND json_type({document}, '$.request_sha256') = 'text'
AND json_type({document}, '$.schema') = 'text'
AND json_type({document}, '$.storage_key') = 'text'
AND json_type({document}, '$.uploader_person_id') = 'text'
AND json_type({document}, '$.uploader_user_id') = 'text'
AND json_extract({document}, '$.authorization_version') > 0
AND json_extract({document}, '$.schema') = '{FORMAL_SCHEMA}'
AND json_extract({document}, '$.provider') = '{FORMAL_PROVIDER}'
AND {purpose} IN {FORMAL_PURPOSES_SQL}
AND replace(json_extract({document}, '$.file_id'), '-', '') = {compact_id}
AND json_extract({document}, '$.storage_key') = {alias}.storage_key
AND {alias}.storage_key = {expected_key}
AND json_extract({document}, '$.uploader_user_id') = {alias}.uploaded_by
AND length(json_extract({document}, '$.uploader_person_id')) = 36
AND length(json_extract({document}, '$.idempotency_key_hash')) = 64
AND json_extract({document}, '$.idempotency_key_hash') NOT GLOB '*[^0-9a-f]*'
AND length(json_extract({document}, '$.request_sha256')) = 64
AND json_extract({document}, '$.request_sha256') NOT GLOB '*[^0-9a-f]*'
AND length({alias}.sha256) = 64
AND {alias}.sha256 NOT GLOB '*[^0-9a-f]*'
AND {alias}.size_bytes BETWEEN 1 AND {MAXIMUM_SIZE_BYTES}
AND {alias}.mime_type IN (
    'application/pdf','image/jpeg','image/png','image/webp',
    'image/heic','image/heif','video/mp4','video/quicktime'
)
AND (
    ({alias}.mime_type = 'application/pdf'
     AND lower({alias}.original_filename) LIKE '%.pdf')
 OR ({alias}.mime_type = 'image/jpeg'
     AND (lower({alias}.original_filename) LIKE '%.jpg'
          OR lower({alias}.original_filename) LIKE '%.jpeg'))
 OR ({alias}.mime_type = 'image/png'
     AND lower({alias}.original_filename) LIKE '%.png')
 OR ({alias}.mime_type = 'image/webp'
     AND lower({alias}.original_filename) LIKE '%.webp')
 OR ({alias}.mime_type = 'image/heic'
     AND lower({alias}.original_filename) LIKE '%.heic')
 OR ({alias}.mime_type = 'image/heif'
     AND lower({alias}.original_filename) LIKE '%.heif')
 OR ({alias}.mime_type = 'video/mp4'
     AND lower({alias}.original_filename) LIKE '%.mp4')
 OR ({alias}.mime_type = 'video/quicktime'
     AND lower({alias}.original_filename) LIKE '%.mov')
)
AND {alias}.original_filename IS NOT NULL
AND length({alias}.original_filename) BETWEEN 1 AND 200
AND length(CAST({alias}.original_filename AS BLOB)) <= 255
AND {alias}.original_filename = trim({alias}.original_filename)
AND instr({alias}.original_filename, '/') = 0
AND instr({alias}.original_filename, char(92)) = 0
AND {alias}.uploaded_by IS NOT NULL
AND julianday({alias}.created_at) <= julianday(CURRENT_TIMESTAMP)
{identity_sql}
{completion_sql}
""".strip()


def _postgresql_binding_file_sql(
    *,
    file_expression: str,
    purpose: str,
    user_expression: str,
    person_expression: str | None,
    bound_at_expression: str,
    require_current_identity: bool,
) -> str:
    base = _postgresql_file_shape_sql(
        "file_row",
        status="available",
        require_current_identity=require_current_identity,
    )
    person = "" if person_expression is None else (
        "AND (file_row.metadata_jsonb->>'uploader_person_id') = "
        f"{person_expression}::text"
    )
    return f"""
EXISTS (
    SELECT 1 FROM public.files AS file_row
     WHERE file_row.id = {file_expression}
       AND ({base})
       AND (file_row.metadata_jsonb->>'purpose') = '{purpose}'
       AND file_row.uploaded_by = {user_expression}
       {person}
       AND (file_row.metadata_jsonb->'completion'->>'verified_at')::timestamptz
           <= {bound_at_expression}
)
""".strip()


def _sqlite_binding_file_sql(
    *,
    file_expression: str,
    purpose: str,
    user_expression: str,
    person_expression: str | None,
    bound_at_expression: str,
    require_current_identity: bool,
) -> str:
    base = _sqlite_file_shape_sql(
        "file_row",
        status="available",
        require_current_identity=require_current_identity,
    )
    person = "" if person_expression is None else (
        "AND replace(json_extract(file_row.metadata_jsonb, "
        "'$.uploader_person_id'), '-', '') = "
        f"replace({person_expression}, '-', '')"
    )
    return f"""
EXISTS (
    SELECT 1 FROM files AS file_row
     WHERE replace(file_row.id, '-', '') = replace({file_expression}, '-', '')
       AND ({base})
       AND json_extract(file_row.metadata_jsonb, '$.purpose') = '{purpose}'
       AND file_row.uploaded_by = {user_expression}
       {person}
       AND julianday(json_extract(
               file_row.metadata_jsonb, '$.completion.verified_at'
           )) <= julianday({bound_at_expression})
)
""".strip()


def _upgrade_blocker_sql(dialect: str, prefix: str) -> str:
    shape = (
        lambda alias, status: _postgresql_file_shape_sql(
            alias,
            status=status,
            require_current_identity=False,
        )
        if dialect == "postgresql"
        else _sqlite_file_shape_sql(
            alias,
            status=status,
            require_current_identity=False,
        )
    )
    binding = (
        _postgresql_binding_file_sql
        if dialect == "postgresql"
        else _sqlite_binding_file_sql
    )
    request_binding = binding(
        file_expression="request_file.file_id",
        purpose="request_attachment",
        user_expression="request_file.created_by_user_id",
        person_expression=None,
        bound_at_expression="request_file.created_at",
        require_current_identity=False,
    )
    external_binding = binding(
        file_expression="registration.evidence_file_id",
        purpose="external_approval_evidence",
        user_expression="registration.registered_by_user_id",
        person_expression="registration.registered_by_person_id",
        bound_at_expression="registration.registered_at",
        require_current_identity=False,
    )
    stocktake_binding = binding(
        file_expression="attachment.file_id",
        purpose="stocktake_evidence",
        user_expression="completion.completed_by_user_id",
        person_expression="completion.completed_by_person_id",
        bound_at_expression="completion.completed_at",
        require_current_identity=False,
    )
    id_match = (
        "replace(attachment.document_id, '-', '') = replace(completion.id::text, '-', '')"
        if dialect == "postgresql"
        else "replace(attachment.document_id, '-', '') = replace(completion.id, '-', '')"
    )
    return f"""
EXISTS (
    SELECT 1 FROM {prefix}files AS file_row
     WHERE {_formal_candidate_sql(dialect, 'file_row')}
       AND NOT (({shape('file_row', 'pending')}) OR ({shape('file_row', 'available')}))
)
OR EXISTS (
    SELECT 1 FROM {prefix}material_request_files AS request_file
     WHERE request_file.purpose = 'request_attachment'
       AND NOT ({request_binding})
)
OR EXISTS (
    SELECT 1 FROM {prefix}approval_external_registrations AS registration
     WHERE NOT ({external_binding})
)
OR EXISTS (
    SELECT 1 FROM {prefix}approval_external_registrations
     GROUP BY evidence_file_id HAVING count(*) <> 1
)
OR EXISTS (
    SELECT 1 FROM {prefix}document_attachments AS attachment
     WHERE (attachment.document_type = 'stocktake_scope_count_completion'
            OR attachment.attachment_type = 'stocktake_evidence')
       AND NOT (
           attachment.document_type = 'stocktake_scope_count_completion'
           AND attachment.attachment_type = 'stocktake_evidence'
           AND attachment.status = 'active'
           AND EXISTS (
               SELECT 1 FROM {prefix}stocktake_scope_count_completions AS completion
                WHERE {id_match}
                  AND attachment.uploaded_by = completion.completed_by_user_id
                  AND attachment.created_at = completion.completed_at
                  AND ({stocktake_binding})
           )
       )
)
OR EXISTS (
    SELECT 1 FROM {prefix}document_attachments
     WHERE document_type = 'stocktake_scope_count_completion'
       AND attachment_type = 'stocktake_evidence'
     GROUP BY file_id HAVING count(*) <> 1
)
""".strip()


def _postgresql_file_function_sql() -> str:
    old_pending = _postgresql_file_shape_sql(
        "OLD", status="pending", require_current_identity=True
    )
    new_pending = _postgresql_file_shape_sql(
        "NEW", status="pending", require_current_identity=True
    )
    new_available = _postgresql_file_shape_sql(
        "NEW", status="available", require_current_identity=True
    )
    base_keys = (
        "authorization_version,file_id,idempotency_key_hash,provider,purpose,"
        "request_sha256,schema,storage_key,uploader_person_id,uploader_user_id"
    )
    base_equal = " AND ".join(
        f"NEW.metadata_jsonb->'{key}' = OLD.metadata_jsonb->'{key}'"
        for key in base_keys.split(",")
    )
    return f"""
CREATE FUNCTION public.{PG_FILE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'formal files are immutable';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NOT ({new_pending}) THEN
            RAISE EXCEPTION 'formal file upload intent is invalid';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE'
       OR NOT ({old_pending})
       OR NOT ({new_available})
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
       OR NEW.sha256 IS DISTINCT FROM OLD.sha256
       OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
       OR NEW.mime_type IS DISTINCT FROM OLD.mime_type
       OR NEW.original_filename IS DISTINCT FROM OLD.original_filename
       OR NEW.uploaded_by IS DISTINCT FROM OLD.uploaded_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NOT ({base_equal}) THEN
        RAISE EXCEPTION 'formal file completion transition is invalid';
    END IF;
    RETURN NEW;
END
$$
"""


def _postgresql_binding_function_sql() -> str:
    request_valid = _postgresql_binding_file_sql(
        file_expression="NEW.file_id",
        purpose="request_attachment",
        user_expression="NEW.created_by_user_id",
        person_expression=None,
        bound_at_expression="NEW.created_at",
        require_current_identity=True,
    )
    external_insert_valid = _postgresql_binding_file_sql(
        file_expression="NEW.evidence_file_id",
        purpose="external_approval_evidence",
        user_expression="NEW.registered_by_user_id",
        person_expression="NEW.registered_by_person_id",
        bound_at_expression="NEW.registered_at",
        require_current_identity=True,
    )
    external_static_valid = _postgresql_binding_file_sql(
        file_expression="NEW.evidence_file_id",
        purpose="external_approval_evidence",
        user_expression="NEW.registered_by_user_id",
        person_expression="NEW.registered_by_person_id",
        bound_at_expression="NEW.registered_at",
        require_current_identity=False,
    )
    stocktake_valid = _postgresql_binding_file_sql(
        file_expression="NEW.file_id",
        purpose="stocktake_evidence",
        user_expression="completion.completed_by_user_id",
        person_expression="completion.completed_by_person_id",
        bound_at_expression="completion.completed_at",
        require_current_identity=True,
    )
    return f"""
CREATE FUNCTION public.{PG_BINDING_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'formal file bindings are immutable';
    END IF;
    IF TG_TABLE_NAME = 'material_request_files' THEN
        IF NEW.purpose <> 'request_attachment' THEN
            RAISE EXCEPTION 'formal request attachment purpose is invalid';
        END IF;
        PERFORM id FROM public.files WHERE id = NEW.file_id FOR UPDATE;
        IF NOT ({request_valid}) THEN
            RAISE EXCEPTION 'formal request attachment is invalid';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'approval_external_registrations' THEN
        IF TG_OP = 'UPDATE' AND (
            NEW.id IS DISTINCT FROM OLD.id
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
        ) THEN
            RAISE EXCEPTION 'external approval evidence identity is immutable';
        END IF;
        PERFORM id FROM public.files WHERE id = NEW.evidence_file_id FOR UPDATE;
        IF (TG_OP = 'INSERT' AND NOT ({external_insert_valid}))
           OR (TG_OP = 'UPDATE' AND NOT ({external_static_valid})) THEN
            RAISE EXCEPTION 'external approval evidence is invalid';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'document_attachments' THEN
        -- The API receives table-level INSERT because PostgreSQL has no
        -- row-scoped grant.  This ALWAYS trigger is therefore the capability
        -- boundary: phase one permits only stocktake evidence here.  Demand
        -- attachments and external approval evidence use their dedicated
        -- tables, and any future document purpose requires a reviewed
        -- migration that explicitly expands this allowlist.
        IF TG_OP <> 'INSERT'
           OR NEW.document_type <> 'stocktake_scope_count_completion'
           OR NEW.attachment_type <> 'stocktake_evidence'
           OR NEW.status <> 'active' THEN
            RAISE EXCEPTION 'formal stocktake evidence is append-only';
        END IF;
        PERFORM id FROM public.files WHERE id = NEW.file_id FOR UPDATE;
        IF NOT EXISTS (
            SELECT 1
              FROM public.stocktake_scope_count_completions AS completion
             WHERE replace(NEW.document_id, '-', '') = replace(completion.id::text, '-', '')
               AND NEW.uploaded_by = completion.completed_by_user_id
               AND NEW.created_at = completion.completed_at
               AND ({stocktake_valid})
        ) THEN
            RAISE EXCEPTION 'formal stocktake evidence binding is invalid';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'formal file binding target is invalid';
END
$$
"""


def _create_postgresql_triggers() -> None:
    for trigger_name, (table_name, kind) in PG_TRIGGERS.items():
        function_name = (
            PG_FILE_FUNCTION if table_name == "files" else PG_BINDING_FUNCTION
        )
        if kind == "truncate":
            sql = (
                f"CREATE TRIGGER {trigger_name} BEFORE TRUNCATE ON public.{table_name} "
                f"FOR EACH STATEMENT EXECUTE FUNCTION public.{function_name}()"
            )
        elif kind == "insert":
            sql = (
                f"CREATE TRIGGER {trigger_name} BEFORE INSERT "
                f"ON public.{table_name} FOR EACH ROW "
                f"EXECUTE FUNCTION public.{function_name}()"
            )
        else:
            sql = (
                f"CREATE TRIGGER {trigger_name} BEFORE INSERT OR UPDATE OR DELETE "
                f"ON public.{table_name} FOR EACH ROW "
                f"EXECUTE FUNCTION public.{function_name}()"
            )
        op.execute(sql)
        op.execute(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
        )


def _sqlite_trigger_names() -> tuple[str, ...]:
    return (
        "trg_files_formal_insert_0036",
        "trg_files_formal_update_0036",
        "trg_files_formal_delete_0036",
        "trg_material_request_files_formal_insert_0036",
        "trg_approval_external_registrations_evidence_insert_0036",
        "trg_approval_external_registrations_evidence_update_0036",
        "trg_approval_external_registrations_evidence_delete_0036",
        "trg_document_attachments_stocktake_evidence_insert_0036",
        "trg_document_attachments_stocktake_evidence_update_0036",
        "trg_document_attachments_stocktake_evidence_delete_0036",
    )


def _create_sqlite_triggers() -> None:
    pending = _sqlite_file_shape_sql(
        "NEW", status="pending", require_current_identity=True
    )
    old_pending = _sqlite_file_shape_sql(
        "OLD", status="pending", require_current_identity=True
    )
    available = _sqlite_file_shape_sql(
        "NEW", status="available", require_current_identity=True
    )
    base_keys = (
        "authorization_version,file_id,idempotency_key_hash,provider,purpose,"
        "request_sha256,schema,storage_key,uploader_person_id,uploader_user_id"
    )
    base_changed = " OR ".join(
        "json_extract(NEW.metadata_jsonb, '$.{0}') IS NOT "
        "json_extract(OLD.metadata_jsonb, '$.{0}')".format(key)
        for key in base_keys.split(",")
    )
    op.execute(f"""
CREATE TRIGGER trg_files_formal_insert_0036 BEFORE INSERT ON files
WHEN NOT ({pending})
BEGIN SELECT RAISE(ABORT, 'formal file upload intent is invalid'); END
""")
    op.execute(f"""
CREATE TRIGGER trg_files_formal_update_0036 BEFORE UPDATE ON files
WHEN NOT (({old_pending}) AND ({available})
 AND NEW.id IS OLD.id AND NEW.storage_key IS OLD.storage_key
 AND NEW.sha256 IS OLD.sha256 AND NEW.size_bytes IS OLD.size_bytes
 AND NEW.mime_type IS OLD.mime_type
 AND NEW.original_filename IS OLD.original_filename
 AND NEW.uploaded_by IS OLD.uploaded_by AND NEW.created_at IS OLD.created_at
 AND NOT ({base_changed}))
BEGIN SELECT RAISE(ABORT, 'formal file completion transition is invalid'); END
""")
    op.execute("""
CREATE TRIGGER trg_files_formal_delete_0036 BEFORE DELETE ON files
BEGIN SELECT RAISE(ABORT, 'formal files are immutable'); END
""")

    request_valid = _sqlite_binding_file_sql(
        file_expression="NEW.file_id",
        purpose="request_attachment",
        user_expression="NEW.created_by_user_id",
        person_expression=None,
        bound_at_expression="NEW.created_at",
        require_current_identity=True,
    )
    op.execute(f"""
CREATE TRIGGER trg_material_request_files_formal_insert_0036
BEFORE INSERT ON material_request_files
WHEN NEW.purpose <> 'request_attachment' OR NOT ({request_valid})
BEGIN SELECT RAISE(ABORT, 'formal request attachment is invalid'); END
""")
    external_insert_valid = _sqlite_binding_file_sql(
        file_expression="NEW.evidence_file_id",
        purpose="external_approval_evidence",
        user_expression="NEW.registered_by_user_id",
        person_expression="NEW.registered_by_person_id",
        bound_at_expression="NEW.registered_at",
        require_current_identity=True,
    )
    external_static_valid = _sqlite_binding_file_sql(
        file_expression="NEW.evidence_file_id",
        purpose="external_approval_evidence",
        user_expression="NEW.registered_by_user_id",
        person_expression="NEW.registered_by_person_id",
        bound_at_expression="NEW.registered_at",
        require_current_identity=False,
    )
    op.execute(f"""
CREATE TRIGGER trg_approval_external_registrations_evidence_insert_0036
BEFORE INSERT ON approval_external_registrations
WHEN NOT ({external_insert_valid})
BEGIN SELECT RAISE(ABORT, 'external approval evidence is invalid'); END
""")
    op.execute(f"""
CREATE TRIGGER trg_approval_external_registrations_evidence_update_0036
BEFORE UPDATE ON approval_external_registrations
WHEN NOT ({external_static_valid})
 OR NEW.id IS NOT OLD.id OR NEW.step_id IS NOT OLD.step_id
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
BEGIN SELECT RAISE(ABORT, 'external approval evidence identity is immutable'); END
""")
    op.execute("""
CREATE TRIGGER trg_approval_external_registrations_evidence_delete_0036
BEFORE DELETE ON approval_external_registrations
BEGIN SELECT RAISE(ABORT, 'external approval evidence is immutable'); END
""")

    stocktake_valid = _sqlite_binding_file_sql(
        file_expression="NEW.file_id",
        purpose="stocktake_evidence",
        user_expression="completion.completed_by_user_id",
        person_expression="completion.completed_by_person_id",
        bound_at_expression="completion.completed_at",
        require_current_identity=True,
    )
    # SQLite has no database roles, but keeps the same fail-closed semantic as
    # PostgreSQL: document_attachments is not a generic upload escape hatch.
    op.execute(f"""
CREATE TRIGGER trg_document_attachments_stocktake_evidence_insert_0036
BEFORE INSERT ON document_attachments
WHEN NEW.document_type <> 'stocktake_scope_count_completion'
 OR NEW.attachment_type <> 'stocktake_evidence'
 OR NEW.status <> 'active'
 OR NOT EXISTS (
    SELECT 1 FROM stocktake_scope_count_completions AS completion
     WHERE replace(NEW.document_id, '-', '') = replace(completion.id, '-', '')
       AND NEW.uploaded_by = completion.completed_by_user_id
       AND NEW.created_at = completion.completed_at
       AND ({stocktake_valid})
 )
BEGIN SELECT RAISE(ABORT, 'formal stocktake evidence binding is invalid'); END
""")
    for operation in ("UPDATE", "DELETE"):
        op.execute(f"""
CREATE TRIGGER trg_document_attachments_stocktake_evidence_{operation.lower()}_0036
BEFORE {operation} ON document_attachments
BEGIN SELECT RAISE(ABORT, 'formal stocktake evidence is append-only'); END
""")


def _apply_postgresql_acl() -> None:
    op.execute("REVOKE ALL ON TABLE public.files FROM PUBLIC")
    op.execute("REVOKE ALL ON TABLE public.document_attachments FROM PUBLIC")
    op.execute(
        f"REVOKE ALL ON TABLE public.files, public.document_attachments "
        f"FROM {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"GRANT SELECT, INSERT ON TABLE public.files TO {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"GRANT UPDATE (status, metadata_jsonb) ON TABLE public.files "
        f"TO {PRODUCTION_API_ROLE}"
    )
    op.execute(
        "GRANT SELECT, INSERT ON TABLE public.document_attachments "
        f"TO {PRODUCTION_API_ROLE}"
    )
    for function_name in (PG_FILE_FUNCTION, PG_BINDING_FUNCTION):
        signature = f"public.{function_name}()"
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
        op.execute(
            f"REVOKE EXECUTE ON FUNCTION {signature} FROM {PRODUCTION_API_ROLE}"
        )
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{MIGRATION_ROLE}') THEN
        EXECUTE 'ALTER FUNCTION public.{PG_FILE_FUNCTION}() OWNER TO {MIGRATION_ROLE}';
        EXECUTE 'ALTER FUNCTION public.{PG_BINDING_FUNCTION}() OWNER TO {MIGRATION_ROLE}';
    END IF;
END
$$
"""
    )


def _restore_postgresql_acl() -> None:
    op.execute(
        f"REVOKE ALL ON TABLE public.files, public.document_attachments "
        f"FROM {PRODUCTION_API_ROLE}"
    )
    op.execute(f"GRANT SELECT ON TABLE public.files TO {PRODUCTION_API_ROLE}")
