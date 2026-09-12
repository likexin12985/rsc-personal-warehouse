"""Bind SN consumption and its mutable lifecycle/position to immutable stock."""
import hashlib
from pathlib import Path
import runpy

from alembic import op

revision = "20261002_0092"
down_revision = "20261001_0091"
branch_labels = depends_on = None
OLD_HASH = "025c7a50a93bfb8033c3235374dbb9c92a05e41136c2177b3efeb7c3402d8d8a"
NEW_HASH = "f431511b69cc669fefac0ee0c92fd8507a730123cff94fba3969d4d1003382cd"
TABLES = ("inventory_serials", "serial_current_positions", "inventory_transactions",
          "inventory_movements", "inventory_movement_serials")
TERMINAL_FACTS = """EXISTS (SELECT 1 FROM inventory_serials WHERE lifecycle_status <> 'active')
 OR EXISTS (SELECT 1 FROM inventory_movement_serials sn JOIN inventory_transactions tx ON tx.id = sn.transaction_id
            WHERE tx.movement_type IN ('consume', 'scrap'))"""

CHECK_BODY = """
DECLARE
    serial public.inventory_serials%ROWTYPE;
    position public.serial_current_positions%ROWTYPE;
    move record;
    expected_account uuid := NULL;
    expected_movement uuid := NULL;
    expected_cursor bigint := 0;
    expected_status text := 'active';
    terminal_time timestamptz := NULL;
BEGIN
    SELECT * INTO serial FROM public.inventory_serials WHERE id = checked_serial;
    IF NOT FOUND THEN
        RAISE EXCEPTION '0092 serial identity missing' USING ERRCODE = '23514';
    END IF;
    FOR move IN
        SELECT movement.*, tx.ledger_cursor, tx.movement_type, tx.source_document_type,
               tx.effective_at, source.material_id AS source_material, target.material_id AS target_material,
               source.lot_id AS source_lot, target.lot_id AS target_lot
        FROM public.inventory_movement_serials sn
        JOIN public.inventory_movements movement ON movement.id = sn.movement_id AND movement.transaction_id = sn.transaction_id
        JOIN public.inventory_transactions tx ON tx.id = movement.transaction_id AND tx.status = 'posted'
        LEFT JOIN public.stock_accounts source ON source.id = movement.from_account_id
        LEFT JOIN public.stock_accounts target ON target.id = movement.to_account_id
        WHERE sn.serial_id = checked_serial ORDER BY tx.ledger_cursor, movement.line_no
    LOOP
        IF move.ledger_cursor <= expected_cursor OR move.from_account_id IS DISTINCT FROM expected_account
           OR expected_status <> 'active'
           OR (move.from_account_id IS NULL AND move.to_account_id IS NULL)
           OR (move.from_account_id IS NOT NULL AND (move.source_material IS DISTINCT FROM serial.material_id OR move.source_lot IS DISTINCT FROM serial.lot_id))
           OR (move.to_account_id IS NOT NULL AND (move.target_material IS DISTINCT FROM serial.material_id OR move.target_lot IS DISTINCT FROM serial.lot_id))
           OR move.quantity <> (SELECT count(*) FROM public.inventory_movement_serials WHERE movement_id = move.id)
           OR (SELECT count(*) FROM public.material_inventory_policies policy
               WHERE policy.material_id = serial.material_id AND policy.effective_from <= move.effective_at
                 AND (policy.effective_to IS NULL OR policy.effective_to > move.effective_at)
                 AND policy.tracking_mode IN ('serial', 'lot_and_serial')) <> 1 THEN
            RAISE EXCEPTION '0092 serial ledger chain or dimensions invalid' USING ERRCODE = '23514';
        END IF;
        IF move.movement_type = 'consume' THEN
            IF move.source_document_type <> 'work_order_material'
               OR move.external_boundary_code IS DISTINCT FROM 'work_order_material_consume'
               OR move.from_account_id IS NULL OR move.to_account_id IS NOT NULL THEN
                RAISE EXCEPTION '0092 serial consumption requires original work order' USING ERRCODE = '23514';
            END IF;
            expected_status := 'consumed';
            terminal_time := move.created_at;
        ELSIF move.movement_type = 'scrap' THEN
            RAISE EXCEPTION '0092 serial scrap requires controlled lifecycle evidence' USING ERRCODE = '23514';
        END IF;
        expected_account := move.to_account_id;
        expected_movement := move.id;
        expected_cursor := move.ledger_cursor;
    END LOOP;
    SELECT * INTO position FROM public.serial_current_positions WHERE serial_id = checked_serial;
    IF serial.lifecycle_status <> expected_status
       OR (expected_status = 'consumed' AND serial.updated_at IS DISTINCT FROM terminal_time)
       OR (expected_movement IS NULL AND FOUND)
       OR (expected_movement IS NOT NULL AND (NOT FOUND
           OR position.last_movement_id IS DISTINCT FROM expected_movement
           OR position.stock_account_id IS DISTINCT FROM expected_account)) THEN
        RAISE EXCEPTION '0092 serial lifecycle or position projection drift' USING ERRCODE = '23514';
    END IF;
END;
"""
DISPATCH_BODY = """
DECLARE checked_serial uuid;
BEGIN
    IF TG_TABLE_NAME = 'inventory_serials' THEN
        checked_serial := NEW.id;
    ELSIF TG_OP = 'DELETE' THEN
        checked_serial := OLD.serial_id;
    ELSE
        checked_serial := NEW.serial_id;
    END IF;
    PERFORM public.rsc_check_serial_lifecycle_0092(checked_serial);
    RETURN NULL;
END;
"""
GUARD_BODY = """
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION '0092 serial identities and projections cannot be deleted or truncated' USING ERRCODE = '23514';
    END IF;
    IF (to_jsonb(NEW) - ARRAY['lifecycle_status','updated_at']) IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['lifecycle_status','updated_at'])
       OR (NEW.lifecycle_status = OLD.lifecycle_status AND NEW.updated_at IS DISTINCT FROM OLD.updated_at) THEN
        RAISE EXCEPTION '0092 serial identity is immutable; lifecycle requires stock facts' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
"""
FUNCTIONS = {
    ("rsc_check_serial_lifecycle_0092", "uuid"): ("checked_serial uuid", "void", CHECK_BODY),
    ("rsc_dispatch_serial_lifecycle_0092", ""): ("", "trigger", DISPATCH_BODY),
    ("rsc_guard_serial_identity_0092", ""): ("", "trigger", GUARD_BODY),
}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key, value in FUNCTIONS.items()}
TRIGGERS = {
    "trg_inventory_serials_lifecycle_0092": ("inventory_serials", "INSERT OR UPDATE", "rsc_dispatch_serial_lifecycle_0092", 21, True),
    "trg_serial_current_positions_lifecycle_0092": ("serial_current_positions", "INSERT OR UPDATE OR DELETE", "rsc_dispatch_serial_lifecycle_0092", 29, True),
    "trg_inventory_movement_serials_lifecycle_0092": ("inventory_movement_serials", "INSERT", "rsc_dispatch_serial_lifecycle_0092", 5, True),
    "trg_inventory_serials_identity_0092": ("inventory_serials", "UPDATE OR DELETE", "rsc_guard_serial_identity_0092", 27, False),
    "trg_inventory_serials_no_truncate_0092": ("inventory_serials", "TRUNCATE", "rsc_guard_serial_identity_0092", 34, False),
    "trg_serial_current_positions_no_truncate_0092": ("serial_current_positions", "TRUNCATE", "rsc_guard_serial_identity_0092", 34, False),
}


def _transition(upgrade):
    db = op.get_bind()
    if db.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError("0092 supports only PostgreSQL and SQLite")
    prior = runpy.run_path(str(Path(__file__).with_name("20260927_0087_inbound_fulfillment_boundary.py")))
    prior["_begin_sqlite"]()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE " + ", ".join("public." + table for table in TABLES) + " IN SHARE ROW EXCLUSIVE MODE")
    prior["_preflight"](TERMINAL_FACTS, "0092 transition blocked: terminal SN facts require reviewed migration")
    if db.dialect.name != "postgresql":
        return
    if upgrade:
        for (name, signature), (args, result, body) in FUNCTIONS.items():
            op.execute(f"CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${body}$body$")
            op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api")
        op.execute("DO $body$ DECLARE item record; BEGIN FOR item IN SELECT id FROM public.inventory_serials LOOP PERFORM public.rsc_check_serial_lifecycle_0092(item.id); END LOOP; END $body$")
        for name, (table, events, function, _, deferred) in TRIGGERS.items():
            if deferred:
                sql = f"CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW"
            else:
                sql = f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events == 'TRUNCATE' else 'ROW'}"
            op.execute(sql + f" EXECUTE FUNCTION public.{function}()")
            op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
        op.execute("GRANT UPDATE (lifecycle_status, updated_at) ON public.inventory_serials TO star_oam_api")
    else:
        for (name, signature), digest in FUNCTION_HASHES.items():
            op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p
                WHERE p.oid = 'public.{name}({signature})'::regprocedure
                  AND encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') = '{digest}'
                  AND p.proowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
                  AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl WHERE acl.grantee <> p.proowner))
                THEN RAISE EXCEPTION '0092 function source or ownership drift'; END IF; END $body$""")
        op.execute("REVOKE UPDATE (lifecycle_status, updated_at) ON public.inventory_serials FROM star_oam_api")
        for name, (table, *_) in TRIGGERS.items():
            op.execute(f"DROP TRIGGER {name} ON public.{table}")
        for name, signature in reversed(FUNCTIONS):
            op.execute(f"DROP FUNCTION public.{name}({signature})")
    source = runpy.run_path(str(Path(__file__).with_name("20260912_0072_outbound_postings.py")))
    replace = source["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
    replace(signature="public.rsc_oam_runtime_binding_ready_0044()",
            expected_hash=OLD_HASH if upgrade else NEW_HASH, replacement_hash=NEW_HASH if upgrade else OLD_HASH,
            replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),),
            label="serial_lifecycle_readiness_0092")


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
