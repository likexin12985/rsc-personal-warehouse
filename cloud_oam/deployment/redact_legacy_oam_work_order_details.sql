\set ON_ERROR_STOP on
\pset format unaligned
\pset fieldsep '|'

-- Controlled, one-scope removal of the retired plaintext OAM work-order
-- detail/relation staging payloads.  This file is intentionally not an
-- Alembic migration: operators must first capture and review its dry-run
-- evidence, then repeat with the exact replacement snapshot and audit hash.
--
-- Dry run (the default):
--   psql ... \
--     -v edge_source_instance=authorized-admin-mac \
--     -v scope_key=work-orders:recent-30d \
--     -f deployment/redact_legacy_oam_work_order_details.sql
--
-- Explicit execution after review of the dry-run output:
--   psql ... \
--     -v edge_source_instance=authorized-admin-mac \
--     -v scope_key=work-orders:recent-30d \
--     -v replacement_snapshot_id=s-reviewed-clean-full-snapshot \
--     -v expected_audit_sha256=<exact dry-run audit_sha256> \
--     -v confirm_cleanup=I_UNDERSTAND_PURGE_LEGACY_OAM_WORK_ORDER_DETAILS \
--     -f deployment/redact_legacy_oam_work_order_details.sql
--
-- Never run this file through application/edge/projector credentials.  It
-- must run as star_oam_migrator during an approved maintenance window.

\if :{?edge_source_instance}
\else
\echo 'edge_source_instance is required; refusing to guess the source scope'
\quit 3
\endif
\if :{?scope_key}
\else
\echo 'scope_key is required; refusing to guess the source scope'
\quit 3
\endif
\if :{?replacement_snapshot_id}
\else
\set replacement_snapshot_id ''
\endif
\if :{?expected_audit_sha256}
\else
\set expected_audit_sha256 ''
\endif
\if :{?confirm_cleanup}
\else
\set confirm_cleanup ''
\endif

BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5min';
SET LOCAL idle_in_transaction_session_timeout = '2min';
SET LOCAL TIME ZONE 'UTC';

DO $$
DECLARE
    missing_tables text;
    wrong_owner_tables text;
BEGIN
    IF current_user <> 'star_oam_migrator'
       OR session_user <> 'star_oam_migrator' THEN
        RAISE EXCEPTION
            'legacy OAM detail redaction requires the exact migrator login';
    END IF;

    SELECT pg_catalog.string_agg(required_table, ', ' ORDER BY required_table)
      INTO missing_tables
      FROM pg_catalog.unnest(ARRAY[
          'external_sync_snapshots',
          'external_sync_snapshot_batches',
          'external_sync_snapshot_records',
          'external_sync_current_records',
          'source_systems',
          'sync_runs',
          'sync_batches',
          'sync_inbox_events'
      ]::text[]) AS required(required_table)
     WHERE pg_catalog.to_regclass(
               pg_catalog.format('public.%I', required_table)
           ) IS NULL;
    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION
            'redaction schema is incomplete; missing tables: %', missing_tables;
    END IF;

    SELECT pg_catalog.string_agg(relation.relname, ', ' ORDER BY relation.relname)
      INTO wrong_owner_tables
      FROM pg_catalog.pg_class AS relation
      JOIN pg_catalog.pg_namespace AS schema_row
        ON schema_row.oid = relation.relnamespace
     WHERE schema_row.nspname = 'public'
       AND relation.relname IN (
           'external_sync_snapshots',
           'external_sync_snapshot_batches',
           'external_sync_snapshot_records',
           'external_sync_current_records',
           'source_systems',
           'sync_runs',
           'sync_batches',
           'sync_inbox_events'
       )
       AND pg_catalog.pg_get_userbyid(relation.relowner)
           <> 'star_oam_migrator';
    IF wrong_owner_tables IS NOT NULL THEN
        RAISE EXCEPTION
            'redaction tables are not migration-owned: %', wrong_owner_tables;
    END IF;

    -- No table may acquire an unreviewed FK to a payload-bearing child table.
    -- If the schema evolves, stop here rather than cascading or orphaning new
    -- evidence that this artifact does not understand.
    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_constraint AS constraint_row
         WHERE constraint_row.contype = 'f'
           AND constraint_row.confrelid IN (
               'public.external_sync_current_records'::regclass,
               'public.external_sync_snapshot_batches'::regclass,
               'public.external_sync_snapshot_records'::regclass
           )
    ) THEN
        RAISE EXCEPTION
            'an unreviewed FK references legacy staging children; refusing cleanup';
    END IF;
END
$$;

CREATE TEMPORARY TABLE legacy_oam_detail_cleanup_input (
    edge_source_instance text NOT NULL,
    scope_key text NOT NULL,
    replacement_snapshot_id text NOT NULL,
    expected_audit_sha256 text NOT NULL,
    confirm_cleanup text NOT NULL
) ON COMMIT DROP;

INSERT INTO pg_temp.legacy_oam_detail_cleanup_input (
    edge_source_instance,
    scope_key,
    replacement_snapshot_id,
    expected_audit_sha256,
    confirm_cleanup
)
VALUES (
    :'edge_source_instance'::text,
    :'scope_key'::text,
    :'replacement_snapshot_id'::text,
    :'expected_audit_sha256'::text,
    :'confirm_cleanup'::text
);

DO $$
DECLARE
    cleanup_input pg_temp.legacy_oam_detail_cleanup_input%ROWTYPE;
BEGIN
    SELECT * INTO STRICT cleanup_input
      FROM pg_temp.legacy_oam_detail_cleanup_input;
    IF cleanup_input.edge_source_instance
           <> pg_catalog.btrim(cleanup_input.edge_source_instance)
       OR pg_catalog.length(cleanup_input.edge_source_instance) NOT BETWEEN 1 AND 128
       OR cleanup_input.edge_source_instance ~ '[[:cntrl:]]' THEN
        RAISE EXCEPTION 'edge_source_instance is empty or non-canonical';
    END IF;
    IF cleanup_input.scope_key <> pg_catalog.btrim(cleanup_input.scope_key)
       OR pg_catalog.length(cleanup_input.scope_key) NOT BETWEEN 1 AND 160
       OR cleanup_input.scope_key !~ '^work-orders:recent-[1-9][0-9]*d$'
       OR cleanup_input.scope_key ~ '[[:cntrl:]]' THEN
        RAISE EXCEPTION 'scope_key is not a canonical work-order scope';
    END IF;
    IF cleanup_input.replacement_snapshot_id <> ''
       AND (
           cleanup_input.replacement_snapshot_id
               <> pg_catalog.btrim(cleanup_input.replacement_snapshot_id)
           OR cleanup_input.replacement_snapshot_id
               !~ '^[A-Za-z0-9._:-]{8,64}$'
       ) THEN
        RAISE EXCEPTION 'replacement_snapshot_id is malformed';
    END IF;
    IF cleanup_input.expected_audit_sha256 <> ''
       AND cleanup_input.expected_audit_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'expected_audit_sha256 must be lowercase SHA-256';
    END IF;
END
$$;

SELECT pg_catalog.pg_try_advisory_xact_lock(
    pg_catalog.hashtextextended(
        'external-sync-scope:'
        || pg_catalog.length(cleanup_input.edge_source_instance)::text
        || ':' || cleanup_input.edge_source_instance
        || ':' || pg_catalog.length(cleanup_input.scope_key)::text
        || ':' || cleanup_input.scope_key,
        0
    )
) AS cleanup_scope_lock_acquired
FROM pg_temp.legacy_oam_detail_cleanup_input AS cleanup_input
\gset

\if :cleanup_scope_lock_acquired
\else
ROLLBACK;
\echo 'the exact source/scope is busy; no data was changed'
\quit 4
\endif

-- Materialize every snapshot that still owns either retired entity.  The
-- exact source/scope join prevents a copied ID from broadening the target.
CREATE TEMPORARY TABLE legacy_oam_detail_cleanup_candidates
ON COMMIT DROP AS
WITH cleanup_input AS (
    SELECT * FROM pg_temp.legacy_oam_detail_cleanup_input
),
candidate_ids(snapshot_ref_id) AS (
    SELECT snapshot.id
      FROM public.external_sync_snapshots AS snapshot
      JOIN cleanup_input
        ON snapshot.source_instance = cleanup_input.edge_source_instance
       AND snapshot.scope_key = cleanup_input.scope_key
     WHERE EXISTS (
               SELECT 1
                 FROM public.external_sync_snapshot_records AS record
                WHERE record.snapshot_ref_id = snapshot.id
                  AND record.entity_type IN (
                      'work_order_detail', 'work_order_relation'
                  )
           )
        OR EXISTS (
               SELECT 1
                 FROM public.external_sync_snapshot_batches AS batch
                WHERE batch.snapshot_ref_id = snapshot.id
                  AND batch.entity_type IN (
                      'work_order_detail', 'work_order_relation'
                  )
           )
        OR EXISTS (
               SELECT 1
                 FROM public.external_sync_current_records AS current_record
                WHERE current_record.source_instance
                          = cleanup_input.edge_source_instance
                  AND current_record.scope_key = cleanup_input.scope_key
                  AND current_record.entity_type IN (
                      'work_order_detail', 'work_order_relation'
                  )
                  AND current_record.last_snapshot_id = snapshot.id
           )
)
SELECT
    snapshot.id,
    snapshot.source_system,
    snapshot.source_instance,
    snapshot.snapshot_id,
    snapshot.scope_key,
    snapshot.sync_mode,
    snapshot.company_id,
    snapshot.org_code,
    snapshot.snapshot_at,
    snapshot.received_at,
    snapshot.completed_at,
    snapshot.status AS status_before,
    snapshot.manifest_sha256,
    'oam-work-order:' || pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                snapshot.source_instance || pg_catalog.chr(0)
                    || snapshot.snapshot_id,
                'UTF8'
            )
        ),
        'hex'
    ) AS formal_run_key
FROM candidate_ids AS candidate
JOIN public.external_sync_snapshots AS snapshot
  ON snapshot.id = candidate.snapshot_ref_id;

SELECT pg_catalog.count(*) > 0 AS cleanup_targets_present
FROM pg_temp.legacy_oam_detail_cleanup_candidates
\gset

\if :cleanup_targets_present
\else
ROLLBACK;
\echo 'DRY_RUN no retired work-order detail/relation payloads exist in this scope'
\quit 0
\endif

-- Lock the old snapshot rows.  Batch ingestion also locks these rows, so no
-- retired payload can be appended to a candidate while its evidence is being
-- summarized and removed.
DO $$
BEGIN
    PERFORM 1
      FROM public.external_sync_snapshots AS snapshot
     WHERE snapshot.id IN (
         SELECT candidate.id
           FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
     )
     ORDER BY snapshot.id
     FOR UPDATE;
END
$$;

-- Select the newest completed full snapshot, or the exact replacement ID
-- supplied for execution.  Invalid JSON stays NULL and fails the evidence
-- block below; it is never silently skipped in favour of an older snapshot.
CREATE TEMPORARY TABLE legacy_oam_detail_cleanup_replacement
ON COMMIT DROP AS
WITH cleanup_input AS (
    SELECT * FROM pg_temp.legacy_oam_detail_cleanup_input
)
SELECT
    snapshot.*,
    pg_catalog.to_char(
        snapshot.snapshot_at AT TIME ZONE 'UTC',
        CASE
            WHEN (
                pg_catalog.date_part(
                    'microseconds', snapshot.snapshot_at
                )::bigint % 1000000
            ) = 0
            THEN 'YYYY-MM-DD"T"HH24:MI:SS'
            ELSE 'YYYY-MM-DD"T"HH24:MI:SS.US'
        END
    ) || '+00:00' AS canonical_snapshot_at,
    CASE
        WHEN snapshot.manifest_json IS JSON OBJECT WITH UNIQUE KEYS
        THEN snapshot.manifest_json::jsonb
        ELSE NULL
    END AS parsed_manifest
FROM public.external_sync_snapshots AS snapshot
JOIN cleanup_input
  ON snapshot.source_instance = cleanup_input.edge_source_instance
 AND snapshot.scope_key = cleanup_input.scope_key
WHERE snapshot.source_system = 'starcharge_oam'
  AND snapshot.status = 'complete'
  AND snapshot.sync_mode = 'full'
  AND (
      cleanup_input.replacement_snapshot_id = ''
      OR snapshot.snapshot_id = cleanup_input.replacement_snapshot_id
  )
ORDER BY snapshot.snapshot_at DESC, snapshot.completed_at DESC, snapshot.id DESC
LIMIT 1;

-- Reconstruct the exact canonical wire representation emitted by the edge
-- writer.  This proves the replacement rows themselves, rather than trusting
-- only the receiver's stored counts and digests.
CREATE TEMPORARY TABLE legacy_oam_detail_cleanup_replacement_rows
ON COMMIT DROP AS
WITH replacement AS (
    SELECT * FROM pg_temp.legacy_oam_detail_cleanup_replacement
),
parsed AS MATERIALIZED (
    SELECT
        record.*,
        CASE
            WHEN record.payload_json IS JSON OBJECT WITH UNIQUE KEYS
            THEN record.payload_json::jsonb
            ELSE NULL
        END AS parsed_payload
      FROM public.external_sync_snapshot_records AS record
      JOIN replacement ON replacement.id = record.snapshot_ref_id
     WHERE record.entity_type = 'work_order'
),
canonical AS (
    SELECT
        parsed.*,
        CASE
            WHEN parsed.parsed_payload IS NOT NULL THEN
                '{"authCompanyId":'
                || pg_catalog.to_json(
                       parsed.parsed_payload->>'authCompanyId'
                   )::text
                || ',"code":'
                || pg_catalog.to_json(parsed.parsed_payload->>'code')::text
                || ',"executorId":'
                || pg_catalog.to_json(
                       parsed.parsed_payload->>'executorId'
                   )::text
                || ',"id":'
                || pg_catalog.to_json(parsed.parsed_payload->>'id')::text
                || ',"province":'
                || pg_catalog.to_json(
                       parsed.parsed_payload->>'province'
                   )::text
                || ',"statusCode":'
                || pg_catalog.to_json(
                       parsed.parsed_payload->>'statusCode'
                   )::text
                || ',"updateTime":'
                || pg_catalog.to_json(
                       parsed.parsed_payload->>'updateTime'
                   )::text
                || '}'
        END AS canonical_payload_json,
        CASE
            WHEN parsed.source_updated_at IS NOT NULL THEN
                pg_catalog.to_char(
                    parsed.source_updated_at AT TIME ZONE 'UTC',
                    CASE
                        WHEN (
                            pg_catalog.date_part(
                                'microseconds', parsed.source_updated_at
                            )::bigint % 1000000
                        ) = 0
                        THEN 'YYYY-MM-DD"T"HH24:MI:SS'
                        ELSE 'YYYY-MM-DD"T"HH24:MI:SS.US'
                    END
                ) || '+00:00'
        END AS canonical_source_updated_at,
        CASE
            WHEN parsed.parsed_payload->>'updateTime' ~
                 '^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?([Zz]|[+-][0-9]{2}:[0-9]{2})?$'
            THEN CASE
                WHEN parsed.parsed_payload->>'updateTime' ~ '[Zz]$' THEN
                    pg_catalog.regexp_replace(
                        parsed.parsed_payload->>'updateTime',
                        '[Zz]$',
                        '+00:00'
                    )::timestamptz
                WHEN parsed.parsed_payload->>'updateTime' ~
                     '[+-][0-9]{2}:[0-9]{2}$' THEN
                    (parsed.parsed_payload->>'updateTime')::timestamptz
                ELSE
                    (parsed.parsed_payload->>'updateTime')::timestamp
                        AT TIME ZONE 'Asia/Shanghai'
            END
        END AS payload_source_updated_at
      FROM parsed
),
wire AS (
    SELECT
        canonical.*,
        '{"business_key":'
        || pg_catalog.to_json(canonical.business_key)::text
        || ',"data":' || canonical.canonical_payload_json
        || ',"source_updated_at":'
        || pg_catalog.to_json(
               canonical.canonical_source_updated_at
           )::text
        || '}' AS final_wire_json,
        '{"business_key":'
        || pg_catalog.to_json(canonical.business_key)::text
        || ',"data":' || canonical.canonical_payload_json
        || ',"operation":'
        || pg_catalog.to_json(canonical.operation)::text
        || ',"source_updated_at":'
        || pg_catalog.to_json(
               canonical.canonical_source_updated_at
           )::text
        || '}' AS delta_wire_json
      FROM canonical
)
SELECT
    wire.*,
    pg_catalog.row_number() OVER (ORDER BY wire.business_key) AS wire_row_number
FROM wire;

CREATE TEMPORARY TABLE legacy_oam_detail_cleanup_replacement_batches
ON COMMIT DROP AS
WITH replacement AS (
    SELECT *
      FROM pg_temp.legacy_oam_detail_cleanup_replacement
),
ordered_batches AS (
    SELECT
        batch.*,
        COALESCE(
            pg_catalog.sum(batch.record_count) OVER (
                ORDER BY batch.sequence
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ),
            0
        ) + 1 AS first_wire_row,
        pg_catalog.sum(batch.record_count) OVER (
            ORDER BY batch.sequence
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS last_wire_row
      FROM public.external_sync_snapshot_batches AS batch
      JOIN replacement ON replacement.id = batch.snapshot_ref_id
     WHERE batch.entity_type = 'work_order'
),
batch_records AS (
    SELECT
        batch.*,
        replacement.source_system,
        replacement.source_instance AS replacement_source_instance,
        replacement.snapshot_id,
        replacement.scope_key,
        replacement.sync_mode,
        replacement.company_id,
        replacement.org_code,
        replacement.canonical_snapshot_at,
        '[' || COALESCE(
            pg_catalog.string_agg(
                row_evidence.delta_wire_json,
                ',' ORDER BY row_evidence.business_key
            ),
            ''
        ) || ']' AS canonical_records_json,
        pg_catalog.count(row_evidence.id) AS assigned_record_count
      FROM ordered_batches AS batch
      CROSS JOIN replacement
      LEFT JOIN pg_temp.legacy_oam_detail_cleanup_replacement_rows AS row_evidence
        ON row_evidence.wire_row_number
           BETWEEN batch.first_wire_row AND batch.last_wire_row
     GROUP BY
        batch.id,
        batch.snapshot_ref_id,
        batch.source_instance,
        batch.batch_id,
        batch.entity_type,
        batch.sequence,
        batch.total_sequences,
        batch.record_count,
        batch.body_sha256,
        batch.received_at,
        batch.first_wire_row,
        batch.last_wire_row,
        replacement.source_system,
        replacement.source_instance,
        replacement.snapshot_id,
        replacement.scope_key,
        replacement.sync_mode,
        replacement.company_id,
        replacement.org_code,
        replacement.canonical_snapshot_at
),
wire AS (
    SELECT
        batch_records.*,
        '{"company_id":'
        || pg_catalog.to_json(batch_records.company_id)::text
        || ',"entity_type":"work_order"'
        || ',"org_code":'
        || pg_catalog.to_json(batch_records.org_code)::text
        || ',"records":' || batch_records.canonical_records_json
        || ',"scope_key":'
        || pg_catalog.to_json(batch_records.scope_key)::text
        || ',"sequence":' || batch_records.sequence::text
        || ',"snapshot_at":'
        || pg_catalog.to_json(batch_records.canonical_snapshot_at)::text
        || ',"snapshot_id":'
        || pg_catalog.to_json(batch_records.snapshot_id)::text
        || ',"source_system":'
        || pg_catalog.to_json(batch_records.source_system)::text
        || ',"sync_mode":'
        || pg_catalog.to_json(batch_records.sync_mode)::text
        || ',"total_sequences":'
        || batch_records.total_sequences::text
        || '}' AS canonical_body_json
      FROM batch_records
)
SELECT
    wire.*,
    pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(wire.canonical_body_json, 'UTF8')
        ),
        'hex'
    ) AS reconstructed_body_sha256
FROM wire;

CREATE TEMPORARY TABLE legacy_oam_detail_cleanup_replacement_integrity
ON COMMIT DROP AS
WITH manifest_wire AS (
    SELECT
        replacement.manifest_json,
        '{"company_id":'
        || pg_catalog.to_json(replacement.company_id)::text
        || ',"entities":[{"batch_count":'
        || replacement.parsed_manifest->'entities'->0->>'batch_count'
        || ',"delta_record_count":'
        || replacement.parsed_manifest->'entities'->0->>'delta_record_count'
        || ',"delta_sha256":'
        || pg_catalog.to_json(
               replacement.parsed_manifest->'entities'->0->>'delta_sha256'
           )::text
        || ',"entity_type":"work_order","final_record_count":'
        || replacement.parsed_manifest->'entities'->0->>'final_record_count'
        || ',"final_sha256":'
        || pg_catalog.to_json(
               replacement.parsed_manifest->'entities'->0->>'final_sha256'
           )::text
        || '}],"org_code":'
        || pg_catalog.to_json(replacement.org_code)::text
        || ',"scope_key":'
        || pg_catalog.to_json(replacement.scope_key)::text
        || ',"snapshot_at":'
        || pg_catalog.to_json(replacement.canonical_snapshot_at)::text
        || ',"snapshot_id":'
        || pg_catalog.to_json(replacement.snapshot_id)::text
        || ',"source_system":'
        || pg_catalog.to_json(replacement.source_system)::text
        || ',"sync_mode":"full"}' AS canonical_manifest_body_json
      FROM pg_temp.legacy_oam_detail_cleanup_replacement AS replacement
),
manifest_evidence AS (
    SELECT
        pg_catalog.encode(
            pg_catalog.sha256(
                pg_catalog.convert_to(manifest_wire.manifest_json, 'UTF8')
            ),
            'hex'
        ) AS manifest_json_sha256,
        pg_catalog.encode(
            pg_catalog.sha256(
                pg_catalog.convert_to(
                    manifest_wire.canonical_manifest_body_json,
                    'UTF8'
                )
            ),
            'hex'
        ) AS reconstructed_manifest_sha256
      FROM manifest_wire
),
row_evidence AS (
    SELECT
        pg_catalog.count(*) AS record_count,
        pg_catalog.encode(
            pg_catalog.sha256(
                pg_catalog.convert_to(
                    '[' || COALESCE(
                        pg_catalog.string_agg(
                            row_evidence.final_wire_json,
                            ',' ORDER BY row_evidence.business_key
                        ),
                        ''
                    ) || ']',
                    'UTF8'
                )
            ),
            'hex'
        ) AS final_sha256,
        pg_catalog.encode(
            pg_catalog.sha256(
                pg_catalog.convert_to(
                    '[' || COALESCE(
                        pg_catalog.string_agg(
                            row_evidence.delta_wire_json,
                            ',' ORDER BY row_evidence.business_key
                        ),
                        ''
                    ) || ']',
                    'UTF8'
                )
            ),
            'hex'
        ) AS delta_sha256,
        pg_catalog.encode(
            pg_catalog.sha256(
                pg_catalog.convert_to(
                    COALESCE(
                        pg_catalog.string_agg(
                            pg_catalog.jsonb_build_array(
                                row_evidence.id,
                                row_evidence.business_key,
                                row_evidence.source_updated_at,
                                row_evidence.payload_sha256,
                                row_evidence.final_wire_json,
                                row_evidence.delta_wire_json
                            )::text,
                            pg_catalog.chr(30)
                            ORDER BY row_evidence.id
                        ),
                        ''
                    ),
                    'UTF8'
                )
            ),
            'hex'
        ) AS snapshot_rows_evidence_sha256
      FROM pg_temp.legacy_oam_detail_cleanup_replacement_rows AS row_evidence
),
current_evidence AS (
    SELECT
        pg_catalog.count(*) AS current_record_count,
        pg_catalog.encode(
            pg_catalog.sha256(
                pg_catalog.convert_to(
                    COALESCE(
                        pg_catalog.string_agg(
                            pg_catalog.jsonb_build_array(
                                current_record.id,
                                current_record.business_key,
                                current_record.source_updated_at,
                                current_record.payload_sha256,
                                current_record.last_snapshot_id
                            )::text,
                            pg_catalog.chr(30)
                            ORDER BY current_record.id
                        ),
                        ''
                    ),
                    'UTF8'
                )
            ),
            'hex'
        ) AS current_rows_evidence_sha256
      FROM public.external_sync_current_records AS current_record
      CROSS JOIN pg_temp.legacy_oam_detail_cleanup_replacement AS replacement
     WHERE current_record.source_instance = replacement.source_instance
       AND current_record.scope_key = replacement.scope_key
       AND current_record.entity_type = 'work_order'
),
batch_evidence AS (
    SELECT
        pg_catalog.count(*) AS batch_count,
        COALESCE(pg_catalog.sum(batch.record_count), 0) AS batch_record_count,
        pg_catalog.encode(
            pg_catalog.sha256(
                pg_catalog.convert_to(
                    COALESCE(
                        pg_catalog.string_agg(
                            pg_catalog.jsonb_build_array(
                                batch.id,
                                batch.source_instance,
                                batch.batch_id,
                                batch.sequence,
                                batch.total_sequences,
                                batch.record_count,
                                batch.assigned_record_count,
                                batch.body_sha256,
                                batch.reconstructed_body_sha256
                            )::text,
                            pg_catalog.chr(30)
                            ORDER BY batch.sequence
                        ),
                        ''
                    ),
                    'UTF8'
                )
            ),
            'hex'
        ) AS batches_evidence_sha256
      FROM pg_temp.legacy_oam_detail_cleanup_replacement_batches AS batch
)
SELECT
    manifest_evidence.manifest_json_sha256,
    manifest_evidence.reconstructed_manifest_sha256,
    row_evidence.*,
    current_evidence.current_record_count,
    current_evidence.current_rows_evidence_sha256,
    batch_evidence.batch_count,
    batch_evidence.batch_record_count,
    batch_evidence.batches_evidence_sha256,
    pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                pg_catalog.jsonb_build_array(
                    manifest_evidence.manifest_json_sha256,
                    manifest_evidence.reconstructed_manifest_sha256,
                    row_evidence.record_count,
                    row_evidence.final_sha256,
                    row_evidence.delta_sha256,
                    row_evidence.snapshot_rows_evidence_sha256,
                    current_evidence.current_record_count,
                    current_evidence.current_rows_evidence_sha256,
                    batch_evidence.batch_count,
                    batch_evidence.batch_record_count,
                    batch_evidence.batches_evidence_sha256
                )::text,
                'UTF8'
            )
        ),
        'hex'
    ) AS replacement_evidence_sha256
FROM manifest_evidence
CROSS JOIN row_evidence
CROSS JOIN current_evidence
CROSS JOIN batch_evidence;

DO $$
DECLARE
    replacement pg_temp.legacy_oam_detail_cleanup_replacement%ROWTYPE;
    integrity pg_temp.legacy_oam_detail_cleanup_replacement_integrity%ROWTYPE;
    manifest_entity jsonb;
    final_record_count bigint;
    delta_record_count bigint;
    batch_count bigint;
BEGIN
    SELECT * INTO STRICT replacement
      FROM pg_temp.legacy_oam_detail_cleanup_replacement;
    SELECT * INTO STRICT integrity
      FROM pg_temp.legacy_oam_detail_cleanup_replacement_integrity;
    PERFORM 1
      FROM public.external_sync_snapshots
     WHERE id = replacement.id
     FOR UPDATE;
    PERFORM 1
      FROM public.external_sync_snapshot_records AS record
     WHERE record.snapshot_ref_id = replacement.id
     ORDER BY record.id
     FOR SHARE;
    PERFORM 1
      FROM public.external_sync_snapshot_batches AS batch
     WHERE batch.snapshot_ref_id = replacement.id
     ORDER BY batch.id
     FOR SHARE;
    PERFORM 1
      FROM public.external_sync_current_records AS current_record
     WHERE current_record.source_instance = replacement.source_instance
       AND current_record.scope_key = replacement.scope_key
       AND current_record.entity_type = 'work_order'
     ORDER BY current_record.id
     FOR SHARE;

    IF replacement.completed_at IS NULL
       OR replacement.completed_at < replacement.snapshot_at
       OR replacement.parsed_manifest IS NULL THEN
        RAISE EXCEPTION 'replacement full snapshot is incomplete';
    END IF;
    IF COALESCE(replacement.manifest_sha256, '') !~ '^[0-9a-f]{64}$'
       OR replacement.manifest_sha256
          IS DISTINCT FROM integrity.reconstructed_manifest_sha256 THEN
        RAISE EXCEPTION 'replacement manifest hash is invalid';
    END IF;
    IF replacement.parsed_manifest->>'source_system'
           IS DISTINCT FROM replacement.source_system
       OR replacement.parsed_manifest->>'snapshot_id'
           IS DISTINCT FROM replacement.snapshot_id
       OR replacement.parsed_manifest->>'scope_key'
           IS DISTINCT FROM replacement.scope_key
       OR replacement.parsed_manifest->>'sync_mode' IS DISTINCT FROM 'full'
       OR replacement.parsed_manifest->>'company_id'
           IS DISTINCT FROM replacement.company_id
       OR replacement.parsed_manifest->>'org_code'
           IS DISTINCT FROM replacement.org_code
       OR (replacement.parsed_manifest->>'snapshot_at')::timestamptz
           IS DISTINCT FROM replacement.snapshot_at THEN
        RAISE EXCEPTION 'replacement manifest coordinates do not match its row';
    END IF;
    IF pg_catalog.jsonb_typeof(replacement.parsed_manifest->'entities')
           IS DISTINCT FROM 'array'
       OR COALESCE(
              pg_catalog.jsonb_array_length(
                  replacement.parsed_manifest->'entities'
              ),
              -1
          ) <> 1
       OR replacement.parsed_manifest->'entities'->0->>'entity_type'
           IS DISTINCT FROM 'work_order' THEN
        RAISE EXCEPTION
            'replacement full snapshot must contain only work_order';
    END IF;

    manifest_entity := replacement.parsed_manifest->'entities'->0;
    IF COALESCE(manifest_entity->>'final_record_count', '') !~ '^[0-9]+$'
       OR COALESCE(manifest_entity->>'delta_record_count', '') !~ '^[0-9]+$'
       OR COALESCE(manifest_entity->>'batch_count', '') !~ '^[0-9]+$'
       OR COALESCE(manifest_entity->>'final_sha256', '')
          !~ '^[0-9a-f]{64}$'
       OR COALESCE(manifest_entity->>'delta_sha256', '')
          !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'replacement work_order manifest metrics are invalid';
    END IF;
    final_record_count := (manifest_entity->>'final_record_count')::bigint;
    delta_record_count := (manifest_entity->>'delta_record_count')::bigint;
    batch_count := (manifest_entity->>'batch_count')::bigint;

    IF EXISTS (
        SELECT 1
          FROM pg_temp.legacy_oam_detail_cleanup_replacement_rows AS row_evidence
         WHERE row_evidence.parsed_payload IS NULL
            OR pg_catalog.jsonb_object_length(row_evidence.parsed_payload) <> 7
            OR EXISTS (
                SELECT 1
                  FROM pg_catalog.jsonb_each(
                      row_evidence.parsed_payload
                  ) AS payload_field(field_name, field_value)
                 WHERE pg_catalog.jsonb_typeof(payload_field.field_value)
                       <> 'string'
            )
            OR ARRAY(
                SELECT payload_key
                  FROM pg_catalog.jsonb_object_keys(
                      row_evidence.parsed_payload
                  ) AS payload_keys(payload_key)
                 ORDER BY payload_key
            ) <> ARRAY[
                'authCompanyId', 'code', 'executorId', 'id', 'province',
                'statusCode', 'updateTime'
            ]::text[]
            OR row_evidence.canonical_payload_json <> row_evidence.payload_json
            OR row_evidence.payload_sha256 !~ '^[0-9a-f]{64}$'
            OR row_evidence.payload_sha256 <> pg_catalog.encode(
                pg_catalog.sha256(
                    pg_catalog.convert_to(row_evidence.payload_json, 'UTF8')
                ),
                'hex'
            )
            OR row_evidence.source_updated_at IS NULL
            OR row_evidence.payload_source_updated_at IS NULL
            OR row_evidence.payload_source_updated_at
               <> row_evidence.source_updated_at
            OR row_evidence.source_updated_at > replacement.snapshot_at
            OR row_evidence.business_key
               <> 'work-order:' || (row_evidence.parsed_payload->>'code')
            OR row_evidence.parsed_payload->>'authCompanyId'
               <> replacement.company_id
            OR row_evidence.parsed_payload->>'id' = ''
            OR row_evidence.parsed_payload->>'code' = ''
            OR row_evidence.parsed_payload->>'executorId' = ''
            OR row_evidence.parsed_payload->>'statusCode' NOT IN (
                'to_be_create', 'create', 'wait_receive', 'wait_connect',
                'wait_process', 'processing', 'process_finish', 'transferring',
                'wait_client_accept', 'client_accept_pass',
                'wait_platform_accept', 'platform_accept_pass',
                'wait_source_accept', 'source_accept_pass', 'end', 'stopping',
                'stopped', 'rejected', 'closed', 'hang',
                'wait_install_command', 'transfer_reject'
            )
            OR row_evidence.parsed_payload->>'updateTime'
               <> pg_catalog.btrim(
                   row_evidence.parsed_payload->>'updateTime'
               )
            OR row_evidence.operation <> 'upsert'
    ) THEN
        RAISE EXCEPTION
            'replacement work_order payload is not the exact seven-field contract';
    END IF;
    IF (
        SELECT pg_catalog.count(DISTINCT row_evidence.parsed_payload->>'id')
          FROM pg_temp.legacy_oam_detail_cleanup_replacement_rows AS row_evidence
    ) <> integrity.record_count OR (
        SELECT pg_catalog.count(DISTINCT row_evidence.parsed_payload->>'code')
          FROM pg_temp.legacy_oam_detail_cleanup_replacement_rows AS row_evidence
    ) <> integrity.record_count THEN
        RAISE EXCEPTION 'replacement work_order source IDs or codes are duplicated';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.external_sync_snapshot_records AS record
         WHERE record.snapshot_ref_id = replacement.id
           AND (
               record.entity_type <> 'work_order'
               OR record.operation <> 'upsert'
               OR record.source_updated_at IS NULL
               OR record.payload_sha256 !~ '^[0-9a-f]{64}$'
               OR record.payload_sha256 <> pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(record.payload_json, 'UTF8')
                   ),
                   'hex'
               )
           )
    ) OR EXISTS (
        SELECT 1
          FROM public.external_sync_snapshot_batches AS batch
         WHERE batch.snapshot_ref_id = replacement.id
           AND batch.entity_type <> 'work_order'
    ) THEN
        RAISE EXCEPTION
            'replacement snapshot contains non-minimal or corrupt staging rows';
    END IF;

    IF integrity.record_count <> final_record_count
       OR integrity.record_count <> delta_record_count
       OR integrity.final_sha256 <> manifest_entity->>'final_sha256'
       OR integrity.delta_sha256 <> manifest_entity->>'delta_sha256' THEN
        RAISE EXCEPTION
            'replacement final/delta count or SHA-256 does not match manifest';
    END IF;
    IF integrity.batch_count <> batch_count
       OR integrity.batch_record_count <> delta_record_count THEN
        RAISE EXCEPTION 'replacement batch totals do not match manifest';
    END IF;
    IF batch_count = 0 AND delta_record_count <> 0 THEN
        RAISE EXCEPTION 'replacement has records without batches';
    ELSIF batch_count > 0 AND EXISTS (
        SELECT 1
          FROM pg_temp.legacy_oam_detail_cleanup_replacement_batches AS batch
         WHERE (
               batch.source_instance <> replacement.source_instance
               OR batch.replacement_source_instance
                  <> replacement.source_instance
               OR batch.batch_id <> replacement.snapshot_id
                  || '-work_order-' || batch.sequence::text
               OR batch.assigned_record_count <> batch.record_count
               OR batch.sequence NOT BETWEEN 1 AND batch_count
               OR batch.total_sequences <> batch_count
               OR batch.body_sha256 !~ '^[0-9a-f]{64}$'
               OR batch.body_sha256 <> batch.reconstructed_body_sha256
           )
    ) THEN
        RAISE EXCEPTION
            'replacement batch sequence or canonical body SHA-256 is invalid';
    END IF;

    IF (
        SELECT pg_catalog.count(*)
          FROM public.external_sync_current_records AS current_record
         WHERE current_record.source_instance = replacement.source_instance
           AND current_record.scope_key = replacement.scope_key
           AND current_record.entity_type = 'work_order'
           AND current_record.last_snapshot_id = replacement.id
    ) <> final_record_count
       OR integrity.current_record_count <> final_record_count OR EXISTS (
        SELECT 1
          FROM public.external_sync_current_records AS current_record
         WHERE current_record.source_instance = replacement.source_instance
           AND current_record.scope_key = replacement.scope_key
           AND current_record.entity_type = 'work_order'
           AND (
               current_record.last_snapshot_id <> replacement.id
               OR current_record.source_updated_at IS NULL
               OR current_record.payload_sha256 !~ '^[0-9a-f]{64}$'
               OR current_record.payload_sha256 <> pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(current_record.payload_json, 'UTF8')
                   ),
                   'hex'
               )
           )
    ) THEN
        RAISE EXCEPTION
            'replacement is not the complete current work_order mirror';
    END IF;

    -- A full snapshot carries one upsert delta for every final row.  Prove
    -- current and snapshot records are identical without exposing payloads.
    IF final_record_count <> delta_record_count OR EXISTS (
        SELECT
            current_record.business_key,
            current_record.source_updated_at,
            current_record.payload_sha256,
            current_record.payload_json
          FROM public.external_sync_current_records AS current_record
         WHERE current_record.source_instance = replacement.source_instance
           AND current_record.scope_key = replacement.scope_key
           AND current_record.entity_type = 'work_order'
        EXCEPT
        SELECT
            record.business_key,
            record.source_updated_at,
            record.payload_sha256,
            record.payload_json
          FROM public.external_sync_snapshot_records AS record
         WHERE record.snapshot_ref_id = replacement.id
           AND record.entity_type = 'work_order'
    ) OR EXISTS (
        SELECT
            record.business_key,
            record.source_updated_at,
            record.payload_sha256,
            record.payload_json
          FROM public.external_sync_snapshot_records AS record
         WHERE record.snapshot_ref_id = replacement.id
           AND record.entity_type = 'work_order'
        EXCEPT
        SELECT
            current_record.business_key,
            current_record.source_updated_at,
            current_record.payload_sha256,
            current_record.payload_json
          FROM public.external_sync_current_records AS current_record
         WHERE current_record.source_instance = replacement.source_instance
           AND current_record.scope_key = replacement.scope_key
           AND current_record.entity_type = 'work_order'
    ) THEN
        RAISE EXCEPTION
            'replacement full snapshot and current mirror disagree';
    END IF;
END
$$;

DO $$
DECLARE
    replacement pg_temp.legacy_oam_detail_cleanup_replacement%ROWTYPE;
BEGIN
    SELECT * INTO STRICT replacement
      FROM pg_temp.legacy_oam_detail_cleanup_replacement;

    IF EXISTS (
        SELECT 1
          FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
         WHERE candidate.source_system <> 'starcharge_oam'
            OR candidate.source_instance <> replacement.source_instance
            OR candidate.scope_key <> replacement.scope_key
            OR candidate.company_id <> replacement.company_id
            OR candidate.org_code <> replacement.org_code
            OR candidate.id = replacement.id
            OR candidate.snapshot_at >= replacement.snapshot_at
            OR candidate.received_at >= replacement.completed_at
            OR candidate.status_before NOT IN (
                'receiving', 'complete', 'rejected_stale'
            )
    ) THEN
        RAISE EXCEPTION
            'replacement is not strictly newer than every legacy snapshot';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.external_sync_current_records AS current_record
          JOIN public.external_sync_snapshots AS snapshot
            ON snapshot.id = current_record.last_snapshot_id
         WHERE current_record.source_instance = replacement.source_instance
           AND current_record.scope_key = replacement.scope_key
           AND current_record.entity_type IN (
               'work_order_detail', 'work_order_relation'
           )
           AND (
               snapshot.source_instance <> replacement.source_instance
               OR snapshot.scope_key <> replacement.scope_key
               OR snapshot.id NOT IN (
                   SELECT candidate.id
                     FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
               )
           )
    ) THEN
        RAISE EXCEPTION
            'legacy current rows have an unexpected snapshot coordinate';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.external_sync_snapshot_records AS record
         WHERE record.snapshot_ref_id IN (
             SELECT candidate.id
               FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
         )
           AND record.entity_type NOT IN (
               'work_order', 'work_order_detail', 'work_order_relation'
           )
    ) OR EXISTS (
        SELECT 1
          FROM public.external_sync_snapshot_batches AS batch
         WHERE batch.snapshot_ref_id IN (
             SELECT candidate.id
               FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
         )
           AND batch.entity_type NOT IN (
               'work_order', 'work_order_detail', 'work_order_relation'
           )
    ) THEN
        RAISE EXCEPTION
            'legacy snapshot contains an unrelated entity; refusing broad redaction';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.sync_runs AS run
          JOIN pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
            ON candidate.formal_run_key = run.run_key
         WHERE run.status IN (
             'pending', 'receiving', 'validating', 'validated', 'projecting'
         )
    ) THEN
        RAISE EXCEPTION
            'a formal projection run is active for a legacy snapshot';
    END IF;

    -- Bind the audit summary to the actual retired bytes, not merely to a
    -- possibly stale stored digest.  Corrupt legacy evidence requires a
    -- separate incident review and is never silently destroyed here.
    IF EXISTS (
        SELECT 1
          FROM public.external_sync_current_records AS current_record
          JOIN pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
            ON candidate.id = current_record.last_snapshot_id
         WHERE current_record.source_instance = replacement.source_instance
           AND current_record.scope_key = replacement.scope_key
           AND current_record.entity_type IN (
               'work_order_detail', 'work_order_relation'
           )
           AND (
               current_record.payload_sha256 !~ '^[0-9a-f]{64}$'
               OR current_record.payload_sha256 <> pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(current_record.payload_json, 'UTF8')
                   ),
                   'hex'
               )
           )
    ) OR EXISTS (
        SELECT 1
          FROM public.external_sync_snapshot_records AS record
         WHERE record.snapshot_ref_id IN (
                   SELECT candidate.id
                     FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
               )
           AND record.entity_type IN (
               'work_order_detail', 'work_order_relation'
           )
           AND (
               record.payload_sha256 !~ '^[0-9a-f]{64}$'
               OR record.payload_sha256 <> pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(record.payload_json, 'UTF8')
                   ),
                   'hex'
               )
           )
    ) OR EXISTS (
        SELECT 1
          FROM public.external_sync_snapshot_batches AS batch
         WHERE batch.snapshot_ref_id IN (
                   SELECT candidate.id
                     FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
               )
           AND batch.entity_type IN (
               'work_order_detail', 'work_order_relation'
           )
           AND batch.body_sha256 !~ '^[0-9a-f]{64}$'
    ) THEN
        RAISE EXCEPTION
            'legacy payload or batch hash evidence is corrupt; refusing cleanup';
    END IF;
END
$$;

CREATE TEMPORARY TABLE legacy_oam_detail_cleanup_plan
ON COMMIT DROP AS
SELECT
    candidate.id,
    candidate.snapshot_id,
    candidate.snapshot_at,
    candidate.status_before,
    candidate.manifest_sha256,
    pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(snapshot.manifest_json, 'UTF8')
        ),
        'hex'
    ) AS manifest_evidence_sha256,
    candidate.formal_run_key,
    (
        SELECT pg_catalog.count(*)
          FROM public.external_sync_current_records AS current_record
         WHERE current_record.source_instance = candidate.source_instance
           AND current_record.scope_key = candidate.scope_key
           AND current_record.entity_type IN (
               'work_order_detail', 'work_order_relation'
           )
           AND current_record.last_snapshot_id = candidate.id
    ) AS current_record_count,
    (
        SELECT pg_catalog.encode(
            pg_catalog.sha256(
                pg_catalog.convert_to(
                    COALESCE(
                        pg_catalog.string_agg(
                            pg_catalog.jsonb_build_array(
                                current_record.id,
                                current_record.entity_type,
                                current_record.business_key,
                                current_record.payload_sha256,
                                current_record.last_snapshot_id
                            )::text,
                            pg_catalog.chr(30)
                            ORDER BY current_record.id
                        ),
                        ''
                    ),
                    'UTF8'
                )
            ),
            'hex'
        )
          FROM public.external_sync_current_records AS current_record
         WHERE current_record.source_instance = candidate.source_instance
           AND current_record.scope_key = candidate.scope_key
           AND current_record.entity_type IN (
               'work_order_detail', 'work_order_relation'
           )
           AND current_record.last_snapshot_id = candidate.id
    ) AS current_record_evidence_sha256,
    (
        SELECT pg_catalog.count(*)
          FROM public.external_sync_snapshot_records AS record
         WHERE record.snapshot_ref_id = candidate.id
           AND record.entity_type IN (
               'work_order_detail', 'work_order_relation'
           )
    ) AS snapshot_record_count,
    (
        SELECT pg_catalog.encode(
            pg_catalog.sha256(
                pg_catalog.convert_to(
                    COALESCE(
                        pg_catalog.string_agg(
                            pg_catalog.jsonb_build_array(
                                record.id,
                                record.entity_type,
                                record.business_key,
                                record.operation,
                                record.payload_sha256
                            )::text,
                            pg_catalog.chr(30)
                            ORDER BY record.id
                        ),
                        ''
                    ),
                    'UTF8'
                )
            ),
            'hex'
        )
          FROM public.external_sync_snapshot_records AS record
         WHERE record.snapshot_ref_id = candidate.id
           AND record.entity_type IN (
               'work_order_detail', 'work_order_relation'
           )
    ) AS snapshot_record_evidence_sha256,
    (
        SELECT pg_catalog.count(*)
          FROM public.external_sync_snapshot_batches AS batch
         WHERE batch.snapshot_ref_id = candidate.id
           AND batch.entity_type IN (
               'work_order_detail', 'work_order_relation'
           )
    ) AS snapshot_batch_count,
    (
        SELECT pg_catalog.encode(
            pg_catalog.sha256(
                pg_catalog.convert_to(
                    COALESCE(
                        pg_catalog.string_agg(
                            pg_catalog.jsonb_build_array(
                                batch.id,
                                batch.entity_type,
                                batch.sequence,
                                batch.record_count,
                                batch.body_sha256
                            )::text,
                            pg_catalog.chr(30)
                            ORDER BY batch.id
                        ),
                        ''
                    ),
                    'UTF8'
                )
            ),
            'hex'
        )
          FROM public.external_sync_snapshot_batches AS batch
         WHERE batch.snapshot_ref_id = candidate.id
           AND batch.entity_type IN (
               'work_order_detail', 'work_order_relation'
           )
    ) AS snapshot_batch_evidence_sha256,
    (
        SELECT pg_catalog.count(*)
          FROM public.sync_runs AS run
         WHERE run.run_key = candidate.formal_run_key
    ) AS formal_run_count,
    COALESCE((
        SELECT pg_catalog.string_agg(run.status, ',' ORDER BY run.status)
          FROM public.sync_runs AS run
         WHERE run.run_key = candidate.formal_run_key
    ), '') AS formal_run_statuses
FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
JOIN public.external_sync_snapshots AS snapshot
  ON snapshot.id = candidate.id;

CREATE TEMPORARY TABLE legacy_oam_detail_cleanup_formal_evidence
ON COMMIT DROP AS
WITH related_runs AS (
    SELECT run.*
      FROM public.sync_runs AS run
     WHERE run.run_key IN (
         SELECT candidate.formal_run_key
           FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
     )
),
evidence_rows(evidence_kind, evidence_key, evidence_value) AS (
    SELECT
        'run',
        run.id::text,
        pg_catalog.to_jsonb(run)::text
      FROM related_runs AS run
    UNION ALL
    SELECT
        'batch',
        batch.id::text,
        pg_catalog.to_jsonb(batch)::text
      FROM public.sync_batches AS batch
     WHERE batch.run_id IN (SELECT run.id FROM related_runs AS run)
    UNION ALL
    SELECT
        'event',
        event.id::text,
        pg_catalog.to_jsonb(event)::text
      FROM public.sync_inbox_events AS event
     WHERE event.batch_id IN (
         SELECT batch.id
           FROM public.sync_batches AS batch
          WHERE batch.run_id IN (SELECT run.id FROM related_runs AS run)
     )
)
SELECT
    pg_catalog.count(*) AS evidence_row_count,
    pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                COALESCE(
                    pg_catalog.string_agg(
                        pg_catalog.jsonb_build_array(
                            evidence_kind, evidence_key, evidence_value
                        )::text,
                        pg_catalog.chr(30)
                        ORDER BY evidence_kind, evidence_key
                    ),
                    ''
                ),
                'UTF8'
            )
        ),
        'hex'
    ) AS evidence_sha256
FROM evidence_rows;

CREATE TEMPORARY TABLE legacy_oam_detail_cleanup_summary
ON COMMIT DROP AS
WITH replacement AS (
    SELECT * FROM pg_temp.legacy_oam_detail_cleanup_replacement
),
replacement_integrity AS (
    SELECT *
      FROM pg_temp.legacy_oam_detail_cleanup_replacement_integrity
),
totals AS (
    SELECT
        pg_catalog.count(*) AS snapshot_count,
        COALESCE(pg_catalog.sum(plan.current_record_count), 0)
            AS current_record_count,
        COALESCE(pg_catalog.sum(plan.snapshot_record_count), 0)
            AS snapshot_record_count,
        COALESCE(pg_catalog.sum(plan.snapshot_batch_count), 0)
            AS snapshot_batch_count,
        pg_catalog.string_agg(
            pg_catalog.jsonb_build_array(
                plan.id,
                plan.snapshot_id,
                plan.status_before,
                plan.manifest_sha256,
                plan.manifest_evidence_sha256,
                plan.current_record_count,
                plan.current_record_evidence_sha256,
                plan.snapshot_record_count,
                plan.snapshot_record_evidence_sha256,
                plan.snapshot_batch_count,
                plan.snapshot_batch_evidence_sha256,
                plan.formal_run_count,
                plan.formal_run_statuses
            )::text,
            pg_catalog.chr(30)
            ORDER BY plan.id
        ) AS plan_evidence
      FROM pg_temp.legacy_oam_detail_cleanup_plan AS plan
)
SELECT
    cleanup_input.edge_source_instance,
    cleanup_input.scope_key,
    replacement.snapshot_id AS replacement_snapshot_id,
    replacement.manifest_sha256 AS replacement_manifest_sha256,
    replacement_integrity.manifest_json_sha256
        AS replacement_manifest_json_sha256,
    replacement_integrity.reconstructed_manifest_sha256
        AS replacement_reconstructed_manifest_sha256,
    replacement_integrity.record_count AS replacement_record_count,
    replacement_integrity.final_sha256 AS replacement_final_sha256,
    replacement_integrity.delta_sha256 AS replacement_delta_sha256,
    replacement_integrity.snapshot_rows_evidence_sha256
        AS replacement_snapshot_rows_evidence_sha256,
    replacement_integrity.current_record_count
        AS replacement_current_record_count,
    replacement_integrity.current_rows_evidence_sha256
        AS replacement_current_rows_evidence_sha256,
    replacement_integrity.batch_count AS replacement_batch_count,
    replacement_integrity.batch_record_count
        AS replacement_batch_record_count,
    replacement_integrity.batches_evidence_sha256
        AS replacement_batches_evidence_sha256,
    replacement_integrity.replacement_evidence_sha256,
    totals.snapshot_count,
    totals.current_record_count,
    totals.snapshot_record_count,
    totals.snapshot_batch_count,
    formal_evidence.evidence_row_count AS formal_evidence_row_count,
    formal_evidence.evidence_sha256 AS formal_evidence_sha256,
    pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                pg_catalog.jsonb_build_array(
                    'rsc.legacy-oam-work-order-detail-redaction.v2',
                    cleanup_input.edge_source_instance,
                    cleanup_input.scope_key,
                    replacement.id,
                    replacement.snapshot_id,
                    replacement.manifest_sha256,
                    replacement_integrity.manifest_json_sha256,
                    replacement_integrity.reconstructed_manifest_sha256,
                    replacement_integrity.record_count,
                    replacement_integrity.final_sha256,
                    replacement_integrity.delta_sha256,
                    replacement_integrity.snapshot_rows_evidence_sha256,
                    replacement_integrity.current_record_count,
                    replacement_integrity.current_rows_evidence_sha256,
                    replacement_integrity.batch_count,
                    replacement_integrity.batch_record_count,
                    replacement_integrity.batches_evidence_sha256,
                    replacement_integrity.replacement_evidence_sha256,
                    totals.snapshot_count,
                    totals.current_record_count,
                    totals.snapshot_record_count,
                    totals.snapshot_batch_count,
                    formal_evidence.evidence_row_count,
                    formal_evidence.evidence_sha256,
                    totals.plan_evidence
                )::text,
                'UTF8'
            )
        ),
        'hex'
    ) AS audit_sha256
FROM pg_temp.legacy_oam_detail_cleanup_input AS cleanup_input
CROSS JOIN replacement
CROSS JOIN replacement_integrity
CROSS JOIN totals
CROSS JOIN pg_temp.legacy_oam_detail_cleanup_formal_evidence AS formal_evidence;

SELECT
    'DRY_RUN_PLAN' AS result,
    summary.edge_source_instance,
    summary.scope_key,
    summary.replacement_snapshot_id,
    summary.replacement_manifest_sha256,
    summary.replacement_manifest_json_sha256,
    summary.replacement_reconstructed_manifest_sha256,
    summary.replacement_record_count,
    summary.replacement_final_sha256,
    summary.replacement_delta_sha256,
    summary.replacement_snapshot_rows_evidence_sha256,
    summary.replacement_current_record_count,
    summary.replacement_current_rows_evidence_sha256,
    summary.replacement_batch_count,
    summary.replacement_batch_record_count,
    summary.replacement_batches_evidence_sha256,
    summary.replacement_evidence_sha256,
    summary.snapshot_count,
    summary.current_record_count,
    summary.snapshot_record_count,
    summary.snapshot_batch_count,
    summary.formal_evidence_row_count,
    summary.formal_evidence_sha256,
    summary.audit_sha256
FROM pg_temp.legacy_oam_detail_cleanup_summary AS summary;

SELECT
    'SNAPSHOT_PLAN' AS result,
    plan.snapshot_id,
    plan.snapshot_at,
    plan.status_before,
    plan.manifest_sha256,
    plan.manifest_evidence_sha256,
    plan.current_record_count,
    plan.current_record_evidence_sha256,
    plan.snapshot_record_count,
    plan.snapshot_record_evidence_sha256,
    plan.snapshot_batch_count,
    plan.snapshot_batch_evidence_sha256,
    plan.formal_run_count,
    plan.formal_run_statuses
FROM pg_temp.legacy_oam_detail_cleanup_plan AS plan
ORDER BY plan.snapshot_at, plan.id;

SELECT
    cleanup_input.confirm_cleanup <> '' AS cleanup_confirmation_supplied,
    cleanup_input.confirm_cleanup
        = 'I_UNDERSTAND_PURGE_LEGACY_OAM_WORK_ORDER_DETAILS'
        AS cleanup_confirmation_exact,
    cleanup_input.replacement_snapshot_id <> ''
        AND cleanup_input.expected_audit_sha256 <> ''
        AS cleanup_execution_coordinates_present,
    cleanup_input.replacement_snapshot_id = summary.replacement_snapshot_id
        AS cleanup_replacement_exact,
    cleanup_input.expected_audit_sha256 = summary.audit_sha256
        AS cleanup_audit_hash_exact
FROM pg_temp.legacy_oam_detail_cleanup_input AS cleanup_input
CROSS JOIN pg_temp.legacy_oam_detail_cleanup_summary AS summary
\gset

\if :cleanup_confirmation_supplied
\if :cleanup_confirmation_exact
\else
ROLLBACK;
\echo 'confirm_cleanup was supplied but is not the exact approval token'
\quit 3
\endif
\else
ROLLBACK;
\echo 'DRY_RUN complete; no data was changed'
\quit 0
\endif

\if :cleanup_execution_coordinates_present
\else
ROLLBACK;
\echo 'execution requires replacement_snapshot_id and expected_audit_sha256'
\quit 3
\endif
\if :cleanup_replacement_exact
\else
ROLLBACK;
\echo 'replacement snapshot changed or does not match the reviewed dry run'
\quit 4
\endif
\if :cleanup_audit_hash_exact
\else
ROLLBACK;
\echo 'cleanup evidence changed; rerun and review a new dry-run summary'
\quit 4
\endif

CREATE TEMPORARY TABLE legacy_oam_detail_cleanup_actual (
    current_record_count bigint NOT NULL DEFAULT 0,
    snapshot_record_count bigint NOT NULL DEFAULT 0,
    snapshot_batch_count bigint NOT NULL DEFAULT 0,
    redacted_snapshot_count bigint NOT NULL DEFAULT 0
) ON COMMIT DROP;
INSERT INTO pg_temp.legacy_oam_detail_cleanup_actual DEFAULT VALUES;

WITH deleted AS (
    DELETE FROM public.external_sync_current_records AS current_record
     USING pg_temp.legacy_oam_detail_cleanup_input AS cleanup_input
     WHERE current_record.source_instance = cleanup_input.edge_source_instance
       AND current_record.scope_key = cleanup_input.scope_key
       AND current_record.entity_type IN (
           'work_order_detail', 'work_order_relation'
       )
       AND current_record.last_snapshot_id IN (
           SELECT candidate.id
             FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
       )
    RETURNING current_record.id
)
UPDATE pg_temp.legacy_oam_detail_cleanup_actual
   SET current_record_count = (
       SELECT pg_catalog.count(*) FROM deleted
   );

WITH deleted AS (
    DELETE FROM public.external_sync_snapshot_records AS record
     WHERE record.snapshot_ref_id IN (
               SELECT candidate.id
                 FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
           )
       AND record.entity_type IN (
           'work_order_detail', 'work_order_relation'
       )
    RETURNING record.id
)
UPDATE pg_temp.legacy_oam_detail_cleanup_actual
   SET snapshot_record_count = (
       SELECT pg_catalog.count(*) FROM deleted
   );

WITH deleted AS (
    DELETE FROM public.external_sync_snapshot_batches AS batch
     WHERE batch.snapshot_ref_id IN (
               SELECT candidate.id
                 FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
           )
       AND batch.entity_type IN (
           'work_order_detail', 'work_order_relation'
       )
    RETURNING batch.id
)
UPDATE pg_temp.legacy_oam_detail_cleanup_actual
   SET snapshot_batch_count = (
       SELECT pg_catalog.count(*) FROM deleted
   );

WITH redacted AS (
    UPDATE public.external_sync_snapshots AS snapshot
       SET status = 'redacted_legacy'
     WHERE snapshot.id IN (
               SELECT candidate.id
                 FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
           )
       AND snapshot.status = (
           SELECT plan.status_before
             FROM pg_temp.legacy_oam_detail_cleanup_plan AS plan
            WHERE plan.id = snapshot.id
       )
    RETURNING snapshot.id
)
UPDATE pg_temp.legacy_oam_detail_cleanup_actual
   SET redacted_snapshot_count = (
       SELECT pg_catalog.count(*) FROM redacted
   );

DO $$
DECLARE
    summary pg_temp.legacy_oam_detail_cleanup_summary%ROWTYPE;
    actual pg_temp.legacy_oam_detail_cleanup_actual%ROWTYPE;
    formal_evidence_sha256_after text;
    formal_evidence_row_count_after bigint;
BEGIN
    SELECT * INTO STRICT summary
      FROM pg_temp.legacy_oam_detail_cleanup_summary;
    SELECT * INTO STRICT actual
      FROM pg_temp.legacy_oam_detail_cleanup_actual;
    IF actual.current_record_count <> summary.current_record_count
       OR actual.snapshot_record_count <> summary.snapshot_record_count
       OR actual.snapshot_batch_count <> summary.snapshot_batch_count
       OR actual.redacted_snapshot_count <> summary.snapshot_count THEN
        RAISE EXCEPTION
            'deleted/redacted counts differ from the reviewed cleanup plan';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.external_sync_current_records AS current_record
          JOIN pg_temp.legacy_oam_detail_cleanup_input AS cleanup_input
            ON current_record.source_instance
                   = cleanup_input.edge_source_instance
           AND current_record.scope_key = cleanup_input.scope_key
         WHERE current_record.entity_type IN (
             'work_order_detail', 'work_order_relation'
         )
    ) OR EXISTS (
        SELECT 1
          FROM public.external_sync_snapshot_records AS record
         WHERE record.snapshot_ref_id IN (
                   SELECT candidate.id
                     FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
               )
           AND record.entity_type IN (
               'work_order_detail', 'work_order_relation'
           )
    ) OR EXISTS (
        SELECT 1
          FROM public.external_sync_snapshot_batches AS batch
         WHERE batch.snapshot_ref_id IN (
                   SELECT candidate.id
                     FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
               )
           AND batch.entity_type IN (
               'work_order_detail', 'work_order_relation'
           )
    ) OR EXISTS (
        SELECT 1
          FROM public.external_sync_snapshots AS snapshot
         WHERE snapshot.id IN (
                   SELECT candidate.id
                     FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
               )
           AND snapshot.status <> 'redacted_legacy'
    ) THEN
        RAISE EXCEPTION 'legacy detail redaction postcondition failed';
    END IF;

    -- Recompute formal run/batch/inbox evidence.  The cleanup never writes
    -- these tables; a different digest means an unexpected concurrent or
    -- cascading mutation and aborts the whole serializable transaction.
    WITH related_runs AS (
        SELECT run.*
          FROM public.sync_runs AS run
         WHERE run.run_key IN (
             SELECT candidate.formal_run_key
               FROM pg_temp.legacy_oam_detail_cleanup_candidates AS candidate
         )
    ),
    evidence_rows(evidence_kind, evidence_key, evidence_value) AS (
        SELECT
            'run',
            run.id::text,
            pg_catalog.to_jsonb(run)::text
          FROM related_runs AS run
        UNION ALL
        SELECT
            'batch',
            batch.id::text,
            pg_catalog.to_jsonb(batch)::text
          FROM public.sync_batches AS batch
         WHERE batch.run_id IN (SELECT run.id FROM related_runs AS run)
        UNION ALL
        SELECT
            'event',
            event.id::text,
            pg_catalog.to_jsonb(event)::text
          FROM public.sync_inbox_events AS event
         WHERE event.batch_id IN (
             SELECT batch.id
               FROM public.sync_batches AS batch
              WHERE batch.run_id IN (SELECT run.id FROM related_runs AS run)
         )
    )
    SELECT
        pg_catalog.count(*),
        pg_catalog.encode(
            pg_catalog.sha256(
                pg_catalog.convert_to(
                    COALESCE(
                        pg_catalog.string_agg(
                            pg_catalog.jsonb_build_array(
                                evidence_kind, evidence_key, evidence_value
                            )::text,
                            pg_catalog.chr(30)
                            ORDER BY evidence_kind, evidence_key
                        ),
                        ''
                    ),
                    'UTF8'
                )
            ),
            'hex'
        )
      INTO formal_evidence_row_count_after, formal_evidence_sha256_after
      FROM evidence_rows;
    IF formal_evidence_row_count_after <> summary.formal_evidence_row_count
       OR formal_evidence_sha256_after <> summary.formal_evidence_sha256 THEN
        RAISE EXCEPTION 'formal projection run evidence changed during cleanup';
    END IF;
END
$$;

SELECT
    'EXECUTED' AS result,
    summary.edge_source_instance,
    summary.scope_key,
    summary.replacement_snapshot_id,
    summary.replacement_manifest_sha256,
    summary.replacement_manifest_json_sha256,
    summary.replacement_reconstructed_manifest_sha256,
    summary.replacement_record_count,
    summary.replacement_final_sha256,
    summary.replacement_delta_sha256,
    summary.replacement_batch_count,
    summary.replacement_batch_record_count,
    summary.replacement_evidence_sha256,
    actual.redacted_snapshot_count,
    actual.current_record_count AS deleted_current_records,
    actual.snapshot_record_count AS deleted_snapshot_records,
    actual.snapshot_batch_count AS deleted_snapshot_batches,
    summary.formal_evidence_row_count,
    summary.formal_evidence_sha256,
    summary.audit_sha256
FROM pg_temp.legacy_oam_detail_cleanup_summary AS summary
CROSS JOIN pg_temp.legacy_oam_detail_cleanup_actual AS actual;

COMMIT;
