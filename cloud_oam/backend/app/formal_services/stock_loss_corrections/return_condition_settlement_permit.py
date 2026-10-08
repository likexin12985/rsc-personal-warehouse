"""Private exact execute/release permit, bound to one active DB transaction.

This candidate is not activated. The dedicated service must hold the ledger and
principal/reference locks; native deferred source/authority/account/effect
checks remain mandatory before exposing settlement writes.
"""
from copy import deepcopy
from dataclasses import dataclass
from sqlalchemy import select

from app.inventory_models import StockAccount, StockBalance, InventorySerial, SerialCurrentPosition, FormalMaterial, InventoryMovement
from app.return_condition_settlement_requests import validate_settlement
from app.return_condition_schema import TRANSITIONS
from app.formal_services import inventory_posting as posting, inventory_query as inventory, stock_loss_sources as sources
from app.formal_services.serial_ledger import rebuild_serial_states, SerialLedgerError
from app.formal_services.work_order_query import _aware
from . import return_condition_authority as authority, return_condition_history_read as history
from . import return_condition_history as graph, return_condition_identity as identity
from .return_condition_posting_authority import KEY
from .return_condition_settlement_source import SettlementSource, account
from .return_condition_planning import settlement_outline
from .return_condition_submission import _files


@dataclass(frozen=True)
class SettlementPermit:
    transaction: object
    request: object
    prepared: SettlementSource
    command: object
    event: dict
    key_hash: str
    request_hash: str


def invalid():
    posting._fail('condition_settlement_authority_invalid','precondition_failed',
        '执行或释放必须绑定本次事务、准确审批和原冻结份额')


def issue(db, *, request, prepared, command, event, key_hash, request_hash):
    request=validate_settlement(request)
    transaction=db.get_nested_transaction() or db.get_transaction()
    if KEY in db.info or type(prepared) is not SettlementSource or transaction is None or not transaction.is_active:
        invalid()
    permit=SettlementPermit(transaction,request,prepared,command,deepcopy(event),key_hash,request_hash)
    db.info[KEY]=permit
    return permit


def require(db, *, actor, command, permit, permission_resource, permission_action,
        reversed_transaction_id, opening_task_id, current_cursor, idempotency_key_hash,
        request_hash, request_reference, occurred_at, event_suffix,
        receipt_authority, scrap_authority, scrap_recovery_authority):
    if (type(permit) is not SettlementPermit or db.info.get(KEY) is not permit
            or (db.get_nested_transaction() or db.get_transaction()) is not permit.transaction
            or permit.transaction is None or not permit.transaction.is_active):
        invalid()
    request=validate_settlement(permit.request);prepared=permit.prepared;event=permit.event
    if (type(prepared) is not SettlementSource or actor!=prepared.admission.actor or command!=permit.command
            or permission_resource!='stock_operation' or permission_action!=authority.ACTIONS[request.action]
            or command.source_document_type!='stock_condition_event' or command.source_document_id!=str(event['id'])
            or command.movement_type!=('status_change' if request.action=='execute' else 'unfreeze')
            or event['kind']!=request.action or event['created_at']!=occurred_at or command.effective_at!=occurred_at
            or current_cursor!=prepared.source_cursor or idempotency_key_hash!=permit.key_hash
            or request_hash!=permit.request_hash or request_reference!=posting._request_reference(request.request_id)
            or event_suffix!='posted' or reversed_transaction_id is not None or opening_task_id is not None
            or any(v is not None for v in (receipt_authority,scrap_authority,scrap_recovery_authority))
            or len(command.movements)!=1):
        invalid()
    current=authority.authorize_action(db,actor=actor,case_id=request.case_id,
        expected_event_id=request.expected_event_id,kind=request.action)
    if current!=prepared.admission or current.previous_request_hash!=request.expected_event_hash:
        invalid()
    proved=history.read(db,actor=current.actor,inbound_line_id=current.inbound_line_id)
    states=[s for s in proved.graph.projection.cases if s.case_id==request.case_id]
    if (proved.basis!=prepared.basis or proved.graph.fingerprint!=prepared.history_hash
            or proved.observed_ledger_cursor!=current_cursor or states!=[prepared.case_state]
            or prepared.case_state.terminal_decision_id!=request.expected_event_id):
        invalid()
    state=states[0];chosen=state.claim.selected
    cases=graph.tables()['stock_condition_cases'];events=graph.tables()['stock_condition_events']
    case=db.execute(select(cases).where(cases.c.id==request.case_id)).mappings().one()
    previous=db.execute(select(events).where(events.c.id==request.expected_event_id)).mappings().one()
    transitions=[after for kind,before,after in TRANSITIONS
        if kind==request.action and before==state.status]
    if (len(transitions)!=1 or previous['case_id']!=case['id']
            or previous['to_state']!=state.status or previous['request_hash']!=request.expected_event_hash):
        invalid()
    expected=dict(kind=request.action,case_id=case['id'],submit_event_id=case['submit_event_id'],
        inbound_line_id=case['inbound_line_id'],source_account_id=case['source_account_id'],
        frozen_account_id=case['frozen_account_id'],quantity=chosen.quantity,from_account_id=state.claim.frozen.id,
        previous_event_id=previous['id'],previous_sequence=previous['event_sequence'],
        decision_event_id=previous['id'],decision_kind=previous['kind'],from_state=previous['to_state'],
        to_state=transitions[0],posting_transaction_id=None,posting_movement_id=None,
        event_sequence=len(proved.graph.event_ids)+1,actor_user_id=current.actor.user_id,
        actor_person_id=current.actor.person_id,authorization_version=current.actor.authorization_version,
        reason=request.reason,request_id=request.request_id,
        idempotency_key_hash=posting._storage_hash('stock-condition:'+request.idempotency_key))
    if any(k not in event or event[k]!=v for k,v in expected.items()): invalid()
    evidence=_files(db,current.actor,request,occurred_at)
    binding=identity.identity(case,event,serial_ids=chosen.serial_ids,
        evidence=[dict(file_id=f.file_id,metadata_sha256=f.metadata_sha256) for f in evidence],
        previous_request_hash=request.expected_event_hash)
    if (event.get('plan_jsonb'),event.get('plan_hash'),event.get('command_jsonb'),event.get('request_hash'))!=(
            binding.plan,binding.plan_hash,binding.command,binding.request_hash): invalid()
    move=command.movements[0]
    held=db.get(StockAccount,move.from_account_id,populate_existing=True)
    target=db.get(StockAccount,move.to_account_id,populate_existing=True)
    if held is None or target is None or account(held)!=prepared.frozen:
        invalid()
    if any(getattr(target,k)!=v for k,v in prepared.target_dimensions): invalid()
    if prepared.target is not None:
        if account(target)!=prepared.target: invalid()
    else:
        # Only a newly materialized execution target may lack a current balance.
        # Native account admission must also demand its exact committed event.
        if (request.action!='execute' or _aware(target.created_at)!=occurred_at
                or _aware(target.updated_at)!=occurred_at
                or db.get(StockBalance,target.id,populate_existing=True) is not None
                or db.scalar(select(InventoryMovement.id).where(
                    (InventoryMovement.from_account_id==target.id)|(InventoryMovement.to_account_id==target.id)).limit(1)) is not None):
            invalid()
    outline=settlement_outline(proved.basis,claim=state.claim,target=account(target),execute=request.action=='execute')
    if ((move.from_account_id,move.to_account_id,move.quantity,tuple(move.serial_ids),command.movement_type)!=
            (outline.from_account_id,outline.to_account_id,outline.quantity,outline.serial_ids,outline.movement_type)
            or event['to_account_id']!=target.id or event['movement_type']!=outline.movement_type):
        invalid()
    expected_command,expected_key,expected_hash=identity.inventory_identity(event,chosen.serial_ids)
    if (command,idempotency_key_hash,request_hash)!=(expected_command,expected_key,expected_hash): invalid()
    snapshot=inventory._projection_snapshot(db)
    if snapshot.ledger_cursor!=current_cursor: invalid()
    inventory._validate_current_projection_integrity(db,snapshot=snapshot,account_ids={held.id})
    balance=db.get(StockBalance,held.id,populate_existing=True)
    if (balance is None or balance.quantity<chosen.quantity
            or (balance.quantity,balance.version)!=(prepared.frozen_quantity,prepared.frozen_version)): invalid()
    policies,fingerprint=sources._policies(db,{held.material_id},occurred_at)
    if fingerprint!=prepared.policy_fingerprint: invalid()
    material=db.get(FormalMaterial,held.material_id,populate_existing=True)
    scans={s.serial_id:s for s in request.serial_verifications}
    if set(scans)!=set(chosen.serial_ids) or material is None: invalid()
    try:
        rebuilt=rebuild_serial_states(db,chosen.serial_ids,through_cursor=current_cursor) if chosen.serial_ids else {}
    except SerialLedgerError:
        invalid()
    observed=[]
    for serial_id in sorted(chosen.serial_ids,key=str):
        serial=db.get(InventorySerial,serial_id,populate_existing=True)
        position=db.get(SerialCurrentPosition,serial_id,populate_existing=True);actual=rebuilt.get(serial_id);scan=scans[serial_id]
        if (serial is None or position is None or actual is None or actual.stock_account_id!=held.id
                or actual.lifecycle_status!='active' or serial.lifecycle_status!=actual.lifecycle_status
                or (serial.material_id,serial.lot_id)!=(held.material_id,held.lot_id)
                or (position.stock_account_id,position.last_movement_id)!=(actual.stock_account_id,actual.last_movement_id)
                or (scan.sku_code,scan.serial_no,scan.qr_code)!=(material.sku_code,serial.serial_no,serial.qr_code)):
            invalid()
        observed.append((serial_id,serial.serial_no,serial.qr_code,position.stock_account_id,position.last_movement_id,serial.lifecycle_status))
    if tuple(observed)!=prepared.serial_fingerprint: invalid()
    fresh=authority.authorize_action(db,actor=current.actor,case_id=request.case_id,
        expected_event_id=request.expected_event_id,kind=request.action)
    if fresh!=current: invalid()
    inventory._ensure_projection_snapshot_current(db,snapshot)
    return {held.id:held,target.id:target}
