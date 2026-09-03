"""Seal formal material-request draft content to immutable command facts.

Revision ID: 20260903_0046
Revises: 20260903_0045
Create Date: 2026-09-03

The 0045 approval boundary proves state and approval causality, but a caller
with the runtime table grants could still replace draft lines or attachment
bindings without appending the matching ``create``/``update_draft`` command.
This revision gives PostgreSQL sole ownership of a deterministic content
projection digest on those commands and revalidates the current revision,
line and attachment post-image at transaction end.

Approval, allocation, reservation, outbound, shipment, logistics signature,
OAM receipt, personal inbound, notification and reconciliation projections are
deliberately excluded.  They remain independent state machines under the V1.0
baseline.  This migration creates no business facts and does not enable the
application write flag.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op


revision: str = "20260903_0046"
down_revision: Union[str, Sequence[str], None] = "20260903_0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
OAM_RUNTIME_READY_FUNCTION = "rsc_oam_runtime_binding_ready_0044"
PREVIOUS_SCHEMA_REVISION = "20260903_0045"

MANIFEST_COLUMN = "projection_manifest_sha256"
MANIFEST_CONSTRAINT = "ck_material_request_commands_projection_manifest_0046"
MANIFEST_COMMENT = (
    "PostgreSQL trigger-owned material-request content projection digest"
)
CONTENT_OPERATIONS = ("create", "update_draft", "submit")
CONTENT_OPERATIONS_SQL = "('create', 'update_draft', 'submit')"
MANIFEST_SCHEMA = "rsc.material_request.content.pg16.v1"

PG_CONTENT_GUARD_FUNCTION = "rsc_guard_material_request_content_write_0046"
PG_CONTENT_VALIDATE_FUNCTION = (
    "rsc_validate_material_request_content_causality_0046"
)
PG_CONTENT_DISPATCH_FUNCTION = (
    "rsc_dispatch_material_request_content_causality_0046"
)

IMMEDIATE_TRIGGER_TABLES = (
    "material_request_revisions",
    "material_request_lines",
    "material_request_files",
    "material_request_commands",
)
IMMEDIATE_TRIGGER_BINDINGS = tuple(
    (
        table_name,
        f"trg_{table_name}_content_write_0046",
    )
    for table_name in IMMEDIATE_TRIGGER_TABLES
)
DEFERRED_TRIGGER_BINDINGS = tuple(
    (
        table_name,
        f"trg_{table_name}_content_causality_0046",
    )
    for table_name in IMMEDIATE_TRIGGER_TABLES
)
TRIGGER_BINDINGS = IMMEDIATE_TRIGGER_BINDINGS + DEFERRED_TRIGGER_BINDINGS

CONTENT_TABLES = (
    "material_requests",
    "material_request_revisions",
    "material_request_lines",
    "material_request_files",
    "material_request_commands",
)
UPGRADE_BLOCKER = (
    "0046 material request content causality requires an empty formal "
    "material-request graph"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0046 while formal material-request facts exist"
)
GUARD_ERROR = "formal material request content projection is invalid"


def upgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect == "sqlite":
        op.add_column(
            "material_request_commands",
            sa.Column(
                MANIFEST_COLUMN,
                sa.String(length=64),
                nullable=True,
                comment=MANIFEST_COMMENT,
            ),
        )
        return
    if dialect != "postgresql":
        raise RuntimeError("0046 supports only PostgreSQL and SQLite test databases")

    _lock_content_tables()
    _emit_empty_graph_guard(UPGRADE_BLOCKER)
    op.add_column(
        "material_request_commands",
        sa.Column(
            MANIFEST_COLUMN,
            sa.String(length=64),
            nullable=True,
            comment=MANIFEST_COMMENT,
        ),
        schema="public",
    )
    op.create_check_constraint(
        MANIFEST_CONSTRAINT,
        "material_request_commands",
        (
            f"((operation IN {CONTENT_OPERATIONS_SQL} AND "
            f"{MANIFEST_COLUMN} IS NOT NULL AND "
            f"{MANIFEST_COLUMN} ~ '^[0-9a-f]{{64}}$') OR "
            f"(operation NOT IN {CONTENT_OPERATIONS_SQL} AND "
            f"{MANIFEST_COLUMN} IS NULL))"
        ),
        schema="public",
    )
    _create_postgresql_functions()
    _create_postgresql_triggers()
    _replace_oam_runtime_ready_function(revision)


def downgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect == "sqlite":
        op.drop_column("material_request_commands", MANIFEST_COLUMN)
        return
    if dialect != "postgresql":
        raise RuntimeError("0046 supports only PostgreSQL and SQLite test databases")
    if context.is_offline_mode():
        raise RuntimeError("0046 PostgreSQL downgrade requires an online connection")

    _lock_content_tables()
    _emit_empty_graph_guard(DOWNGRADE_BLOCKER)
    for table_name, trigger_name in reversed(DEFERRED_TRIGGER_BINDINGS):
        op.execute(f"DROP TRIGGER {trigger_name} ON public.{table_name}")
    for table_name, trigger_name in reversed(IMMEDIATE_TRIGGER_BINDINGS):
        op.execute(f"DROP TRIGGER {trigger_name} ON public.{table_name}")
    for function_name, argument_types in (
        (PG_CONTENT_DISPATCH_FUNCTION, ""),
        (PG_CONTENT_VALIDATE_FUNCTION, "uuid"),
        (PG_CONTENT_GUARD_FUNCTION, ""),
    ):
        op.execute(f"DROP FUNCTION public.{function_name}({argument_types})")
    op.drop_constraint(
        MANIFEST_CONSTRAINT,
        "material_request_commands",
        type_="check",
        schema="public",
    )
    op.drop_column(
        "material_request_commands", MANIFEST_COLUMN, schema="public"
    )
    _replace_oam_runtime_ready_function(PREVIOUS_SCHEMA_REVISION)


def _lock_content_tables() -> None:
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in CONTENT_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )


def _emit_empty_graph_guard(message: str) -> None:
    condition = "\n       OR ".join(
        f"EXISTS (SELECT 1 FROM public.{table_name})"
        for table_name in CONTENT_TABLES
    )
    op.execute(
        f"""
DO $rsc_0046$
BEGIN
    IF {condition} THEN
        RAISE EXCEPTION '{message}';
    END IF;
END
$rsc_0046$
"""
    )


def _utc_timestamp_json(expression: str) -> str:
    return (
        "pg_catalog.to_char(pg_catalog.timezone('UTC', "
        f"{expression}), 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')"
    )


def _projection_manifest_expression(revision_id_expression: str) -> str:
    """Return one canonical PostgreSQL expression used by both guard paths."""

    revision_created_at = _utc_timestamp_json("manifest_revision.created_at")
    line_created_at = _utc_timestamp_json("manifest_line.created_at")
    binding_created_at = _utc_timestamp_json("manifest_binding.created_at")
    return f"""
(
    SELECT pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                pg_catalog.jsonb_build_object(
                    'schema', '{MANIFEST_SCHEMA}',
                    'revision', pg_catalog.jsonb_build_object(
                        'id', manifest_revision.id::text,
                        'request_id', manifest_revision.request_id::text,
                        'revision_no', manifest_revision.revision_no,
                        'previous_revision_id',
                            CASE
                                WHEN manifest_revision.previous_revision_id IS NULL
                                    THEN NULL
                                ELSE manifest_revision.previous_revision_id::text
                            END,
                        'created_by_user_id',
                            manifest_revision.created_by_user_id,
                        'created_at', {revision_created_at},
                        'work_order_id',
                            CASE
                                WHEN manifest_revision.work_order_id IS NULL
                                    THEN NULL
                                ELSE manifest_revision.work_order_id::text
                            END,
                        'purpose', manifest_revision.purpose,
                        'urgency', manifest_revision.urgency,
                        'expected_date',
                            CASE
                                WHEN manifest_revision.expected_date IS NULL
                                    THEN NULL
                                ELSE pg_catalog.to_char(
                                    manifest_revision.expected_date,
                                    'YYYY-MM-DD'
                                )
                            END,
                        'address_snapshot',
                            manifest_revision.address_snapshot_jsonb,
                        'address_masked',
                            manifest_revision.address_masked_jsonb,
                        'contact_envelope',
                            manifest_revision.contact_snapshot_jsonb,
                        'contact_masked',
                            manifest_revision.contact_masked_jsonb,
                        'note', manifest_revision.note,
                        'approval_mode', manifest_revision.approval_mode
                    ),
                    'lines', pg_catalog.coalesce((
                        SELECT pg_catalog.jsonb_agg(
                            pg_catalog.jsonb_build_object(
                                'id', manifest_line.id::text,
                                'request_id', manifest_line.request_id::text,
                                'revision_id', manifest_line.revision_id::text,
                                'revision_no', manifest_line.revision_no,
                                'line_no', manifest_line.line_no,
                                'client_line_key',
                                    manifest_line.client_line_key::text,
                                'material_id', manifest_line.material_id::text,
                                'suggested_substitute_material_id',
                                    CASE
                                        WHEN manifest_line.suggested_substitute_material_id
                                             IS NULL THEN NULL
                                        ELSE manifest_line.suggested_substitute_material_id::text
                                    END,
                                'requested_qty',
                                    manifest_line.requested_qty::text,
                                'required_date',
                                    CASE
                                        WHEN manifest_line.required_date IS NULL
                                            THEN NULL
                                        ELSE pg_catalog.to_char(
                                            manifest_line.required_date,
                                            'YYYY-MM-DD'
                                        )
                                    END,
                                'note', manifest_line.note,
                                'created_at', {line_created_at}
                            ) ORDER BY manifest_line.line_no, manifest_line.id
                        )
                          FROM public.material_request_lines AS manifest_line
                         WHERE manifest_line.request_id =
                                   manifest_revision.request_id
                           AND manifest_line.revision_id = manifest_revision.id
                           AND manifest_line.revision_no =
                                   manifest_revision.revision_no
                    ), '[]'::jsonb),
                    'files', pg_catalog.coalesce((
                        SELECT pg_catalog.jsonb_agg(
                            pg_catalog.jsonb_build_object(
                                'binding_id', manifest_binding.id::text,
                                'request_id',
                                    manifest_binding.request_id::text,
                                'revision_id',
                                    manifest_binding.revision_id::text,
                                'revision_no',
                                    manifest_binding.revision_no,
                                'request_line_id',
                                    CASE
                                        WHEN manifest_binding.request_line_id IS NULL
                                            THEN NULL
                                        ELSE manifest_binding.request_line_id::text
                                    END,
                                'file_id', manifest_binding.file_id::text,
                                'purpose', manifest_binding.purpose,
                                'created_by_user_id',
                                    manifest_binding.created_by_user_id,
                                'created_at', {binding_created_at},
                                'file_sha256', manifest_file.sha256,
                                'file_size_bytes', manifest_file.size_bytes,
                                'file_mime_type', manifest_file.mime_type,
                                'file_original_filename',
                                    manifest_file.original_filename,
                                'file_uploaded_by', manifest_file.uploaded_by
                            ) ORDER BY manifest_binding.id
                        )
                          FROM public.material_request_files AS manifest_binding
                          JOIN public.files AS manifest_file
                            ON manifest_file.id = manifest_binding.file_id
                         WHERE manifest_binding.request_id =
                                   manifest_revision.request_id
                           AND manifest_binding.revision_id = manifest_revision.id
                           AND manifest_binding.revision_no =
                                   manifest_revision.revision_no
                    ), '[]'::jsonb)
                )::text,
                'UTF8'
            )
        ),
        'hex'
    )
      FROM public.material_request_revisions AS manifest_revision
     WHERE manifest_revision.id = {revision_id_expression}
)
""".strip()


def _content_guard_sql() -> str:
    manifest_expression = _projection_manifest_expression("guard_revision.id")
    return f"""
CREATE FUNCTION public.{PG_CONTENT_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict error
DECLARE
    guard_request_id uuid;
    guard_request public.material_requests%ROWTYPE;
    guard_revision public.material_request_revisions%ROWTYPE;
    guard_computed_manifest text;
    guard_origin_manifest text;
    guard_result_kind text;
BEGIN
    IF TG_TABLE_NAME = 'material_request_commands' THEN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        guard_request_id := NEW.request_id;
    ELSIF TG_OP = 'DELETE' THEN
        guard_request_id := OLD.request_id;
    ELSE
        guard_request_id := NEW.request_id;
    END IF;

    SELECT request.* INTO guard_request
      FROM public.material_requests AS request
     WHERE request.id = guard_request_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF TG_TABLE_NAME = 'material_request_files' AND TG_OP = 'INSERT' THEN
        IF NEW.created_by_user_id <> guard_request.requester_user_id
           OR NOT EXISTS (
               SELECT 1
                 FROM public.files AS file_row
                WHERE file_row.id = NEW.file_id
                  AND file_row.uploaded_by = guard_request.requester_user_id
                  AND file_row.status = 'available'
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;

    IF TG_TABLE_NAME <> 'material_request_commands' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.{MANIFEST_COLUMN} IS NOT NULL THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF NEW.operation NOT IN {CONTENT_OPERATIONS_SQL} THEN
        RETURN NEW;
    END IF;

    IF NEW.target_version <> guard_request.version
       OR NEW.actor_user_id <> guard_request.requester_user_id
       OR NEW.actor_person_id <> guard_request.requester_person_id
       OR jsonb_typeof(NEW.request_jsonb) <> 'object'
       OR (SELECT count(*) FROM jsonb_object_keys(NEW.request_jsonb)) <> 9
       OR NOT NEW.request_jsonb ?& ARRAY[
           'schema','operation','request_id','revision_id','revision_no',
           'target_version','approval_attempt_no','payload_sha256',
           'sensitive_fields'
       ]
       OR NEW.request_jsonb->>'schema' IS DISTINCT FROM
           'rsc.material_request_command.v1'
       OR NEW.request_jsonb->>'operation' IS DISTINCT FROM NEW.operation
       OR NEW.request_jsonb->>'request_id'
          IS DISTINCT FROM guard_request.id::text
       OR NEW.request_jsonb->>'target_version'
          IS DISTINCT FROM NEW.target_version::text
       OR NEW.request_jsonb->>'payload_sha256'
          IS DISTINCT FROM NEW.request_hash
       OR NEW.request_jsonb->>'sensitive_fields'
          IS DISTINCT FROM 'excluded'
       OR jsonb_typeof(NEW.request_jsonb->'revision_no')
          IS DISTINCT FROM 'number'
       OR jsonb_typeof(NEW.request_jsonb->'target_version')
          IS DISTINCT FROM 'number'
       OR (NEW.operation IN ('create', 'update_draft') AND
           NEW.request_jsonb->'approval_attempt_no'
           IS DISTINCT FROM 'null'::jsonb)
       OR (NEW.operation = 'submit' AND (
           jsonb_typeof(NEW.request_jsonb->'approval_attempt_no')
               IS DISTINCT FROM 'number'
           OR NEW.request_jsonb->>'approval_attempt_no'
               !~ '^[1-9][0-9]*$'
           OR jsonb_typeof(NEW.result_jsonb->'approval_attempt_no')
               IS DISTINCT FROM 'number'
           OR NEW.result_jsonb->>'approval_attempt_no'
               !~ '^[1-9][0-9]*$'
           OR NEW.request_jsonb->>'approval_attempt_no'
               IS DISTINCT FROM
               NEW.result_jsonb->>'approval_attempt_no'
       ))
       OR jsonb_typeof(NEW.result_jsonb) IS DISTINCT FROM 'object'
       OR NEW.result_jsonb->>'request_id'
          IS DISTINCT FROM guard_request.id::text THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    SELECT candidate.* INTO guard_revision
      FROM public.material_request_revisions AS candidate
     WHERE candidate.request_id = guard_request.id
       AND candidate.id::text = NEW.request_jsonb->>'revision_id'
       AND candidate.revision_no::text = NEW.request_jsonb->>'revision_no'
       AND candidate.revision_no = guard_request.revision_no;
    IF NOT FOUND
       OR NEW.result_jsonb->>'revision_id'
          IS DISTINCT FROM guard_revision.id::text
       OR NEW.result_jsonb->>'revision_no'
          IS DISTINCT FROM guard_revision.revision_no::text
       OR guard_revision.created_by_user_id <>
           guard_request.requester_user_id THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF NEW.operation = 'submit' AND NOT EXISTS (
        SELECT 1
          FROM public.approval_instances AS approval_instance
         WHERE approval_instance.id::text =
                   NEW.result_jsonb->>'approval_instance_id'
           AND approval_instance.request_id = guard_request.id
           AND approval_instance.request_revision_id = guard_revision.id
           AND approval_instance.revision_no = guard_revision.revision_no
           AND approval_instance.attempt_no::text =
                   NEW.request_jsonb->>'approval_attempt_no'
           AND approval_instance.created_at = NEW.occurred_at
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    guard_result_kind := CASE NEW.operation
        WHEN 'create' THEN 'create'
        WHEN 'update_draft' THEN 'draft'
        ELSE 'submit'
    END;
    IF NEW.result_jsonb->>'kind' IS DISTINCT FROM guard_result_kind
       OR (NEW.operation = 'create' AND (
           guard_request.status <> 'draft'
           OR guard_request.version <> 0
           OR guard_revision.status <> 'draft'
           OR guard_revision.revision_no <> 1
           OR guard_revision.previous_revision_id IS NOT NULL
       ))
       OR (NEW.operation = 'update_draft' AND (
           guard_request.status NOT IN ('draft', 'returned')
           OR guard_revision.status <> 'draft'
       ))
       OR (NEW.operation = 'submit' AND (
           guard_request.status <> 'approval_in_progress'
           OR guard_revision.status <> 'sealed'
       )) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    guard_computed_manifest := {manifest_expression};
    IF guard_computed_manifest IS NULL THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF NEW.operation = 'submit' THEN
        SELECT command.{MANIFEST_COLUMN}
          INTO guard_origin_manifest
          FROM public.material_request_commands AS command
         WHERE command.request_id = guard_request.id
           AND command.operation IN ('create', 'update_draft')
           AND command.request_jsonb->>'revision_id' = guard_revision.id::text
           AND command.request_jsonb->>'revision_no' =
                   guard_revision.revision_no::text
         ORDER BY command.target_version DESC
         LIMIT 1;
        IF NOT FOUND
           OR guard_origin_manifest IS DISTINCT FROM guard_computed_manifest THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;

    NEW.{MANIFEST_COLUMN} := guard_computed_manifest;
    RETURN NEW;
END
$$
"""


def _content_validator_sql() -> str:
    manifest_expression = _projection_manifest_expression("guard_revision.id")
    return f"""
CREATE FUNCTION public.{PG_CONTENT_VALIDATE_FUNCTION}(
    checked_request_id uuid
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict error
DECLARE
    guard_request public.material_requests%ROWTYPE;
    guard_revision public.material_request_revisions%ROWTYPE;
    guard_origin public.material_request_commands%ROWTYPE;
    guard_submit public.material_request_commands%ROWTYPE;
    guard_computed_manifest text;
    guard_line_ids jsonb;
    guard_line_count bigint;
    guard_minimum_line_no integer;
    guard_maximum_line_no integer;
    guard_file_count bigint;
    guard_create_count bigint;
    guard_submit_count bigint;
    guard_expected_origin_version bigint;
BEGIN
    SELECT request.* INTO guard_request
      FROM public.material_requests AS request
     WHERE request.id = checked_request_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.material_request_commands AS command
         WHERE command.request_id = checked_request_id
           AND (
               (command.operation IN {CONTENT_OPERATIONS_SQL}
                AND (command.{MANIFEST_COLUMN} IS NULL
                     OR command.{MANIFEST_COLUMN} !~ '^[0-9a-f]{{64}}$'))
               OR (command.operation NOT IN {CONTENT_OPERATIONS_SQL}
                   AND command.{MANIFEST_COLUMN} IS NOT NULL)
           )
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    FOR guard_revision IN
        SELECT revision_row.*
          FROM public.material_request_revisions AS revision_row
         WHERE revision_row.request_id = checked_request_id
         ORDER BY revision_row.revision_no
    LOOP
        guard_computed_manifest := {manifest_expression};
        SELECT command.* INTO guard_origin
          FROM public.material_request_commands AS command
         WHERE command.request_id = checked_request_id
           AND command.operation IN ('create', 'update_draft')
           AND command.request_jsonb->>'revision_id' = guard_revision.id::text
           AND command.request_jsonb->>'revision_no' =
                   guard_revision.revision_no::text
         ORDER BY command.target_version DESC
         LIMIT 1;
        IF NOT FOUND
           OR guard_computed_manifest IS NULL
           OR guard_origin.{MANIFEST_COLUMN}
              IS DISTINCT FROM guard_computed_manifest
           OR guard_origin.actor_user_id <>
              guard_request.requester_user_id
           OR guard_origin.actor_person_id <>
              guard_request.requester_person_id
           OR guard_origin.created_at IS DISTINCT FROM
              guard_origin.occurred_at
           OR guard_origin.result_jsonb->>'request_id' IS DISTINCT FROM
              checked_request_id::text
           OR guard_origin.result_jsonb->>'revision_id' IS DISTINCT FROM
              guard_revision.id::text
           OR guard_origin.result_jsonb->>'revision_no' IS DISTINCT FROM
              guard_revision.revision_no::text THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;

        SELECT pg_catalog.coalesce(
                   pg_catalog.jsonb_agg(
                       pg_catalog.to_jsonb(line.id::text)
                       ORDER BY line.line_no, line.id
                   ),
                   '[]'::jsonb
               ),
               count(*), min(line.line_no), max(line.line_no)
          INTO guard_line_ids, guard_line_count,
               guard_minimum_line_no, guard_maximum_line_no
          FROM public.material_request_lines AS line
         WHERE line.request_id = checked_request_id
           AND line.revision_id = guard_revision.id
           AND line.revision_no = guard_revision.revision_no;
        SELECT count(*) INTO guard_file_count
          FROM public.material_request_files AS binding
         WHERE binding.request_id = checked_request_id
           AND binding.revision_id = guard_revision.id
           AND binding.revision_no = guard_revision.revision_no;
        IF guard_line_count NOT BETWEEN 1 AND 200
           OR guard_minimum_line_no <> 1
           OR guard_maximum_line_no <> guard_line_count
           OR guard_file_count > 20
           OR guard_origin.result_jsonb->'line_ids'
              IS DISTINCT FROM guard_line_ids
           OR EXISTS (
               SELECT 1
                 FROM public.material_request_lines AS line
                WHERE line.request_id = checked_request_id
                  AND line.revision_id = guard_revision.id
                  AND line.revision_no = guard_revision.revision_no
                  AND line.created_at IS DISTINCT FROM
                      guard_origin.occurred_at
           )
           OR EXISTS (
               SELECT 1
                 FROM public.material_request_files AS binding
                 JOIN public.files AS file_row ON file_row.id = binding.file_id
                WHERE binding.request_id = checked_request_id
                  AND binding.revision_id = guard_revision.id
                  AND binding.revision_no = guard_revision.revision_no
                  AND (
                      binding.purpose <> 'request_attachment'
                      OR binding.request_line_id IS NOT NULL
                      OR binding.created_by_user_id <>
                         guard_request.requester_user_id
                      OR file_row.uploaded_by <>
                         guard_request.requester_user_id
                      OR file_row.status <> 'available'
                      OR binding.created_at IS DISTINCT FROM
                         guard_origin.occurred_at
                  )
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;

        SELECT count(*) INTO guard_create_count
          FROM public.material_request_commands AS command
         WHERE command.request_id = checked_request_id
           AND command.operation = 'create'
           AND command.request_jsonb->>'revision_id' = guard_revision.id::text
           AND command.request_jsonb->>'revision_no' =
                   guard_revision.revision_no::text;
        IF (guard_revision.revision_no = 1 AND guard_create_count <> 1)
           OR (guard_revision.revision_no > 1 AND guard_create_count <> 0) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;

        SELECT count(*) INTO guard_submit_count
          FROM public.material_request_commands AS command
         WHERE command.request_id = checked_request_id
           AND command.operation = 'submit'
           AND command.request_jsonb->>'revision_id' = guard_revision.id::text
           AND command.request_jsonb->>'revision_no' =
                   guard_revision.revision_no::text;
        IF guard_revision.status = 'sealed' THEN
            IF guard_submit_count <> 1 THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
            SELECT command.* INTO STRICT guard_submit
              FROM public.material_request_commands AS command
             WHERE command.request_id = checked_request_id
               AND command.operation = 'submit'
               AND command.request_jsonb->>'revision_id' =
                       guard_revision.id::text
               AND command.request_jsonb->>'revision_no' =
                       guard_revision.revision_no::text;
            IF guard_submit.{MANIFEST_COLUMN}
                   IS DISTINCT FROM guard_computed_manifest
               OR guard_submit.{MANIFEST_COLUMN}
                   IS DISTINCT FROM guard_origin.{MANIFEST_COLUMN}
               OR guard_submit.target_version <>
                   guard_origin.target_version + 1
               OR guard_revision.sealed_at IS DISTINCT FROM
                   guard_submit.occurred_at THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
        ELSIF guard_revision.status = 'draft' THEN
            IF guard_submit_count <> 0 THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
            IF guard_revision.revision_no <> guard_request.revision_no
               OR guard_request.status NOT IN ('draft', 'returned', 'cancelled')
               OR guard_revision.updated_at IS DISTINCT FROM
                  guard_origin.occurred_at THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
            guard_expected_origin_version := CASE guard_request.status
                WHEN 'cancelled' THEN guard_request.version - 1
                ELSE guard_request.version
            END;
            IF guard_origin.target_version <> guard_expected_origin_version
               OR (guard_request.status = 'draft'
                   AND guard_request.version = 0
                   AND guard_origin.operation <> 'create')
               OR ((guard_request.status <> 'draft'
                    OR guard_request.version > 0)
                   AND guard_origin.operation <> 'update_draft') THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
        ELSE
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END LOOP;
END
$$
"""


def _content_dispatcher_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_CONTENT_DISPATCH_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict error
DECLARE
    guard_old_request_id uuid;
    guard_new_request_id uuid;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        guard_old_request_id := OLD.request_id;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        guard_new_request_id := NEW.request_id;
    END IF;

    IF guard_old_request_id IS NOT NULL THEN
        PERFORM public.{PG_CONTENT_VALIDATE_FUNCTION}(guard_old_request_id);
    END IF;
    IF guard_new_request_id IS NOT NULL
       AND guard_new_request_id IS DISTINCT FROM guard_old_request_id THEN
        PERFORM public.{PG_CONTENT_VALIDATE_FUNCTION}(guard_new_request_id);
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$
"""


def _create_postgresql_functions() -> None:
    for sql in (
        _content_guard_sql(),
        _content_validator_sql(),
        _content_dispatcher_sql(),
    ):
        op.execute(sql)
    for function_name, argument_types in (
        (PG_CONTENT_GUARD_FUNCTION, ""),
        (PG_CONTENT_VALIDATE_FUNCTION, "uuid"),
        (PG_CONTENT_DISPATCH_FUNCTION, ""),
    ):
        op.execute(
            f"ALTER FUNCTION public.{function_name}({argument_types}) "
            f"OWNER TO {MIGRATION_ROLE}"
        )
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{function_name}({argument_types}) "
            f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
        )


def _create_postgresql_triggers() -> None:
    for table_name, trigger_name in IMMEDIATE_TRIGGER_BINDINGS:
        operations = (
            "INSERT"
            if table_name == "material_request_commands"
            else "INSERT OR UPDATE OR DELETE"
        )
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE {operations} "
            f"ON public.{table_name} FOR EACH ROW EXECUTE FUNCTION "
            f"public.{PG_CONTENT_GUARD_FUNCTION}()"
        )
    for table_name, trigger_name in DEFERRED_TRIGGER_BINDINGS:
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {trigger_name} "
            f"AFTER INSERT OR UPDATE OR DELETE ON public.{table_name} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
            f"public.{PG_CONTENT_DISPATCH_FUNCTION}()"
        )
    for table_name, trigger_name in TRIGGER_BINDINGS:
        op.execute(
            f"ALTER TABLE public.{table_name} "
            f"ENABLE ALWAYS TRIGGER {trigger_name}"
        )


def _replace_oam_runtime_ready_function(expected_revision: str) -> None:
    op.execute(_oam_runtime_ready_function_sql(expected_revision))


def _oam_runtime_ready_function_sql(expected_revision: str) -> str:
    if expected_revision not in {PREVIOUS_SCHEMA_REVISION, revision}:
        raise ValueError("unsupported OAM runtime readiness revision")
    return f"""
CREATE OR REPLACE FUNCTION public.{OAM_RUNTIME_READY_FUNCTION}()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT (
        SELECT pg_catalog.count(*) = 1
           AND pg_catalog.min(version_num) = '{expected_revision}'
          FROM public.alembic_version
    ) AND CASE session_user::text
        WHEN 'edge_inbox' THEN EXISTS (
            SELECT 1
              FROM public.oam_sync_scope_bindings AS ingress
             WHERE ingress.enabled
               AND ingress.principal_name = session_user::text
               AND ingress.capability = 'edge_ingress'
               AND (
                   ingress.entity_type <> 'work_order'
                   OR EXISTS (
                       SELECT 1
                         FROM public.source_systems AS source
                        WHERE source.code = ingress.source_system
                          AND source.mode = 'read_only'
                          AND source.enabled
                          AND source.configuration_jsonb =
                              pg_catalog.jsonb_build_object(
                                  'projection_schema',
                                  'rsc.oam_work_order_projection.v1',
                                  'edge_source_instance',
                                  ingress.source_instance,
                                  'work_order_company_id',
                                  ingress.company_id,
                                  'work_order_org_code',
                                  ingress.org_code,
                                  'work_order_scope_key',
                                  ingress.scope_key
                              )
                   )
               )
        )
        WHEN 'star_oam_projector' THEN EXISTS (
            SELECT 1
              FROM public.oam_sync_scope_bindings AS write_work_order
              JOIN public.oam_sync_scope_bindings AS read_work_order
                ON read_work_order.source_system = write_work_order.source_system
               AND read_work_order.source_instance = write_work_order.source_instance
               AND read_work_order.scope_key = write_work_order.scope_key
               AND read_work_order.company_id = write_work_order.company_id
               AND read_work_order.org_code = write_work_order.org_code
               AND read_work_order.enabled
               AND read_work_order.principal_name = write_work_order.principal_name
               AND read_work_order.capability = 'projector_read'
               AND read_work_order.entity_type = 'work_order'
              JOIN public.oam_sync_scope_bindings AS read_employee
                ON read_employee.source_system = write_work_order.source_system
               AND read_employee.source_instance = write_work_order.source_instance
               AND read_employee.scope_key = write_work_order.scope_key
               AND read_employee.company_id = write_work_order.company_id
               AND read_employee.org_code = write_work_order.org_code
               AND read_employee.enabled
               AND read_employee.principal_name = write_work_order.principal_name
               AND read_employee.capability = 'projector_read'
               AND read_employee.entity_type = 'employee'
              JOIN public.source_systems AS source
                ON source.code = write_work_order.source_system
               AND source.mode = 'read_only'
               AND source.enabled
               AND source.configuration_jsonb = pg_catalog.jsonb_build_object(
                   'projection_schema', 'rsc.oam_work_order_projection.v1',
                   'edge_source_instance', write_work_order.source_instance,
                   'work_order_company_id', write_work_order.company_id,
                   'work_order_org_code', write_work_order.org_code,
                   'work_order_scope_key', write_work_order.scope_key
               )
             WHERE write_work_order.enabled
               AND write_work_order.principal_name = session_user::text
               AND write_work_order.capability = 'projector_write'
               AND write_work_order.entity_type = 'work_order'
        ) AND (
            SELECT pg_catalog.count(*) = 3
              FROM public.oam_sync_scope_bindings AS binding
             WHERE binding.enabled
               AND binding.principal_name = session_user::text
        )
        ELSE false
    END
$$
"""
