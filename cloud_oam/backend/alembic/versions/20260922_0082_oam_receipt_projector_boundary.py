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
    previous = runpy.run_path(
        str(Path(__file__).with_name("20260921_0081_oam_receipt_evidence.py"))
    )
    migration = previous["_replace_readiness"]
    # 0081's helper is intentionally used through its public migration helper;
    # the body is unchanged and only the readiness head coordinate advances.
    del migration
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
DECLARE
    source_code text;
    source_id uuid;
    external_id text;
BEGIN
    IF session_user::text <> 'star_oam_projector'
       OR p_operation NOT IN ('select', 'insert')
       OR p_required_capability NOT IN ('projector_read', 'projector_write')
       OR p_table_name NOT IN (
           'external_sync_snapshots', 'external_sync_current_records',
           'source_systems', 'sync_runs', 'external_objects',
           'external_object_mappings', 'shipments', 'oam_receipt_evidence'
       ) THEN
        RETURN false;
    END IF;

    IF p_table_name = 'source_systems' THEN
        RETURN (p_row->>'code') = 'starcharge_oam'
           AND (p_row->>'mode') = 'read_only'
           AND (p_row->>'enabled') = 'true';
    END IF;

    IF p_table_name = 'external_sync_snapshots' THEN
        RETURN (p_row->>'source_system') = 'starcharge_oam'
           AND (p_row->>'scope_key') LIKE 'oam-receipts:%'
           AND EXISTS (
               SELECT 1
                 FROM public.oam_receipt_sync_scope_bindings binding
                WHERE binding.enabled
                  AND binding.principal_name = session_user::text
                  AND binding.capability = p_required_capability
                  AND binding.source_system = 'starcharge_oam'
                  AND binding.source_instance = p_row->>'source_instance'
                  AND binding.scope_key = p_row->>'scope_key'
                  AND binding.company_id = p_row->>'company_id'
                  AND binding.org_code = p_row->>'org_code'
                  AND binding.entity_type = 'oam_receipt'
           );
    END IF;

    IF p_table_name = 'external_sync_current_records' THEN
        RETURN (p_row->>'source_system') = 'starcharge_oam'
           AND (p_row->>'entity_type') = 'oam_receipt'
           AND (p_row->>'scope_key') LIKE 'oam-receipts:%'
           AND EXISTS (
               SELECT 1
                 FROM public.oam_receipt_sync_scope_bindings binding
                WHERE binding.enabled
                  AND binding.principal_name = session_user::text
                  AND binding.capability = p_required_capability
                  AND binding.source_system = 'starcharge_oam'
                  AND binding.source_instance = p_row->>'source_instance'
                  AND binding.scope_key = p_row->>'scope_key'
                  AND binding.entity_type = 'oam_receipt'
           );
    END IF;

    IF p_table_name = 'sync_runs' THEN
        BEGIN
            source_id := (p_row->>'source_system_id')::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
            RETURN false;
        END;
        RETURN (p_row->>'run_key') LIKE 'oam-receipt:%'
           AND (p_row->>'scope_key') LIKE 'oam-receipts:%'
           AND EXISTS (
               SELECT 1
                 FROM public.source_systems source
                 JOIN public.oam_receipt_sync_scope_bindings binding
                   ON binding.enabled
                  AND binding.principal_name = session_user::text
                  AND binding.capability = p_required_capability
                  AND binding.source_system = source.code
                  AND binding.scope_key = p_row->>'scope_key'
                  AND binding.entity_type = 'oam_receipt'
                WHERE source.id = source_id
                  AND source.code = 'starcharge_oam'
                  AND source.mode = 'read_only'
                  AND source.enabled
           );
    END IF;

    IF p_table_name = 'external_objects' THEN
        BEGIN
            source_id := (p_row->>'source_system_id')::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
            RETURN false;
        END;
        RETURN (p_row->>'entity_type') = 'oam_receipt'
           AND EXISTS (
               SELECT 1 FROM public.source_systems source
                WHERE source.id = source_id
                  AND source.code = 'starcharge_oam'
                  AND source.mode = 'read_only' AND source.enabled
           );
    END IF;

    IF p_table_name = 'external_object_mappings' THEN
        RETURN (p_row->>'local_object_type') = 'shipment'
           AND (p_row->>'status') = 'approved'
           AND EXISTS (
               SELECT 1
                 FROM public.external_objects external_object
                 JOIN public.source_systems source
                   ON source.id = external_object.source_system_id
                WHERE external_object.id::text = p_row->>'external_object_id'
                  AND external_object.entity_type = 'oam_receipt'
                  AND source.code = 'starcharge_oam'
                  AND source.mode = 'read_only' AND source.enabled
                  AND EXISTS (
                      SELECT 1
                        FROM public.external_sync_current_records current_record
                       WHERE current_record.source_system = source.code
                         AND current_record.entity_type = 'oam_receipt'
                         AND current_record.scope_key LIKE 'oam-receipts:%'
                         AND current_record.business_key =
                             'oam-receipt:' || external_object.external_id
                  )
           );
    END IF;

    IF p_table_name = 'shipments' THEN
        RETURN EXISTS (
            SELECT 1
              FROM public.external_object_mappings mapping
              JOIN public.external_objects external_object
                ON external_object.id = mapping.external_object_id
              JOIN public.source_systems source
                ON source.id = external_object.source_system_id
             WHERE mapping.local_object_type = 'shipment'
               AND mapping.local_object_id = p_row->>'id'
               AND mapping.status = 'approved'
               AND external_object.entity_type = 'oam_receipt'
               AND source.code = 'starcharge_oam'
               AND source.mode = 'read_only' AND source.enabled
               AND EXISTS (
                   SELECT 1
                     FROM public.external_sync_current_records current_record
                    WHERE current_record.source_system = source.code
                      AND current_record.entity_type = 'oam_receipt'
                      AND current_record.scope_key LIKE 'oam-receipts:%'
                      AND current_record.business_key =
                          'oam-receipt:' || external_object.external_id
               )
        );
    END IF;

    IF p_table_name = 'oam_receipt_evidence' THEN
        RETURN (p_operation = 'select' OR p_required_capability = 'projector_write')
           AND (p_row->>'status') IN ('synced', 'exception')
           AND EXISTS (
               SELECT 1
                 FROM public.external_objects external_object
                 JOIN public.external_object_mappings mapping
                   ON mapping.external_object_id = external_object.id
                  AND mapping.local_object_type = 'shipment'
                  AND mapping.local_object_id = p_row->>'shipment_id'
                  AND mapping.status = 'approved'
                 JOIN public.source_systems source
                   ON source.id = external_object.source_system_id
                WHERE external_object.id::text = p_row->>'external_object_id'
                  AND external_object.entity_type = 'oam_receipt'
                  AND source.code = 'starcharge_oam'
                  AND source.mode = 'read_only' AND source.enabled
           );
    END IF;
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
    for table in RECEIPT_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY oam_receipt_sync_scope_bindings_migrator_0082 "
        "ON public.oam_receipt_sync_scope_bindings AS PERMISSIVE FOR ALL "
        "TO star_oam_migrator USING (true) WITH CHECK (true)"
    )
    for table in RECEIPT_TABLES:
        op.execute(
            f"CREATE POLICY {table}_migrator_receipt_0082 ON public.{table} "
            "AS PERMISSIVE FOR ALL TO star_oam_migrator USING (true) WITH CHECK (true)"
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
    op.execute(
        "CREATE POLICY oam_receipt_evidence_api_insert_0082 "
        "ON public.oam_receipt_evidence AS PERMISSIVE FOR INSERT TO star_oam_api WITH CHECK (true)"
    )
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
        "LOCK TABLE public.alembic_version, public.oam_receipt_sync_scope_bindings "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.oam_receipt_sync_scope_bindings) "
        "THEN RAISE EXCEPTION '0082 downgrade blocked: receipt scope bindings exist'; END IF; END $$"
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
    op.execute("DROP POLICY IF EXISTS shipments_api_select_receipt_0082 ON public.shipments")
    op.execute("DROP POLICY IF EXISTS shipments_api_insert_receipt_0082 ON public.shipments")
    op.execute("DROP POLICY IF EXISTS oam_receipt_evidence_api_select_0082 ON public.oam_receipt_evidence")
    op.execute("DROP POLICY IF EXISTS oam_receipt_evidence_api_insert_0082 ON public.oam_receipt_evidence")
    op.execute("DROP POLICY IF EXISTS oam_receipt_sync_scope_bindings_migrator_0082 ON public.oam_receipt_sync_scope_bindings")
    for table in ("shipments", "oam_receipt_evidence"):
        op.execute(f"ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "REVOKE SELECT ON TABLE public.oam_receipt_sync_scope_bindings FROM star_oam_migrator"
    )
    op.execute(
        "REVOKE SELECT ON TABLE public.oam_receipt_evidence FROM star_oam_projector"
    )
    op.execute(
        "REVOKE INSERT ON TABLE public.oam_receipt_evidence FROM star_oam_projector"
    )
    op.execute("REVOKE SELECT ON TABLE public.shipments FROM star_oam_projector")
    op.execute(
        "DROP FUNCTION public.rsc_oam_receipt_rls_check_0082(text,text,text,jsonb)"
    )
    op.execute("DROP TABLE public.oam_receipt_sync_scope_bindings")
    _replace_readiness(upgrade=False)
