"""Independent immutable physical outbound facts with exact source CAS."""
from pathlib import Path
import runpy
from alembic import context, op
import sqlalchemy as sa

revision = "20260912_0072"
down_revision = "20260911_0071"
branch_labels = depends_on = None
TABLES = ("outbound_postings", "outbound_posting_serials")
DOWNGRADE_BLOCKER = "cannot downgrade 0072 while outbound facts exist"


def _previous():
    return runpy.run_path(str(Path(__file__).with_name("20260911_0071_reservation_picking.py")))


def _create_tables():
    op.create_table(
        "outbound_postings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("outbound_line_id", sa.Uuid(), sa.ForeignKey("outbound_lines.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("pick_id", sa.Uuid(), sa.ForeignKey("stock_reservation_picks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("posting_no", sa.String(100), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("allocation_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_line_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("request_version", sa.BigInteger(), nullable=False),
        sa.Column("outbound_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("source_stock_account_id", sa.Uuid(), sa.ForeignKey("stock_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_stock_account_id", sa.Uuid(), sa.ForeignKey("stock_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_balance_version", sa.BigInteger(), nullable=False),
        sa.Column("source_ledger_cursor", sa.BigInteger(), nullable=False),
        sa.Column("outbound_transaction_id", sa.Uuid(), sa.ForeignKey("inventory_transactions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_person_id", sa.Uuid(), sa.ForeignKey("people.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("posting_no", name="uq_outbound_postings_number"),
        sa.UniqueConstraint("idempotency_key_hash", name="uq_outbound_postings_key"),
        sa.UniqueConstraint("outbound_transaction_id", name="uq_outbound_postings_transaction"),
        sa.UniqueConstraint("id", "pick_id", name="uq_outbound_postings_binding"),
        sa.ForeignKeyConstraint(["reservation_id", "allocation_id"], ["stock_reservations.id", "stock_reservations.allocation_id"], name="fk_outbound_postings_reservation", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["request_line_id", "request_id", "revision_id"], ["material_request_lines.id", "material_request_lines.request_id", "material_request_lines.revision_id"], name="fk_outbound_postings_line", ondelete="RESTRICT"),
        sa.CheckConstraint("outbound_qty > 0", name="ck_outbound_postings_qty"),
        sa.CheckConstraint("length(trim(reason)) BETWEEN 1 AND 500", name="ck_outbound_postings_reason"),
        sa.CheckConstraint("request_version > 0 AND revision_no > 0 AND authorization_version > 0", name="ck_outbound_postings_versions"),
        sa.CheckConstraint("source_balance_version >= 0 AND source_ledger_cursor >= 0", name="ck_outbound_postings_coordinate"),
        sa.CheckConstraint("source_stock_account_id <> target_stock_account_id", name="ck_outbound_postings_accounts"),
        sa.CheckConstraint("length(idempotency_key_hash) = 64 AND length(request_hash) = 64", name="ck_outbound_postings_hashes"),
    )
    op.create_index("ix_outbound_postings_request", "outbound_postings", ["request_id", "request_version"])
    op.create_index("ix_outbound_postings_reservation", "outbound_postings", ["reservation_id"])
    op.create_index("ix_outbound_postings_pick_id", "outbound_postings", ["pick_id"])
    op.create_table("outbound_posting_serials",
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        sa.Column("pick_id", sa.Uuid(), nullable=False),
        sa.Column("serial_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("posting_id", "serial_id", name="pk_outbound_posting_serials"),
        sa.UniqueConstraint("pick_id", "serial_id", name="uq_outbound_posting_serial_once"),
        sa.ForeignKeyConstraint(["posting_id", "pick_id"], ["outbound_postings.id", "outbound_postings.pick_id"],
            name="fk_outbound_posting_serials_posting", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pick_id", "serial_id"], ["stock_reservation_pick_serials.pick_id", "stock_reservation_pick_serials.serial_id"],
            name="fk_outbound_posting_serials_original", ondelete="RESTRICT"),
    )


STATE_BODY = """
BEGIN
    IF EXISTS (SELECT 1 FROM public.outbound_postings p
        WHERE p.request_id = checked_request_id AND p.request_version <= checked_version)
       AND NOT EXISTS (SELECT 1 FROM public.material_request_lines l JOIN public.material_requests r ON r.id = l.request_id
        WHERE r.id = checked_request_id AND l.revision_no = r.revision_no
          AND l.final_approved_qty > l.cancelled_qty
          AND (SELECT COALESCE(sum(p.outbound_qty), 0) FROM public.outbound_postings p
                WHERE p.request_line_id = l.id AND p.request_version <= checked_version) < l.final_approved_qty - l.cancelled_qty)
    THEN RETURN 'outbound'; END IF;
    RETURN public.rsc_material_request_picking_state_0071(checked_request_id, checked_version);
END
"""
BINDING_BODY = """
DECLARE original public.stock_reservation_picks%ROWTYPE; request_row public.material_requests%ROWTYPE;
BEGIN
    SELECT * INTO original FROM public.stock_reservation_picks WHERE id = NEW.pick_id;
    IF NOT FOUND THEN RAISE EXCEPTION '0072 reservation missing' USING ERRCODE = '23514'; END IF;
    SELECT * INTO request_row FROM public.material_requests WHERE id = original.request_id FOR UPDATE;
    IF NEW.request_id <> original.request_id OR NEW.request_line_id <> original.request_line_id
       OR NEW.revision_id <> original.revision_id OR NEW.revision_no <> original.revision_no
       OR NEW.allocation_id <> original.allocation_id
       OR NEW.source_stock_account_id <> original.target_stock_account_id
       OR NOT EXISTS (
           SELECT 1 FROM public.stock_accounts s JOIN public.stock_accounts t ON t.id = NEW.target_stock_account_id
            WHERE s.id = original.target_stock_account_id AND s.availability_bucket = 'picking'
              AND t.availability_bucket = 'in_transit'
              AND (s.owner_org_id, s.custodian_person_id, s.location_id, s.material_id, s.condition_code, s.lot_id)
                  IS NOT DISTINCT FROM (t.owner_org_id, t.custodian_person_id, t.location_id, t.material_id, t.condition_code, t.lot_id)
       )
       OR request_row.revision_no <> original.revision_no
       OR NEW.request_version <> request_row.version + 1
       OR request_row.status NOT IN ('approved', 'partially_approved')
       OR request_row.outbound_status NOT IN ('pending_pick', 'picked')
       OR NEW.outbound_line_id <> original.outbound_line_id
       OR NEW.reservation_id <> original.reservation_id
       OR NEW.outbound_qty + COALESCE((SELECT sum(outbound_qty)
             FROM public.outbound_postings WHERE pick_id = original.id), 0) > original.picked_qty
       OR NOT EXISTS (
           SELECT 1 FROM public.inventory_transactions t JOIN public.inventory_movements m ON m.transaction_id = t.id
            WHERE t.id = NEW.outbound_transaction_id AND t.status = 'posted' AND t.movement_type = 'outbound'
              AND t.source_document_type = 'material_request_outbound' AND t.source_document_id = NEW.id::text
              AND t.actor_user_id = NEW.actor_user_id AND t.effective_at = NEW.created_at
              AND t.ledger_cursor > NEW.source_ledger_cursor AND t.reversed_transaction_id IS NULL
              AND m.from_account_id = original.target_stock_account_id AND m.to_account_id = NEW.target_stock_account_id
              AND m.quantity = NEW.outbound_qty AND m.line_no = 1 AND m.external_boundary_code IS NULL
       ) OR (SELECT count(*) FROM public.inventory_movements WHERE transaction_id = NEW.outbound_transaction_id) <> 1
    THEN RAISE EXCEPTION '0072 picking binding invalid' USING ERRCODE = '23514'; END IF;
    RETURN NEW;
END
"""
GRAPH_BODY = """
DECLARE f record; command_row public.material_request_commands%ROWTYPE; tx public.inventory_transactions%ROWTYPE;
        request_row public.material_requests%ROWTYPE; expected_operation text; serial_count bigint;
BEGIN
    SELECT * INTO request_row FROM public.material_requests WHERE id = checked_request_id;
    IF NOT FOUND THEN RAISE EXCEPTION '0072 request missing' USING ERRCODE = '23514'; END IF;
    IF EXISTS (SELECT 1 FROM public.stock_reservation_picks p WHERE p.request_id = checked_request_id
        AND (SELECT COALESCE(sum(o.outbound_qty), 0) FROM public.outbound_postings o WHERE o.pick_id = p.id) > p.picked_qty)
    THEN RAISE EXCEPTION '0072 outbound exceeds original pick' USING ERRCODE = '23514'; END IF;
    FOR f IN
        SELECT r.id, r.request_version, r.request_id, r.request_line_id, r.allocation_id,
               r.actor_user_id, r.actor_person_id, r.authorization_version, r.created_at,
               r.idempotency_key_hash, r.request_hash, r.outbound_transaction_id AS transaction_id,
               r.source_stock_account_id AS source_id, r.target_stock_account_id AS target_id,
               r.outbound_qty AS quantity, 'outbound'::text AS operation, 'posting_id'::text AS id_field,
               'outbound_posting'::text AS aggregate_type, 'material_request_outbound_posted'::text AS action,
               'material_request_outbound'::text AS document_type, r.outbound_line_id, r.reservation_id
          FROM public.outbound_postings r WHERE r.request_id = checked_request_id
    LOOP
        SELECT * INTO tx FROM public.inventory_transactions WHERE id = f.transaction_id;
        IF NOT FOUND OR tx.source_document_type <> f.document_type OR tx.source_document_id <> f.id::text
           OR tx.actor_user_id <> f.actor_user_id OR tx.effective_at <> f.created_at
           OR tx.movement_type <> f.operation OR tx.status <> 'posted'
           OR (SELECT count(*) FROM public.inventory_movements m WHERE m.transaction_id = tx.id) <> 1
           OR NOT EXISTS (SELECT 1 FROM public.inventory_movements m WHERE m.transaction_id = tx.id
               AND m.from_account_id = f.source_id AND m.to_account_id = f.target_id
               AND m.quantity = f.quantity AND m.line_no = 1 AND m.external_boundary_code IS NULL)
        THEN RAISE EXCEPTION '0072 reservation ledger graph invalid' USING ERRCODE = '23514'; END IF;
        SELECT * INTO command_row FROM public.material_request_commands c
         WHERE c.request_id = checked_request_id AND c.target_version = f.request_version AND c.operation = f.operation;
        IF NOT FOUND OR command_row.idempotency_key_hash <> f.idempotency_key_hash
           OR command_row.request_hash <> f.request_hash OR command_row.actor_user_id <> f.actor_user_id
           OR command_row.actor_person_id <> f.actor_person_id OR command_row.authorization_version <> f.authorization_version
           OR command_row.occurred_at <> f.created_at
           OR command_row.result_jsonb->>f.id_field IS DISTINCT FROM f.id::text
           OR command_row.result_jsonb->>'request_id' IS DISTINCT FROM checked_request_id::text
           OR command_row.result_jsonb->>'request_version' IS DISTINCT FROM f.request_version::text
           OR command_row.result_jsonb->>'outbound_line_id' IS DISTINCT FROM f.outbound_line_id::text
           OR command_row.result_jsonb->>'reservation_id' IS DISTINCT FROM f.reservation_id::text
           OR command_row.result_jsonb->>'source_stock_account_id' IS DISTINCT FROM f.source_id::text
           OR command_row.result_jsonb->>'target_stock_account_id' IS DISTINCT FROM f.target_id::text
           OR command_row.result_jsonb->'state_axes'->>'reservation_status' IS DISTINCT FROM
               public.rsc_material_request_reservation_state_0070(checked_request_id, f.request_version)
           OR command_row.result_jsonb->'state_axes'->>'outbound_status' IS DISTINCT FROM
               public.rsc_material_request_outbound_state_0072(checked_request_id, f.request_version)
           OR (SELECT count(*) FROM public.audit_events a WHERE a.stream_key = 'material_request'
                AND a.action = f.action AND a.aggregate_type = f.aggregate_type AND a.aggregate_id = f.id::text
                AND a.actor_user_id = f.actor_user_id AND a.occurred_at = f.created_at
                AND a.after_jsonb->>'command_id' = command_row.id::text
                AND a.after_jsonb->>'request_hash' = command_row.request_hash
                AND a.after_jsonb->>'result_hash' = command_row.result_hash) <> 1
           OR (SELECT count(*) FROM public.state_transition_events s WHERE s.reason = f.action AND s.actor_id = f.actor_user_id
                AND s.metadata_jsonb->>'command_id' = command_row.id::text
                AND s.metadata_jsonb->>'request_version' = f.request_version::text
                AND s.occurred_at = f.created_at
                AND (
                    (public.rsc_material_request_outbound_state_0072(checked_request_id, f.request_version - 1)
                        IS DISTINCT FROM command_row.result_jsonb->'state_axes'->>'outbound_status'
                     AND s.aggregate_type = 'material_request' AND s.aggregate_id = checked_request_id::text
                     AND s.from_status = public.rsc_material_request_outbound_state_0072(checked_request_id, f.request_version - 1)
                     AND s.to_status = command_row.result_jsonb->'state_axes'->>'outbound_status')
                    OR (public.rsc_material_request_outbound_state_0072(checked_request_id, f.request_version - 1)
                        = command_row.result_jsonb->'state_axes'->>'outbound_status'
                     AND s.aggregate_type = f.aggregate_type AND s.aggregate_id = f.id::text
                     AND s.from_status IS NULL AND s.to_status = 'outbound')
                )) <> 1
        THEN RAISE EXCEPTION '0072 reservation command graph invalid' USING ERRCODE = '23514'; END IF;
        SELECT count(*) INTO serial_count FROM public.outbound_posting_serials WHERE posting_id = f.id;
        IF EXISTS (SELECT serial_id FROM public.outbound_posting_serials WHERE posting_id = f.id
                   EXCEPT SELECT serial_id FROM public.inventory_movement_serials WHERE transaction_id = tx.id)
        THEN RAISE EXCEPTION '0072 picking serial graph invalid' USING ERRCODE = '23514'; END IF;
        IF tx.reversed_transaction_id IS NOT NULL OR tx.posted_at IS NULL
        THEN RAISE EXCEPTION '0072 outbound posting incomplete' USING ERRCODE = '23514'; END IF;
        IF serial_count <> (SELECT count(*) FROM public.inventory_movement_serials WHERE transaction_id = tx.id)
           OR (serial_count > 0 AND serial_count <> f.quantity)
        THEN RAISE EXCEPTION '0072 reservation serial count invalid' USING ERRCODE = '23514'; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM public.material_request_commands c WHERE c.request_id = checked_request_id
         AND c.operation = 'outbound' AND NOT EXISTS (
             SELECT 1 FROM public.outbound_postings p WHERE p.request_id = c.request_id AND p.request_version = c.target_version))
       OR request_row.outbound_status IS DISTINCT FROM public.rsc_material_request_outbound_state_0072(checked_request_id, request_row.version)
    THEN RAISE EXCEPTION '0072 picking projection invalid' USING ERRCODE = '23514'; END IF;
END
"""


DISPATCH_BODY = """
DECLARE checked_id uuid;
BEGIN
    IF TG_TABLE_NAME IN ('outbound_postings', 'stock_reservation_picks', 'material_request_commands') THEN checked_id := NEW.request_id;
    ELSIF TG_TABLE_NAME = 'material_requests' THEN checked_id := NEW.id;
    ELSIF TG_TABLE_NAME = 'outbound_posting_serials' THEN
        SELECT request_id INTO checked_id FROM public.outbound_postings WHERE id = NEW.posting_id;
    ELSIF TG_TABLE_NAME = 'inventory_transactions' THEN
        IF NEW.source_document_type <> 'material_request_outbound' THEN RETURN NEW; END IF;
        SELECT request_id INTO checked_id FROM public.outbound_postings WHERE id::text = NEW.source_document_id;
        IF checked_id IS NULL THEN RAISE EXCEPTION '0072 orphan outbound transaction' USING ERRCODE = '23514'; END IF;
    END IF;
    IF checked_id IS NOT NULL THEN PERFORM public.rsc_validate_outbound_graph_0072(checked_id); END IF;
    RETURN NEW;
END
"""
FUNCTIONS = {
    "rsc_material_request_outbound_state_0072": ("checked_request_id uuid, checked_version bigint", "text", STATE_BODY),
    "rsc_guard_outbound_binding_0072": ("", "trigger", BINDING_BODY),
    "rsc_validate_outbound_graph_0072": ("checked_request_id uuid", "void", GRAPH_BODY),
    "rsc_dispatch_outbound_graph_0072": ("", "trigger", DISPATCH_BODY),
}
GRAPH_TABLES = (*TABLES, "stock_reservation_picks", "material_requests", "material_request_commands", "inventory_transactions")


def _create_postgresql_guards():
    for name, (arguments, result, body) in FUNCTIONS.items():
        signature = ", ".join(a.split()[-1] for a in arguments.split(", ")) if arguments else ""
        op.execute(f"CREATE FUNCTION public.{name}({arguments}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${body}$body$")
        op.execute(f"ALTER FUNCTION public.{name}({signature}) OWNER TO star_oam_migrator")
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api")
    for table in TABLES:
        for suffix, event, granularity in (("immutable", "UPDATE OR DELETE", "ROW"), ("no_truncate", "TRUNCATE", "STATEMENT")):
            name = f"trg_{table}_{suffix}_0072"
            op.execute(f"CREATE TRIGGER {name} BEFORE {event} ON public.{table} FOR EACH {granularity} EXECUTE FUNCTION public.rsc_guard_reservation_release_immutable_0070()")
            op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
    name = "trg_outbound_postings_binding_0072"
    op.execute(f"CREATE TRIGGER {name} BEFORE INSERT ON public.outbound_postings FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_outbound_binding_0072()")
    op.execute(f"ALTER TABLE public.outbound_postings ENABLE ALWAYS TRIGGER {name}")
    for table in GRAPH_TABLES:
        name = f"trg_{table}_outbound_graph_0072"
        op.execute(f"CREATE CONSTRAINT TRIGGER {name} AFTER INSERT OR UPDATE ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_dispatch_outbound_graph_0072()")
        op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")


def source_changes():
    previous = _previous()
    old = previous["REQUEST_OUTBOUND_NEW"]
    new = old.replace("OLD.outbound_status NOT IN ('not_started', 'pending_pick')",
        "OLD.outbound_status NOT IN ('not_started', 'pending_pick', 'picked')").replace(
        "NEW.outbound_status NOT IN ('pending_pick', 'picked')",
        "NEW.outbound_status NOT IN ('pending_pick', 'picked', 'outbound')").replace(
        "c.operation = 'pick'", "c.operation IN ('pick', 'outbound')")
    return {
        ("rsc_guard_material_request_identity_0029", ""): ((old, new),),
        ("rsc_validate_material_request_approval_projection_0045", "uuid"): (
            ("'reserve', 'release', 'pick'", "'reserve', 'release', 'pick', 'outbound'"),),
        ("rsc_validate_material_request_supply_causality_0059", "uuid, bigint"): (
            ("OR request_row.outbound_status NOT IN ('not_started', 'pending_pick', 'picked')",
             "OR request_row.outbound_status NOT IN ('not_started', 'pending_pick', 'picked', 'outbound')"),
            ("public.rsc_material_request_picking_state_0071(checked_request_id, command_row.target_version)",
             "public.rsc_material_request_outbound_state_0072(checked_request_id, command_row.target_version)"),),
        ("rsc_validate_picking_graph_0071", "uuid"): (
            ("public.rsc_material_request_picking_state_0071(checked_request_id, request_row.version)",
             "public.rsc_material_request_outbound_state_0072(checked_request_id, request_row.version)"),),
    }


FUNCTION_HASHES = {
    ('rsc_guard_material_request_identity_0029', ''): ("0c6ce152e38c27d8069b4a5732405703d51473a30407a50d1cc4c066fcd5b9ad", "5392ed5737789b4974096e630ed77927952d44b8231c66f59e65d6c88f9603d9"),
    ('rsc_validate_material_request_approval_projection_0045', 'uuid'): ("a82b909450e908ab3bece5cc7c834c03b5f999546d8fc6a0ed3a6d2ced395fa3", "b5d39b3c6287e98657aa7802c628e2d67e3c2a541c1b4c5d99930b15c8cd4110"),
    ('rsc_validate_material_request_supply_causality_0059', 'uuid, bigint'): ("c4d83a251d3e0e9f32b74f174f56563196a7550b80cfc52659f38c1410c73f11", "f2da8696a99dfa4a0a10432f016a9347c3f53f4d066474105eb5390e6f5a16a2"),
    ('rsc_validate_picking_graph_0071', 'uuid'): ("0944c7c92b87b738047acf04c0a1658585f21b3ee50636d7a182fbd1ac2e0d75", "5a97fd00534fca9523b96f330b3ed09208435c76b1afa9d884a231d5cf7a108d"),
}
RUNTIME_READY_BODY_SHA256_0072 = "7bc4ca43bc203431db82c14473ec0d177831ff31ccf9c4711b142a74e6a1cd43"


def _replace_functions(*, upgrade):
    previous = _previous()
    replace = previous["_previous"]()["_previous"]()["_replace_function_source"]
    for coordinate, replacements in source_changes().items():
        before, after = FUNCTION_HASHES[coordinate]
        replace(signature=f"public.{coordinate[0]}({coordinate[1]})",
                expected_hash=before if upgrade else after, replacement_hash=after if upgrade else before,
                replacements=replacements if upgrade else tuple((new, old) for old, new in reversed(replacements)), label="outbound_0072")
    replace(signature="public.rsc_oam_runtime_binding_ready_0044()",
            expected_hash=previous["RUNTIME_READY_BODY_SHA256_0071"] if upgrade else RUNTIME_READY_BODY_SHA256_0072,
            replacement_hash=RUNTIME_READY_BODY_SHA256_0072 if upgrade else previous["RUNTIME_READY_BODY_SHA256_0071"],
            replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),), label="outbound_readiness_0072")


def _operations(dialect, *, upgrade):
    old = _previous()["_previous"]()["_previous"]()
    fn = old["_open_command_operations"]
    before = old["COMMAND_OPERATION_CHECK_NEW"].replace("'release')", "'release', 'pick')")
    fn.__globals__["COMMAND_OPERATION_CHECK_OLD"] = before
    fn.__globals__["COMMAND_OPERATION_CHECK_NEW"] = before.replace("'pick')", "'pick', 'outbound')")
    fn(dialect, upgrade=upgrade)


def _sqlite_guards(*, upgrade):
    old = op.get_bind().exec_driver_sql("SELECT sql FROM sqlite_master WHERE name = 'trg_material_requests_update_guard_0029'").scalar_one()
    pairs = (
        ("OLD.outbound_status IN ('not_started', 'pending_pick')", "OLD.outbound_status IN ('not_started', 'pending_pick', 'picked')"),
        ("NEW.outbound_status IN ('pending_pick', 'picked')", "NEW.outbound_status IN ('pending_pick', 'picked', 'outbound')"),
        ("c.operation = 'pick'", "c.operation IN ('pick', 'outbound')"),
    )
    for before, after in pairs if upgrade else ((b, a) for a, b in reversed(pairs)):
        if old.count(before) != 1: raise RuntimeError("0072 SQLite request guard mismatch")
        old = old.replace(before, after)
    op.execute("DROP TRIGGER trg_material_requests_update_guard_0029")
    op.execute(old)
    if upgrade:
        for table in TABLES:
            for event in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER trg_{table}_{event.lower()}_0072 BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT, '0072 outbound facts are append-only'); END")


def _verify_runtime_ready(expected_hash: str) -> None:
    if expected_hash not in {
        _previous()["RUNTIME_READY_BODY_SHA256_0071"],
        RUNTIME_READY_BODY_SHA256_0072,
    }:
        raise ValueError("unsupported 0072 readiness hash")
    op.execute(
        f"""
DO $rsc_0072_readiness$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('public.rsc_oam_runtime_binding_ready_0044()');
    migrator_oid oid := pg_catalog.to_regrole('star_oam_migrator');
BEGIN
    IF current_user <> 'star_oam_migrator' OR session_user <> 'star_oam_migrator'
       OR function_oid IS NULL OR migrator_oid IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid = function_oid
              AND function_row.proowner = migrator_oid
              AND function_row.prokind = 'f'
              AND function_row.pronargs = 0
              AND function_row.prorettype = 'boolean'::pg_catalog.regtype
              AND function_row.proretset = false
              AND function_row.proargmodes IS NULL
              AND function_row.pronargdefaults = 0
              AND function_row.provariadic = 0
              AND function_row.proparallel = 'u'
              AND function_row.provolatile = 's'
              AND function_row.prosecdef
              AND NOT function_row.proisstrict
              AND NOT function_row.proleakproof
              AND EXISTS (
                  SELECT 1 FROM pg_catalog.pg_language AS language_row
                   WHERE language_row.oid = function_row.prolang
                     AND language_row.lanname = 'sql'
              )
              AND function_row.proconfig = ARRAY['search_path=pg_catalog']::text[]
       )
       OR (SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               function_row.prosrc, 'UTF8')), 'hex')
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid = function_oid)
          IS DISTINCT FROM '{expected_hash}' THEN
        RAISE EXCEPTION '0072 picking catalog mismatch: readiness function identity or hash mismatch';
    END IF;
END
$rsc_0072_readiness$
"""
    )



def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _previous()["_previous"]()["_previous"]()["_lock_postgresql_upgrade_boundary"]()
        _verify_runtime_ready(_previous()["RUNTIME_READY_BODY_SHA256_0071"])
    _create_tables()
    _operations(dialect, upgrade=True)
    if dialect == "postgresql":
        _create_postgresql_guards()
        _replace_functions(upgrade=True)
        for table in TABLES:
            op.execute(f"ALTER TABLE public.{table} OWNER TO star_oam_migrator")
            op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC, star_oam_api")
            op.execute(f"GRANT SELECT, INSERT ON TABLE public.{table} TO star_oam_api")
    elif dialect == "sqlite":
        _sqlite_guards(upgrade=True)
    else:
        raise RuntimeError("0072 supports only PostgreSQL and SQLite")


def downgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("LOCK TABLE public.material_requests, public.outbound_postings, public.outbound_posting_serials IN ACCESS EXCLUSIVE MODE")
    if not context.is_offline_mode() and any(op.get_bind().execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {t})")).scalar() for t in TABLES):
        raise RuntimeError(DOWNGRADE_BLOCKER)
    if dialect == "postgresql":
        _replace_functions(upgrade=False)
        for table in GRAPH_TABLES:
            op.execute(f"DROP TRIGGER trg_{table}_outbound_graph_0072 ON public.{table}")
        op.execute("DROP TRIGGER trg_outbound_postings_binding_0072 ON public.outbound_postings")
        for table in TABLES:
            for suffix in ("immutable", "no_truncate"):
                op.execute(f"DROP TRIGGER trg_{table}_{suffix}_0072 ON public.{table}")
        for name, (arguments, _, _) in reversed(tuple(FUNCTIONS.items())):
            signature = ", ".join(a.split()[-1] for a in arguments.split(", ")) if arguments else ""
            op.execute(f"DROP FUNCTION public.{name}({signature})")
    else:
        _sqlite_guards(upgrade=False)
    _operations(dialect, upgrade=False)
    for table in reversed(TABLES):
        op.drop_table(table)
