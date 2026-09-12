"""Exclude sealed parent replacements without weakening ordinary seal proofs."""
import hashlib
from pathlib import Path
import runpy

from alembic import op

revision = "20261005_0095"
down_revision = "20261004_0094"
branch_labels = depends_on = None
OLD_HASH = "afc5526e1799f4b6a3b956524fcfaa84e94842166226864d54c1e94ae110961a"
NEW_HASH = "f5a10152b6e89e2001c6aec103343b54b4002110b5203e843045676875d89268"
OLD_CHECK = "operation_type IN ('occupy', 'consume', 'release')"
NEW_CHECK = "operation_type IN ('occupy', 'consume', 'release', 'replace')"

CHECK_BODY = """
DECLARE
    seal public.work_order_command_seals%ROWTYPE;
    replacement public.work_order_replacements%ROWTYPE;
BEGIN
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key = 'inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0095 inventory ledger head missing' USING ERRCODE = '23514'; END IF;
    IF TG_TABLE_NAME = 'work_order_command_seals' THEN
        SELECT * INTO STRICT seal FROM public.work_order_command_seals WHERE id = NEW.id;
        IF seal.operation_type <> 'replace' THEN RETURN NULL; END IF;
        -- The unchanged 0094 trigger still requires exact current identity,
        -- request reference, time, and the complete immutable seal audit.
        IF EXISTS (SELECT 1 FROM public.work_order_replacements parent_fact
            WHERE parent_fact.operator_person_id = seal.operator_person_id
              AND parent_fact.oam_work_order_id = seal.oam_work_order_id
              AND parent_fact.request_id = seal.request_id) THEN
            RAISE EXCEPTION '0095 executed replacement cannot be sealed' USING ERRCODE = '23514';
        END IF;
    ELSE
        SELECT * INTO STRICT replacement FROM public.work_order_replacements WHERE id = NEW.id;
        IF EXISTS (SELECT 1 FROM public.work_order_command_seals original_seal
            WHERE original_seal.operation_type = 'replace'
              AND original_seal.operator_person_id = replacement.operator_person_id
              AND original_seal.oam_work_order_id = replacement.oam_work_order_id
              AND original_seal.request_id = replacement.request_id) THEN
            RAISE EXCEPTION '0095 sealed replacement cannot execute' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NULL;
END;
"""
FUNCTIONS = {("rsc_guard_work_order_replacement_seal_0095", ""): ("", "trigger", CHECK_BODY)}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key, value in FUNCTIONS.items()}
TRIGGERS = {
    "trg_work_order_seals_replacement_0095": ("work_order_command_seals", "INSERT", "rsc_guard_work_order_replacement_seal_0095", 5, True),
    "trg_work_order_replacements_seal_0095": ("work_order_replacements", "INSERT", "rsc_guard_work_order_replacement_seal_0095", 5, True),
}


def _constraint(upgrade):
    check = NEW_CHECK if upgrade else OLD_CHECK
    if op.get_bind().dialect.name == "sqlite":
        # SQLite rebuilds the table to change its CHECK. Reinstall the existing
        # immutable guards inside the same migration transaction, preserving rows.
        for event in ("update", "delete"):
            op.execute(f"DROP TRIGGER trg_work_order_seals_{event}_0094")
        with op.batch_alter_table("work_order_command_seals") as batch:
            batch.drop_constraint("ck_work_order_command_seals_operation", type_="check")
            batch.create_check_constraint("ck_work_order_command_seals_operation", check)
        for event in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER trg_work_order_seals_{event.lower()}_0094 BEFORE {event} ON work_order_command_seals BEGIN SELECT RAISE(ABORT, '0094 command seals are append-only'); END")
    else:
        op.execute("ALTER TABLE public.work_order_command_seals DROP CONSTRAINT ck_work_order_command_seals_operation")
        op.execute(f"ALTER TABLE public.work_order_command_seals ADD CONSTRAINT ck_work_order_command_seals_operation CHECK ({check})")


def _transition(upgrade):
    db = op.get_bind()
    if db.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError("0095 supports PostgreSQL and SQLite only")
    folder = Path(__file__).parent
    previous = runpy.run_path(str(folder / "20260927_0087_inbound_fulfillment_boundary.py"))
    previous["_begin_sqlite"]()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE public.inventory_ledger_heads, public.inventory_transactions, public.work_order_material_operations, public.work_order_replacements, public.work_order_command_seals, public.audit_events, public.state_transition_events IN SHARE ROW EXCLUSIVE MODE")
    if not upgrade:
        previous["_preflight"]("EXISTS (SELECT 1 FROM work_order_command_seals WHERE operation_type = 'replace')",
            "0095 downgrade blocked: replacement command seals must be retained")
    if db.dialect.name == "postgresql":
        helper = runpy.run_path(str(folder / "20260912_0072_outbound_postings.py"))
        replace = helper["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
        if upgrade:
            for (name, signature), (args, result, body) in FUNCTIONS.items():
                op.execute(f"CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${body}$body$")
                op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api")
            for name, (table, events, function, _, _) in TRIGGERS.items():
                op.execute(f"CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.{function}()")
                op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
        else:
            for (name, signature), digest in FUNCTION_HASHES.items():
                op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p
                    WHERE p.oid = 'public.{name}({signature})'::regprocedure
                      AND encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') = '{digest}'
                      AND p.proowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
                      AND p.prosecdef AND p.proconfig = ARRAY['search_path=pg_catalog, public']
                      AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl WHERE acl.grantee <> p.proowner))
                    THEN RAISE EXCEPTION '0095 function source, configuration or ownership drift'; END IF; END $body$""")
            for name, (table, *_) in TRIGGERS.items():
                op.execute(f"DROP TRIGGER {name} ON public.{table}")
            for name, signature in FUNCTIONS:
                op.execute(f"DROP FUNCTION public.{name}({signature})")
        replace(signature="public.rsc_oam_runtime_binding_ready_0044()", expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,
            replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),), label="work_order_replacement_seal_readiness_0095")
    _constraint(upgrade)


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
