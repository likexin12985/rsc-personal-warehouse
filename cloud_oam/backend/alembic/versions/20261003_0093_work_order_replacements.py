"""Bind replacement consumption, removed-stock recovery and serial pairs."""
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261003_0093"
down_revision = "20261002_0092"
branch_labels = depends_on = None
OLD_HASH = "f431511b69cc669fefac0ee0c92fd8507a730123cff94fba3969d4d1003382cd"
NEW_HASH = "f11f1e298ab43adf66e578f808b061d1e5ae905191ce5dccb59a61fdf172b465"

CHECK_BODY = """
DECLARE
    replacement public.work_order_replacements%ROWTYPE;
    consumed public.work_order_material_operations%ROWTYPE;
    recovered public.work_order_material_operations%ROWTYPE;
    consume_tx public.inventory_transactions%ROWTYPE;
    recover_tx public.inventory_transactions%ROWTYPE;
    consumed_command jsonb;
    recovered_command jsonb;
    expected_recovery jsonb := '[]'::jsonb;
    expected_pairs jsonb;
    expected_command jsonb;
    intent record;
    source public.stock_accounts%ROWTYPE;
    target public.stock_accounts%ROWTYPE;
BEGIN
    SELECT * INTO replacement FROM public.work_order_replacements WHERE id = checked_replacement;
    IF NOT FOUND THEN RAISE EXCEPTION '0093 replacement command missing' USING ERRCODE = '23514'; END IF;
    SELECT * INTO consumed FROM public.work_order_material_operations WHERE id = replacement.consume_operation_id;
    IF NOT FOUND THEN RAISE EXCEPTION '0093 consumption fact missing' USING ERRCODE = '23514'; END IF;
    SELECT * INTO recovered FROM public.work_order_material_operations WHERE id = replacement.recover_operation_id;
    IF NOT FOUND THEN RAISE EXCEPTION '0093 recovery fact missing' USING ERRCODE = '23514'; END IF;
    SELECT * INTO consume_tx FROM public.inventory_transactions WHERE id = consumed.posting_transaction_id;
    SELECT * INTO recover_tx FROM public.inventory_transactions WHERE id = recovered.posting_transaction_id;
    IF consumed.replacement_id IS DISTINCT FROM replacement.id OR recovered.replacement_id IS DISTINCT FROM replacement.id
       OR consumed.operation_type <> 'consume' OR recovered.operation_type <> 'recover'
       OR consumed.oam_work_order_id <> replacement.oam_work_order_id OR recovered.oam_work_order_id <> replacement.oam_work_order_id
       OR consumed.operator_person_id <> replacement.operator_person_id OR recovered.operator_person_id <> replacement.operator_person_id
       OR consume_tx.id IS NULL OR recover_tx.id IS NULL OR consume_tx.actor_user_id <> recover_tx.actor_user_id
       OR recover_tx.ledger_cursor <> consume_tx.ledger_cursor + 1
       OR replacement.created_at > consume_tx.created_at OR consume_tx.created_at > recover_tx.created_at
       OR (SELECT count(*) FROM public.work_order_material_operations WHERE replacement_id = replacement.id) <> 2
       OR replacement.idempotency_key_hash !~ '^[0-9a-f]{64}$' OR replacement.request_hash !~ '^[0-9a-f]{64}$'
       OR replacement.request_id !~ '^[A-Za-z0-9._:-]{8,160}$'
       OR replacement.replacement_no <> 'WOR-' || upper(substr(replacement.idempotency_key_hash, 1, 24))
       OR consumed.idempotency_key_hash <> encode(sha256(convert_to('work-order-replacement:' || replacement.idempotency_key_hash || ':' || 'consume', 'UTF8')), 'hex')
       OR recovered.idempotency_key_hash <> encode(sha256(convert_to('work-order-replacement:' || replacement.idempotency_key_hash || ':' || 'recover', 'UTF8')), 'hex') THEN
        RAISE EXCEPTION '0093 replacement operation binding invalid' USING ERRCODE = '23514';
    END IF;
    PERFORM public.rsc_check_work_order_material_transaction_0090(consume_tx.id);
    PERFORM public.rsc_check_work_order_material_transaction_0090(recover_tx.id);
    SELECT after_jsonb->'command' INTO STRICT consumed_command FROM public.audit_events
        WHERE stream_key = 'material_request' AND aggregate_type = 'work_order_material_operation'
          AND aggregate_id = consumed.id::text AND action = 'work_order_material.consume';
    SELECT after_jsonb->'command' INTO STRICT recovered_command FROM public.audit_events
        WHERE stream_key = 'material_request' AND aggregate_type = 'work_order_material_operation'
          AND aggregate_id = recovered.id::text AND action = 'work_order_material.recover';
    IF jsonb_typeof(replacement.command_jsonb->'recover_lines') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION '0093 replacement recovery command invalid' USING ERRCODE = '23514';
    END IF;
    IF jsonb_array_length(replacement.command_jsonb->'recover_lines') <> jsonb_array_length(recovered_command->'lines') THEN
        RAISE EXCEPTION '0093 replacement recovery line count invalid' USING ERRCODE = '23514';
    END IF;
    FOR intent IN SELECT value, ordinality FROM jsonb_array_elements(replacement.command_jsonb->'recover_lines') WITH ORDINALITY LOOP
        SELECT account.* INTO source FROM public.stock_accounts account
          JOIN public.work_order_material_lines line ON line.stock_account_id = account.id
          WHERE line.operation_id = consumed.id AND account.id::text = intent.value->>'basis_stock_account_id';
        IF NOT FOUND THEN RAISE EXCEPTION '0093 recovery basis is not consumed stock' USING ERRCODE = '23514'; END IF;
        SELECT account.* INTO target FROM public.stock_accounts account
          JOIN public.work_order_material_lines line ON line.stock_account_id = account.id
          WHERE line.operation_id = recovered.id AND line.line_no = intent.ordinality;
        IF NOT FOUND OR source.owner_org_id <> target.owner_org_id OR source.location_id <> target.location_id
           OR source.custodian_person_id IS DISTINCT FROM target.custodian_person_id
           OR target.custodian_person_id IS DISTINCT FROM replacement.operator_person_id
           OR (intent.value->'target_stock_account_id' IS DISTINCT FROM 'null'::jsonb
               AND intent.value->>'target_stock_account_id' IS DISTINCT FROM target.id::text) THEN
            RAISE EXCEPTION '0093 recovery changed stock custody or explicit target' USING ERRCODE = '23514';
        END IF;
        expected_recovery := expected_recovery || jsonb_build_array(
            ((recovered_command->'lines'->(intent.ordinality::integer - 1)) - ARRAY['stock_account_id', 'target_stock_account_id'])
            || jsonb_build_object('basis_stock_account_id', source.id::text, 'lot_id', target.lot_id::text,
                                 'target_stock_account_id', intent.value->'target_stock_account_id'));
    END LOOP;
    IF EXISTS (SELECT stock_account_id::text FROM public.work_order_material_lines WHERE operation_id = consumed.id
               EXCEPT SELECT value->>'basis_stock_account_id' FROM jsonb_array_elements(expected_recovery)) THEN
        RAISE EXCEPTION '0093 a consumed line has no removed-part recovery' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT 1 FROM public.work_order_replacement_pairs WHERE replacement_id = replacement.id AND operation_id <> consumed.id)
       OR EXISTS (SELECT pair.installed_serial_id FROM public.work_order_replacement_pairs pair WHERE pair.replacement_id = replacement.id
                  EXCEPT SELECT sn.serial_id FROM public.work_order_material_serials sn JOIN public.work_order_material_lines line
                    ON line.id = sn.operation_line_id WHERE line.operation_id = consumed.id)
       OR EXISTS (SELECT sn.serial_id FROM public.work_order_material_serials sn JOIN public.work_order_material_lines line
                    ON line.id = sn.operation_line_id WHERE line.operation_id = consumed.id
                  EXCEPT SELECT pair.installed_serial_id FROM public.work_order_replacement_pairs pair WHERE pair.replacement_id = replacement.id)
       OR EXISTS (SELECT pair.removed_serial_id FROM public.work_order_replacement_pairs pair WHERE pair.replacement_id = replacement.id
                  EXCEPT SELECT sn.serial_id FROM public.work_order_material_serials sn JOIN public.work_order_material_lines line
                    ON line.id = sn.operation_line_id WHERE line.operation_id = recovered.id)
       OR EXISTS (SELECT sn.serial_id FROM public.work_order_material_serials sn JOIN public.work_order_material_lines line
                    ON line.id = sn.operation_line_id WHERE line.operation_id = recovered.id
                  EXCEPT SELECT pair.removed_serial_id FROM public.work_order_replacement_pairs pair WHERE pair.replacement_id = replacement.id)
       OR EXISTS (SELECT 1 FROM public.work_order_replacement_pairs pair WHERE pair.replacement_id = replacement.id
                  AND pair.installed_serial_id = pair.removed_serial_id)
       OR EXISTS (SELECT 1 FROM public.work_order_replacement_pairs pair
                  JOIN public.work_order_material_serials installed_sn ON installed_sn.serial_id = pair.installed_serial_id
                  JOIN public.work_order_material_lines installed ON installed.id = installed_sn.operation_line_id AND installed.operation_id = consumed.id
                  JOIN public.work_order_material_serials removed_sn ON removed_sn.serial_id = pair.removed_serial_id
                  JOIN public.work_order_material_lines removed ON removed.id = removed_sn.operation_line_id AND removed.operation_id = recovered.id
                  WHERE pair.replacement_id = replacement.id AND
                    (expected_recovery->(removed.line_no::integer - 1)->>'basis_stock_account_id') IS DISTINCT FROM installed.stock_account_id::text) THEN
        RAISE EXCEPTION '0093 serial pairs do not exactly link installed and removed stock' USING ERRCODE = '23514';
    END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('installed_serial_id', installed_serial_id::text, 'removed_serial_id', removed_serial_id::text)
               ORDER BY installed_serial_id::text, removed_serial_id::text), '[]'::jsonb) INTO expected_pairs
        FROM public.work_order_replacement_pairs WHERE replacement_id = replacement.id;
    expected_command := jsonb_build_object('work_order_id', replacement.oam_work_order_id::text,
        'operator_person_id', replacement.operator_person_id::text, 'consume_lines', consumed_command->'lines',
        'recover_lines', expected_recovery, 'replacement_pairs', expected_pairs);
    IF replacement.command_jsonb <> expected_command
       OR replacement.request_hash <> encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected_command), 'UTF8')), 'hex')
       OR (SELECT count(*) FROM public.audit_events WHERE stream_key = 'material_request'
            AND actor_user_id = consume_tx.actor_user_id AND aggregate_type = 'work_order_material_replacement'
            AND aggregate_id = replacement.id::text AND action = 'work_order_material.replace' AND request_id = replacement.request_id
            AND before_jsonb = '{}'::jsonb AND after_jsonb = jsonb_build_object('work_order_id', replacement.oam_work_order_id::text,
                'consume_operation_id', consumed.id::text, 'recover_operation_id', recovered.id::text,
                'request_hash', replacement.request_hash, 'command', expected_command)) <> 1
       OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type = 'work_order_material_replacement'
            AND aggregate_id = replacement.id::text AND event_type = 'work_order_material_replacement_posted'
            AND payload_jsonb = jsonb_build_object('work_order_id', replacement.oam_work_order_id::text,
                'consume_operation_id', consumed.id::text, 'recover_operation_id', recovered.id::text)) <> 1 THEN
        RAISE EXCEPTION '0093 replacement command audit or outbox mismatch' USING ERRCODE = '23514';
    END IF;
END;
"""

DISPATCH_BODY = """
DECLARE checked_replacement uuid;
BEGIN
    IF TG_TABLE_NAME = 'work_order_replacements' THEN checked_replacement := NEW.id;
    ELSE checked_replacement := NEW.replacement_id; END IF;
    IF checked_replacement IS NOT NULL THEN
        PERFORM public.rsc_check_work_order_replacement_0093(checked_replacement);
    END IF;
    RETURN NULL;
END;
"""

FUNCTIONS = {
    ("rsc_check_work_order_replacement_0093", "uuid"): ("checked_replacement uuid", "void", CHECK_BODY),
    ("rsc_dispatch_work_order_replacement_0093", ""): ("", "trigger", DISPATCH_BODY),
}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key, value in FUNCTIONS.items()}


def _create_tables():
    identifier = sa.Uuid()
    op.create_table("work_order_replacements",
        sa.Column("id", identifier, primary_key=True),
        sa.Column("replacement_no", sa.String(100), nullable=False),
        sa.Column("oam_work_order_id", identifier, sa.ForeignKey("oam_work_orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operator_person_id", identifier, sa.ForeignKey("people.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("consume_operation_id", identifier, sa.ForeignKey("work_order_material_operations.id", deferrable=True, initially="DEFERRED"), nullable=False),
        sa.Column("recover_operation_id", identifier, sa.ForeignKey("work_order_material_operations.id", deferrable=True, initially="DEFERRED"), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(160), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("command_jsonb", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("replacement_no", name="uq_work_order_replacements_no"),
        sa.UniqueConstraint("idempotency_key_hash", name="uq_work_order_replacements_key"),
        sa.UniqueConstraint("operator_person_id", "request_id", name="uq_work_order_replacements_request"),
        sa.UniqueConstraint("consume_operation_id", name="uq_work_order_replacements_consume"),
        sa.UniqueConstraint("recover_operation_id", name="uq_work_order_replacements_recover"),
        sa.CheckConstraint("consume_operation_id <> recover_operation_id", name="ck_work_order_replacements_distinct_operations"),
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE work_order_material_operations ADD COLUMN replacement_id CHAR(32) REFERENCES work_order_replacements(id) DEFERRABLE INITIALLY DEFERRED")
        op.execute("ALTER TABLE work_order_replacement_pairs ADD COLUMN replacement_id CHAR(32) NOT NULL REFERENCES work_order_replacements(id) ON DELETE RESTRICT")
        for event in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER trg_work_order_replacements_{event.lower()}_0093 BEFORE {event} ON work_order_replacements BEGIN SELECT RAISE(ABORT, '0093 replacement facts are append-only'); END")
    else:
        op.add_column("work_order_material_operations", sa.Column("replacement_id", identifier, nullable=True))
        op.create_foreign_key("fk_work_order_operation_replacement_0093", "work_order_material_operations", "work_order_replacements", ["replacement_id"], ["id"], deferrable=True, initially="DEFERRED")
        op.add_column("work_order_replacement_pairs", sa.Column("replacement_id", identifier, nullable=False))
        op.create_foreign_key("fk_work_order_pair_replacement_0093", "work_order_replacement_pairs", "work_order_replacements", ["replacement_id"], ["id"], ondelete="RESTRICT")


def _drop_tables():
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("fk_work_order_operation_replacement_0093", "work_order_material_operations", type_="foreignkey")
        op.drop_constraint("fk_work_order_pair_replacement_0093", "work_order_replacement_pairs", type_="foreignkey")
    op.drop_column("work_order_material_operations", "replacement_id")
    op.drop_column("work_order_replacement_pairs", "replacement_id")
    op.drop_table("work_order_replacements")


ACCOUNT_BRANCH = """    IF EXISTS (
        SELECT 1 FROM public.work_order_replacements replacement
        JOIN public.work_order_material_operations operation ON operation.id = replacement.recover_operation_id
          AND operation.replacement_id = replacement.id AND operation.operation_type = 'recover'
        JOIN public.inventory_transactions tx ON tx.id = operation.posting_transaction_id
        JOIN public.inventory_movements movement ON movement.transaction_id = tx.id AND movement.to_account_id = NEW.id
        JOIN public.work_order_material_lines line ON line.operation_id = operation.id AND line.line_no = movement.line_no
          AND line.stock_account_id = NEW.id AND line.material_id = NEW.material_id AND line.quantity = movement.quantity
        JOIN LATERAL jsonb_array_elements(replacement.command_jsonb->'recover_lines') WITH ORDINALITY intent(value, number)
          ON intent.number = line.line_no
        JOIN public.stock_accounts source ON source.id::text = intent.value->>'basis_stock_account_id'
        JOIN public.work_order_material_lines consumed ON consumed.operation_id = replacement.consume_operation_id
          AND consumed.stock_account_id = source.id
        JOIN public.stock_balances balance ON balance.stock_account_id = NEW.id AND balance.version = 1
          AND balance.ledger_cursor = tx.ledger_cursor
        JOIN public.stock_locations location ON location.id = NEW.location_id
        JOIN public.inventory_opening_establishments established ON established.owner_org_id = NEW.owner_org_id
          AND established.location_id = NEW.location_id
        WHERE tx.status = 'posted' AND tx.movement_type = 'inbound' AND tx.source_document_type = 'work_order_material'
          AND tx.source_document_id = replacement.oam_work_order_id::text
          AND movement.from_account_id IS NULL AND movement.external_boundary_code = 'work_order_material_recover'
          AND NEW.owner_org_id = source.owner_org_id AND NEW.location_id = source.location_id
          AND NEW.custodian_person_id = source.custodian_person_id AND NEW.custodian_person_id = replacement.operator_person_id
          AND NEW.availability_bucket = 'available' AND NEW.condition_code IN ('used', 'damaged')
          AND NEW.condition_code = line.condition_before AND source.availability_bucket = 'reserved'
          AND location.location_type = 'personal' AND location.status = 'active' AND location.custodian_person_id = NEW.custodian_person_id
          AND NEW.created_at >= established.established_at AND NEW.created_at <= tx.created_at AND NEW.updated_at = NEW.created_at
          AND balance.quantity = (SELECT sum(initial.quantity) FROM public.inventory_movements initial
            WHERE initial.transaction_id = tx.id AND initial.to_account_id = NEW.id)
          AND NOT EXISTS (SELECT 1 FROM public.inventory_movements earlier JOIN public.inventory_transactions previous ON previous.id = earlier.transaction_id
            WHERE (earlier.from_account_id = NEW.id OR earlier.to_account_id = NEW.id) AND previous.ledger_cursor < tx.ledger_cursor)
    ) THEN
        RETURN NEW;
    END IF;

"""


def _changed(old, changes):
    new = old
    for anchor, replacement in changes:
        if new.count(anchor) != 1:
            raise RuntimeError("0093 canonical source anchor drift")
        new = new.replace(anchor, replacement)
    return new


def _sources():
    folder = Path(__file__).parent
    prior_account = runpy.run_path(str(folder / "20261001_0091_work_order_account_admission.py"))
    _, account_old = prior_account["_account_sources"]()
    anchor = runpy.run_path(str(folder / "20260928_0088_receipt_account_admission.py"))["ANCHOR"]
    account_new = _changed(account_old, ((anchor, ACCOUNT_BRANCH + anchor),))
    work_order = runpy.run_path(str(folder / "20260930_0090_work_order_reservation_causality.py"))
    work_old = work_order["CHECK_BODY"]
    work_new = _changed(work_old, ((
        "OR EXISTS (SELECT 1 FROM public.work_order_replacement_pairs WHERE operation_id = fact.id)",
        "OR EXISTS (SELECT 1 FROM public.work_order_replacement_pairs pair WHERE pair.operation_id = fact.id AND (fact.replacement_id IS NULL OR pair.replacement_id IS DISTINCT FROM fact.replacement_id))",
    ),))
    serial = runpy.run_path(str(folder / "20261002_0092_serial_consumption_projection.py"))
    serial_old = serial["CHECK_BODY"]
    serial_new = _changed(serial_old, (
        ("expected_account uuid := NULL;", "expected_account uuid := NULL;\n    expected_owner uuid := NULL;"),
        ("tx.effective_at, source.material_id", "source.owner_org_id AS source_owner, target.owner_org_id AS target_owner, tx.effective_at, source.material_id"),
        ("OR expected_status <> 'active'", """OR (expected_status <> 'active' AND NOT (
               expected_status = 'consumed' AND move.movement_type = 'inbound'
               AND move.source_document_type = 'work_order_material' AND move.external_boundary_code IS NOT DISTINCT FROM 'work_order_material_recover'
               AND move.from_account_id IS NULL AND move.to_account_id IS NOT NULL AND move.target_owner IS NOT DISTINCT FROM expected_owner
               AND EXISTS (SELECT 1 FROM public.work_order_replacements replacement
                   JOIN public.work_order_material_operations recovery ON recovery.id = replacement.recover_operation_id
                     AND recovery.replacement_id = replacement.id
                   JOIN public.work_order_replacement_pairs pair ON pair.replacement_id = replacement.id
                     AND pair.operation_id = replacement.consume_operation_id AND pair.removed_serial_id = checked_serial
                   WHERE recovery.posting_transaction_id = move.transaction_id)))"""),
        ("IF move.movement_type = 'consume' THEN", """IF expected_status = 'consumed' THEN
            expected_status := 'active';
            terminal_time := move.created_at;
        END IF;
        IF move.movement_type = 'consume' THEN"""),
        ("expected_movement := move.id;", "expected_movement := move.id;\n        expected_owner := CASE WHEN move.to_account_id IS NOT NULL THEN move.target_owner ELSE move.source_owner END;"),
        ("expected_status = 'consumed' AND serial.updated_at", "terminal_time IS NOT NULL AND serial.updated_at"),
    ))
    return {
        "public.rsc_require_opening_observation_account_0023()": (account_old, account_new),
        "public.rsc_check_work_order_material_transaction_0090(uuid)": (work_old, work_new),
        "public.rsc_check_serial_lifecycle_0092(uuid)": (serial_old, serial_new),
    }


TRIGGERS = {
    "trg_work_order_replacements_proof_0093": ("work_order_replacements", "INSERT", "rsc_dispatch_work_order_replacement_0093", 5, True),
    "trg_work_order_operations_replacement_0093": ("work_order_material_operations", "INSERT", "rsc_dispatch_work_order_replacement_0093", 5, True),
    "trg_work_order_pairs_replacement_0093": ("work_order_replacement_pairs", "INSERT", "rsc_dispatch_work_order_replacement_0093", 5, True),
    "trg_work_order_replacements_immutable_0093": ("work_order_replacements", "UPDATE OR DELETE", "rsc_guard_work_order_facts_0090", 27, False),
    "trg_work_order_replacements_no_truncate_0093": ("work_order_replacements", "TRUNCATE", "rsc_guard_work_order_facts_0090", 34, False),
}


def _transition(upgrade):
    db = op.get_bind()
    if db.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError("0093 supports PostgreSQL and SQLite only")
    previous = runpy.run_path(str(Path(__file__).with_name("20260927_0087_inbound_fulfillment_boundary.py")))
    previous["_begin_sqlite"]()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE public.stock_accounts, public.stock_balances, public.inventory_serials, public.serial_current_positions, public.inventory_transactions, public.inventory_movements, public.inventory_movement_serials, public.work_order_material_operations, public.work_order_material_lines, public.work_order_material_serials, public.work_order_replacement_pairs IN SHARE ROW EXCLUSIVE MODE")
    facts = "EXISTS (SELECT 1 FROM work_order_replacement_pairs)"
    if not upgrade:
        if db.dialect.name == "postgresql":
            op.execute("LOCK TABLE public.work_order_replacements IN SHARE ROW EXCLUSIVE MODE")
        facts += " OR EXISTS (SELECT 1 FROM work_order_replacements) OR EXISTS (SELECT 1 FROM work_order_material_operations WHERE replacement_id IS NOT NULL)"
    previous["_preflight"](facts, "0093 transition blocked: replacement facts require reviewed migration")
    if upgrade:
        _create_tables()
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
            op.execute("GRANT SELECT, INSERT ON TABLE public.work_order_replacements TO star_oam_api")
        else:
            for (name, signature), digest in FUNCTION_HASHES.items():
                op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p
                    WHERE p.oid = 'public.{name}({signature})'::regprocedure
                      AND encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') = '{digest}'
                      AND p.proowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
                      AND p.prosecdef AND p.proconfig = ARRAY['search_path=pg_catalog, public']
                      AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl WHERE acl.grantee <> p.proowner))
                    THEN RAISE EXCEPTION '0093 function source, configuration or ownership drift'; END IF; END $body$""")
            for name, (table, *_) in TRIGGERS.items():
                op.execute(f"DROP TRIGGER {name} ON public.{table}")
            for name, signature in reversed(FUNCTIONS):
                op.execute(f"DROP FUNCTION public.{name}({signature})")
        for signature, (old, new) in _sources().items():
            replace(signature=signature, expected_hash=hashlib.sha256((old if upgrade else new).encode()).hexdigest(),
                replacement_hash=hashlib.sha256((new if upgrade else old).encode()).hexdigest(),
                replacements=((old, new),) if upgrade else ((new, old),), label="work_order_replacement_sources_0093")
        replace(signature="public.rsc_oam_runtime_binding_ready_0044()", expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,
            replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),), label="work_order_replacement_readiness_0093")
    if not upgrade:
        _drop_tables()


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
