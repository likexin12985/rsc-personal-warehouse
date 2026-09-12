"""Seal work-order material facts and reserve usage to their original work order.

SQLite supplies migration/uniqueness coverage only. PostgreSQL owns the deferred
whole-transaction proof. Existing unsealed history requires a reviewed migration;
neither upgrading nor downgrading guesses or deletes historical stock facts.
"""
import hashlib
from pathlib import Path
import runpy

from alembic import op

revision = "20260930_0090"
down_revision = "20260929_0089"
branch_labels = depends_on = None
OLD_HASH = "3c677c5264803e808fe4765dbb340df946e4768a78f2a986cb0f2a296ca8e196"
NEW_HASH = "394df0dcbd0168dc59e9c3853748dc0a794db913e658d482864afaf6aa9de583"
FACT_TABLES = ("work_order_material_operations", "work_order_material_lines",
               "work_order_material_serials", "work_order_replacement_pairs")
PROOF_TABLES = ("inventory_transactions", "inventory_movements", "inventory_movement_serials",
                *FACT_TABLES)
FACTS = " OR ".join(f"EXISTS (SELECT 1 FROM {table})" for table in FACT_TABLES) + " OR EXISTS (SELECT 1 FROM inventory_transactions WHERE source_document_type = 'work_order_material')"

LOCK_BODY = """
BEGIN
    IF NEW.source_document_type = 'work_order_material' THEN
        PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key = 'inventory' FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION '0090 ledger head unavailable' USING ERRCODE = '55000';
        END IF;
        PERFORM 1 FROM public.oam_work_orders WHERE id::text = NEW.source_document_id FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION '0090 work order missing' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
"""

CHECK_BODY = """
DECLARE
    tx public.inventory_transactions%ROWTYPE;
    fact public.work_order_material_operations%ROWTYPE;
    expected_type text;
    expected_command jsonb;
    expected_lines jsonb;
BEGIN
    SELECT * INTO tx FROM public.inventory_transactions WHERE id = checked_transaction;
    IF NOT FOUND THEN
        RAISE EXCEPTION '0090 inventory transaction missing' USING ERRCODE = '23514';
    END IF;
    IF tx.source_document_type <> 'work_order_material' THEN
        IF EXISTS (SELECT 1 FROM public.work_order_material_operations WHERE posting_transaction_id = tx.id) THEN
            RAISE EXCEPTION '0090 unrelated transaction cannot bind a work order' USING ERRCODE = '23514';
        END IF;
        RETURN;
    END IF;
    expected_type := CASE tx.movement_type WHEN 'reserve' THEN 'occupy' WHEN 'release' THEN 'release'
                        WHEN 'consume' THEN 'consume' WHEN 'inbound' THEN 'recover' END;
    IF expected_type IS NULL OR tx.status <> 'posted' OR tx.reversed_transaction_id IS NOT NULL
       OR (SELECT count(*) FROM public.work_order_material_operations WHERE posting_transaction_id = tx.id) <> 1 THEN
        RAISE EXCEPTION '0090 work order transaction requires one supported operation' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO STRICT fact FROM public.work_order_material_operations WHERE posting_transaction_id = tx.id;
    IF fact.status <> 'posted' OR fact.operation_type <> expected_type
       OR fact.oam_work_order_id::text <> tx.source_document_id
       OR fact.idempotency_key_hash !~ '^[0-9a-f]{64}$' OR fact.request_hash !~ '^[0-9a-f]{64}$'
       OR tx.posting_key <> 'work-order-material:' || expected_type || ':' || tx.source_document_id || ':' || fact.idempotency_key_hash
       OR NOT EXISTS (SELECT 1 FROM public.oam_work_orders wo JOIN public.users actor ON actor.id = tx.actor_user_id
            WHERE wo.id = fact.oam_work_order_id AND wo.engineer_person_id = fact.operator_person_id
              AND actor.person_id = fact.operator_person_id AND wo.status = 'active')
       OR EXISTS (SELECT 1 FROM public.work_order_replacement_pairs WHERE operation_id = fact.id) THEN
        RAISE EXCEPTION '0090 work order operation origin mismatch' USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.work_order_material_lines WHERE operation_id = fact.id)
       OR (SELECT count(*) FROM public.work_order_material_lines WHERE operation_id = fact.id)
           <> (SELECT count(*) FROM public.inventory_movements WHERE transaction_id = tx.id)
       OR EXISTS (
          SELECT 1 FROM public.work_order_material_lines line
          LEFT JOIN public.inventory_movements movement ON movement.transaction_id = tx.id AND movement.line_no = line.line_no
          LEFT JOIN public.stock_accounts account ON account.id = line.stock_account_id
          LEFT JOIN public.stock_locations location ON location.id = account.location_id
          LEFT JOIN public.stock_accounts other ON other.id = movement.to_account_id
          WHERE line.operation_id = fact.id AND (
            movement.id IS NULL OR line.line_no < 1 OR line.quantity <> movement.quantity
            OR line.condition_after IS NOT NULL OR line.material_id <> account.material_id
            OR line.condition_before <> account.condition_code
            OR account.custodian_person_id IS DISTINCT FROM fact.operator_person_id
            OR location.location_type <> 'personal' OR location.status <> 'active'
            OR location.custodian_person_id IS DISTINCT FROM fact.operator_person_id
            OR line.stock_account_id IS DISTINCT FROM CASE WHEN expected_type = 'recover' THEN movement.to_account_id ELSE movement.from_account_id END
            OR account.availability_bucket <> CASE WHEN expected_type IN ('consume', 'release') THEN 'reserved' ELSE 'available' END
            OR (expected_type = 'consume' AND (movement.to_account_id IS NOT NULL OR movement.external_boundary_code IS DISTINCT FROM 'work_order_material_consume'))
            OR (expected_type = 'recover' AND (movement.from_account_id IS NOT NULL OR movement.external_boundary_code IS DISTINCT FROM 'work_order_material_recover' OR account.condition_code NOT IN ('used', 'damaged')))
            OR (expected_type IN ('occupy', 'release') AND (
                other.id IS NULL OR movement.external_boundary_code IS NOT NULL
                OR other.owner_org_id <> account.owner_org_id OR other.location_id <> account.location_id
                OR other.custodian_person_id IS DISTINCT FROM account.custodian_person_id
                OR other.material_id <> account.material_id OR other.condition_code <> account.condition_code
                OR other.lot_id IS DISTINCT FROM account.lot_id
                OR other.availability_bucket <> CASE WHEN expected_type = 'occupy' THEN 'reserved' ELSE 'available' END))
          )
       ) THEN
        RAISE EXCEPTION '0090 operation lines do not match personal stock movements' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.work_order_material_lines line
        JOIN public.inventory_movements movement ON movement.transaction_id = tx.id AND movement.line_no = line.line_no
        WHERE line.operation_id = fact.id AND (
            EXISTS (SELECT sn.serial_id FROM public.work_order_material_serials sn WHERE sn.operation_line_id = line.id
                    EXCEPT SELECT sn.serial_id FROM public.inventory_movement_serials sn WHERE sn.movement_id = movement.id)
            OR EXISTS (SELECT sn.serial_id FROM public.inventory_movement_serials sn WHERE sn.movement_id = movement.id
                    EXCEPT SELECT sn.serial_id FROM public.work_order_material_serials sn WHERE sn.operation_line_id = line.id)
            OR EXISTS (SELECT 1 FROM public.work_order_material_serials sn
                LEFT JOIN public.inventory_serials serial ON serial.id = sn.serial_id
                WHERE sn.operation_line_id = line.id AND (NOT sn.sku_verified OR NOT sn.qr_verified
                    OR serial.id IS NULL OR serial.material_id <> line.material_id))
        )
    ) THEN
        RAISE EXCEPTION '0090 operation serials do not match inventory movements' USING ERRCODE = '23514';
    END IF;
    SELECT jsonb_agg(jsonb_build_object(
        'material_id', line.material_id::text, 'stock_account_id', line.stock_account_id::text,
        'quantity', to_char(line.quantity, 'FM999999999999990.000'), 'condition_before', line.condition_before,
        'target_stock_account_id', CASE WHEN expected_type IN ('occupy', 'release') THEN movement.to_account_id::text END,
        'serial_ids', COALESCE((SELECT jsonb_agg(sn.serial_id::text ORDER BY sn.serial_id::text)
            FROM public.work_order_material_serials sn WHERE sn.operation_line_id = line.id), '[]'::jsonb),
        'serial_verifications', COALESCE((SELECT jsonb_agg(jsonb_build_object('serial_id', sn.serial_id::text,
            'sku_code', material.sku_code, 'serial_no', serial.serial_no, 'qr_code', serial.qr_code) ORDER BY sn.serial_id::text)
            FROM public.work_order_material_serials sn JOIN public.inventory_serials serial ON serial.id = sn.serial_id
            JOIN public.materials material ON material.id = serial.material_id
            WHERE sn.operation_line_id = line.id), '[]'::jsonb)
    ) ORDER BY line.line_no) INTO expected_lines
    FROM public.work_order_material_lines line JOIN public.inventory_movements movement
      ON movement.transaction_id = tx.id AND movement.line_no = line.line_no
    WHERE line.operation_id = fact.id;
    expected_command := jsonb_build_object('operation_type', expected_type, 'work_order_id', fact.oam_work_order_id::text,
        'operator_person_id', fact.operator_person_id::text, 'lines', expected_lines, 'replacement_pairs', '[]'::jsonb);
    IF fact.request_hash <> encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected_command), 'UTF8')), 'hex')
       OR (SELECT count(*) FROM public.audit_events audit WHERE audit.stream_key = 'material_request'
            AND audit.actor_user_id = tx.actor_user_id AND audit.aggregate_type = 'work_order_material_operation'
            AND audit.aggregate_id = fact.id::text AND audit.action = 'work_order_material.' || expected_type
            AND audit.request_id = 'work-order-material:' || fact.idempotency_key_hash
            AND audit.after_jsonb = jsonb_build_object('work_order_id', fact.oam_work_order_id::text,
                'operation_type', expected_type, 'posting_transaction_id', tx.id::text,
                'line_count', jsonb_array_length(expected_lines), 'request_hash', fact.request_hash, 'command', expected_command)) <> 1
       OR (SELECT count(*) FROM public.outbox_events event WHERE event.aggregate_type = 'work_order_material_operation'
            AND event.aggregate_id = fact.id::text AND event.event_type = 'work_order_material_operation_posted'
            AND event.idempotency_key = 'work-order-material-operation:' || fact.id::text
            AND event.payload_jsonb = jsonb_build_object('work_order_id', fact.oam_work_order_id::text,
                'operation_type', expected_type, 'operation_no', fact.operation_no, 'posting_transaction_id', tx.id::text)) <> 1 THEN
        RAISE EXCEPTION '0090 work order command audit or outbox mismatch' USING ERRCODE = '23514';
    END IF;
    -- Use ledger cursor order, never caller-controlled effective time or account balance.
    -- Checking every prefix also rejects consume-then-reserve within one transaction.
    IF EXISTS (
        SELECT 1 FROM (
            SELECT sum(CASE WHEN previous.movement_type = 'reserve' THEN movement.quantity ELSE -movement.quantity END)
                OVER (PARTITION BY CASE WHEN previous.movement_type = 'reserve' THEN movement.to_account_id ELSE movement.from_account_id END
                      ORDER BY previous.ledger_cursor, movement.line_no ROWS UNBOUNDED PRECEDING) AS remaining
            FROM public.inventory_transactions previous JOIN public.inventory_movements movement ON movement.transaction_id = previous.id
            WHERE previous.source_document_type = 'work_order_material' AND previous.source_document_id = tx.source_document_id
              AND previous.status = 'posted' AND previous.movement_type IN ('reserve', 'consume', 'release')
              AND previous.ledger_cursor <= tx.ledger_cursor
        ) history WHERE remaining < 0
    ) THEN
        RAISE EXCEPTION '0090 work order reserved quantity exhausted' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM (
            SELECT sum(CASE WHEN previous.movement_type = 'reserve' THEN 1 ELSE -1 END)
                OVER (PARTITION BY sn.serial_id,
                      CASE WHEN previous.movement_type = 'reserve' THEN movement.to_account_id ELSE movement.from_account_id END
                      ORDER BY previous.ledger_cursor, movement.line_no ROWS UNBOUNDED PRECEDING) AS remaining
            FROM public.inventory_transactions previous JOIN public.inventory_movements movement ON movement.transaction_id = previous.id
            JOIN public.inventory_movement_serials sn ON sn.movement_id = movement.id
            WHERE previous.source_document_type = 'work_order_material' AND previous.source_document_id = tx.source_document_id
              AND previous.status = 'posted' AND previous.movement_type IN ('reserve', 'consume', 'release')
              AND previous.ledger_cursor <= tx.ledger_cursor
        ) history WHERE remaining NOT IN (0, 1)
    ) THEN
        RAISE EXCEPTION '0090 serial is not reserved by this work order' USING ERRCODE = '23514';
    END IF;
END;
"""

DISPATCH_BODY = """
DECLARE checked_transaction uuid;
BEGIN
    IF TG_TABLE_NAME = 'inventory_transactions' THEN checked_transaction := NEW.id;
    ELSIF TG_TABLE_NAME IN ('inventory_movements', 'inventory_movement_serials') THEN checked_transaction := NEW.transaction_id;
    ELSIF TG_TABLE_NAME = 'work_order_material_operations' THEN checked_transaction := NEW.posting_transaction_id;
    ELSIF TG_TABLE_NAME IN ('work_order_material_lines', 'work_order_replacement_pairs') THEN
        SELECT posting_transaction_id INTO checked_transaction FROM public.work_order_material_operations WHERE id = NEW.operation_id;
    ELSIF TG_TABLE_NAME = 'work_order_material_serials' THEN
        SELECT operation.posting_transaction_id INTO checked_transaction FROM public.work_order_material_operations operation
            JOIN public.work_order_material_lines line ON line.operation_id = operation.id WHERE line.id = NEW.operation_line_id;
    ELSE RAISE EXCEPTION '0090 unknown proof table' USING ERRCODE = '55000';
    END IF;
    IF checked_transaction IS NULL THEN
        RAISE EXCEPTION '0090 work order posting binding missing' USING ERRCODE = '23514';
    END IF;
    PERFORM public.rsc_check_work_order_material_transaction_0090(checked_transaction);
    RETURN NEW;
END;
"""
IMMUTABLE_BODY = """
BEGIN
    RAISE EXCEPTION '0090 work order facts are append-only' USING ERRCODE = '55000';
END;
"""
FUNCTIONS = {
    ("rsc_lock_work_order_material_0090", ""): ("", "trigger", LOCK_BODY),
    ("rsc_check_work_order_material_transaction_0090", "uuid"): ("checked_transaction uuid", "void", CHECK_BODY),
    ("rsc_dispatch_work_order_material_0090", ""): ("", "trigger", DISPATCH_BODY),
    ("rsc_guard_work_order_facts_0090", ""): ("", "trigger", IMMUTABLE_BODY),
}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key, value in FUNCTIONS.items()}


def _transition(upgrade):
    db = op.get_bind()
    if db.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError("0090 supports only PostgreSQL and SQLite")
    prior = runpy.run_path(str(Path(__file__).with_name("20260927_0087_inbound_fulfillment_boundary.py")))
    prior["_begin_sqlite"]()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE " + ", ".join("public." + name for name in PROOF_TABLES) + " IN SHARE ROW EXCLUSIVE MODE")
    prior["_preflight"](FACTS, "0090 transition blocked: work order facts require reviewed migration")
    if upgrade:
        op.create_index("uq_work_order_material_posting_0090", "work_order_material_operations", ["posting_transaction_id"], unique=True)
    else:
        op.drop_index("uq_work_order_material_posting_0090", table_name="work_order_material_operations")
    if db.dialect.name == "postgresql":
        if upgrade:
            for (name, signature), (args, result, body) in FUNCTIONS.items():
                op.execute(f"CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${body}$body$")
                op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api")
            for table in PROOF_TABLES:
                name = f"trg_{table}_proof_0090"
                op.execute(f"CREATE CONSTRAINT TRIGGER {name} AFTER INSERT ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_dispatch_work_order_material_0090()")
                op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
            op.execute("CREATE TRIGGER trg_inventory_transactions_work_order_lock_0090 BEFORE INSERT ON public.inventory_transactions FOR EACH ROW EXECUTE FUNCTION public.rsc_lock_work_order_material_0090()")
            op.execute("ALTER TABLE public.inventory_transactions ENABLE ALWAYS TRIGGER trg_inventory_transactions_work_order_lock_0090")
            for table in FACT_TABLES:
                name = f"trg_{table}_no_truncate_0090"
                op.execute(f"CREATE TRIGGER {name} BEFORE TRUNCATE ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.rsc_guard_work_order_facts_0090()")
                op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
        else:
            for table in FACT_TABLES:
                op.execute(f"DROP TRIGGER trg_{table}_no_truncate_0090 ON public.{table}")
            for table in PROOF_TABLES:
                op.execute(f"DROP TRIGGER trg_{table}_proof_0090 ON public.{table}")
            op.execute("DROP TRIGGER trg_inventory_transactions_work_order_lock_0090 ON public.inventory_transactions")
            for name, signature in reversed(FUNCTIONS):
                op.execute(f"DROP FUNCTION public.{name}({signature})")
        source = runpy.run_path(str(Path(__file__).with_name("20260912_0072_outbound_postings.py")))
        replace = source["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
        replace(signature="public.rsc_oam_runtime_binding_ready_0044()",
                expected_hash=OLD_HASH if upgrade else NEW_HASH,
                replacement_hash=NEW_HASH if upgrade else OLD_HASH,
                replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),),
                label="work_order_readiness_0090")


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
