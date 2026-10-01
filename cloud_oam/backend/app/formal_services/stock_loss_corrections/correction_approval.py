"""Distinct immutable HQ approval after a proved loss inverse.

Approval moves no stock and does not reuse the original loss decision. The
caller owns commit/rollback. Native COMMIT authority and execution selection
constraints are still required before production activation.
"""
from . import correction_seal_facts

from datetime import datetime, timezone
from uuid import uuid4

from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from .chain_projection import project
from .correction_models import StockLossCorrectionDecision as Decision
from .request_contracts import CorrectionApprove, original_request, validate, require_original_row
from .history_chain import verify_inverse as verify_original_inverse
from .historical_holds import read_hold_snapshot
from .history_events import load_event_checked_inventory_history
from . import business_events
from . import inverse_recovery
from . import correction_recovery
from . import request_authority
from . import sealed_inverse


def approve(db, *, actor, request):
    request = validate(request)
    if type(request) is not CorrectionApprove:
        raise ValueError('an exact independent correction approval is required')
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    refs = request_authority.references(db, actor=actor, request=request)
    current, root, order, inverse = refs.actor, refs.root, refs.order, refs.reversal
    binding = original_request(actor=current, request=request)
    keys = sealed_inverse._keys(request)
    sealed_inverse.require_unsealed(db, actor=current, request=request)
    correction_seal_facts.require_unsealed(db, actor=current, request=request)
    existing = correction_recovery._coordinates(db, actor=current, request=request, keys=keys)
    if existing is not None:
        require_original_row(row=existing, actor=current, request=request)
        sources._fail('loss_correction_request_requires_recovery', '原请求已有事实，请回读完整原请求', 409)
    inverse_recovery._evidence(db, actor=current, request=request, keys=keys, row=None)
    verify_original_inverse(db, reversal_id=inverse.id)
    loaded = load_event_checked_inventory_history(db, root_disposition_id=root.id)
    h = loaded.history; state = project(h.basis, h.executions, h.reversals, h.decisions)
    if state.pending_reversal_id != inverse.id or state.active_execution_id is not None:
        sources._fail('loss_correction_inverse_not_pending', '准确冲销已被后续执行消耗，请核验当前处置', 409)
    holds = read_hold_snapshot(db, source_account_id=root.source_account_id)
    share = next((line for line in holds.lines if line.line_id == root.line_id), None)
    if (share is None or share.pending_reversal_id != inverse.id or share.frozen_quantity != root.quantity
            or share.frozen_serial_ids != h.basis.serial_ids):
        sources._fail('loss_correction_frozen_share_changed', '原冲销的冻结数量或SN份额不完整', 412)
    current = request_authority.references(db, actor=current, request=request).actor
    row = Decision(id=uuid4(), root_disposition_id=root.id, actor_user_id=current.user_id,
        actor_person_id=current.person_id, authorization_version=current.authorization_version,
        request_id=request.request_id, idempotency_key_hash=binding.key_hash, request_hash=binding.request_hash,
        reason=request.reason, command_jsonb=binding.document, reversal_id=inverse.id,
        expected_reversal_hash=inverse.request_hash, disposition=request.disposition,
        created_at=datetime.now(timezone.utc))
    db.add(row); db.flush()
    business_events.record(db, row=row, root=root, order=order)
    request_authority.authorize(db, actor=current, order=order, action='approve_loss_correction')
    verified = load_event_checked_inventory_history(db, root_disposition_id=root.id)
    if not any(fact.id == row.id for fact in verified.history.decisions):
        sources._fail('loss_correction_approval_evidence_invalid', '独立纠正批准未完整回读，事务必须回滚', 503)
    return business_events.payload(row, root=root, order=order)
