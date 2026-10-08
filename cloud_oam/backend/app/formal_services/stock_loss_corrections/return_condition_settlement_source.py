"""Current, non-mutating settlement preparation; never a posting permit.

The writer must rerun this proof while holding ledger/reference locks and bind
an actual transaction permit and deferred SQL authority before any stock write.
No missing target account or balance is materialized by this reader.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select

from app.inventory_models import StockAccount, StockBalance, InventorySerial, SerialCurrentPosition, FormalMaterial
from app.return_condition_settlement_requests import validate_settlement
from app.formal_services import inventory_posting as posting, inventory_query as inventory, stock_loss_sources as sources
from app.formal_services.serial_ledger import rebuild_serial_states, SerialLedgerError
from app.formal_services.stock_loss_planning import Account, TrackedQuantity
from . import return_condition_authority as authority, return_condition_history_read as history
from . import return_condition_history as graph
from .historical_original import _bound


@dataclass(frozen=True)
class SettlementSource:
    admission: object
    basis: object
    case_state: object
    source_cursor: int
    history_hash: str
    frozen: Account
    frozen_quantity: Decimal
    frozen_version: int
    target: Account | None
    target_dimensions: tuple
    serial_fingerprint: tuple
    policy_fingerprint: tuple
    posting_allowed: bool = field(default=False, init=False)


def fail(code, message):
    sources._fail('return_condition_settlement_'+code,message,409)


def account(row):
    return Account(row.id,row.owner_org_id,row.custodian_person_id,row.location_id,
        row.material_id,row.condition_code,row.availability_bucket,row.lot_id)


def _current(db, actor, request, proved, state, snapshot):
    frozen=db.get(StockAccount,state.claim.frozen.id,populate_existing=True)
    source=proved.basis.source
    if frozen is None or account(frozen) != state.claim.frozen:
        fail('source_changed','原冻结账户维度发生变化')
    dimensions=dict(owner_org_id=source.owner_org_id,custodian_person_id=source.custodian_person_id,
        location_id=source.location_id,material_id=source.material_id,lot_id=source.lot_id,
        condition_code='damaged' if request.action=='execute' else source.condition,availability_bucket='available')
    targets=tuple(db.scalars(select(StockAccount).filter_by(**dimensions).limit(2)
        .execution_options(populate_existing=True)))
    if len(targets)>1 or (request.action=='release' and (len(targets)!=1 or targets[0].id!=source.id)):
        fail('target_changed','释放必须返回准确原账户，目标维度必须唯一')
    target=targets[0] if targets else None
    accounts={frozen.id:frozen}
    if target is not None: accounts[target.id]=target
    at=datetime.now(timezone.utc)
    posting._require_active_account_masters(db,accounts)
    posting._require_no_active_hard_freezes(db,accounts,effective_at=at)
    opening=inventory._validated_opening_evidence(db,actor=actor,snapshot=snapshot,
        required_pairs={(source.owner_org_id,source.location_id)},discover_authorized_zero_scopes=False)
    if not opening.complete:
        fail('opening_required','原冻结区域缺少可信期初证明')
    inventory._validate_current_projection_integrity(db,snapshot=snapshot,account_ids=set(accounts))
    balance=db.get(StockBalance,frozen.id,populate_existing=True)
    if balance is None or balance.quantity<state.claim.selected.quantity:
        fail('stock_changed','当前冻结数量不足，不能执行或释放')
    policies,fingerprint=sources._policies(db,{source.material_id},at)
    policy=policies[source.material_id]; selected=state.claim.selected
    if (policy.tracking_mode,policy.quantity_scale,policy.allow_fraction)!=(selected.tracking_mode,selected.quantity_scale,selected.allow_fraction):
        fail('policy_changed','追踪策略与原冻结份额不一致，须先核验策略变更')
    TrackedQuantity(selected.quantity,policy.tracking_mode,policy.quantity_scale,policy.allow_fraction,selected.serial_ids).validate(source.lot_id)
    scans={item.serial_id:item for item in request.serial_verifications}
    if set(scans)!=set(selected.serial_ids):
        fail('serial_selection_changed','必须逐件核对全部原冻结SN，不能替换或部分结算')
    material=db.get(FormalMaterial,source.material_id,populate_existing=True)
    if material is None:
        fail('material_changed','原冻结物料不存在')
    try:
        states=rebuild_serial_states(db,selected.serial_ids,through_cursor=snapshot.ledger_cursor) if selected.serial_ids else {}
    except SerialLedgerError:
        inventory._invalid_current_projection()
    serials=[]
    for identifier in sorted(selected.serial_ids,key=str):
        serial=db.get(InventorySerial,identifier,populate_existing=True)
        position=db.get(SerialCurrentPosition,identifier,populate_existing=True);actual=states.get(identifier);scan=scans[identifier]
        if (serial is None or position is None or actual is None
                or serial.material_id!=source.material_id or serial.lot_id!=source.lot_id
                or actual.stock_account_id!=frozen.id or actual.lifecycle_status!='active'
                or serial.lifecycle_status!=actual.lifecycle_status
                or position.stock_account_id!=actual.stock_account_id or position.last_movement_id!=actual.last_movement_id):
            fail('serial_position_changed','SN已不在准确冻结账户或当前投影不一致')
        if (scan.sku_code,scan.serial_no,scan.qr_code)!=(material.sku_code,serial.serial_no,serial.qr_code):
            fail('serial_scan_changed','物料号、SN和二维码必须与当前准确实物记录一致')
        serials.append((identifier,serial.serial_no,serial.qr_code,position.stock_account_id,position.last_movement_id,serial.lifecycle_status))
    return (account(frozen),balance.quantity,balance.version,account(target) if target else None,
        tuple(sorted(dimensions.items())),tuple(serials),fingerprint)


def inspect_settlement_source(db, *, actor, request):
    request=validate_settlement(request)
    with db.no_autoflush:
        admitted=authority.authorize_action(db,actor=actor,case_id=request.case_id,
            expected_event_id=request.expected_event_id,kind=request.action)
        if admitted.previous_request_hash!=request.expected_event_hash:
            fail('decision_changed','审批事件摘要已变化，请重新核对原单')
        before=_bound(db)
        proved=history.read(db,actor=admitted.actor,inbound_line_id=admitted.inbound_line_id)
        matches=[state for state in proved.graph.projection.cases if state.case_id==request.case_id]
        if len(matches)!=1 or matches[0].terminal_decision_id!=request.expected_event_id:
            fail('decision_changed','必须绑定完整历史中的准确终审或撤销事件')
        state=matches[0]
        allowed=('approved',) if request.action=='execute' else ('rejected_pending_release','cancelled_pending_release')
        if state.status not in allowed:
            fail('stage_changed','当前纠正状态不允许执行或释放')
        snapshot=inventory._projection_snapshot(db)
        if snapshot.ledger_cursor!=before[0]: graph.changed()
        facts=_current(db,admitted.actor,request,proved,state,snapshot)
        latest=authority.authorize_action(db,actor=admitted.actor,case_id=request.case_id,
            expected_event_id=request.expected_event_id,kind=request.action)
        _,fresh_hash=graph.capture(db,admitted.inbound_line_id)
        fresh=_current(db,latest.actor,request,proved,state,snapshot)
        final=authority.authorize_action(db,actor=latest.actor,case_id=request.case_id,
            expected_event_id=request.expected_event_id,kind=request.action)
        if latest!=admitted or final!=admitted or fresh!=facts or fresh_hash!=proved.graph.fingerprint or _bound(db)!=before:
            graph.changed()
        inventory._ensure_projection_snapshot_current(db,snapshot)
        return SettlementSource(admitted,proved.basis,state,snapshot.ledger_cursor,proved.graph.fingerprint,*facts)
