"""Install the least-privilege PostgreSQL boundary for OAM receipt projection."""

from pathlib import Path
import runpy

from alembic import op


revision = "20260922_0082"
down_revision = "20260921_0081"
branch_labels = depends_on = None

RUNTIME_READY_BODY_SHA256_0081 = (
    "efb632b66d584cd8fe26414abd5420ddb59d7fbf97ad50e13864bb73237b14b6"
)
RUNTIME_READY_BODY_SHA256_0082 = (
    "c6318534d12f800c067ad8ced6c2700018545c95c50e8b5078c56f3037b06210"
)

RECEIPT_TABLES = (
    "external_sync_snapshots",
    "external_sync_current_records",
    "source_systems",
    "sync_runs",
    "external_objects",
    "external_object_mappings",
    "shipments",
    "oam_receipt_evidence",
)


def _replace_readiness(*, upgrade: bool) -> None:
    older = runpy.run_path(
        str(Path(__file__).with_name("20260920_0080_inbound_posting_acl.py"))
    )
    replace = older["_migration_0072"]()["_previous"]()["_previous"]()["_previous"]()[
        "_replace_function_source"
    ]
    replace(
        signature="public.rsc_oam_runtime_binding_ready_0044()",
        expected_hash=(
            RUNTIME_READY_BODY_SHA256_0081
            if upgrade
            else RUNTIME_READY_BODY_SHA256_0082
        ),
        replacement_hash=(
            RUNTIME_READY_BODY_SHA256_0082
            if upgrade
            else RUNTIME_READY_BODY_SHA256_0081
        ),
        replacements=((down_revision, revision),)
        if upgrade
        else ((revision, down_revision),),
        label="runtime_readiness_0082",
    )


_RECEIPT_RLS_CHECK_SQL = r"""
CREATE FUNCTION public.rsc_oam_receipt_rls_check_0082(
    p_operation text,
    p_required_capability text,
    p_table_name text,
    p_row jsonb
)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF session_user::text <> 'star_oam_projector' OR p_row IS NULL
       OR p_operation IS NULL OR p_required_capability IS NULL
       OR p_table_name IS NULL
       OR (p_operation, p_required_capability) NOT IN (
           ('select', 'projector_read'), ('insert', 'projector_write'),
           ('update', 'projector_write'))
       OR (p_operation <> 'select' AND p_table_name NOT IN (
           'sync_runs', 'oam_receipt_evidence'))
       OR (p_operation = 'update' AND p_table_name <> 'sync_runs') THEN
        RETURN false;
    END IF;

    -- All SECURITY DEFINER joins repeat the complete scope coordinates. Owner
    -- access to an inner table must never turn into unscoped runtime access.
    RETURN COALESCE((
        WITH bindings AS MATERIALIZED (
            SELECT binding.*
              FROM public.oam_receipt_sync_scope_bindings binding
             WHERE binding.enabled
               AND binding.principal_name = session_user::text
               AND binding.capability = p_required_capability
               AND binding.source_system = 'starcharge_oam'
               AND binding.entity_type = 'oam_receipt'
        ), sources AS MATERIALIZED (
            SELECT source.id, source.code
              FROM public.source_systems source
             WHERE source.code = 'starcharge_oam'
               AND source.mode = 'read_only' AND source.enabled
               AND EXISTS (SELECT 1 FROM bindings)
        ), snapshots AS MATERIALIZED (
            SELECT snapshot.*, source.id AS source_id
              FROM public.external_sync_snapshots snapshot
              JOIN sources source ON source.code = snapshot.source_system
             WHERE snapshot.status = 'complete'
               AND EXISTS (
                   SELECT 1 FROM bindings binding
                    WHERE binding.source_instance = snapshot.source_instance
                      AND binding.scope_key = snapshot.scope_key
                      AND binding.company_id = snapshot.company_id
                      AND binding.org_code = snapshot.org_code
               )
        ), records AS MATERIALIZED (
            SELECT record.*, snapshot.source_id
              FROM public.external_sync_current_records record
              JOIN snapshots snapshot ON snapshot.id = record.last_snapshot_id
               AND snapshot.source_system = record.source_system
               AND snapshot.source_instance = record.source_instance
               AND snapshot.scope_key = record.scope_key
             WHERE record.entity_type = 'oam_receipt'
        ), objects AS MATERIALIZED (
            SELECT object.*
              FROM public.external_objects object
             WHERE object.entity_type = 'oam_receipt'
               AND object.deleted_at IS NULL
               AND EXISTS (
                   SELECT 1 FROM records record
                    WHERE record.source_id = object.source_system_id
                      AND record.business_key = 'oam-receipt:' || object.external_id
               )
        ), mappings AS MATERIALIZED (
            SELECT mapping.*
              FROM public.external_object_mappings mapping
              JOIN objects object ON object.id = mapping.external_object_id
             WHERE mapping.local_object_type = 'shipment'
               AND mapping.status = 'approved'
        ), unique_mappings AS MATERIALIZED (
            SELECT mapping.* FROM mappings mapping
             WHERE (SELECT count(*) FROM mappings other
                     WHERE other.external_object_id = mapping.external_object_id) = 1
               AND mapping.approved_by IS NOT NULL
               AND mapping.approved_at IS NOT NULL
        )
        SELECT CASE p_table_name
        WHEN 'oam_receipt_sync_scope_bindings' THEN
            EXISTS (
                SELECT 1 FROM bindings binding
                  JOIN public.oam_receipt_sync_scope_bindings writable
                    ON writable.enabled
                   AND writable.principal_name = binding.principal_name
                   AND writable.capability = 'projector_write'
                   AND writable.source_system = binding.source_system
                   AND writable.source_instance = binding.source_instance
                   AND writable.scope_key = binding.scope_key
                   AND writable.company_id = binding.company_id
                   AND writable.org_code = binding.org_code
                   AND writable.entity_type = binding.entity_type
                 WHERE EXISTS (SELECT 1 FROM sources)
            )
        WHEN 'source_systems' THEN
            EXISTS (SELECT 1 FROM sources source WHERE source.id::text = p_row->>'id')
        WHEN 'external_sync_snapshots' THEN
            EXISTS (SELECT 1 FROM snapshots snapshot WHERE snapshot.id = p_row->>'id')
        WHEN 'external_sync_current_records' THEN
            EXISTS (SELECT 1 FROM records record WHERE record.id = p_row->>'id')
        WHEN 'external_objects' THEN
            EXISTS (SELECT 1 FROM objects object WHERE object.id::text = p_row->>'id')
        WHEN 'external_object_mappings' THEN
            EXISTS (SELECT 1 FROM mappings mapping WHERE mapping.id::text = p_row->>'id')
        WHEN 'shipments' THEN
            EXISTS (SELECT 1 FROM unique_mappings mapping
                     WHERE mapping.local_object_id = p_row->>'id')
        WHEN 'sync_runs' THEN
            (p_row->>'status') IN ('validating', 'completed', 'failed')
            AND p_row->>'watermark_from' IS NULL
            AND p_row->>'failure_detail' IS NULL
            AND EXISTS (
                SELECT 1 FROM snapshots snapshot
                 WHERE p_row->>'run_key' = 'oam-receipt:' || snapshot.id
                   AND p_row->>'source_system_id' = snapshot.source_id::text
                   AND p_row->>'scope_key' = snapshot.scope_key
                   AND p_row->>'mode' = snapshot.sync_mode
                   AND (p_row->>'watermark_to')::timestamptz = snapshot.snapshot_at
                   AND p_row->>'manifest_sha256' = snapshot.manifest_sha256
                   AND (p_operation = 'select' OR p_row->>'status' <> 'completed' OR (
                       p_row->>'completed_at' IS NOT NULL
                       AND p_row->>'failure_code' IS NULL
                       AND snapshot.manifest_sha256 = encode(sha256(convert_to(snapshot.manifest_json, 'UTF8')), 'hex')
                       AND jsonb_array_length(snapshot.manifest_json::jsonb->'entities') = 1
                       AND snapshot.manifest_json::jsonb->'entities'->0->>'entity_type' = 'oam_receipt'
                       AND (snapshot.manifest_json::jsonb->'entities'->0->>'final_record_count')::bigint
                           = (SELECT count(*) FROM records record WHERE record.last_snapshot_id = snapshot.id)
                       AND NOT EXISTS (
                           SELECT 1 FROM records record
                            WHERE record.last_snapshot_id = snapshot.id
                              AND NOT EXISTS (
                                  SELECT 1 FROM objects object
                                    JOIN unique_mappings mapping ON mapping.external_object_id = object.id
                                    JOIN public.oam_receipt_evidence evidence
                                      ON evidence.external_object_id = object.id
                                     AND evidence.shipment_id::text = mapping.local_object_id
                                   WHERE object.source_system_id = record.source_id
                                     AND record.business_key = 'oam-receipt:' || object.external_id
                                     AND evidence.payload_sha256 = record.payload_sha256
                                     AND evidence.source_time = record.source_updated_at
                                     AND evidence.source_version = record.payload_json::jsonb->>'sourceVersion'
                                     AND evidence.status = record.payload_json::jsonb->>'status'
                              )
                       )
                   ))
            )
        WHEN 'oam_receipt_evidence' THEN
            EXISTS (
                SELECT 1 FROM objects object
                 WHERE object.id::text = p_row->>'external_object_id'
                   AND (
                       p_operation = 'select'
                       OR (
                           EXISTS (
                               SELECT 1 FROM unique_mappings mapping
                                JOIN public.shipments shipment
                                  ON shipment.id::text = mapping.local_object_id
                                WHERE mapping.external_object_id = object.id
                                  AND shipment.id::text = p_row->>'shipment_id'
                           )
                           AND (SELECT count(*) FROM records record
                                WHERE record.source_id = object.source_system_id
                                  AND record.business_key = 'oam-receipt:' || object.external_id) = 1
                           AND EXISTS (
                               SELECT 1 FROM records record
                                WHERE record.source_id = object.source_system_id
                                  AND record.business_key = 'oam-receipt:' || object.external_id
                                  AND record.payload_sha256 = p_row->>'payload_sha256'
                                  AND record.payload_sha256 = encode(sha256(convert_to(record.payload_json, 'UTF8')), 'hex')
                                  AND record.payload_json::jsonb - ARRAY['id','status','sourceTime','sourceVersion']::text[] = '{}'::jsonb
                                  AND record.payload_json::jsonb->>'id' = object.external_id
                                  AND record.payload_json::jsonb->>'status' IN ('synced', 'exception')
                                  AND record.payload_json::jsonb->>'status' = p_row->>'status'
                                  AND record.payload_json::jsonb->>'sourceVersion' ~ '^[A-Za-z0-9._:-]{1,160}$'
                                  AND record.payload_json::jsonb->>'sourceVersion' = p_row->>'source_version'
                                  AND record.payload_json::jsonb->>'sourceTime' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
                                  AND (record.payload_json::jsonb->>'sourceTime')::timestamptz = record.source_updated_at
                                  AND record.source_updated_at = (p_row->>'source_time')::timestamptz
                           )
                       )
                   )
            )
        ELSE false END
    ), false);
EXCEPTION WHEN invalid_text_representation OR invalid_datetime_format OR datetime_field_overflow
    OR invalid_parameter_value OR numeric_value_out_of_range THEN
    RETURN false;
END
$$;
"""


def _policy(table: str, command: str, capability: str) -> str:
    policy = f"{table}_projector_{command}_receipt_0082"
    expression = (
        "public.rsc_oam_receipt_rls_check_0082("
        f"'{command}', '{capability}', '{table}', pg_catalog.to_jsonb({table}))"
    )
    if command == "update":
        return (
            f"CREATE POLICY {policy} ON public.{table} AS PERMISSIVE "
            f"FOR UPDATE TO star_oam_projector USING ({expression}) "
            f"WITH CHECK ({expression})"
        )
    if command == "insert":
        return (
            f"CREATE POLICY {policy} ON public.{table} AS PERMISSIVE "
            f"FOR INSERT TO star_oam_projector WITH CHECK ({expression})"
        )
    return (
        f"CREATE POLICY {policy} ON public.{table} AS PERMISSIVE "
        f"FOR SELECT TO star_oam_projector USING ({expression})"
    )


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        return
    if dialect != "postgresql":
        raise RuntimeError("0082 supports only PostgreSQL and SQLite")
    op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
    _replace_readiness(upgrade=True)
    op.execute(
        """
        CREATE TABLE public.oam_receipt_sync_scope_bindings (
            id uuid PRIMARY KEY,
            principal_name text NOT NULL DEFAULT 'star_oam_projector',
            capability text NOT NULL,
            source_system text NOT NULL DEFAULT 'starcharge_oam',
            source_instance text NOT NULL,
            scope_key text NOT NULL,
            company_id text NOT NULL,
            org_code text NOT NULL,
            entity_type text NOT NULL DEFAULT 'oam_receipt',
            enabled boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_oam_receipt_binding_principal_0082
                CHECK (principal_name = 'star_oam_projector'),
            CONSTRAINT ck_oam_receipt_binding_capability_0082
                CHECK (capability IN ('projector_read', 'projector_write')),
            CONSTRAINT ck_oam_receipt_binding_source_0082
                CHECK (source_system = 'starcharge_oam'),
            CONSTRAINT ck_oam_receipt_binding_entity_0082
                CHECK (entity_type = 'oam_receipt'),
            CONSTRAINT ck_oam_receipt_binding_scope_0082
                CHECK (scope_key ~ '^oam-receipts:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
            CONSTRAINT ck_oam_receipt_binding_instance_0082
                CHECK (length(trim(source_instance)) BETWEEN 1 AND 128),
            CONSTRAINT ck_oam_receipt_binding_company_0082
                CHECK (length(trim(company_id)) BETWEEN 1 AND 80),
            CONSTRAINT ck_oam_receipt_binding_org_0082
                CHECK (length(trim(org_code)) BETWEEN 1 AND 80),
            CONSTRAINT uq_oam_receipt_binding_coordinate_0082
                UNIQUE (principal_name, capability, source_system,
                        source_instance, scope_key, company_id, org_code,
                        entity_type)
        )
        """
    )
    op.execute(
        "GRANT SELECT ON TABLE public.oam_receipt_sync_scope_bindings "
        "TO star_oam_migrator"
    )
    op.execute(_RECEIPT_RLS_CHECK_SQL)
    op.execute(
        "REVOKE ALL ON FUNCTION public.rsc_oam_receipt_rls_check_0082(text,text,text,jsonb) "
        "FROM PUBLIC, star_oam_api"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.rsc_oam_receipt_rls_check_0082(text,text,text,jsonb) "
        "TO star_oam_projector, star_oam_migrator"
    )
    for table in (*RECEIPT_TABLES, "oam_receipt_sync_scope_bindings"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY oam_receipt_sync_scope_bindings_migrator_0082 "
        "ON public.oam_receipt_sync_scope_bindings AS PERMISSIVE FOR ALL "
        "TO star_oam_migrator USING (true) WITH CHECK (true)"
    )
    for table in ("shipments", "oam_receipt_evidence"):
        op.execute(
            f"CREATE POLICY {table}_migrator_receipt_0082 ON public.{table} "
            "AS PERMISSIVE FOR ALL TO star_oam_migrator USING (true) WITH CHECK (true)"
        )
    for table in ("shipments", "oam_receipt_evidence", "oam_receipt_sync_scope_bindings"):
        op.execute(
            f"CREATE POLICY {table}_backup_select_receipt_0082 ON public.{table} "
            "AS PERMISSIVE FOR SELECT TO star_oam_backup USING (true)"
        )
    for table in (
        "external_sync_snapshots",
        "external_sync_current_records",
        "source_systems",
        "sync_runs",
        "external_objects",
        "external_object_mappings",
        "shipments",
    ):
        op.execute(_policy(table, "select", "projector_read"))
    op.execute(_policy("sync_runs", "insert", "projector_write"))
    op.execute(_policy("sync_runs", "update", "projector_write"))
    op.execute(_policy("oam_receipt_evidence", "select", "projector_read"))
    op.execute(_policy("oam_receipt_evidence", "insert", "projector_write"))
    op.execute(
        "CREATE POLICY shipments_api_select_receipt_0082 ON public.shipments "
        "AS PERMISSIVE FOR SELECT TO star_oam_api USING (true)"
    )
    op.execute(
        "CREATE POLICY shipments_api_insert_receipt_0082 ON public.shipments "
        "AS PERMISSIVE FOR INSERT TO star_oam_api WITH CHECK (true)"
    )
    op.execute(
        "CREATE POLICY oam_receipt_evidence_api_select_0082 "
        "ON public.oam_receipt_evidence AS PERMISSIVE FOR SELECT TO star_oam_api USING (true)"
    )
    op.execute("REVOKE INSERT ON public.oam_receipt_evidence FROM star_oam_api")
    op.execute(
        "GRANT SELECT ON TABLE public.shipments TO star_oam_projector"
    )
    op.execute(
        "GRANT SELECT ON TABLE public.oam_receipt_evidence TO star_oam_projector"
    )
    op.execute(
        "GRANT INSERT (id, external_object_id, shipment_id, status, source_time, "
        "source_version, payload_sha256, created_at) "
        "ON TABLE public.oam_receipt_evidence TO star_oam_projector"
    )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        return
    if dialect != "postgresql":
        raise RuntimeError("0082 supports only PostgreSQL and SQLite")
    op.execute(
        "LOCK TABLE public.alembic_version, public.oam_receipt_sync_scope_bindings, "
        "public.sync_runs, public.oam_receipt_evidence IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.oam_receipt_sync_scope_bindings) "
        "OR EXISTS (SELECT 1 FROM public.sync_runs WHERE run_key LIKE 'oam-receipt:%') OR EXISTS (SELECT 1 FROM public.oam_receipt_evidence) THEN RAISE EXCEPTION '0082 downgrade blocked: receipt bindings or facts exist'; END IF; END $$"
    )
    for table in (
        "oam_receipt_evidence",
        "shipments",
        "external_object_mappings",
        "external_objects",
        "sync_runs",
        "source_systems",
        "external_sync_current_records",
        "external_sync_snapshots",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table}_projector_select_receipt_0082 ON public.{table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_projector_insert_receipt_0082 ON public.{table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_migrator_receipt_0082 ON public.{table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_projector_update_receipt_0082 ON public.{table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_backup_select_receipt_0082 ON public.{table}")
    op.execute("DROP POLICY IF EXISTS shipments_api_select_receipt_0082 ON public.shipments")
    op.execute("DROP POLICY IF EXISTS shipments_api_insert_receipt_0082 ON public.shipments")
    op.execute("DROP POLICY IF EXISTS oam_receipt_evidence_api_select_0082 ON public.oam_receipt_evidence")
    op.execute("DROP POLICY IF EXISTS oam_receipt_evidence_api_insert_0082 ON public.oam_receipt_evidence")
    op.execute("DROP POLICY IF EXISTS oam_receipt_sync_scope_bindings_migrator_0082 ON public.oam_receipt_sync_scope_bindings")
    for table in ("shipments", "oam_receipt_evidence"):
        op.execute(f"ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
    op.execute(
        "REVOKE SELECT ON TABLE public.oam_receipt_sync_scope_bindings FROM star_oam_migrator"
    )
    op.execute(
        "REVOKE SELECT ON TABLE public.oam_receipt_evidence FROM star_oam_projector"
    )
    op.execute(
        "REVOKE INSERT (id, external_object_id, shipment_id, status, source_time, "
        "source_version, payload_sha256, created_at) "
        "ON TABLE public.oam_receipt_evidence FROM star_oam_projector"
    )
    op.execute("REVOKE SELECT ON TABLE public.shipments FROM star_oam_projector")
    op.execute(
        "DROP FUNCTION public.rsc_oam_receipt_rls_check_0082(text,text,text,jsonb)"
    )
    op.execute("DROP TABLE public.oam_receipt_sync_scope_bindings")
    op.execute("GRANT INSERT ON public.oam_receipt_evidence TO star_oam_api")
    _replace_readiness(upgrade=False)
