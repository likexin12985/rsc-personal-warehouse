"""Receipt-bound inbound request seals; preserve the older acceptance seals.

No historical seal is inferred or moved. The new fact and its audit are
append-only. Populated seals prevent downgrade. Cross-command request reuse
and late inventory evidence are checked at PostgreSQL commit under the ledger
lock; only READ COMMITTED is supported for this commit fence.
"""
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa

revision = "20261020_0110"
down_revision = "20261019_0109"
branch_labels = depends_on = None
OLD_HASH = "8422e51272e7384f2c464f6f37292e846f6c0dd12a6584c5f9624700571ead52"
NEW_HASH = "2e5527dd8ffbfc5c799fa215bcdb5de1d078d62ec7b6ce6f7d621ef40034b935"
TABLE = "stock_operation_return_inbound_seals"
REQUEST_TABLES = ("stock_operation_orders", "stock_operation_cancellations", "stock_operation_outbounds",
    "stock_operation_shipments", "stock_operation_receipts", "stock_operation_command_seals",
    "stock_operation_return_inbounds", TABLE)
FUNCTION_NAME = "rsc_guard_return_inbound_seal_0110"

CHECK_BODY = """
DECLARE
    seal public.stock_operation_return_inbound_seals%ROWTYPE;
    acceptance public.stock_operation_receipts%ROWTYPE;
    observed_actor text;
    observed_request text;
    observed_reference text;
    identifier uuid;
    body jsonb;
    claims bigint;
BEGIN
    IF TG_TABLE_NAME='audit_events' THEN
        IF NEW.aggregate_type='stock_operation_return_inbound_seal' THEN
            identifier := NEW.aggregate_id::uuid;
            SELECT * INTO seal FROM public.stock_operation_return_inbound_seals WHERE id=identifier;
            IF NOT FOUND THEN RAISE EXCEPTION '0110 detached inbound seal audit' USING ERRCODE='23514'; END IF;
            observed_actor:=seal.actor_user_id; observed_request:=seal.request_id;
        ELSIF NEW.stream_key='inventory' THEN
            observed_actor:=NEW.actor_user_id; observed_reference:=NEW.request_id;
        ELSIF NEW.stream_key='material_request' THEN
            observed_actor:=NEW.actor_user_id; observed_request:=NEW.request_id;
        ELSE RETURN NULL;
        END IF;
    ELSIF TG_TABLE_NAME='state_transition_events' THEN
        IF NEW.aggregate_type<>'inventory_transaction' THEN RETURN NULL; END IF;
        observed_actor:=NEW.actor_id; observed_reference:=NEW.metadata_jsonb->>'request_reference';
    ELSE
        observed_actor:=NEW.actor_user_id; observed_request:=NEW.request_id;
    END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION '0110 request fence requires read committed' USING ERRCODE='23514';
    END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0110 inventory ledger head missing' USING ERRCODE='23514'; END IF;
    IF observed_request IS NOT NULL THEN
        SELECT count(*) INTO claims FROM (
            SELECT actor_user_id,request_id FROM public.stock_operation_orders
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_cancellations
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_outbounds
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_shipments
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_receipts
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_command_seals
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_return_inbounds
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_return_inbound_seals
        ) fact WHERE actor_user_id=observed_actor AND request_id=observed_request;
        IF claims>1 THEN RAISE EXCEPTION '0110 return request namespace conflict' USING ERRCODE='23514'; END IF;
    END IF;
    FOR seal IN SELECT * FROM public.stock_operation_return_inbound_seals
        WHERE actor_user_id=observed_actor AND
            (request_id=observed_request OR request_reference=observed_reference)
    LOOP
        SELECT * INTO acceptance FROM public.stock_operation_receipts WHERE id=seal.receipt_id;
        IF NOT FOUND OR acceptance.shipment_id<>seal.shipment_id
           OR acceptance.actor_user_id<>seal.actor_user_id OR acceptance.operator_person_id<>seal.operator_person_id
           OR seal.created_at<acceptance.created_at OR seal.created_at>clock_timestamp()
           OR seal.authorization_version<1 OR seal.request_id !~ '^[A-Za-z0-9._:-]{8,160}$'
           OR seal.request_hash !~ '^[a-f0-9]{64}$'
           OR seal.request_reference<>'inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(seal.request_id,'UTF8')),'hex') THEN
            RAISE EXCEPTION '0110 inbound seal exact acceptance mismatch' USING ERRCODE='23514';
        END IF;
        PERFORM public.rsc_check_return_receiver_0105(seal.shipment_id,seal.actor_user_id,
            seal.operator_person_id,seal.authorization_version,seal.created_at);
        IF EXISTS (SELECT 1 FROM public.audit_events e WHERE e.actor_user_id=seal.actor_user_id AND
                ((e.stream_key='inventory' AND e.request_id=seal.request_reference) OR
                 (e.stream_key='material_request' AND e.request_id=seal.request_id)))
           OR EXISTS (SELECT 1 FROM public.state_transition_events e WHERE e.aggregate_type='inventory_transaction'
                AND e.actor_id=seal.actor_user_id AND e.metadata_jsonb->>'request_reference'=seal.request_reference) THEN
            RAISE EXCEPTION '0110 sealed inbound request has execution evidence' USING ERRCODE='23514';
        END IF;
        body:=jsonb_build_object('receipt_id',seal.receipt_id::text,'shipment_id',seal.shipment_id::text,
            'operator_person_id',seal.operator_person_id::text,'authorization_version',seal.authorization_version,
            'request_id',seal.request_id,'request_hash',seal.request_hash);
        IF (SELECT count(*) FROM public.audit_events e WHERE e.stream_key='material_request'
                AND e.aggregate_type='stock_operation_return_inbound_seal' AND e.aggregate_id=seal.id::text)<>1
           OR NOT EXISTS (SELECT 1 FROM public.audit_events e WHERE e.stream_key='material_request'
                AND e.aggregate_type='stock_operation_return_inbound_seal' AND e.aggregate_id=seal.id::text
                AND e.actor_user_id=seal.actor_user_id AND e.action='stock_return_inbound.command_sealed'
                AND e.request_id='stock-return-inbound-seal:' || seal.id::text
                AND e.before_jsonb='{}'::jsonb AND e.after_jsonb=body
                AND e.occurred_at=seal.created_at AND e.created_at=seal.created_at) THEN
            RAISE EXCEPTION '0110 complete inbound seal audit required' USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NULL;
END;
"""
FUNCTION_HASH = hashlib.sha256(CHECK_BODY.encode()).hexdigest()
TRIGGERS = {f"trg_{table}_request_0110": (table, "INSERT", FUNCTION_NAME, 5, True)
    for table in (*REQUEST_TABLES, "audit_events", "state_transition_events")}
TRIGGERS.update({
    "trg_return_inbound_seals_immutable_0110": (TABLE, "UPDATE OR DELETE", "rsc_guard_work_order_facts_0090", 27, False),
    "trg_return_inbound_seals_no_truncate_0110": (TABLE, "TRUNCATE", "rsc_guard_work_order_facts_0090", 34, False),
})


def _create_table():
    op.create_table(TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey("stock_operation_receipts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), sa.ForeignKey("stock_operation_shipments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operator_person_id", sa.Uuid(), sa.ForeignKey("people.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(160), nullable=False),
        sa.Column("request_reference", sa.String(100), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_user_id", "request_id", name="uq_return_inbound_seals_request"),
        sa.CheckConstraint("authorization_version > 0 AND length(request_hash)=64", name="ck_return_inbound_seals_context"))
    op.create_index("ix_return_inbound_seals_receipt", TABLE, ["receipt_id"])


def _transition(upgrade):
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}: raise RuntimeError("0110 supports PostgreSQL and SQLite only")
    folder = Path(__file__).parent
    helper = runpy.run_path(str(folder / "20260927_0087_inbound_fulfillment_boundary.py"))
    helper["_begin_sqlite"]()
    if dialect == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        tables = (*REQUEST_TABLES[:-1], "inventory_ledger_heads", "audit_events", "state_transition_events")
        op.execute("LOCK TABLE " + ", ".join("public." + name for name in tables) + " IN SHARE ROW EXCLUSIVE MODE")
    if upgrade:
        # Reject pre-existing ambiguous inbound coordinates without rewriting
        # either fact. Older acceptance seals keep their original semantics.
        collisions = " OR ".join(f"EXISTS (SELECT 1 FROM stock_operation_return_inbounds i JOIN {name} f ON f.actor_user_id=i.actor_user_id AND f.request_id=i.request_id)" for name in REQUEST_TABLES[:-2])
        helper["_preflight"](collisions, "0110 pre-existing inbound request conflict requires investigation")
        _create_table()
    else:
        if dialect == "postgresql": op.execute(f"LOCK TABLE public.{TABLE} IN SHARE ROW EXCLUSIVE MODE")
        helper["_preflight"](f"EXISTS (SELECT 1 FROM {TABLE})", "0110 downgrade blocked: inbound request seals must be retained")
    if dialect == "postgresql":
        if upgrade:
            op.execute(f"CREATE FUNCTION public.{FUNCTION_NAME}() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${CHECK_BODY}$body$")
            op.execute(f"REVOKE ALL ON FUNCTION public.{FUNCTION_NAME}() FROM PUBLIC, star_oam_api")
            for name, (table, events, function, _, deferred) in TRIGGERS.items():
                prefix = (f"CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW" if deferred
                    else f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events == 'TRUNCATE' else 'ROW'}")
                op.execute(prefix + f" EXECUTE FUNCTION public.{function}()")
                op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
            op.execute(f"REVOKE ALL ON TABLE public.{TABLE} FROM PUBLIC")
            op.execute(f"GRANT SELECT, INSERT ON TABLE public.{TABLE} TO star_oam_api")
        else:
            op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p
                WHERE p.oid='public.{FUNCTION_NAME}()'::regprocedure
                  AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{FUNCTION_HASH}'
                  AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND p.prosecdef
                  AND p.proconfig=ARRAY['search_path=pg_catalog, public']
                  AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl WHERE acl.grantee<>p.proowner))
                THEN RAISE EXCEPTION '0110 function source, ownership or ACL drift'; END IF; END $body$""")
            for name, (table, *_) in TRIGGERS.items(): op.execute(f"DROP TRIGGER {name} ON public.{table}")
            op.execute(f"DROP FUNCTION public.{FUNCTION_NAME}()")
        replace = runpy.run_path(str(folder / "20260912_0072_outbound_postings.py"))["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
        replace(signature="public.rsc_oam_runtime_binding_ready_0044()", expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,
            replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),), label="return_inbound_seal_readiness_0110")
    if dialect == "sqlite":
        # SQLite proves local immutability and request uniqueness, not the
        # PostgreSQL deferred audit proof or concurrent commit fence.
        if upgrade:
            for event in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER trg_return_inbound_seals_{event.lower()}_0110 BEFORE {event} ON {TABLE} BEGIN SELECT RAISE(ABORT, '0110 inbound seals are append-only'); END")
            for table in REQUEST_TABLES:
                conflicts = " OR ".join(f"EXISTS (SELECT 1 FROM {name} WHERE actor_user_id=NEW.actor_user_id AND request_id=NEW.request_id)" for name in REQUEST_TABLES if name != table)
                op.execute(f"CREATE TRIGGER trg_{table}_request_0110 BEFORE INSERT ON {table} WHEN {conflicts} BEGIN SELECT RAISE(ABORT, '0110 return request namespace conflict'); END")
        else:
            for table in REQUEST_TABLES: op.execute(f"DROP TRIGGER trg_{table}_request_0110")
    if not upgrade: op.drop_table(TABLE)


def upgrade(): _transition(True)
def downgrade(): _transition(False)
