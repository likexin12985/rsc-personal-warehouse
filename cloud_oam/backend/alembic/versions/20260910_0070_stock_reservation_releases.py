"""Append reservation release evidence without rewriting original reservations.

Quantity and SN ownership belong to the original reservation, not the shared
reserved account. Published 0069 is retained unchanged; all function changes
are exact, reversible source replacements which preserve identity and ACLs.
"""
from __future__ import annotations

from pathlib import Path
import runpy

from alembic import context, op
import sqlalchemy as sa

revision = "20260910_0070"
down_revision = "20260909_0069"
branch_labels = depends_on = None
TABLES = ("stock_reservation_releases", "stock_reservation_release_serials")
DOWNGRADE_BLOCKER = "cannot downgrade 0070 while reservation release facts exist"


def _previous():
    return runpy.run_path(str(Path(__file__).with_name("20260909_0069_stock_reservations.py")))


def _create_tables():
    op.create_table(
        "stock_reservation_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("release_no", sa.String(100), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("allocation_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_line_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("request_version", sa.BigInteger(), nullable=False),
        sa.Column("released_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("source_stock_account_id", sa.Uuid(), sa.ForeignKey("stock_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_stock_account_id", sa.Uuid(), sa.ForeignKey("stock_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_balance_version", sa.BigInteger(), nullable=False),
        sa.Column("source_ledger_cursor", sa.BigInteger(), nullable=False),
        sa.Column("release_transaction_id", sa.Uuid(), sa.ForeignKey("inventory_transactions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_person_id", sa.Uuid(), sa.ForeignKey("people.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("release_no", name="uq_stock_reservation_releases_number"),
        sa.UniqueConstraint("idempotency_key_hash", name="uq_stock_reservation_releases_key"),
        sa.UniqueConstraint("release_transaction_id", name="uq_stock_reservation_releases_transaction"),
        sa.UniqueConstraint("id", "reservation_id", "allocation_id", name="uq_stock_reservation_releases_binding"),
        sa.ForeignKeyConstraint(["reservation_id", "allocation_id"], ["stock_reservations.id", "stock_reservations.allocation_id"], name="fk_stock_reservation_releases_reservation", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["request_line_id", "request_id", "revision_id"], ["material_request_lines.id", "material_request_lines.request_id", "material_request_lines.revision_id"], name="fk_stock_reservation_releases_line", ondelete="RESTRICT"),
        sa.CheckConstraint("released_qty > 0", name="ck_stock_reservation_releases_qty"),
        sa.CheckConstraint("length(trim(reason)) BETWEEN 1 AND 500", name="ck_stock_reservation_releases_reason"),
        sa.CheckConstraint("request_version > 0 AND revision_no > 0 AND authorization_version > 0", name="ck_stock_reservation_releases_versions"),
        sa.CheckConstraint("source_balance_version >= 0 AND source_ledger_cursor >= 0", name="ck_stock_reservation_releases_coordinate"),
        sa.CheckConstraint("source_stock_account_id <> target_stock_account_id", name="ck_stock_reservation_releases_accounts"),
        sa.CheckConstraint("length(idempotency_key_hash) = 64 AND length(request_hash) = 64", name="ck_stock_reservation_releases_hashes"),
    )
    op.create_index("ix_stock_reservation_releases_request", TABLES[0], ["request_id", "request_version"])
    op.create_index("ix_stock_reservation_releases_reservation", TABLES[0], ["reservation_id"])
    op.create_table(
        "stock_reservation_release_serials",
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("allocation_id", sa.Uuid(), nullable=False),
        sa.Column("serial_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("release_id", "serial_id", name="pk_stock_reservation_release_serials"),
        sa.UniqueConstraint("reservation_id", "serial_id", name="uq_stock_reservation_release_serial_once"),
        sa.ForeignKeyConstraint(["release_id", "reservation_id", "allocation_id"], ["stock_reservation_releases.id", "stock_reservation_releases.reservation_id", "stock_reservation_releases.allocation_id"], name="fk_stock_reservation_release_serials_release", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reservation_id", "allocation_id", "serial_id"], ["stock_reservation_serials.reservation_id", "stock_reservation_serials.allocation_id", "stock_reservation_serials.serial_id"], name="fk_stock_reservation_release_serials_original", ondelete="RESTRICT"),
    )


# Created before the request guard which calls it. The version argument is also
# used to verify historical supply/reservation results after later releases.
STATE_BODY = """
DECLARE reserved numeric; released numeric; approved numeric;
BEGIN
    SELECT COALESCE(sum(f.reserved_qty), 0) INTO reserved
      FROM public.stock_reservations f
     WHERE f.request_id = checked_request_id AND f.request_version <= checked_version;
    SELECT COALESCE(sum(f.released_qty), 0) INTO released
      FROM public.stock_reservation_releases f
     WHERE f.request_id = checked_request_id AND f.request_version <= checked_version;
    SELECT COALESCE(sum(l.final_approved_qty - l.cancelled_qty), 0) INTO approved
      FROM public.material_request_lines l JOIN public.material_requests r ON r.id = l.request_id
     WHERE r.id = checked_request_id AND l.revision_no = r.revision_no
       AND l.status IN ('approved', 'partially_approved');
    IF released < 0 OR released > reserved THEN
        RAISE EXCEPTION '0070 reservation quantity graph invalid' USING ERRCODE = '23514';
    END IF;
    IF released > 0 THEN
        IF released = reserved THEN RETURN 'released'; END IF;
        RETURN 'partially_released';
    END IF;
    IF reserved = 0 THEN RETURN 'not_reserved'; END IF;
    IF approved > 0 AND reserved >= approved THEN RETURN 'reserved'; END IF;
    RETURN 'pending';
END
"""

IMMUTABLE_BODY = """
BEGIN
    RAISE EXCEPTION '0070 reservation release facts are append-only' USING ERRCODE = '23514';
END
"""

RELEASE_BINDING_BODY = """
DECLARE original public.stock_reservations%ROWTYPE; request_row public.material_requests%ROWTYPE;
BEGIN
    SELECT * INTO original FROM public.stock_reservations WHERE id = NEW.reservation_id;
    IF NOT FOUND THEN RAISE EXCEPTION '0070 reservation missing' USING ERRCODE = '23514'; END IF;
    SELECT * INTO request_row FROM public.material_requests WHERE id = original.request_id FOR UPDATE;
    IF NEW.request_id <> original.request_id OR NEW.request_line_id <> original.request_line_id
       OR NEW.revision_id <> original.revision_id OR NEW.revision_no <> original.revision_no
       OR NEW.allocation_id <> original.allocation_id
       OR NEW.source_stock_account_id <> original.stock_account_id
       OR NEW.target_stock_account_id <> original.source_stock_account_id
       OR request_row.revision_no <> original.revision_no
       OR NEW.request_version <> request_row.version + 1
       OR request_row.status NOT IN ('approved', 'partially_approved')
       OR request_row.outbound_status <> 'not_started'
       OR original.status <> 'reserved' OR original.released_qty <> 0
       OR original.release_transaction_id IS NOT NULL
       OR NEW.released_qty + COALESCE((SELECT sum(released_qty)
             FROM public.stock_reservation_releases WHERE reservation_id = original.id), 0) > original.reserved_qty
       OR NOT EXISTS (
           SELECT 1 FROM public.inventory_transactions t JOIN public.inventory_movements m ON m.transaction_id = t.id
            WHERE t.id = NEW.release_transaction_id AND t.status = 'posted' AND t.movement_type = 'release'
              AND t.source_document_type = 'material_request_reservation_release' AND t.source_document_id = NEW.id::text
              AND t.actor_user_id = NEW.actor_user_id AND t.effective_at = NEW.created_at
              AND t.ledger_cursor > NEW.source_ledger_cursor AND t.reversed_transaction_id IS NULL
              AND m.from_account_id = original.stock_account_id AND m.to_account_id = original.source_stock_account_id
              AND m.quantity = NEW.released_qty AND m.line_no = 1 AND m.external_boundary_code IS NULL
       ) OR (SELECT count(*) FROM public.inventory_movements WHERE transaction_id = NEW.release_transaction_id) <> 1
    THEN RAISE EXCEPTION '0070 reservation release binding invalid' USING ERRCODE = '23514'; END IF;
    RETURN NEW;
END
"""

GRAPH_BODY = """
DECLARE f record; command_row public.material_request_commands%ROWTYPE; tx public.inventory_transactions%ROWTYPE;
        request_row public.material_requests%ROWTYPE; expected_operation text; serial_count bigint;
BEGIN
    SELECT * INTO request_row FROM public.material_requests WHERE id = checked_request_id;
    IF NOT FOUND THEN RAISE EXCEPTION '0070 request missing' USING ERRCODE = '23514'; END IF;
    IF EXISTS (
        SELECT 1 FROM public.stock_allocations a WHERE a.request_id = checked_request_id
         AND (SELECT COALESCE(sum(r.reserved_qty), 0) FROM public.stock_reservations r WHERE r.allocation_id = a.id) > a.allocated_qty
    ) OR EXISTS (
        SELECT 1 FROM public.stock_reservations r WHERE r.request_id = checked_request_id
         AND (SELECT COALESCE(sum(x.released_qty), 0) FROM public.stock_reservation_releases x WHERE x.reservation_id = r.id) > r.reserved_qty
    ) OR EXISTS (
        SELECT s.allocation_id, s.serial_id FROM public.stock_reservation_serials s
          JOIN public.stock_reservations r ON r.id = s.reservation_id WHERE r.request_id = checked_request_id
         GROUP BY s.allocation_id, s.serial_id HAVING count(*) <> 1
    ) THEN RAISE EXCEPTION '0070 reservation quantity graph invalid' USING ERRCODE = '23514'; END IF;
    FOR f IN
        SELECT r.id, r.request_version, r.request_id, r.request_line_id, r.allocation_id,
               r.actor_user_id, r.actor_person_id, r.authorization_version, r.created_at,
               r.idempotency_key_hash, r.request_hash, r.reserve_transaction_id AS transaction_id,
               r.source_stock_account_id AS source_id, r.stock_account_id AS target_id,
               r.reserved_qty AS quantity, 'reserve'::text AS operation,
               'reservation_id'::text AS id_field, 'stock_reservation'::text AS aggregate_type,
               'material_request_reservation_created'::text AS action,
               'material_request_reservation'::text AS document_type
          FROM public.stock_reservations r WHERE r.request_id = checked_request_id
        UNION ALL
        SELECT r.id, r.request_version, r.request_id, r.request_line_id, r.allocation_id,
               r.actor_user_id, r.actor_person_id, r.authorization_version, r.created_at,
               r.idempotency_key_hash, r.request_hash, r.release_transaction_id,
               r.source_stock_account_id, r.target_stock_account_id, r.released_qty,
               'release', 'release_id', 'stock_reservation_release',
               'material_request_reservation_released', 'material_request_reservation_release'
          FROM public.stock_reservation_releases r WHERE r.request_id = checked_request_id
    LOOP
        SELECT * INTO tx FROM public.inventory_transactions WHERE id = f.transaction_id;
        IF NOT FOUND OR tx.source_document_type <> f.document_type OR tx.source_document_id <> f.id::text
           OR tx.actor_user_id <> f.actor_user_id OR tx.effective_at <> f.created_at
           OR tx.movement_type <> f.operation OR tx.status <> 'posted'
           OR (SELECT count(*) FROM public.inventory_movements m WHERE m.transaction_id = tx.id) <> 1
           OR NOT EXISTS (SELECT 1 FROM public.inventory_movements m WHERE m.transaction_id = tx.id
               AND m.from_account_id = f.source_id AND m.to_account_id = f.target_id
               AND m.quantity = f.quantity AND m.line_no = 1 AND m.external_boundary_code IS NULL)
        THEN RAISE EXCEPTION '0070 reservation ledger graph invalid' USING ERRCODE = '23514'; END IF;
        SELECT * INTO command_row FROM public.material_request_commands c
         WHERE c.request_id = checked_request_id AND c.target_version = f.request_version AND c.operation = f.operation;
        IF NOT FOUND OR command_row.idempotency_key_hash <> f.idempotency_key_hash
           OR command_row.request_hash <> f.request_hash OR command_row.actor_user_id <> f.actor_user_id
           OR command_row.actor_person_id <> f.actor_person_id OR command_row.authorization_version <> f.authorization_version
           OR command_row.occurred_at <> f.created_at
           OR command_row.result_jsonb->>f.id_field IS DISTINCT FROM f.id::text
           OR command_row.result_jsonb->>'request_id' IS DISTINCT FROM checked_request_id::text
           OR command_row.result_jsonb->>'request_version' IS DISTINCT FROM f.request_version::text
           OR command_row.result_jsonb->'state_axes'->>'reservation_status' IS DISTINCT FROM
               public.rsc_material_request_reservation_state_0070(checked_request_id, f.request_version)
           OR (SELECT count(*) FROM public.audit_events a WHERE a.stream_key = 'material_request'
                AND a.action = f.action AND a.aggregate_type = f.aggregate_type AND a.aggregate_id = f.id::text
                AND a.actor_user_id = f.actor_user_id AND a.occurred_at = f.created_at
                AND a.after_jsonb->>'command_id' = command_row.id::text
                AND a.after_jsonb->>'request_hash' = command_row.request_hash
                AND a.after_jsonb->>'result_hash' = command_row.result_hash) <> 1
           OR (SELECT count(*) FROM public.state_transition_events s WHERE s.aggregate_type = 'material_request'
                AND s.aggregate_id = checked_request_id::text AND s.reason = f.action AND s.actor_id = f.actor_user_id
                AND s.metadata_jsonb->>'command_id' = command_row.id::text
                AND s.metadata_jsonb->>'request_version' = f.request_version::text
                AND s.to_status = command_row.result_jsonb->'state_axes'->>'reservation_status') <> 1
        THEN RAISE EXCEPTION '0070 reservation command graph invalid' USING ERRCODE = '23514'; END IF;
        IF f.operation = 'reserve' THEN
            SELECT count(*) INTO serial_count FROM public.stock_reservation_serials WHERE reservation_id = f.id;
            IF EXISTS (SELECT serial_id FROM public.stock_reservation_serials WHERE reservation_id = f.id
                       EXCEPT SELECT serial_id FROM public.inventory_movement_serials WHERE transaction_id = tx.id)
            THEN RAISE EXCEPTION '0070 reservation serial graph invalid' USING ERRCODE = '23514'; END IF;
        ELSE
            SELECT count(*) INTO serial_count FROM public.stock_reservation_release_serials WHERE release_id = f.id;
            IF EXISTS (SELECT serial_id FROM public.stock_reservation_release_serials WHERE release_id = f.id
                       EXCEPT SELECT serial_id FROM public.inventory_movement_serials WHERE transaction_id = tx.id)
            THEN RAISE EXCEPTION '0070 release serial graph invalid' USING ERRCODE = '23514'; END IF;
        END IF;
        IF serial_count <> (SELECT count(*) FROM public.inventory_movement_serials WHERE transaction_id = tx.id)
           OR (serial_count > 0 AND serial_count <> f.quantity)
        THEN RAISE EXCEPTION '0070 reservation serial count invalid' USING ERRCODE = '23514'; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM public.material_request_commands c WHERE c.request_id = checked_request_id
         AND c.operation IN ('reserve', 'release') AND NOT EXISTS (
             SELECT 1 FROM public.stock_reservations r WHERE c.operation = 'reserve' AND r.request_id = c.request_id AND r.request_version = c.target_version
             UNION ALL
             SELECT 1 FROM public.stock_reservation_releases r WHERE c.operation = 'release' AND r.request_id = c.request_id AND r.request_version = c.target_version))
       OR (EXISTS (SELECT 1 FROM public.stock_reservations WHERE request_id = checked_request_id)
           AND request_row.reservation_status IS DISTINCT FROM public.rsc_material_request_reservation_state_0070(checked_request_id, request_row.version))
    THEN RAISE EXCEPTION '0070 reservation projection invalid' USING ERRCODE = '23514'; END IF;
END
"""

DISPATCH_BODY = """
DECLARE request_id uuid; fact_id uuid;
BEGIN
    IF TG_TABLE_NAME IN ('stock_reservations', 'stock_reservation_releases', 'material_request_commands') THEN
        request_id := NEW.request_id;
    ELSIF TG_TABLE_NAME = 'material_requests' THEN request_id := NEW.id;
    ELSIF TG_TABLE_NAME = 'stock_reservation_serials' THEN
        SELECT r.request_id INTO request_id FROM public.stock_reservations r WHERE r.id = NEW.reservation_id;
    ELSIF TG_TABLE_NAME = 'stock_reservation_release_serials' THEN
        SELECT r.request_id INTO request_id FROM public.stock_reservation_releases r WHERE r.id = NEW.release_id;
    ELSIF TG_TABLE_NAME = 'inventory_transactions' THEN
        IF NEW.source_document_type = 'material_request_reservation' THEN
            SELECT r.request_id INTO request_id FROM public.stock_reservations r WHERE r.id::text = NEW.source_document_id;
        ELSIF NEW.source_document_type = 'material_request_reservation_release' THEN
            SELECT r.request_id INTO request_id FROM public.stock_reservation_releases r WHERE r.id::text = NEW.source_document_id;
        ELSE RETURN NEW; END IF;
        IF request_id IS NULL THEN RAISE EXCEPTION '0070 orphan reservation transaction' USING ERRCODE = '23514'; END IF;
    END IF;
    IF request_id IS NOT NULL THEN PERFORM public.rsc_validate_reservation_graph_0070(request_id); END IF;
    RETURN NEW;
END
"""

FUNCTIONS = {
    "rsc_material_request_reservation_state_0070": ("checked_request_id uuid, checked_version bigint", "text", STATE_BODY),
    "rsc_guard_reservation_release_immutable_0070": ("", "trigger", IMMUTABLE_BODY),
    "rsc_guard_reservation_release_binding_0070": ("", "trigger", RELEASE_BINDING_BODY),
    "rsc_validate_reservation_graph_0070": ("checked_request_id uuid", "void", GRAPH_BODY),
    "rsc_dispatch_reservation_graph_0070": ("", "trigger", DISPATCH_BODY),
}
GRAPH_TABLES = (*TABLES, "stock_reservations", "stock_reservation_serials", "material_requests", "material_request_commands", "inventory_transactions")


def _create_postgresql_guards():
    for name, (arguments, result, body) in FUNCTIONS.items():
        op.execute(f"CREATE FUNCTION public.{name}({arguments}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${body}$body$")
        signature = ", ".join(argument.split()[-1] for argument in arguments.split(", ")) if arguments else ""
        op.execute(f"ALTER FUNCTION public.{name}({signature}) OWNER TO star_oam_migrator")
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api")
    for table in TABLES:
        for suffix, event, granularity in (("immutable", "UPDATE OR DELETE", "ROW"), ("no_truncate", "TRUNCATE", "STATEMENT")):
            name = f"trg_{table}_{suffix}_0070"
            op.execute(f"CREATE TRIGGER {name} BEFORE {event} ON public.{table} FOR EACH {granularity} EXECUTE FUNCTION public.rsc_guard_reservation_release_immutable_0070()")
            op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
    name = "trg_stock_reservation_releases_binding_0070"
    op.execute(f"CREATE TRIGGER {name} BEFORE INSERT ON public.stock_reservation_releases FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_reservation_release_binding_0070()")
    op.execute(f"ALTER TABLE public.stock_reservation_releases ENABLE ALWAYS TRIGGER {name}")
    for table in GRAPH_TABLES:
        name = f"trg_{table}_reservation_graph_0070"
        op.execute(f"CREATE CONSTRAINT TRIGGER {name} AFTER INSERT OR UPDATE ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_dispatch_reservation_graph_0070()")
        op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")


def _drop_postgresql_guards():
    for table in GRAPH_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_reservation_graph_0070 ON public.{table}")
    op.execute("DROP TRIGGER trg_stock_reservation_releases_binding_0070 ON public.stock_reservation_releases")
    for table in TABLES:
        for suffix in ("immutable", "no_truncate"):
            op.execute(f"DROP TRIGGER trg_{table}_{suffix}_0070 ON public.{table}")
    for name, (arguments, _, _) in reversed(tuple(FUNCTIONS.items())):
        signature = ", ".join(argument.split()[-1] for argument in arguments.split(", ")) if arguments else ""
        op.execute(f"DROP FUNCTION public.{name}({signature})")


REQUEST_AXIS = """    IF NEW.reservation_status IS DISTINCT FROM OLD.reservation_status THEN
        IF OLD.reservation_status NOT IN ('not_reserved', 'pending', 'reserved', 'partially_released', 'released')
           OR NEW.reservation_status NOT IN ('pending', 'reserved', 'partially_released', 'released')
           OR NEW.version <> OLD.version + 1 OR NEW.updated_at <= OLD.updated_at
           OR NOT EXISTS (
               SELECT 1 FROM public.material_request_commands c
                WHERE c.request_id = NEW.id AND c.target_version = NEW.version
                  AND c.operation IN ('reserve', 'release')
                  AND c.result_jsonb->'state_axes'->>'reservation_status' = NEW.reservation_status
           ) THEN RAISE EXCEPTION 'formal material-request invariant violated'; END IF;
    END IF;"""


def source_changes():
    previous = _previous()
    axis = previous["REQUEST_GUARD_AXIS_NEW"]
    old_reservation = axis[axis.index("    IF NEW.reservation_status IS DISTINCT FROM OLD.reservation_status THEN"):]
    old_approval = previous["APPROVAL_PROJECTION_APPROVED_NEW"]
    old_supply = previous["SUPPLY_VALIDATE_STATE_AXES_NEW"]
    new_supply = old_supply.replace(
        "OR command_row.result_jsonb->'state_axes'->>'reservation_status' NOT IN\n               ('not_reserved', request_row.reservation_status)",
        "OR command_row.result_jsonb->'state_axes'->>'reservation_status' IS DISTINCT FROM\n               public.rsc_material_request_reservation_state_0070(checked_request_id, command_row.target_version)",
    )
    return {
        ("rsc_guard_material_request_identity_0029", ""): ((old_reservation, REQUEST_AXIS),),
        ("rsc_validate_material_request_approval_projection_0045", "uuid"): ((old_approval, old_approval.replace("'cancel_supply_task', 'allocate', 'reserve'", "'cancel_supply_task', 'allocate', 'reserve', 'release'")),),
        ("rsc_validate_material_request_supply_causality_0059", "uuid, bigint"): ((old_supply, new_supply),),
    }


# Populated from the reconstructed published function sources, then asserted
# against those same historical sources by test_database_security.
FUNCTION_HASHES = {
    ("rsc_guard_material_request_identity_0029", ""): (
        "b9f243b9f57c03cacb2f7f78ecda1308a250e625e141ee07629d065b18a928c1",
        "9f51943cbc18db67cd12070c9f95f950377d808389def9ab09bc0472fadac9ba",
    ),
    ("rsc_validate_material_request_approval_projection_0045", "uuid"): (
        "c4c7373e69992d651be7d7ad0d8fd88bdd73d241b4ff6fe824c37f9da0af42eb",
        "7f536d685eba41c23290864d39d4ef28ed135f497ca2bfdb268348f31f0f9c11",
    ),
    ("rsc_validate_material_request_supply_causality_0059", "uuid, bigint"): (
        "ffe14766ed4569ddfca748f5ec5d8c639b8b4edace208b668fd65412e2ef37dd",
        "41463ee6f0e4645fcbf056e69b0f7389f084c045e6379f9f82ab69b509415105",
    ),
}
RUNTIME_READY_BODY_SHA256_0070 = "5c9734c3b500a4b1794fef3a66fa5cdeb38f98e6d7e7130209cf6f6f87a07bfa"


def _replace_functions(*, upgrade):
    previous = _previous()
    for coordinate, replacements in source_changes().items():
        old_hash, new_hash = FUNCTION_HASHES[coordinate]
        previous["_replace_function_source"](
            signature=f"public.{coordinate[0]}({coordinate[1]})",
            expected_hash=old_hash if upgrade else new_hash,
            replacement_hash=new_hash if upgrade else old_hash,
            replacements=replacements if upgrade else tuple((new, old) for old, new in reversed(replacements)),
            label="release_0070",
        )
    previous["_replace_function_source"](
        signature=previous["RUNTIME_READY_SIGNATURE"],
        expected_hash=previous["RUNTIME_READY_BODY_SHA256_0069"] if upgrade else RUNTIME_READY_BODY_SHA256_0070,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0070 if upgrade else previous["RUNTIME_READY_BODY_SHA256_0069"],
        replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),),
        label="release_readiness_0070",
    )


def _sqlite_guards(*, upgrade):
    previous = _previous()
    old = previous["SQLITE_REQUEST_UPDATE_TRIGGER_0069"]
    marker = "  OR (NEW.reservation_status IS NOT OLD.reservation_status AND NOT ("
    replacement = """  OR (NEW.reservation_status IS NOT OLD.reservation_status AND NOT (
      OLD.reservation_status IN ('not_reserved', 'pending', 'reserved', 'partially_released', 'released')
      AND NEW.reservation_status IN ('pending', 'reserved', 'partially_released', 'released')
      AND NEW.version = OLD.version + 1 AND NEW.updated_at > OLD.updated_at
      AND EXISTS (SELECT 1 FROM material_request_commands c
          WHERE c.request_id = NEW.id AND c.target_version = NEW.version AND c.operation IN ('reserve', 'release')
            AND json_extract(c.result_jsonb, '$.state_axes.reservation_status') = NEW.reservation_status)
  ))
BEGIN
    SELECT RAISE(ABORT, 'formal material-request invariant violated');
END"""
    op.execute("DROP TRIGGER trg_material_requests_update_guard_0029")
    op.execute(old[:old.index(marker)] + replacement if upgrade else old)
    if upgrade:
        for table in TABLES:
            for event in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER trg_{table}_{event.lower()}_0070 BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT, '0070 reservation release facts are append-only'); END")


def upgrade():
    dialect = op.get_bind().dialect.name
    previous = _previous()
    if dialect == "postgresql":
        previous["_lock_postgresql_upgrade_boundary"]()
        previous["_verify_runtime_ready"](previous["RUNTIME_READY_BODY_SHA256_0069"])
    _create_tables()
    if dialect == "postgresql":
        _create_postgresql_guards()
        _replace_functions(upgrade=True)
        for table in TABLES:
            op.execute(f"ALTER TABLE public.{table} OWNER TO star_oam_migrator")
            op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC, star_oam_api")
            op.execute(f"GRANT SELECT, INSERT ON TABLE public.{table} TO star_oam_api")
        # Refuse to hide pre-existing orphan or reused reservation evidence.
        op.execute("SELECT public.rsc_validate_reservation_graph_0070(request_id) FROM public.stock_reservations GROUP BY request_id")
    elif dialect == "sqlite":
        _sqlite_guards(upgrade=True)
    else:
        raise RuntimeError("0070 supports only PostgreSQL and SQLite")


def downgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("LOCK TABLE public.material_requests, public.stock_reservations, public.stock_reservation_releases, public.stock_reservation_release_serials IN ACCESS EXCLUSIVE MODE")
    if not context.is_offline_mode() and any(
        op.get_bind().execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar() for table in TABLES
    ):
        raise RuntimeError(DOWNGRADE_BLOCKER)
    if dialect == "postgresql":
        _replace_functions(upgrade=False)
        _drop_postgresql_guards()
    else:
        _sqlite_guards(upgrade=False)
    op.drop_table(TABLES[1])
    op.drop_table(TABLES[0])
