"""Prepare an independently approved exact scrap inverse, without stock writes."""
from datetime import datetime, timezone
import json
from uuid import UUID
from sqlalchemy import select
from app.inventory_models import InventoryTransaction, StockBalance
from app.stock_scrap_recovery_schemas import ScrapRecoveryPreview, ScrapRecoveryExecute, validated_recovery_request
from app.formal_services import inventory_posting as posting, inventory_query as inventory, stock_loss_sources as sources
from app.formal_services.serial_ledger import rebuild_serial_states
from app.formal_services.stock_loss_corrections.historical_original import _bound
from app.formal_services.stock_loss_corrections.historical_holds import read_hold_snapshot
from app.formal_services.stock_loss_corrections.reversal_stock import _custody, StockPreparation
from app.formal_services.work_order_query import _aware
from . import recovery_authority as authority, recovery_facts as facts
from .tables import tables


def approval(db, request, source):
    table = tables()[facts.NAMES['apply']]
    row = db.execute(select(table).where(table.c.id == request.recovery_request_id)).mappings().one_or_none()
    if row is None or row['scrap_line_id'] != source['line']['id'] or row['request_hash'] != request.expected_request_hash:
        authority.fail('request_changed', '恢复必须绑定准确独立找回申请')
    stage, region, final, _ = facts.verify_history(db, application=row, source=source)
    if (stage != 'approved_pending_execution' or final is None or final['id'] != request.headquarters_review_id
            or final['request_hash'] != request.expected_headquarters_hash):
        authority.fail('approval_required', '找回恢复需要准确的独立总部批准')
    return row, region, final


def serial_basis(db, source, *, cursor):
    child, target = source['line'], source['account']
    tx = db.get(InventoryTransaction, child['posting_transaction_id'], populate_existing=True)
    facts.need(tx is not None and tx.ledger_cursor <= cursor)
    table = tables()['stock_scrap_serials']
    serials = db.execute(select(table).where(table.c.scrap_line_id == child['id']).order_by(table.c.serial_id)).mappings().all()
    ids = tuple(r['serial_id'] for r in serials)
    now = rebuild_serial_states(db, ids, through_cursor=cursor)
    prior = rebuild_serial_states(db, ids, through_cursor=tx.ledger_cursor-1)
    result = []
    for r in serials:
        current, old = now[r['serial_id']], prior[r['serial_id']]
        facts.need(current.lifecycle_status == 'scrapped' and current.stock_account_id is None
            and current.last_movement_id == child['posting_movement_id'] and current.owner_org_id == target.owner_org_id
            and old.lifecycle_status == 'active' and old.stock_account_id == target.id
            and old.last_movement_id == r['previous_movement_id']
            and old.admission_movement_id == current.admission_movement_id == r['admission_movement_id'])
        result.append(dict(serial_id=str(r['serial_id']), original_movement_id=str(current.last_movement_id),
            previous_movement_id=str(old.last_movement_id), previous_ledger_cursor=old.ledger_cursor,
            admission_movement_id=str(old.admission_movement_id), lifecycle_before='scrapped', lifecycle_after='active'))
    return tx, result, prior


def document(*, actor, selection, source, custody, cursor, balance_quantity, balance_version, holds, original_tx, serials, fingerprint):
    target, root, fact = source['account'], source['root'], source['fact']
    return dict(schema_version='1.0', stage='scrap_recovery_stock_preparation_only', stock_effect='none',
        intent=selection.model_dump(mode='json'), actor_user_id=actor.user_id, actor_person_id=str(actor.person_id),
        authorization_version=actor.authorization_version, root_disposition_id=str(root.id),
        original_execution_id=str(fact.id), original_transaction_id=str(original_tx.id),
        original_movement_id=str(fact.posting_movement_id), original_ledger_cursor=original_tx.ledger_cursor,
        source_account_id=None, target_account_id=str(target.id), quantity=format(fact.quantity, '.3f'),
        custody_assignment_id=str(custody.id), owner_org_id=str(target.owner_org_id),
        custodian_person_id=str(target.custodian_person_id), location_id=str(target.location_id),
        material_id=str(target.material_id), lot_id=str(target.lot_id) if target.lot_id else None,
        target_condition=target.condition_code, ledger_cursor=cursor,
        target_balance_quantity=format(balance_quantity, '.3f'), target_balance_version=balance_version,
        frozen_holds_before=holds.plan_basis(), restored_line_id=str(root.line_id),
        restored_frozen_quantity=format(fact.quantity, '.3f'), serials=serials,
        serial_ids=[s['serial_id'] for s in serials], policy_fingerprint=fingerprint)


def validate_tracking(source, serials, policies, at):
    target = source['account']
    command = posting.InventoryPostingCommand(transaction_no='scrap-recovery-preparation', movement_type='reversal',
        source_document_type='stock_loss_disposition_reversal', source_document_id=str(source['root'].id),
        posting_key='scrap-recovery-preparation', effective_at=at, movements=(posting.InventoryMovementCommand(
            None, target.id, source['fact'].quantity, tuple(UUID(s['serial_id']) for s in serials), 'stock_operation_scrap'),))
    posting._validate_tracking_rules(command, {target.id: target}, policies)


def prepare(db, *, actor, request):
    request = validated_recovery_request(request)
    if type(request) not in (ScrapRecoveryPreview, ScrapRecoveryExecute):
        raise ValueError('an exact found-stock recovery selection is required')
    selection = ScrapRecoveryPreview.model_validate(request.model_dump(include=set(ScrapRecoveryPreview.model_fields)))
    with db.no_autoflush:
        start = _bound(db)
        source = authority.load_source(db, selection.source)
        current = authority.authorize(db, actor=actor, source=source, stage='execute')
        _, _, final = approval(db, selection, source)
        authority.require_current_scrap(db, source)
        at = datetime.now(timezone.utc)
        facts.need(at > _aware(final['created_at']))
        target = source['account']
        custody = _custody(db, account=target, requester_id=source['order'].requester_id, at=at)
        accounts = {target.id: target}
        posting._require_active_account_masters(db, accounts)
        posting._require_no_active_hard_freezes(db, accounts, effective_at=at)
        snapshot = inventory._projection_snapshot(db)
        opening = inventory._validated_opening_evidence(db, actor=current, snapshot=snapshot,
            required_pairs={(target.owner_org_id, target.location_id)}, discover_authorized_zero_scopes=False)
        if not opening.complete:
            authority.fail('opening_required', '找回目标缺少可信期初证据')
        inventory._validate_current_projection_integrity(db, snapshot=snapshot, account_ids=set(accounts))
        balance = db.get(StockBalance, target.id, populate_existing=True)
        if balance is None:
            inventory._invalid_current_projection()
        holds = read_hold_snapshot(db, source_account_id=target.id, through_cursor=snapshot.ledger_cursor)
        held = next((r for r in holds.lines if r.line_id == source['root'].line_id), None)
        facts.need(holds.observed_ledger_cursor == snapshot.ledger_cursor and held is not None
            and held.active_execution_id == source['fact'].id and held.pending_reversal_id is None
            and held.frozen_quantity == 0 and balance.quantity == holds.balance_quantity and balance.version == holds.balance_version)
        original_tx, serials, _ = serial_basis(db, source, cursor=snapshot.ledger_cursor)
        policies, fingerprint = sources._policies(db, {target.material_id}, at)
        validate_tracking(source, serials, policies, at)
        data = document(actor=current, selection=selection, source=source, custody=custody, cursor=snapshot.ledger_cursor,
            balance_quantity=balance.quantity, balance_version=balance.version, holds=holds,
            original_tx=original_tx, serials=serials, fingerprint=[list(r) for r in fingerprint])
        later = datetime.now(timezone.utc)
        facts.need(_custody(db, account=target, requester_id=source['order'].requester_id, at=later).id == custody.id
            and sources._policies(db, {target.material_id}, later)[1] == fingerprint and _bound(db) == start)
        approval(db, selection, source)
        authority.authorize(db, actor=current, source=source, stage='execute')
        posting._require_active_account_masters(db, accounts)
        posting._require_no_active_hard_freezes(db, accounts, effective_at=later)
        inventory._ensure_projection_snapshot_current(db, snapshot)
        return StockPreparation(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')), sources._hash(data), at)
