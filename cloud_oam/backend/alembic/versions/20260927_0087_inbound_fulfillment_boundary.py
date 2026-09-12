"""Keep inbound orders immutable and bind accepted stock to one versioned posting."""
import hashlib
from pathlib import Path
import runpy

from alembic import op

revision = "20260927_0087"
down_revision = "20260926_0086"
branch_labels = depends_on = None
OLD_HASH = "01d934aa197b9dfcd72520f0b776be07abc3b8cedcf9dfc206533a0eac789710"
NEW_HASH = "1c8de32ac17d4db5e3a12049d0b2e4806a8fbb1bb94fa5248729b0a01e58d34a"
APPROVAL_OLD_HASH = "71567de7cb5a838e00bec2fa8dcfab189cefaf271eebed0e08b8c1afa9a68b60"
APPROVAL_NEW_HASH = "593668f5e6b008d0947a24a017fb9172c5c29dca196a1a5fb6fbe6a5bd597f52"


def posting_matches(schema="public."):
    """Same accepted quantities and SN sets on SQLite and PostgreSQL."""
    s = schema
    return f"""EXISTS (
      SELECT 1 FROM {s}inbound_orders io
      JOIN {s}receipts receipt ON receipt.id = io.receipt_id
      JOIN {s}shipments shipment ON shipment.id = receipt.shipment_id
      JOIN {s}inventory_transactions tx ON tx.id = NEW.inventory_transaction_id
      WHERE io.id = NEW.inbound_order_id AND io.status = 'pending'
        AND io.posting_transaction_id IS NULL
        AND receipt.status IN ('accepted', 'exception')
        AND io.target_location_id = shipment.target_location_id
        AND io.target_person_id = shipment.target_person_id
        AND receipt.receiver_person_id = shipment.target_person_id
        AND tx.status = 'posted' AND tx.movement_type = 'transfer'
        AND tx.source_document_type = 'personal_inbound'
        AND replace(tx.source_document_id, '-', '') = replace(CAST(io.id AS text), '-', '')
        AND replace(tx.posting_key, '-', '') = 'personalinbound:' || replace(CAST(io.id AS text), '-', '')
        AND NEW.created_at >= tx.posted_at
        AND EXISTS (SELECT 1 FROM {s}receipt_lines line WHERE line.receipt_id = receipt.id AND line.accepted_qty > 0)
        AND NOT EXISTS (SELECT 1 FROM {s}receipt_lines line WHERE line.receipt_id = receipt.id
          AND line.accepted_qty > 0 AND line.condition NOT IN ('normal', 'shortage'))
        AND NOT EXISTS (
          SELECT 1 FROM {s}receipt_lines line WHERE line.receipt_id = receipt.id AND line.accepted_qty > 0
          AND NOT EXISTS (
            SELECT 1 FROM {s}shipment_lines shipped
            JOIN {s}outbound_postings outbound ON outbound.id = shipped.outbound_posting_id
            JOIN {s}stock_accounts transit ON transit.id = outbound.target_stock_account_id
            JOIN {s}stock_accounts target ON target.owner_org_id = transit.owner_org_id
              AND target.material_id = transit.material_id AND target.condition_code = transit.condition_code
              AND (target.lot_id = transit.lot_id OR (target.lot_id IS NULL AND transit.lot_id IS NULL))
              AND target.location_id = io.target_location_id AND target.custodian_person_id = io.target_person_id
              AND target.availability_bucket = 'available'
            WHERE shipped.id = line.shipment_line_id AND shipped.shipment_id = shipment.id
              AND transit.availability_bucket = 'in_transit'
          )
        )
        AND NOT EXISTS (SELECT 1 FROM {s}receipt_lines line
          JOIN {s}shipment_lines shipped ON shipped.id = line.shipment_line_id
          WHERE line.receipt_id = receipt.id AND shipped.shipment_id <> shipment.id)
        AND NOT EXISTS (
          WITH expected AS (
            SELECT transit.id AS from_id, target.id AS to_id, sum(line.accepted_qty) AS qty
            FROM {s}receipt_lines line
            JOIN {s}shipment_lines shipped ON shipped.id = line.shipment_line_id
            JOIN {s}outbound_postings outbound ON outbound.id = shipped.outbound_posting_id
            JOIN {s}stock_accounts transit ON transit.id = outbound.target_stock_account_id
            JOIN {s}stock_accounts target ON target.owner_org_id = transit.owner_org_id
              AND target.material_id = transit.material_id AND target.condition_code = transit.condition_code
              AND (target.lot_id = transit.lot_id OR (target.lot_id IS NULL AND transit.lot_id IS NULL))
              AND target.location_id = io.target_location_id AND target.custodian_person_id = io.target_person_id
              AND target.availability_bucket = 'available'
            WHERE line.receipt_id = receipt.id AND line.accepted_qty > 0 AND transit.availability_bucket = 'in_transit'
            GROUP BY transit.id, target.id
          ), actual AS (
            SELECT movement.from_account_id AS from_id, movement.to_account_id AS to_id, sum(movement.quantity) AS qty
            FROM {s}inventory_movements movement WHERE movement.transaction_id = tx.id
            GROUP BY movement.from_account_id, movement.to_account_id
          ) SELECT 1 FROM (
            SELECT * FROM (SELECT * FROM expected EXCEPT SELECT * FROM actual) missing
            UNION ALL SELECT * FROM (SELECT * FROM actual EXCEPT SELECT * FROM expected) extra
          ) differences
        )
        AND NOT EXISTS (
          WITH expected AS (
            SELECT serial.serial_id, count(*) AS occurrences FROM {s}receipt_serials serial
            JOIN {s}receipt_lines line ON line.id = serial.receipt_line_id
            WHERE line.receipt_id = receipt.id AND serial.accepted = TRUE GROUP BY serial.serial_id
          ), actual AS (
            SELECT serial.serial_id, count(*) AS occurrences FROM {s}inventory_movement_serials serial
            JOIN {s}inventory_movements movement ON movement.id = serial.movement_id
            WHERE movement.transaction_id = tx.id GROUP BY serial.serial_id
          ) SELECT 1 FROM (
            SELECT * FROM (SELECT * FROM expected EXCEPT SELECT * FROM actual) missing
            UNION ALL SELECT * FROM (SELECT * FROM actual EXCEPT SELECT * FROM expected) extra
          ) differences
        )
    )"""


GUARD_BODY = f"""
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION '0087 inbound postings are append-only' USING ERRCODE = '55000';
    END IF;
    IF NOT ({posting_matches()}) THEN
        RAISE EXCEPTION '0087 inbound posting does not match accepted receipt stock' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
"""
GUARD_HASH = hashlib.sha256(GUARD_BODY.encode()).hexdigest()

COMMAND_BINDING = """    IF EXISTS (
        SELECT 1 FROM public.material_request_commands command
        LEFT JOIN public.inventory_transactions tx ON tx.idempotency_key_hash = command.idempotency_key_hash
        LEFT JOIN public.inbound_postings posting ON posting.inventory_transaction_id = tx.id
        LEFT JOIN public.inbound_orders inbound ON inbound.id = posting.inbound_order_id
        LEFT JOIN public.receipts receipt ON receipt.id = inbound.receipt_id
        WHERE command.request_id = checked_request_id AND command.operation = 'personal_inbound'
          AND (tx.id IS NULL OR inbound.id IS NULL OR receipt.id IS NULL
            OR tx.status <> 'posted' OR tx.movement_type <> 'transfer'
            OR tx.source_document_type <> 'personal_inbound' OR tx.source_document_id <> inbound.id::text
            OR tx.posting_key <> 'personal-inbound:' || inbound.id::text
            OR command.request_hash IS DISTINCT FROM tx.request_hash
            OR command.actor_user_id IS DISTINCT FROM tx.actor_user_id
            OR command.occurred_at IS DISTINCT FROM tx.created_at
            OR command.request_reference <> '/api/v1/material-requests/' || checked_request_id::text || '/inbound-orders/' || inbound.id::text || '/post'
            OR command.request_jsonb IS DISTINCT FROM jsonb_build_object(
                'schema', 'rsc.material_request_fulfillment_command.v1', 'operation', 'personal_inbound',
                'request_id', checked_request_id::text, 'target_version', command.target_version,
                'fact_id', tx.id::text, 'payload_sha256', tx.request_hash)
            OR command.result_jsonb IS DISTINCT FROM jsonb_build_object(
                'schema_version', '1.0', 'operation', 'personal_inbound', 'request_id', checked_request_id::text,
                'target_version', command.target_version, 'fact_id', tx.id::text,
                'personal_inbound_status', command.result_jsonb->>'personal_inbound_status')
            OR command.result_jsonb->>'personal_inbound_status' IS NULL
            OR command.result_jsonb->>'personal_inbound_status' NOT IN ('partially_accepted', 'accepted', 'posted')
            OR (command.target_version = request_row.version AND
                command.result_jsonb->>'personal_inbound_status' IS DISTINCT FROM request_row.personal_inbound_status)
            OR NOT EXISTS (SELECT 1 FROM public.users actor JOIN public.role_assignments assignment ON assignment.user_id = actor.id
                WHERE actor.id = command.actor_user_id AND actor.person_id = command.actor_person_id
                  AND assignment.id = command.actor_role_assignment_id AND assignment.valid_from <= command.occurred_at
                  AND (assignment.valid_to IS NULL OR assignment.valid_to > command.occurred_at))
            OR NOT EXISTS (SELECT 1 FROM public.receipt_lines line
                JOIN public.shipment_lines shipped ON shipped.id = line.shipment_line_id
                JOIN public.outbound_postings outbound ON outbound.id = shipped.outbound_posting_id
                WHERE line.receipt_id = receipt.id AND outbound.request_id = checked_request_id AND line.accepted_qty > 0)
            OR EXISTS (SELECT 1 FROM public.receipt_lines line
                JOIN public.shipment_lines shipped ON shipped.id = line.shipment_line_id
                JOIN public.outbound_postings outbound ON outbound.id = shipped.outbound_posting_id
                WHERE line.receipt_id = receipt.id AND (outbound.request_id <> checked_request_id OR shipped.shipment_id <> receipt.shipment_id))
            OR (SELECT count(*) FROM public.audit_events audit
                WHERE audit.action = 'fulfillment_version_recorded' AND audit.stream_key = 'material_request'
                  AND audit.aggregate_type = 'personal_inbound' AND audit.aggregate_id = tx.id::text
                  AND audit.actor_user_id = command.actor_user_id AND audit.occurred_at = command.occurred_at
                  AND audit.after_jsonb->>'command_id' = command.id::text
                  AND audit.after_jsonb->>'request_id' = checked_request_id::text
                  AND audit.after_jsonb->>'request_version' = command.target_version::text
                  AND audit.after_jsonb->>'request_hash' = command.request_hash
                  AND audit.after_jsonb->>'result_hash' = command.result_hash) <> 1
          )
    ) THEN
        RAISE EXCEPTION '0087 inbound command does not match immutable posting' USING ERRCODE = '23514';
    END IF;

"""


def source_changes():
    older = runpy.run_path(str(Path(__file__).with_name("20260925_0085_fulfillment_version_commands.py")))
    anchor = older["COUNT_ANCHOR"].replace("count(*)", "count(1)")
    return (("'outbound', 'shipment', 'receipt'\n                   )", "'outbound', 'shipment', 'receipt', 'personal_inbound'\n                   )"),
            (anchor, COMMAND_BINDING + anchor.replace("count(1)", "count(2)")))


def _operations(upgrade):
    older = runpy.run_path(str(Path(__file__).with_name("20260909_0069_stock_reservations.py")))
    fn = older["_open_command_operations"]
    before = older["COMMAND_OPERATION_CHECK_NEW"].replace("'release')", "'release', 'pick', 'outbound', 'shipment', 'receipt')")
    fn.__globals__["COMMAND_OPERATION_CHECK_OLD"] = before
    fn.__globals__["COMMAND_OPERATION_CHECK_NEW"] = before.replace("'receipt')", "'receipt', 'personal_inbound')")
    fn(op.get_bind().dialect.name, upgrade=upgrade)


def _postgresql(upgrade):
    op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
    prior = runpy.run_path(str(Path(__file__).with_name("20260912_0072_outbound_postings.py")))
    replace = prior["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
    pairs = source_changes()
    replace(signature="public.rsc_validate_material_request_approval_projection_0045(uuid)",
        expected_hash=APPROVAL_OLD_HASH if upgrade else APPROVAL_NEW_HASH,
        replacement_hash=APPROVAL_NEW_HASH if upgrade else APPROVAL_OLD_HASH,
        replacements=pairs if upgrade else tuple((b, a) for a, b in reversed(pairs)), label="inbound_commands_0087")
    replace(signature="public.rsc_oam_runtime_binding_ready_0044()",
        expected_hash=OLD_HASH if upgrade else NEW_HASH, replacement_hash=NEW_HASH if upgrade else OLD_HASH,
        replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),), label="inbound_readiness_0087")


def _guards(upgrade):
    db = op.get_bind()
    if db.dialect.name == "postgresql":
        if upgrade:
            op.execute(f"CREATE FUNCTION public.rsc_guard_inbound_posting_0087() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${GUARD_BODY}$body$")
            op.execute("REVOKE ALL ON FUNCTION public.rsc_guard_inbound_posting_0087() FROM PUBLIC, star_oam_api")
            for suffix, operation in (("facts", "INSERT OR UPDATE OR DELETE"), ("no_truncate", "TRUNCATE")):
                scope = "ROW" if suffix == "facts" else "STATEMENT"
                name = f"trg_inbound_postings_{suffix}_0087"
                op.execute(f"CREATE TRIGGER {name} BEFORE {operation} ON public.inbound_postings FOR EACH {scope} EXECUTE FUNCTION public.rsc_guard_inbound_posting_0087()")
                op.execute(f"ALTER TABLE public.inbound_postings ENABLE ALWAYS TRIGGER {name}")
        else:
            for suffix in ("facts", "no_truncate"):
                op.execute(f"DROP TRIGGER trg_inbound_postings_{suffix}_0087 ON public.inbound_postings")
            op.execute("DROP FUNCTION public.rsc_guard_inbound_posting_0087()")
    else:
        if upgrade:
            op.execute(f"CREATE TRIGGER trg_inbound_postings_insert_0087 BEFORE INSERT ON inbound_postings WHEN NOT ({posting_matches('')}) BEGIN SELECT RAISE(ABORT, '0087 inbound posting does not match accepted receipt stock'); END")
            for operation in ("update", "delete"):
                op.execute(f"CREATE TRIGGER trg_inbound_postings_{operation}_0087 BEFORE {operation.upper()} ON inbound_postings BEGIN SELECT RAISE(ABORT, '0087 inbound postings are append-only'); END")
        else:
            for operation in ("insert", "update", "delete"):
                op.execute(f"DROP TRIGGER trg_inbound_postings_{operation}_0087")


def _preflight(predicate, message):
    db = op.get_bind()
    if db.dialect.name == "postgresql":
        # Emit the check into offline SQL too; do not inspect the mock connection.
        op.execute(f"DO $$ BEGIN IF {predicate} THEN RAISE EXCEPTION '{message}'; END IF; END $$")
    elif db.exec_driver_sql(f"SELECT {predicate}").scalar():
        raise RuntimeError(message)


def _begin_sqlite():
    if op.get_bind().dialect.name == "sqlite":
        prior = runpy.run_path(str(Path(__file__).with_name("20260926_0086_receipt_evidence_files.py")))
        prior["_files"]()["_ensure_sqlite_migration_transaction"]()


def upgrade():
    db = op.get_bind()
    if db.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError("0087 supports only PostgreSQL and SQLite")
    _begin_sqlite()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.inbound_orders, public.inbound_postings IN SHARE ROW EXCLUSIVE MODE")
    # Never bless legacy postings that predate the command and fact validation.
    _preflight("EXISTS (SELECT 1 FROM inbound_postings)", "0087 upgrade blocked: existing inbound postings require reviewed migration")
    _preflight("EXISTS (SELECT receipt_id FROM inbound_orders GROUP BY receipt_id HAVING count(*) > 1)", "0087 upgrade blocked: duplicate receipt inbound orders require review")
    op.create_index("uq_inbound_orders_receipt", "inbound_orders", ["receipt_id"], unique=True)
    _guards(True)
    if db.dialect.name == "postgresql":
        _postgresql(True)
    _operations(True)


def downgrade():
    db = op.get_bind()
    _begin_sqlite()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.inbound_orders, public.inbound_postings, public.material_request_commands IN SHARE ROW EXCLUSIVE MODE")
    _preflight("EXISTS (SELECT 1 FROM inbound_postings) OR EXISTS (SELECT 1 FROM material_request_commands WHERE operation = 'personal_inbound')", "0087 downgrade blocked: inbound posting commands exist")
    if db.dialect.name == "postgresql":
        _postgresql(False)
    _operations(False)
    _guards(False)
    op.drop_index("uq_inbound_orders_receipt", table_name="inbound_orders")
