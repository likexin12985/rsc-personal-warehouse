"""Bind shipment and receipt facts into the continuous request command chain."""
from pathlib import Path
import runpy

from alembic import op

revision = "20260925_0085"
down_revision = "20260924_0084"
branch_labels = depends_on = None
OLD_HASH = "5f5ac9e237353067ee6fe3277487715eefcb05804731bc252378814524866dbe"
NEW_HASH = "c5b754cc9ddf1a180c9f52414b7ec5a9a5ddb9341e42446a95b59e8c985d80a1"
APPROVAL_OLD_HASH = "b5d39b3c6287e98657aa7802c628e2d67e3c2a541c1b4c5d99930b15c8cd4110"
APPROVAL_NEW_HASH = "71567de7cb5a838e00bec2fa8dcfab189cefaf271eebed0e08b8c1afa9a68b60"
ALLOW_OLD = "'cancel_supply_task', 'allocate', 'reserve', 'release', 'pick', 'outbound'\n                   )"
ALLOW_NEW = "'cancel_supply_task', 'allocate', 'reserve', 'release', 'pick', 'outbound', 'shipment', 'receipt'\n                   )"
COUNT_ANCHOR = """    SELECT count(*), min(target_version), max(target_version)
      INTO command_count, minimum_target_version, maximum_target_version
      FROM public.material_request_commands"""
COMMAND_BINDING = """    IF EXISTS (
        SELECT 1 FROM public.material_request_commands command
        LEFT JOIN public.shipments shipment ON command.operation = 'shipment'
            AND shipment.idempotency_key_hash = command.idempotency_key_hash
        LEFT JOIN public.receipts receipt ON command.operation = 'receipt'
            AND receipt.idempotency_key_hash = command.idempotency_key_hash
        WHERE command.request_id = checked_request_id AND command.operation IN ('shipment', 'receipt')
          AND (
            COALESCE(shipment.id, receipt.id) IS NULL
            OR command.request_hash IS DISTINCT FROM COALESCE(shipment.request_hash, receipt.request_hash)
            OR command.occurred_at IS DISTINCT FROM COALESCE(shipment.created_at, receipt.created_at)
            OR command.request_jsonb IS DISTINCT FROM jsonb_build_object(
                'schema', 'rsc.material_request_fulfillment_command.v1', 'operation', command.operation,
                'request_id', checked_request_id::text, 'target_version', command.target_version,
                'fact_id', COALESCE(shipment.id, receipt.id)::text, 'payload_sha256', command.request_hash)
            OR command.result_jsonb IS DISTINCT FROM jsonb_build_object(
                'schema_version', '1.0', 'operation', command.operation, 'request_id', checked_request_id::text,
                'target_version', command.target_version, 'fact_id', COALESCE(shipment.id, receipt.id)::text,
                'personal_inbound_status', command.result_jsonb->>'personal_inbound_status')
            OR command.result_jsonb->>'personal_inbound_status' IS NULL
            OR command.result_jsonb->>'personal_inbound_status' NOT IN
                ('not_started', 'pending_acceptance', 'partially_accepted', 'accepted', 'posted')
            OR (command.target_version = request_row.version AND
                command.result_jsonb->>'personal_inbound_status' IS DISTINCT FROM request_row.personal_inbound_status)
            OR NOT EXISTS (SELECT 1 FROM public.users actor
                JOIN public.role_assignments assignment ON assignment.user_id = actor.id
                WHERE actor.id = command.actor_user_id AND actor.person_id = command.actor_person_id
                  AND assignment.id = command.actor_role_assignment_id
                  AND assignment.valid_from <= command.occurred_at
                  AND (assignment.valid_to IS NULL OR assignment.valid_to > command.occurred_at))
            OR (command.operation = 'shipment' AND (
                command.request_reference <> '/api/v1/material-requests/' || checked_request_id::text || '/shipments'
                OR shipment.actor_user_id <> command.actor_user_id OR shipment.actor_person_id <> command.actor_person_id
                OR shipment.authorization_version <> command.authorization_version
                OR NOT EXISTS (SELECT 1 FROM public.shipment_lines line
                    JOIN public.outbound_postings outbound ON outbound.id = line.outbound_posting_id
                    WHERE line.shipment_id = shipment.id AND outbound.request_id = checked_request_id)
                OR EXISTS (SELECT 1 FROM public.shipment_lines line
                    JOIN public.outbound_postings outbound ON outbound.id = line.outbound_posting_id
                    WHERE line.shipment_id = shipment.id AND outbound.request_id <> checked_request_id)))
            OR (command.operation = 'receipt' AND (
                command.request_reference NOT IN (
                    '/api/v1/material-requests/' || checked_request_id::text || '/receipts',
                    '/api/v1/material-requests/' || checked_request_id::text || '/my-receipts')
                OR NOT EXISTS (SELECT 1 FROM public.receipt_lines line
                    JOIN public.shipment_lines shipped ON shipped.id = line.shipment_line_id
                    JOIN public.outbound_postings outbound ON outbound.id = shipped.outbound_posting_id
                    WHERE line.receipt_id = receipt.id AND outbound.request_id = checked_request_id)
                OR EXISTS (SELECT 1 FROM public.receipt_lines line
                    JOIN public.shipment_lines shipped ON shipped.id = line.shipment_line_id
                    JOIN public.outbound_postings outbound ON outbound.id = shipped.outbound_posting_id
                    WHERE line.receipt_id = receipt.id AND (outbound.request_id <> checked_request_id OR shipped.shipment_id <> receipt.shipment_id))
                OR (command.request_reference LIKE '%/my-receipts' AND (
                    receipt.receiver_person_id <> command.actor_person_id OR NOT EXISTS (
                        SELECT 1 FROM public.shipments original WHERE original.id = receipt.shipment_id
                          AND original.target_person_id = command.actor_person_id)))))
            OR (SELECT count(*) FROM public.audit_events audit
                WHERE audit.action = 'fulfillment_version_recorded' AND audit.stream_key = 'material_request'
                  AND audit.aggregate_type = command.operation AND audit.aggregate_id = COALESCE(shipment.id, receipt.id)::text
                  AND audit.actor_user_id = command.actor_user_id AND audit.occurred_at = command.occurred_at
                  AND audit.after_jsonb->>'command_id' = command.id::text
                  AND audit.after_jsonb->>'request_id' = checked_request_id::text
                  AND audit.after_jsonb->>'request_version' = command.target_version::text
                  AND audit.after_jsonb->>'request_hash' = command.request_hash
                  AND audit.after_jsonb->>'result_hash' = command.result_hash) <> 1
          )
    ) THEN
        RAISE EXCEPTION '0085 fulfillment command does not match its original fact' USING ERRCODE = '23514';
    END IF;

"""


def source_changes():
    # Preserve the continuous count/min/max version checks. The equivalent
    # count(1) spelling makes the source CAS unambiguous in both directions.
    return ((ALLOW_OLD, ALLOW_NEW), (COUNT_ANCHOR, COMMAND_BINDING + COUNT_ANCHOR.replace("count(*)", "count(1)")))


def _operations(upgrade):
    older = runpy.run_path(str(Path(__file__).with_name("20260909_0069_stock_reservations.py")))
    fn = older["_open_command_operations"]
    before = older["COMMAND_OPERATION_CHECK_NEW"].replace("'release')", "'release', 'pick', 'outbound')")
    fn.__globals__["COMMAND_OPERATION_CHECK_OLD"] = before
    fn.__globals__["COMMAND_OPERATION_CHECK_NEW"] = before.replace("'outbound')", "'outbound', 'shipment', 'receipt')")
    fn(op.get_bind().dialect.name, upgrade=upgrade)


def _postgresql(upgrade):
    op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
    prior = runpy.run_path(str(Path(__file__).with_name("20260912_0072_outbound_postings.py")))
    replace = prior["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
    pairs = source_changes()
    replace(signature="public.rsc_validate_material_request_approval_projection_0045(uuid)",
        expected_hash=APPROVAL_OLD_HASH if upgrade else APPROVAL_NEW_HASH,
        replacement_hash=APPROVAL_NEW_HASH if upgrade else APPROVAL_OLD_HASH,
        replacements=pairs if upgrade else tuple((b, a) for a, b in reversed(pairs)), label="fulfillment_commands_0085")
    replace(signature="public.rsc_oam_runtime_binding_ready_0044()",
        expected_hash=OLD_HASH if upgrade else NEW_HASH, replacement_hash=NEW_HASH if upgrade else OLD_HASH,
        replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),), label="fulfillment_readiness_0085")


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        _postgresql(True)
    _operations(True)


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE public.material_request_commands IN ACCESS EXCLUSIVE MODE")
        op.execute("""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.material_request_commands WHERE operation IN ('shipment', 'receipt')) THEN
                RAISE EXCEPTION '0085 downgrade blocked: fulfillment version commands exist';
            END IF;
        END $$""")
        _postgresql(False)
    elif op.get_bind().exec_driver_sql("SELECT EXISTS (SELECT 1 FROM material_request_commands WHERE operation IN ('shipment', 'receipt'))").scalar():
        raise RuntimeError("0085 downgrade blocked: fulfillment version commands exist")
    _operations(False)
