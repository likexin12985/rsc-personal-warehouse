"""Register physically scanned removed identities without admitting stock."""
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261006_0096"
down_revision = "20261005_0095"
branch_labels = depends_on = None
OLD_HASH = "f5a10152b6e89e2001c6aec103343b54b4002110b5203e843045676875d89268"
NEW_HASH = "1c842e6876982e06ef99d0fe7e9ccb8ba0c0256dafa9e51763ee0946b678147f"
TABLE = "work_order_removed_serial_registrations"
OLD_CHECK = "operation_type IN ('occupy', 'consume', 'release', 'replace')"
NEW_CHECK = "operation_type IN ('occupy', 'consume', 'release', 'replace', 'register_removed')"

CREATE_BODY = """
DECLARE
    sku public.materials%ROWTYPE;
    lot public.inventory_lots%ROWTYPE;
    basis public.stock_accounts%ROWTYPE;
    expected jsonb;
BEGIN
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key = 'inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0096 inventory ledger missing' USING ERRCODE = '23514'; END IF;
    SELECT * INTO basis FROM public.stock_accounts WHERE id = NEW.basis_stock_account_id;
    IF basis.id IS NULL THEN RAISE EXCEPTION '0096 registration basis missing' USING ERRCODE = '23514'; END IF;
    PERFORM public.rsc_lock_opening_stocktake_start_reference_0027(basis.owner_org_id,
        ARRAY[basis.owner_org_id],ARRAY[basis.location_id],ARRAY[NEW.material_id],NEW.registered_at);
    SELECT * INTO basis FROM public.stock_accounts WHERE id = NEW.basis_stock_account_id;
    SELECT * INTO sku FROM public.materials WHERE id = NEW.material_id AND status = 'active';
    SELECT * INTO lot FROM public.inventory_lots WHERE id = NEW.lot_id;
    IF sku.id IS NULL OR basis.id IS NULL OR basis.availability_bucket <> 'reserved'
       OR basis.custodian_person_id IS DISTINCT FROM NEW.operator_person_id
       OR NOT EXISTS (SELECT 1 FROM public.stock_locations location WHERE location.id = basis.location_id
            AND location.location_type = 'personal' AND location.status = 'active'
            AND location.custodian_person_id = NEW.operator_person_id)
       OR NOT EXISTS (SELECT 1 FROM public.users WHERE id = NEW.actor_user_id AND person_id = NEW.operator_person_id
            AND account_status = 'active' AND authorization_version = NEW.authorization_version)
       OR NOT EXISTS (SELECT 1 FROM public.oam_work_orders wo
            JOIN public.external_objects obj ON obj.id = wo.external_object_id
            JOIN public.external_object_versions version ON version.id = obj.current_version_id AND version.external_object_id = obj.id
            JOIN public.source_systems source ON source.id = obj.source_system_id
            WHERE wo.id = NEW.oam_work_order_id AND wo.status = 'active' AND wo.engineer_person_id = NEW.operator_person_id
              AND source.code = 'starcharge_oam' AND source.mode = 'read_only' AND source.enabled
              AND obj.entity_type = 'work_order' AND obj.deleted_at IS NULL AND version.source_version = NEW.source_version
              AND wo.updated_at <= NEW.registered_at AND wo.updated_at >= NEW.registered_at - interval '45 minutes')
       OR (SELECT coalesce(sum(CASE WHEN tx.movement_type = 'reserve' AND movement.to_account_id = basis.id THEN movement.quantity
                    WHEN tx.movement_type IN ('consume','release') AND movement.from_account_id = basis.id THEN -movement.quantity ELSE 0 END), 0)
            FROM public.inventory_movements movement JOIN public.inventory_transactions tx ON tx.id = movement.transaction_id
            WHERE tx.status = 'posted' AND tx.source_document_type = 'work_order_material'
              AND tx.source_document_id = NEW.oam_work_order_id::text) <= 0
       OR (NEW.lot_id IS NOT NULL AND (lot.id IS NULL OR lot.material_id <> sku.id))
       OR (SELECT count(*) FROM public.material_inventory_policies policy WHERE policy.material_id = sku.id
            AND policy.effective_from <= NEW.registered_at AND (policy.effective_to IS NULL OR policy.effective_to > NEW.registered_at)
            AND ((policy.tracking_mode = 'serial' AND NEW.lot_id IS NULL)
              OR (policy.tracking_mode = 'lot_and_serial' AND NEW.lot_id IS NOT NULL))) <> 1 THEN
        RAISE EXCEPTION '0096 registration requires current own order, reserved basis and tracked material' USING ERRCODE = '23514';
    END IF;
    expected := jsonb_build_object('work_order_id', NEW.oam_work_order_id::text, 'operator_person_id', NEW.operator_person_id::text,
        'basis_stock_account_id', NEW.basis_stock_account_id::text, 'sku_code', sku.sku_code,
        'lot_no', lot.lot_no, 'serial_no', NEW.command_jsonb->>'serial_no', 'qr_code', NEW.command_jsonb->>'qr_code',
        'condition_before', NEW.command_jsonb->>'condition_before');
    IF NEW.command_jsonb <> expected OR NOT coalesce(NEW.command_jsonb->>'condition_before' IN ('used','damaged'),false)
       OR jsonb_typeof(NEW.command_jsonb->'serial_no') IS DISTINCT FROM 'string'
       OR jsonb_typeof(NEW.command_jsonb->'qr_code') IS DISTINCT FROM 'string'
       OR length(btrim(NEW.command_jsonb->>'serial_no')) NOT BETWEEN 1 AND 200
       OR length(btrim(NEW.command_jsonb->>'qr_code')) NOT BETWEEN 1 AND 250
       OR (NEW.command_jsonb->>'serial_no') ~ '[[:cntrl:]]' OR (NEW.command_jsonb->>'qr_code') ~ '[[:cntrl:]]'
       OR NEW.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR NEW.idempotency_key_hash !~ '^[0-9a-f]{64}$'
       OR NEW.request_hash <> encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected), 'UTF8')), 'hex')
       OR NEW.registration_no <> 'WORS-' || upper(substr(NEW.idempotency_key_hash, 1, 24))
       OR NEW.registered_at <> NEW.created_at THEN
        RAISE EXCEPTION '0096 registration command proof invalid' USING ERRCODE = '23514';
    END IF;
    INSERT INTO public.inventory_serials(id,material_id,lot_id,serial_no,qr_code,lifecycle_status,created_at,updated_at)
        VALUES (NEW.serial_id,NEW.material_id,NEW.lot_id,NEW.command_jsonb->>'serial_no',NEW.command_jsonb->>'qr_code',
                'active',NEW.registered_at,NEW.registered_at);
    INSERT INTO public.qr_codes(id,code,object_type,object_id,status,printed_at,created_at,updated_at)
        VALUES (NEW.serial_id,NEW.command_jsonb->>'qr_code','serial',NEW.serial_id,'active',NULL,NEW.registered_at,NEW.registered_at);
    RETURN NEW;
END;
"""
PROOF_BODY = """
DECLARE reg public.work_order_removed_serial_registrations%ROWTYPE;
BEGIN
    SELECT * INTO STRICT reg FROM public.work_order_removed_serial_registrations WHERE id = NEW.id;
    IF NOT EXISTS (SELECT 1 FROM public.inventory_serials sn WHERE sn.id = reg.serial_id
            AND sn.material_id = reg.material_id AND sn.lot_id IS NOT DISTINCT FROM reg.lot_id
            AND sn.serial_no = reg.command_jsonb->>'serial_no' AND sn.qr_code = reg.command_jsonb->>'qr_code'
            AND sn.created_at = reg.registered_at)
       OR NOT EXISTS (SELECT 1 FROM public.qr_codes qr WHERE qr.id = reg.serial_id AND qr.object_type = 'serial'
            AND qr.object_id = reg.serial_id AND qr.code = reg.command_jsonb->>'qr_code' AND qr.status = 'active')
       OR (SELECT count(*) FROM public.audit_events WHERE stream_key = 'material_request'
            AND aggregate_type = 'work_order_removed_serial_registration' AND aggregate_id = reg.id::text
            AND action = 'work_order_material.register_removed_serial' AND actor_user_id = reg.actor_user_id
            AND request_id = 'work-order-serial-registration:' || reg.id::text AND occurred_at = reg.registered_at
            AND before_jsonb = '{}'::jsonb AND after_jsonb = jsonb_build_object(
                'work_order_id',reg.oam_work_order_id::text,'operator_person_id',reg.operator_person_id::text,
                'serial_id',reg.serial_id::text,'authorization_version',reg.authorization_version,
                'source_version',reg.source_version,'request_id',reg.request_id,'request_hash',reg.request_hash,'command',reg.command_jsonb)) <> 1
       OR EXISTS (SELECT 1 FROM public.work_order_command_seals seal WHERE seal.actor_user_id = reg.actor_user_id
            AND seal.oam_work_order_id = reg.oam_work_order_id AND seal.operation_type = 'register_removed' AND seal.request_id = reg.request_id) THEN
        RAISE EXCEPTION '0096 registration audit, identity or non-execution proof invalid' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
"""
ORIGIN_BODY = """
DECLARE reg public.work_order_removed_serial_registrations%ROWTYPE; first_move record;
BEGIN
    SELECT * INTO reg FROM public.work_order_removed_serial_registrations WHERE serial_id = NEW.serial_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT movement.*,tx.movement_type,tx.source_document_type,tx.source_document_id INTO first_move
        FROM public.inventory_movement_serials serial
        JOIN public.inventory_movements movement ON movement.id = serial.movement_id
        JOIN public.inventory_transactions tx ON tx.id = movement.transaction_id AND tx.status = 'posted'
        WHERE serial.serial_id = NEW.serial_id ORDER BY tx.ledger_cursor,movement.line_no LIMIT 1;
    IF NOT FOUND THEN RAISE EXCEPTION '0096 registered serial movement lacks posting' USING ERRCODE = '23514'; END IF;
    IF first_move.movement_type <> 'inbound' OR first_move.source_document_type <> 'work_order_material'
       OR first_move.source_document_id <> reg.oam_work_order_id::text OR first_move.from_account_id IS NOT NULL
       OR first_move.external_boundary_code IS DISTINCT FROM 'work_order_material_recover'
       OR NOT EXISTS (SELECT 1 FROM public.work_order_replacements parent
            JOIN public.work_order_material_operations operation ON operation.id = parent.recover_operation_id
            JOIN public.stock_accounts target ON target.id = first_move.to_account_id
            JOIN public.stock_accounts basis ON basis.id = reg.basis_stock_account_id
            WHERE parent.oam_work_order_id = reg.oam_work_order_id AND parent.operator_person_id = reg.operator_person_id
              AND operation.posting_transaction_id = first_move.transaction_id
              AND target.owner_org_id = basis.owner_org_id AND target.location_id = basis.location_id
              AND target.custodian_person_id = reg.operator_person_id AND target.condition_code IN ('used','damaged')
              AND EXISTS (SELECT 1 FROM jsonb_array_elements(parent.command_jsonb->'recover_lines') line
                    WHERE line->>'basis_stock_account_id' = basis.id::text
                      AND line->'serial_ids' @> jsonb_build_array(reg.serial_id::text))) THEN
        RAISE EXCEPTION '0096 new removed identity must first enter through its own paired recovery' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
"""
SEAL_BODY = """
BEGIN
    IF NEW.operation_type = 'register_removed' AND EXISTS (
        SELECT 1 FROM public.work_order_removed_serial_registrations reg
        WHERE reg.actor_user_id = NEW.actor_user_id AND reg.oam_work_order_id = NEW.oam_work_order_id AND reg.request_id = NEW.request_id) THEN
        RAISE EXCEPTION '0096 completed registration cannot be sealed' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
"""
FUNCTIONS = {
    ("rsc_create_removed_identity_0096", ""): ("", "trigger", CREATE_BODY),
    ("rsc_check_removed_registration_0096", ""): ("", "trigger", PROOF_BODY),
    ("rsc_check_removed_origin_0096", ""): ("", "trigger", ORIGIN_BODY),
    ("rsc_check_removed_seal_0096", ""): ("", "trigger", SEAL_BODY),
}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key,value in FUNCTIONS.items()}
TRIGGERS = {
    "trg_removed_registration_create_0096": (TABLE,"INSERT","rsc_create_removed_identity_0096",7,False),
    "trg_removed_registration_proof_0096": (TABLE,"INSERT","rsc_check_removed_registration_0096",5,True),
    "trg_removed_registration_immutable_0096": (TABLE,"UPDATE OR DELETE","rsc_guard_work_order_facts_0090",27,False),
    "trg_removed_registration_no_truncate_0096": (TABLE,"TRUNCATE","rsc_guard_work_order_facts_0090",34,False),
    "trg_removed_serial_origin_0096": ("inventory_movement_serials","INSERT","rsc_check_removed_origin_0096",5,True),
    "trg_removed_registration_seal_0096": ("work_order_command_seals","INSERT","rsc_check_removed_seal_0096",5,True),
}


def _create_table():
    op.create_table(TABLE,
        sa.Column("id",sa.Uuid(),primary_key=True),
        sa.Column("registration_no",sa.String(100),nullable=False),
        sa.Column("serial_id",sa.Uuid(),sa.ForeignKey("inventory_serials.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("oam_work_order_id",sa.Uuid(),sa.ForeignKey("oam_work_orders.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("actor_user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("operator_person_id",sa.Uuid(),sa.ForeignKey("people.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("authorization_version",sa.Integer(),nullable=False),
        sa.Column("source_version",sa.String(1000),nullable=False),
        sa.Column("basis_stock_account_id",sa.Uuid(),sa.ForeignKey("stock_accounts.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("material_id",sa.Uuid(),sa.ForeignKey("materials.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("lot_id",sa.Uuid(),sa.ForeignKey("inventory_lots.id",ondelete="RESTRICT"),nullable=True),
        sa.Column("request_id",sa.String(160),nullable=False),
        sa.Column("request_hash",sa.String(64),nullable=False),
        sa.Column("idempotency_key_hash",sa.String(64),nullable=False),
        sa.Column("command_jsonb",sa.JSON().with_variant(postgresql.JSONB(),"postgresql"),nullable=False),
        sa.Column("registered_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("registration_no",name="uq_removed_registration_no"),
        sa.UniqueConstraint("serial_id",name="uq_removed_registration_serial"),
        sa.UniqueConstraint("idempotency_key_hash",name="uq_removed_registration_key"),
        sa.UniqueConstraint("actor_user_id","oam_work_order_id","request_id",name="uq_removed_registration_request"),
        sa.CheckConstraint("authorization_version > 0",name="ck_removed_registration_version"))
    if op.get_bind().dialect.name == "sqlite":
        for event in ("UPDATE","DELETE"):
            op.execute(f"CREATE TRIGGER trg_removed_registration_{event.lower()}_0096 BEFORE {event} ON {TABLE} BEGIN SELECT RAISE(ABORT, '0096 registrations are immutable'); END")


def _constraint(upgrade):
    check = NEW_CHECK if upgrade else OLD_CHECK
    if op.get_bind().dialect.name == "sqlite":
        for event in ("update","delete"):op.execute(f"DROP TRIGGER trg_work_order_seals_{event}_0094")
        with op.batch_alter_table("work_order_command_seals") as batch:
            batch.drop_constraint("ck_work_order_command_seals_operation",type_="check")
            batch.create_check_constraint("ck_work_order_command_seals_operation",check)
        for event in ("UPDATE","DELETE"):
            op.execute(f"CREATE TRIGGER trg_work_order_seals_{event.lower()}_0094 BEFORE {event} ON work_order_command_seals BEGIN SELECT RAISE(ABORT, '0094 command seals are append-only'); END")
    else:
        op.execute("ALTER TABLE public.work_order_command_seals DROP CONSTRAINT ck_work_order_command_seals_operation")
        op.execute(f"ALTER TABLE public.work_order_command_seals ADD CONSTRAINT ck_work_order_command_seals_operation CHECK ({check})")


def _transition(upgrade):
    db = op.get_bind()
    if db.dialect.name not in {"sqlite","postgresql"}:raise RuntimeError("0096 supports PostgreSQL and SQLite only")
    folder = Path(__file__).parent
    helper = runpy.run_path(str(folder / "20260927_0087_inbound_fulfillment_boundary.py"))
    helper["_begin_sqlite"]()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE public.inventory_ledger_heads, public.inventory_serials, public.inventory_movement_serials, public.qr_codes, public.work_order_command_seals IN SHARE ROW EXCLUSIVE MODE")
        if not upgrade:op.execute(f"LOCK TABLE public.{TABLE} IN SHARE ROW EXCLUSIVE MODE")
    if not upgrade:
        helper["_preflight"](f"EXISTS (SELECT 1 FROM {TABLE}) OR EXISTS (SELECT 1 FROM work_order_command_seals WHERE operation_type='register_removed')",
            "0096 downgrade blocked: removed registrations and seals must be retained")
    if upgrade:_create_table()
    if db.dialect.name == "postgresql":
        if upgrade:
            for (name,signature),(args,result,body) in FUNCTIONS.items():
                op.execute(f"CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${body}$body$")
                op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api")
            for name,(table,events,function,_,deferred) in TRIGGERS.items():
                sql = (f"CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW"
                    if deferred else f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events == 'TRUNCATE' else 'ROW'}")
                op.execute(sql + f" EXECUTE FUNCTION public.{function}()")
                op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
            op.execute(f"GRANT SELECT, INSERT ON public.{TABLE} TO star_oam_api")
        else:
            for (name,signature),digest in FUNCTION_HASHES.items():
                op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid='public.{name}({signature})'::regprocedure
                    AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{digest}'
                    AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND p.prosecdef
                    AND p.proconfig=ARRAY['search_path=pg_catalog, public']
                    AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl WHERE acl.grantee<>p.proowner))
                    THEN RAISE EXCEPTION '0096 function source, configuration or ownership drift'; END IF; END $body$""")
            for name,(table,*_) in TRIGGERS.items():op.execute(f"DROP TRIGGER {name} ON public.{table}")
            for name,signature in FUNCTIONS:op.execute(f"DROP FUNCTION public.{name}({signature})")
        source = runpy.run_path(str(folder / "20260912_0072_outbound_postings.py"))
        replace = source["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
        replace(signature="public.rsc_oam_runtime_binding_ready_0044()",expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),
            label="removed_registration_readiness_0096")
    _constraint(upgrade)
    if not upgrade:op.drop_table(TABLE)


def upgrade():_transition(True)
def downgrade():_transition(False)
