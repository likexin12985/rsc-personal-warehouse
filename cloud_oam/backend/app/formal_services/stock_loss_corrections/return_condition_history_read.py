"""Scoped historical condition outcomes, independent of today's custody/write rights.

No HTTP route or replay is installed. A successful read proves the retained
source and condition graph, not current stock availability or physical evidence.
"""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.inventory_models import StockAccount, InventoryTransaction, InventoryMovement, CustodyAssignment, InventorySerial
from app.stock_operation_models import StockOperationReturnInboundLine, StockOperationReturnInbound, StockLossDisposition, StockOperationShipment
from app.formal_services import inventory_posting as posting, inventory_query as inventory, stock_loss_sources as sources
from app.formal_services.stock_loss_planning import Account, TrackedQuantity
from app.formal_services.work_order_query import _aware
from . import return_history, return_condition_history as graph
from .return_condition_contracts import Basis
from .historical_original import _bound


def _scope(db, actor, inbound_line_id):
    current=posting._require_current_actor(db,posting._validate_supplied_actor(actor))
    inventory._require_inventory_read(db,current)
    line=db.get(StockOperationReturnInboundLine,inbound_line_id,populate_existing=True)
    source=db.get(StockAccount,line.target_account_id,populate_existing=True) if line else None
    if source is None:
        sources._fail('return_condition_history_not_found','准确原入库明细不存在',404)
    for resource in ('inventory','stock_operation'):
        if not current.allows(db,resource,'read',target_scope_type='organization',target_scope_id=str(source.owner_org_id)):
            sources._fail('return_condition_history_forbidden','没有该原入库区域的当前查看权限',403)
    return current,line,source


def _basis(db,line,source,groups):
    header=db.get(StockOperationReturnInbound,line.inbound_id,populate_existing=True)
    graph._need(header is not None and header.status=='posted')
    roots=tuple(db.scalars(select(StockLossDisposition).join(StockOperationShipment,
        StockOperationShipment.operation_id==StockLossDisposition.return_operation_id).where(
        StockOperationShipment.id==header.shipment_id,StockLossDisposition.disposition=='return_to_region')
        .limit(2).execution_options(populate_existing=True)))
    graph._need(len(roots)==1)
    _,fingerprint,_,issues=return_history._verified_graph(db,roots[0])
    selected=[r for r in issues if r.inbound_line_id==line.id]
    graph._need(len(selected)==1)
    issue=selected[0]
    graph._need(issue.inbound_id==header.id and issue.receipt_line_id==line.receipt_line_id
        and issue.original_target_account_id==source.id and source.material_id==line.material_id
        and source.lot_id==line.lot_id and source.condition_code==issue.recorded_condition
        and source.availability_bucket=='available' and source.location_id==header.target_location_id
        and source.custodian_person_id==header.operator_person_id)
    tx=db.get(InventoryTransaction,header.posting_transaction_id,populate_existing=True)
    moves=tuple(db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id==header.posting_transaction_id,
        InventoryMovement.line_no==line.line_no).limit(2).execution_options(populate_existing=True)))
    graph._need(tx is not None and len(moves)==1)
    move=moves[0]
    graph._need(move.from_account_id==line.source_account_id and move.to_account_id==source.id and move.quantity==line.accepted_qty)
    events=groups['stock_condition_events'];cases=groups['stock_condition_cases']
    first=min((e for e in events if e['kind']=='submit'),key=lambda e:e['event_sequence'],default=None)
    # Existing cases use their actual historical policy, not today's mode.
    at=_aware(first['created_at'] if first else tx.effective_at)
    policies,_=sources._policies(db,{source.material_id},at);policy=policies[source.material_id]
    basis=Basis(roots[0].id,line.id,tx.id,move.id,tx.ledger_cursor,
        Account(source.id,source.owner_org_id,source.custodian_person_id,source.location_id,source.material_id,
            source.condition_code,source.availability_bucket,source.lot_id),
        TrackedQuantity(issue.affected_quantity,policy.tracking_mode,policy.quantity_scale,policy.allow_fraction,issue.affected_serial_ids))
    observed=[]
    for case in cases:
        submits=[e for e in events if e['id']==case['submit_event_id'] and e['case_id']==case['id'] and e['kind']=='submit']
        graph._need(len(submits)==1 and case['inbound_id']==header.id)
        event=submits[0];at=_aware(event['created_at']);doc=case['source_jsonb']
        graph._need(at>=_aware(header.created_at) and at>=_aware(tx.posted_at))
        graph._need(doc['receipt_line_id']==str(line.receipt_line_id))
        graph._need((doc['actor_user_id'],doc['actor_person_id'],doc['authorization_version'])==
                   (event['actor_user_id'],str(event['actor_person_id']),event['authorization_version']))
        policies,policy_rows=sources._policies(db,{source.material_id},at);p=policies[source.material_id]
        graph._need((case['tracking_mode'],case['quantity_scale'],case['allow_fraction'])==
                   (p.tracking_mode,p.quantity_scale,p.allow_fraction))
        # Effective-to may legitimately close later. Compare stable identity
        # and dimensions, then require actual validity at the historical event.
        graph._need(len(doc['policy_fingerprint'])==1 and tuple(doc['policy_fingerprint'][0][:6])==policy_rows[0][:6])
        custody=db.get(CustodyAssignment,case['custody_assignment_id'],populate_existing=True)
        graph._need(custody is not None and custody.location_id==source.location_id
            and custody.custodian_person_id==source.custodian_person_id and _aware(custody.valid_from)<=at
            and (custody.valid_to is None or at<_aware(custody.valid_to)))
        graph._need(doc['custody_valid_from']==_aware(custody.valid_from).isoformat())
        saved={UUID(s['serial_id']):s for s in doc['serials']}
        graph._need(set(saved)==set(issue.affected_serial_ids))
        for identifier in issue.affected_serial_ids:
            serial=db.get(InventorySerial,identifier,populate_existing=True)
            graph._need(serial is not None and serial.material_id==source.material_id and serial.lot_id==source.lot_id
                and saved[identifier]['serial_no']==serial.serial_no and saved[identifier]['qr_code']==serial.qr_code)
        observed.append((case['id'],policy_rows,custody.id,_aware(custody.valid_from),
                         _aware(custody.valid_to) if custody.valid_to else None))
    return basis,(fingerprint,tuple(observed))


@dataclass(frozen=True)
class ConditionHistory:
    basis: Basis
    graph: graph.ConditionGraph
    observed_ledger_cursor: int
    current_stock_verified: bool = False
    retry_allowed: bool = False


def read(db, *, actor, inbound_line_id):
    if type(inbound_line_id) is not UUID or not inbound_line_id.int:
        raise ValueError('exact nonzero inbound line UUID required')
    with db.no_autoflush:
        current,line,source=_scope(db,actor,inbound_line_id)
        before=_bound(db);groups,digest=graph.capture(db,inbound_line_id)
        try:
            basis,original=_basis(db,line,source,groups)
            result=graph.verify(db,basis=basis)
            latest,fresh_line,fresh_source=_scope(db,current,inbound_line_id)
            fresh_groups,fresh_digest=graph.capture(db,inbound_line_id)
            fresh_basis,fresh_original=_basis(db,fresh_line,fresh_source,fresh_groups)
            final,_,_=_scope(db,latest,inbound_line_id)
        except (KeyError,ValueError,TypeError,AttributeError):
            graph.invalid()
        if (latest!=current or final!=current or fresh_basis!=basis or fresh_original!=original or digest!=fresh_digest
                or result.fingerprint!=digest or _bound(db)!=before):
            graph.changed()
        return ConditionHistory(basis,result,before[0])
