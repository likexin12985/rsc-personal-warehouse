"""Bind the recipient entrypoint to its own immutable receipt and posting."""
from pathlib import Path
import runpy

from alembic import op

revision = "20260929_0089"
down_revision = "20260928_0088"
branch_labels = depends_on = None
OLD_HASH = "f96331a32393d71f3d9613936232ce3486981ad4ff78ed783d355e491eb11c99"
NEW_HASH = "3c677c5264803e808fe4765dbb340df946e4768a78f2a986cb0f2a296ca8e196"
APPROVAL_OLD_HASH = "593668f5e6b008d0947a24a017fb9172c5c29dca196a1a5fb6fbe6a5bd597f52"
APPROVAL_NEW_HASH = "9b62535f2529a416a338ce9dee244850a94c0da23a1422c3f01ee9727192f9ff"
OLD_REFERENCE = """            OR command.request_reference <> '/api/v1/material-requests/' || checked_request_id::text || '/inbound-orders/' || inbound.id::text || '/post'
"""
NEW_REFERENCE = """            OR NOT (
                command.request_reference = '/api/v1/material-requests/' || checked_request_id::text || '/inbound-orders/' || inbound.id::text || '/post'
                OR (command.request_reference = '/api/v1/material-requests/' || checked_request_id::text || '/my-inbounds'
                    AND command.actor_person_id = receipt.receiver_person_id
                    AND EXISTS (SELECT 1 FROM public.audit_events accepted
                        JOIN public.material_request_commands confirmation
                          ON confirmation.idempotency_key_hash = receipt.idempotency_key_hash
                        WHERE accepted.stream_key = 'material_request' AND accepted.action = 'my_receipt_registered'
                          AND accepted.aggregate_type = 'receipt' AND accepted.aggregate_id = receipt.id::text
                          AND accepted.actor_user_id = command.actor_user_id
                          AND accepted.after_jsonb->>'request_id' = checked_request_id::text
                          AND accepted.after_jsonb->>'request_hash' = receipt.request_hash
                          AND confirmation.request_id = checked_request_id AND confirmation.operation = 'receipt'
                          AND confirmation.actor_user_id = command.actor_user_id
                          AND confirmation.actor_person_id = command.actor_person_id
                          AND confirmation.request_hash = receipt.request_hash
                          AND confirmation.request_reference = '/api/v1/material-requests/' || checked_request_id::text || '/my-receipts'
                          AND confirmation.target_version < command.target_version)
                    AND (SELECT count(*) FROM public.audit_events own_posting
                        WHERE own_posting.stream_key = 'material_request' AND own_posting.action = 'my_inbound_posted'
                          AND own_posting.aggregate_type = 'inbound_order' AND own_posting.aggregate_id = inbound.id::text
                          AND own_posting.actor_user_id = command.actor_user_id
                          AND own_posting.occurred_at >= tx.created_at
                          AND own_posting.after_jsonb->>'request_id' = checked_request_id::text
                          AND own_posting.after_jsonb->>'inventory_transaction_id' = tx.id::text
                          AND own_posting.after_jsonb->>'request_hash' ~ '^[0-9a-f]{64}$'
                          AND own_posting.after_jsonb->'command' = jsonb_build_object(
                              'expected_request_version', command.target_version - 1,
                              'receipt_id', receipt.id::text, 'receipt_request_hash', receipt.request_hash)) = 1
                )
            )
"""
OWN_FACTS = """EXISTS (SELECT 1 FROM material_request_commands WHERE operation = 'personal_inbound'
    AND request_reference LIKE '/api/v1/material-requests/%/my-inbounds')
    OR EXISTS (SELECT 1 FROM audit_events WHERE action = 'my_inbound_posted')"""


def _transition(upgrade):
    db = op.get_bind()
    if db.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError("0089 supports only PostgreSQL and SQLite")
    prior = runpy.run_path(str(Path(__file__).with_name("20260927_0087_inbound_fulfillment_boundary.py")))
    prior["_begin_sqlite"]()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE public.material_request_commands, public.audit_events IN SHARE ROW EXCLUSIVE MODE")
    prior["_preflight"](OWN_FACTS, "0089 transition blocked: personal inbound commands require reviewed migration")
    if db.dialect.name == "postgresql":
        source = runpy.run_path(str(Path(__file__).with_name("20260912_0072_outbound_postings.py")))
        replace = source["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
        replace(signature="public.rsc_validate_material_request_approval_projection_0045(uuid)",
            expected_hash=APPROVAL_OLD_HASH if upgrade else APPROVAL_NEW_HASH,
            replacement_hash=APPROVAL_NEW_HASH if upgrade else APPROVAL_OLD_HASH,
            replacements=((OLD_REFERENCE, NEW_REFERENCE),) if upgrade else ((NEW_REFERENCE, OLD_REFERENCE),),
            label="personal_inbound_approval_0089")
        replace(signature="public.rsc_oam_runtime_binding_ready_0044()",
            expected_hash=OLD_HASH if upgrade else NEW_HASH, replacement_hash=NEW_HASH if upgrade else OLD_HASH,
            replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),),
            label="personal_inbound_readiness_0089")


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
