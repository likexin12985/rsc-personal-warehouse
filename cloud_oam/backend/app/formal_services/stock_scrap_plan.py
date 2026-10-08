"""Non-mutating stock preparation for original and correction-approved scrap.

Internal implementation stage, not a public preview or permission to post.
The eventual writer must repeat this proof under inventory/authority locks,
bind a dedicated scrap order and enforce the same facts at PostgreSQL COMMIT.
No account, fact, lifecycle, notification or file binding is changed here.
The existing complete opening proof takes row locks: use a short ordinary
transaction and end it promptly. PostgreSQL READ ONLY is not supported by
that dependency; replacing it requires a separately verified snapshot proof.
"""
from datetime import datetime, timezone
import json

from sqlalchemy import select

from app.inventory_models import StockAccount, StockBalance, InventorySerial, SerialCurrentPosition
from app.stock_operation_models import StockOperationLine, StockLossDisposition
from app.stock_scrap_schemas import ScrapPreview, ScrapExecute, validated_scrap_request
from . import inventory_posting as posting, inventory_query as inventory
from . import stock_loss_sources as sources, stock_loss_planning as planning
from . import stock_loss_disposition_plan as original
from .stock_loss_plan import _evidence
from .serial_ledger import rebuild_serial_states
from .stock_loss_corrections import request_authority
from .stock_loss_corrections.request_contracts import CorrectionPreview
from .stock_loss_corrections.historical_original import _bound, verify_historical_original
from .stock_loss_corrections.historical_holds import read_hold_snapshot
from .stock_loss_corrections.history_events import load_event_checked_inventory_history
from .stock_loss_corrections.history_chain import verify_inverse
from .stock_loss_corrections.chain_projection import project
from .stock_loss_corrections.reversal_stock import _custody, StockPreparation


def _fail(code, message):
    sources._fail('stock_scrap_' + code, message, 412)


def _references(db, actor, request):
    source = request.source
    if source.kind == 'original':
        current, order, line, decision = original.approved_line(db, actor=actor, request=source)
        if db.scalar(select(StockLossDisposition.id).where(StockLossDisposition.line_id == line.id)):
            _fail('already_disposed', '原报损行已有处置事实，请回查完整历史')
        inverse_id = None
    else:
        selected = CorrectionPreview(**source.model_dump(exclude={'kind'}), reason=request.execution_reason)
        refs = request_authority.references(db, actor=actor, request=selected)
        current, order, decision = refs.actor, refs.order, refs.decision
        line = db.get(StockOperationLine, refs.root.line_id, populate_existing=True)
        verify_inverse(db, reversal_id=refs.reversal.id)
        loaded = load_event_checked_inventory_history(db, root_disposition_id=refs.root.id)
        h = loaded.history
        state = project(h.basis, h.executions, h.reversals, h.decisions)
        if state.active_execution_id is not None or state.pending_reversal_id != refs.reversal.id:
            _fail('correction_not_pending', '指定冲销已被消耗，不能再次报废')
        inverse_id = refs.reversal.id
    if decision.disposition != 'scrap':
        _fail('approval_required', '指定终审必须明确批准报废，不能借用其他处置批准')
    # Scalars remain stable even if populate_existing later refreshes ORM rows.
    binding = (order.id, order.request_hash, order.plan_hash, line.id, line.quantity,
        line.stock_account_id, line.reserved_account_id, decision.id, decision.disposition,
        inverse_id, current.user_id, current.person_id, current.authorization_version)
    return current, order, line, decision, inverse_id, binding


def prepare(db, *, actor, request):
    request = validated_scrap_request(request)
    if type(request) not in (ScrapPreview, ScrapExecute):
        raise ValueError('an explicit scrap preview or execution is required')
    selection = ScrapPreview.model_validate(request.model_dump(include=set(ScrapPreview.model_fields)))
    with db.no_autoflush:
        start = _bound(db)
        current, order, line, decision, inverse_id, binding = _references(db, actor, selection)
        at = datetime.now(timezone.utc)
        source = db.get(StockAccount, line.reserved_account_id, populate_existing=True)
        original_account = db.get(StockAccount, line.stock_account_id, populate_existing=True)
        if source is None or original_account is None:
            _fail('account_missing', '原报损库存账户不完整')
        custody = _custody(db, account=source, requester_id=order.requester_id, at=at)
        accounts = {source.id: source, original_account.id: original_account}
        posting._require_active_account_masters(db, accounts)
        posting._require_no_active_hard_freezes(db, accounts, effective_at=at)
        snapshot = inventory._projection_snapshot(db)
        opening = inventory._validated_opening_evidence(db, actor=current, snapshot=snapshot,
            required_pairs={(source.owner_org_id, source.location_id)}, discover_authorized_zero_scopes=False)
        if not opening.complete:
            _fail('opening_required', '原冻结范围缺少完整期初证据')
        inventory._validate_current_projection_integrity(db, snapshot=snapshot, account_ids=set(accounts))
        holds = read_hold_snapshot(db, source_account_id=source.id, through_cursor=snapshot.ledger_cursor)
        share = next((row for row in holds.lines if row.line_id == line.id), None)
        if (holds.observed_ledger_cursor != snapshot.ledger_cursor or share is None
                or share.active_execution_id is not None or share.pending_reversal_id != inverse_id
                or share.frozen_quantity != line.quantity):
            _fail('hold_changed', '准确报损行的冻结份额已变化')
        for held in holds.lines:
            if held.root_disposition_id is not None:
                verify_historical_original(db, root_disposition_id=held.root_disposition_id)
        balance = db.get(StockBalance, source.id, populate_existing=True)
        if balance is None or balance.quantity < line.quantity:
            _fail('stock_insufficient', '原冻结账户库存不足，不得借用其他库存')
        serial_ids = tuple(sorted(share.frozen_serial_ids, key=str))
        policies, fingerprint = sources._policies(db, {source.material_id}, at)
        policy = policies[source.material_id]
        # Reuse the complete five-outcome planner: no fabricated target account
        # or changed condition may turn scrap into an ordinary stock transfer.
        outline = planning.plan_dispositions(approval_fact_id=decision.id, approval_stage='approved',
            disposition_fact_ids={line.id: line.id},
            held_lines=(planning.HeldLine(line.id, original._account(original_account), original._account(source),
                planning.TrackedQuantity(line.quantity, policy.tracking_mode, policy.quantity_scale,
                    policy.allow_fraction, serial_ids), share.frozen_quantity, share.frozen_serial_ids),),
            decisions=(planning.Disposition(line.id, planning.DispositionKind.SCRAP,
                selection.execution_reason, scrap_operation_id=line.id),))[0]
        # The line ID above is a shape-only placeholder, never a persisted
        # scrap-order ID. Actual order identity belongs to the atomic writer.
        states = rebuild_serial_states(db, serial_ids, through_cursor=snapshot.ledger_cursor)
        serial_basis = []
        for identifier in serial_ids:
            state = states.get(identifier)
            serial = db.get(InventorySerial, identifier, populate_existing=True)
            position = db.get(SerialCurrentPosition, identifier, populate_existing=True)
            if (state is None or serial is None or position is None
                    or state.stock_account_id != source.id or state.lifecycle_status != 'active'
                    or serial.lifecycle_status != state.lifecycle_status
                    or position.stock_account_id != state.stock_account_id
                    or position.last_movement_id != state.last_movement_id
                    or state.admission_movement_id is None):
                _fail('serial_changed', '报废 SN 的准确位置或生命周期不一致')
            serial_basis.append(dict(serial_id=str(identifier), previous_movement_id=str(state.last_movement_id),
                admission_movement_id=str(state.admission_movement_id),
                previous_ledger_cursor=state.ledger_cursor, lifecycle_before='active', lifecycle_after='scrapped'))
        evidence = _evidence(db, current, selection.evidence_file_ids)
        intent = selection.model_dump(mode='json')
        intent['evidence_file_ids'].sort()
        document = dict(schema_version='1.0', stage='scrap_stock_preparation_only', stock_effect='none',
            intent=intent, actor_user_id=current.user_id, actor_person_id=str(current.person_id),
            authorization_version=current.authorization_version, operation_id=str(order.id), line_id=str(line.id),
            decision_id=str(decision.id), predecessor_reversal_id=str(inverse_id) if inverse_id else None,
            source_account_id=str(source.id), target_account_id=None,
            owner_org_id=str(source.owner_org_id), custodian_person_id=str(source.custodian_person_id),
            location_id=str(source.location_id), material_id=str(source.material_id),
            lot_id=str(source.lot_id) if source.lot_id else None, source_condition=source.condition_code,
            custody_assignment_id=str(custody.id), quantity=format(line.quantity, '.3f'),
            serial_ids=list(map(str, serial_ids)), serials=serial_basis,
            movement_type=outline.movement.movement_type,
            external_boundary_code=outline.movement.external_boundary_code,
            ledger_cursor=snapshot.ledger_cursor, source_balance_version=balance.version,
            source_balance_quantity=format(balance.quantity, '.3f'), frozen_holds_before=holds.plan_basis(),
            policy_fingerprint=[list(row) for row in fingerprint], evidence=evidence)
        later = datetime.now(timezone.utc)
        if (sources._policies(db, {source.material_id}, later)[1] != fingerprint
                or _custody(db, account=source, requester_id=order.requester_id, at=later).id != custody.id
                or _evidence(db, current, selection.evidence_file_ids) != evidence):
            _fail('read_changed', '报废预检期间批准、库存、责任、证据或权限发生变化')
        posting._require_active_account_masters(db, accounts)
        posting._require_no_active_hard_freezes(db, accounts, effective_at=later)
        latest = _references(db, current, selection)
        if latest[-1] != binding or latest[0] != current or _bound(db) != start:
            _fail('read_changed', '报废预检期间批准、库存或权限发生变化')
        inventory._ensure_projection_snapshot_current(db, snapshot)
        return StockPreparation(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            sources._hash(document), at)
