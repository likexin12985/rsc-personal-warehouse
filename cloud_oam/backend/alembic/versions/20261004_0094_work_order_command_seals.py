"""Permanently exclude sealed ordinary work-order requests from stock posting."""
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa

revision = "20261004_0094"
down_revision = "20261003_0093"
branch_labels = depends_on = None
OLD_HASH = "f11f1e298ab43adf66e578f808b061d1e5ae905191ce5dccb59a61fdf172b465"
NEW_HASH = "afc5526e1799f4b6a3b956524fcfaa84e94842166226864d54c1e94ae110961a"

CHECK_BODY = """
DECLARE
    seal public.work_order_command_seals%ROWTYPE;
    operation public.work_order_material_operations%ROWTYPE;
    tx public.inventory_transactions%ROWTYPE;
BEGIN
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key = 'inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0094 inventory ledger head missing' USING ERRCODE = '23514'; END IF;
    IF TG_TABLE_NAME = 'work_order_command_seals' THEN
        SELECT * INTO STRICT seal FROM public.work_order_command_seals WHERE id = NEW.id;
        IF seal.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR seal.request_hash !~ '^[0-9a-f]{64}$'
           OR seal.request_reference <> 'inventory-request-' || encode(sha256(
                convert_to('cloud_oam.inventory.request.v1', 'UTF8') || decode('00', 'hex') || convert_to(seal.request_id, 'UTF8')), 'hex')
           OR seal.created_at <> seal.sealed_at
           OR NOT EXISTS (SELECT 1 FROM public.users WHERE id = seal.actor_user_id
                AND person_id = seal.operator_person_id AND account_status = 'active'
                AND authorization_version = seal.authorization_version)
           OR (SELECT count(*) FROM public.audit_events WHERE stream_key = 'material_request'
                AND actor_user_id = seal.actor_user_id AND aggregate_type = 'work_order_command_seal'
                AND aggregate_id = seal.id::text AND action = 'work_order_material.command_sealed'
                AND request_id = 'work-order-seal:' || seal.id::text AND occurred_at = seal.sealed_at
                AND before_jsonb = '{}'::jsonb AND after_jsonb = jsonb_build_object(
                    'work_order_id', seal.oam_work_order_id::text, 'operator_person_id', seal.operator_person_id::text,
                    'operation_type', seal.operation_type, 'request_id', seal.request_id,
                    'request_hash', seal.request_hash, 'authorization_version', seal.authorization_version)) <> 1 THEN
            RAISE EXCEPTION '0094 seal identity or audit proof invalid' USING ERRCODE = '23514';
        END IF;
        IF EXISTS (SELECT 1 FROM public.work_order_material_operations operation_fact
            JOIN public.inventory_transactions transaction_fact ON transaction_fact.id = operation_fact.posting_transaction_id
            WHERE operation_fact.replacement_id IS NULL AND operation_fact.oam_work_order_id = seal.oam_work_order_id
              AND operation_fact.operation_type = seal.operation_type AND transaction_fact.actor_user_id = seal.actor_user_id
              AND (EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key = 'inventory'
                    AND aggregate_type = 'inventory_transaction' AND aggregate_id = transaction_fact.id::text
                    AND request_id = seal.request_reference)
                OR EXISTS (SELECT 1 FROM public.state_transition_events WHERE aggregate_type = 'inventory_transaction'
                    AND aggregate_id = transaction_fact.id::text AND metadata_jsonb->>'request_reference' = seal.request_reference))) THEN
            RAISE EXCEPTION '0094 executed request cannot be sealed' USING ERRCODE = '23514';
        END IF;
    ELSE
        SELECT * INTO STRICT operation FROM public.work_order_material_operations WHERE id = NEW.id;
        IF operation.replacement_id IS NOT NULL OR operation.operation_type NOT IN ('occupy', 'consume', 'release') THEN RETURN NULL; END IF;
        SELECT * INTO STRICT tx FROM public.inventory_transactions WHERE id = operation.posting_transaction_id;
        IF EXISTS (SELECT 1 FROM public.work_order_command_seals existing_seal
            WHERE existing_seal.actor_user_id = tx.actor_user_id AND existing_seal.oam_work_order_id = operation.oam_work_order_id
              AND existing_seal.operation_type = operation.operation_type
              AND (EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key = 'inventory'
                    AND aggregate_type = 'inventory_transaction' AND aggregate_id = tx.id::text
                    AND request_id = existing_seal.request_reference)
                OR EXISTS (SELECT 1 FROM public.state_transition_events WHERE aggregate_type = 'inventory_transaction'
                    AND aggregate_id = tx.id::text AND metadata_jsonb->>'request_reference' = existing_seal.request_reference))) THEN
            RAISE EXCEPTION '0094 sealed request cannot execute' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NULL;
END;
"""
FUNCTIONS = {("rsc_guard_work_order_command_seal_0094", ""): ("", "trigger", CHECK_BODY)}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key, value in FUNCTIONS.items()}
TRIGGERS = {
    "trg_work_order_seals_proof_0094": ("work_order_command_seals", "INSERT", "rsc_guard_work_order_command_seal_0094", 5, True),
    "trg_work_order_operations_seal_0094": ("work_order_material_operations", "INSERT", "rsc_guard_work_order_command_seal_0094", 5, True),
    "trg_work_order_seals_immutable_0094": ("work_order_command_seals", "UPDATE OR DELETE", "rsc_guard_work_order_facts_0090", 27, False),
    "trg_work_order_seals_no_truncate_0094": ("work_order_command_seals", "TRUNCATE", "rsc_guard_work_order_facts_0090", 34, False),
}


def _create_table():
    op.create_table("work_order_command_seals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("oam_work_order_id", sa.Uuid(), sa.ForeignKey("oam_work_orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operator_person_id", sa.Uuid(), sa.ForeignKey("people.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("authorization_version", sa.Integer(), nullable=False),
        sa.Column("operation_type", sa.String(20), nullable=False),
        sa.Column("request_id", sa.String(160), nullable=False),
        sa.Column("request_reference", sa.String(82), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_user_id", "oam_work_order_id", "operation_type", "request_id", name="uq_work_order_command_seals_request"),
        sa.CheckConstraint("operation_type IN ('occupy', 'consume', 'release')", name="ck_work_order_command_seals_operation"),
        sa.CheckConstraint("authorization_version > 0", name="ck_work_order_command_seals_version"),
    )
    if op.get_bind().dialect.name == "sqlite":
        for event in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER trg_work_order_seals_{event.lower()}_0094 BEFORE {event} ON work_order_command_seals BEGIN SELECT RAISE(ABORT, '0094 command seals are append-only'); END")


def _transition(upgrade):
    db = op.get_bind()
    if db.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError("0094 supports PostgreSQL and SQLite only")
    previous = runpy.run_path(str(Path(__file__).with_name("20260927_0087_inbound_fulfillment_boundary.py")))
    previous["_begin_sqlite"]()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE public.inventory_ledger_heads, public.inventory_transactions, public.work_order_material_operations, public.audit_events, public.state_transition_events IN SHARE ROW EXCLUSIVE MODE")
    if not upgrade:
        if db.dialect.name == "postgresql":
            op.execute("LOCK TABLE public.work_order_command_seals IN SHARE ROW EXCLUSIVE MODE")
        previous["_preflight"]("EXISTS (SELECT 1 FROM work_order_command_seals)", "0094 downgrade blocked: command seals must be retained")
    if upgrade:
        _create_table()
    if db.dialect.name == "postgresql":
        helper = runpy.run_path(str(Path(__file__).with_name("20260912_0072_outbound_postings.py")))
        replace = helper["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
        if upgrade:
            for (name, signature), (args, result, body) in FUNCTIONS.items():
                op.execute(f"CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${body}$body$")
                op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api")
            for name, (table, events, function, _, deferred) in TRIGGERS.items():
                if deferred:
                    sql = f"CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW"
                else:
                    sql = f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events == 'TRUNCATE' else 'ROW'}"
                op.execute(sql + f" EXECUTE FUNCTION public.{function}()")
                op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
            op.execute("GRANT SELECT, INSERT ON TABLE public.work_order_command_seals TO star_oam_api")
        else:
            for (name, signature), digest in FUNCTION_HASHES.items():
                op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p
                    WHERE p.oid = 'public.{name}({signature})'::regprocedure
                      AND encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') = '{digest}'
                      AND p.proowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
                      AND p.prosecdef AND p.proconfig = ARRAY['search_path=pg_catalog, public']
                      AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl WHERE acl.grantee <> p.proowner))
                    THEN RAISE EXCEPTION '0094 function source, configuration or ownership drift'; END IF; END $body$""")
            for name, (table, *_) in TRIGGERS.items():
                op.execute(f"DROP TRIGGER {name} ON public.{table}")
            for name, signature in FUNCTIONS:
                op.execute(f"DROP FUNCTION public.{name}({signature})")
        replace(signature="public.rsc_oam_runtime_binding_ready_0044()", expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,
            replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),), label="work_order_command_seal_readiness_0094")
    if not upgrade:
        op.drop_table("work_order_command_seals")


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
