"""Immutable stock-return request seals and late-command mutual exclusion."""
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa

revision = "20261011_0101"
down_revision = "20261010_0100"
branch_labels = depends_on = None
OLD_HASH = "ffe5a9a8416226cd3bb3a2ac4d8bfcd6d3ed0c176fa66ed29e2c86cc5e74ca85"
NEW_HASH = "61bff09975f03d0c8049253fc733692d6d3f5b4a633bb537e9418a96b582544a"
TABLE = "stock_operation_command_seals"

CHECK_BODY = """
DECLARE
    seal public.stock_operation_command_seals%ROWTYPE;
    observed_actor text;
    observed_request text;
    identifier uuid;
    body jsonb;
BEGIN
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0101 inventory ledger head missing' USING ERRCODE='23514'; END IF;
    IF TG_TABLE_NAME IN ('stock_operation_orders','stock_operation_cancellations') THEN
        observed_actor := NEW.actor_user_id; observed_request := NEW.request_id;
        IF EXISTS (SELECT 1 FROM public.stock_operation_command_seals tombstone
            WHERE tombstone.actor_user_id=observed_actor AND tombstone.request_id=observed_request) THEN
            RAISE EXCEPTION '0101 sealed return request cannot execute' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    ELSIF TG_TABLE_NAME='audit_events' THEN
        IF NEW.aggregate_type <> 'stock_operation_command_seal' THEN RETURN NULL; END IF;
        identifier := NEW.aggregate_id::uuid;
    ELSE identifier := NEW.id;
    END IF;
    SELECT * INTO seal FROM public.stock_operation_command_seals WHERE id=identifier;
    IF NOT FOUND THEN RAISE EXCEPTION '0101 seal audit is detached' USING ERRCODE='23514'; END IF;
    IF seal.operation_type NOT IN ('submit_return','cancel_return')
       OR (seal.operation_type='submit_return') IS DISTINCT FROM (seal.operation_id IS NULL)
       OR seal.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR seal.request_hash !~ '^[0-9a-f]{64}$'
       OR seal.request_reference <> 'inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(seal.request_id,'UTF8')),'hex')
       OR seal.authorization_version < 1 OR seal.created_at > clock_timestamp()
       OR NOT EXISTS (SELECT 1 FROM public.users actor WHERE actor.id=seal.actor_user_id
            AND actor.person_id=seal.operator_person_id AND actor.authorization_version=seal.authorization_version)
       OR NOT EXISTS (SELECT 1 FROM public.oam_work_orders wo WHERE wo.id=seal.oam_work_order_id)
       OR (seal.operation_type='cancel_return' AND NOT EXISTS (SELECT 1 FROM public.stock_operation_orders parent
            WHERE parent.id=seal.operation_id AND parent.oam_work_order_id=seal.oam_work_order_id
              AND parent.actor_user_id=seal.actor_user_id AND parent.requester_id=seal.operator_person_id)) THEN
        RAISE EXCEPTION '0101 seal identity or original coordinates invalid' USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM public.stock_operation_orders parent
            WHERE parent.actor_user_id=seal.actor_user_id AND parent.request_id=seal.request_id)
       OR EXISTS (SELECT 1 FROM public.stock_operation_cancellations cancellation
            WHERE cancellation.actor_user_id=seal.actor_user_id AND cancellation.request_id=seal.request_id)
       OR EXISTS (SELECT 1 FROM public.audit_events event WHERE event.actor_user_id=seal.actor_user_id
            AND event.stream_key='material_request' AND event.request_id=seal.request_id
            AND event.aggregate_type IN ('stock_operation_order','stock_operation_cancellation'))
       OR EXISTS (SELECT 1 FROM public.audit_events event JOIN public.inventory_transactions tx ON tx.id::text=event.aggregate_id
            WHERE event.stream_key='inventory' AND event.actor_user_id=seal.actor_user_id AND event.request_id=seal.request_reference
              AND event.aggregate_type='inventory_transaction' AND tx.source_document_type='stock_operation_return')
       OR EXISTS (SELECT 1 FROM public.state_transition_events event JOIN public.inventory_transactions tx ON tx.id::text=event.aggregate_id
            WHERE event.actor_id=seal.actor_user_id AND event.aggregate_type='inventory_transaction'
              AND event.metadata_jsonb->>'request_reference'=seal.request_reference AND tx.source_document_type='stock_operation_return') THEN
        RAISE EXCEPTION '0101 executed return request cannot be sealed' USING ERRCODE='23514';
    END IF;
    body := jsonb_build_object('work_order_id',seal.oam_work_order_id::text,'operator_person_id',seal.operator_person_id::text,
        'operation_id',seal.operation_id::text,'operation_type',seal.operation_type,'authorization_version',seal.authorization_version,
        'request_id',seal.request_id,'request_hash',seal.request_hash);
    IF (SELECT count(*) FROM public.audit_events event WHERE event.stream_key='material_request'
            AND event.aggregate_type='stock_operation_command_seal' AND event.aggregate_id=seal.id::text) <> 1
       OR NOT EXISTS (SELECT 1 FROM public.audit_events event WHERE event.stream_key='material_request'
            AND event.aggregate_type='stock_operation_command_seal' AND event.aggregate_id=seal.id::text
            AND event.actor_user_id=seal.actor_user_id AND event.action='stock_return.command_sealed'
            AND event.request_id='stock-return-seal:' || seal.id::text AND event.before_jsonb='{}'::jsonb
            AND event.after_jsonb=body AND event.occurred_at=seal.created_at AND event.created_at=seal.created_at) THEN
        RAISE EXCEPTION '0101 complete seal audit required' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
"""

FUNCTIONS = {("rsc_guard_stock_operation_seal_0101", ""): ("", "trigger", CHECK_BODY)}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key, value in FUNCTIONS.items()}
TRIGGERS = {
    "trg_stock_operation_seals_proof_0101": (TABLE, "INSERT", "rsc_guard_stock_operation_seal_0101", 5, True),
    "trg_stock_operation_seals_immutable_0101": (TABLE, "UPDATE OR DELETE", "rsc_guard_work_order_facts_0090", 27, False),
    "trg_stock_operation_seals_no_truncate_0101": (TABLE, "TRUNCATE", "rsc_guard_work_order_facts_0090", 34, False),
    "trg_stock_operation_orders_seal_0101": ("stock_operation_orders", "INSERT", "rsc_guard_stock_operation_seal_0101", 5, True),
    "trg_stock_operation_cancellations_seal_0101": ("stock_operation_cancellations", "INSERT", "rsc_guard_stock_operation_seal_0101", 5, True),
    "trg_audit_events_stock_operation_seal_0101": ("audit_events", "INSERT", "rsc_guard_stock_operation_seal_0101", 5, True),
}


def _create_table():
    op.create_table(TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operator_person_id", sa.Uuid(), sa.ForeignKey("people.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("oam_work_order_id", sa.Uuid(), sa.ForeignKey("oam_work_orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("stock_operation_orders.id", ondelete="RESTRICT")),
        sa.Column("operation_type", sa.String(24), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(160), nullable=False),
        sa.Column("request_reference", sa.String(100), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_user_id", "request_id", name="uq_stock_operation_seals_request"),
        sa.CheckConstraint("operation_type IN ('submit_return','cancel_return')", name="ck_stock_operation_seals_type"),
        sa.CheckConstraint("(operation_type='submit_return' AND operation_id IS NULL) OR (operation_type='cancel_return' AND operation_id IS NOT NULL)", name="ck_stock_operation_seals_origin"),
        sa.CheckConstraint("authorization_version > 0 AND length(request_hash)=64", name="ck_stock_operation_seals_context"))


def _transition(upgrade):
    db = op.get_bind()
    if db.dialect.name not in {"postgresql", "sqlite"}: raise RuntimeError("0101 supports PostgreSQL and SQLite only")
    folder = Path(__file__).parent
    helper = runpy.run_path(str(folder / "20260927_0087_inbound_fulfillment_boundary.py"))
    helper["_begin_sqlite"]()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE public.inventory_ledger_heads, public.inventory_transactions, public.stock_operation_orders, public.stock_operation_cancellations, public.audit_events, public.state_transition_events IN SHARE ROW EXCLUSIVE MODE")
    if not upgrade:
        if db.dialect.name == "postgresql": op.execute("LOCK TABLE public.stock_operation_command_seals IN SHARE ROW EXCLUSIVE MODE")
        helper["_preflight"]("EXISTS (SELECT 1 FROM stock_operation_command_seals)", "0101 downgrade blocked: immutable return request seals must be retained")
    if upgrade: _create_table()
    if db.dialect.name == "postgresql":
        replace = runpy.run_path(str(folder / "20260912_0072_outbound_postings.py"))["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
        if upgrade:
            for (name, signature), (args, result, body) in FUNCTIONS.items():
                op.execute(f"CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${body}$body$")
                op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api")
            for name, (table, events, function, _, deferred) in TRIGGERS.items():
                prefix = (f"CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW" if deferred
                    else f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events == 'TRUNCATE' else 'ROW'}")
                op.execute(prefix + f" EXECUTE FUNCTION public.{function}()")
                op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
            op.execute("GRANT SELECT, INSERT ON public.stock_operation_command_seals TO star_oam_api")
        else:
            for (name, signature), digest in FUNCTION_HASHES.items():
                op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p
                    WHERE p.oid='public.{name}({signature})'::regprocedure
                      AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{digest}'
                      AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND p.prosecdef
                      AND p.proconfig=ARRAY['search_path=pg_catalog, public']
                      AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl WHERE acl.grantee<>p.proowner))
                    THEN RAISE EXCEPTION '0101 function source, ownership or ACL drift'; END IF; END $body$""")
            for name, (table, *_) in TRIGGERS.items(): op.execute(f"DROP TRIGGER {name} ON public.{table}")
            for name, signature in FUNCTIONS: op.execute(f"DROP FUNCTION public.{name}({signature})")
        replace(signature="public.rsc_oam_runtime_binding_ready_0044()", expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH, replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),
            label="stock_return_seal_readiness_0101")
    if db.dialect.name == "sqlite" and upgrade:
        for event in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER trg_stock_operation_seals_{event.lower()}_0101 BEFORE {event} ON {TABLE} BEGIN SELECT RAISE(ABORT, '0101 return request seals are append-only'); END")
    if not upgrade: op.drop_table(TABLE)


def upgrade(): _transition(True)
def downgrade(): _transition(False)
