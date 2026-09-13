"""Own departure choices and immutable history without manufacturing scan proof."""
from collections import defaultdict
from datetime import datetime,timezone
from decimal import Decimal,ROUND_DOWN
from types import SimpleNamespace
from sqlalchemy import select
from ..inventory_models import StockBalance,FormalMaterial,InventoryLot,InventorySerial,SerialCurrentPosition
from ..stock_operation_models import StockOperationOutbound,StockOperationCancellation
from ..stock_return_outbound_schemas import StockReturnOutboundOptionLineOut,StockReturnOutboundOptionsOut,StockReturnOutboundHistoryOut
from . import inventory_posting as posting,inventory_query as inventory,stock_return_facts as returns,stock_return_outbound_facts as facts
from .stock_return_outbound_plan import original,outbound_context,transit_dimensions
from .stock_return_plan import authorize
from .stock_return_recovery import _original_order
from .work_order_evidence_snapshot import material_audit_cursor
from .work_order_return_sources import _fail


def _options_basis(db,actor,order):
    context=outbound_context(db,actor,order,datetime.now(timezone.utc))
    snapshot,at,route,rows,accounts,quantities,prior_serials,policies,fingerprint=context
    posting._require_no_active_hard_freezes(db,accounts,effective_at=at)
    targets={identifier:SimpleNamespace(**transit_dimensions(account,order)) for identifier,account in accounts.items()}
    posting._require_no_active_hard_freezes(db,targets,effective_at=at)
    options=[]
    for line in sorted(rows.values(),key=lambda row:str(row.id)):
        source=accounts[line.reserved_account_id];policy=policies[source.material_id]
        balance=db.get(StockBalance,source.id,populate_existing=True)
        held=balance.quantity if balance else Decimal(0)
        remaining=line.quantity-quantities[line.id]
        if remaining<0 or held<0:returns.invalid()
        sku=db.get(FormalMaterial,source.material_id,populate_existing=True)
        lot=db.get(InventoryLot,source.lot_id,populate_existing=True) if source.lot_id else None
        if policy.tracking_mode in {'lot','lot_and_serial'} and lot is None:
            _fail('stock_return_outbound_lot_required','原退料明细缺少当前策略要求的批次，请核验原记录')
        remaining_ids=set(returns.serial_ids(db,line))-prior_serials[line.id]
        serials=[]
        for identifier in sorted(remaining_ids,key=str):
            sn=db.get(InventorySerial,identifier,populate_existing=True)
            position=db.get(SerialCurrentPosition,identifier,populate_existing=True)
            if (sn is None or position is None or position.stock_account_id!=source.id or sn.material_id!=source.material_id
                    or sn.lot_id!=source.lot_id or sn.lifecycle_status!='active'):
                _fail('stock_return_outbound_serial_position','原退料尚未发出的 SN 不在对应待退回账户，请核验原记录')
            serials.append(dict(serial_id=sn.id,serial_no=sn.serial_no))
        tracked=policy.tracking_mode in {'serial','lot_and_serial'}
        if (tracked and Decimal(len(serials))!=remaining) or (not tracked and serials):
            _fail('stock_return_outbound_serial_policy','原退料 SN 数量与当前追踪策略不一致，请核验原记录')
        scale=policy.quantity_scale if policy.allow_fraction and not tracked else 0
        selectable=min(remaining,held).quantize(Decimal(1).scaleb(-scale),rounding=ROUND_DOWN)
        options.append(StockReturnOutboundOptionLineOut(operation_line_id=line.id,source_recovery_line_id=line.source_recovery_line_id,
            source_stock_account_id=source.id,material_id=source.material_id,sku_code=sku.sku_code,material_name=sku.name,base_unit=sku.base_unit,
            condition_code=source.condition_code,lot_id=source.lot_id,lot_no=lot.lot_no if lot else None,tracking_mode=policy.tracking_mode,
            quantity_scale=policy.quantity_scale,allow_fraction=policy.allow_fraction,return_quantity=format(line.quantity,'.3f'),
            departed_quantity=format(quantities[line.id],'.3f'),remaining_quantity=format(remaining,'.3f'),held_quantity=format(held,'.3f'),
            selectable_quantity=format(selectable,'.3f'),serials=tuple(serials)))
    inventory._ensure_projection_snapshot_current(db,snapshot)
    return snapshot,route,tuple(options),fingerprint


def outbound_options(db,*,actor,work_order_id,operation_id):
    current=authorize(db,actor,'outbound_return')
    with db.no_autoflush:
        audit=material_audit_cursor(db)
        order,original_result=original(db,current,operation_id,work_order_id)
        first=_options_basis(db,current,order)
        latest=_options_basis(db,current,order)
        if first!=latest or material_audit_cursor(db)!=audit or returns.order_result(db,actor=current,order=order)!=original_result:
            _fail('stock_return_outbound_options_changed','退料明细、库存或接收责任在读取期间变化，请刷新后重新选择')
        snapshot,route,lines,_policies=latest
        inventory._ensure_projection_snapshot_current(db,snapshot)
        authorize(db,current,'outbound_return')
        return StockReturnOutboundOptionsOut(operation_id=order.id,operation_no=order.operation_no,work_order_id=work_order_id,
            person_id=current.person_id,authorization_version=current.authorization_version,ledger_cursor=snapshot.ledger_cursor,
            queried_at=datetime.now(timezone.utc),destination=route,lines=lines)


def outbound_history(db,*,actor,work_order_id,operation_id):
    current=authorize(db,actor,'read')
    with db.no_autoflush:
        audit=material_audit_cursor(db);snapshot=inventory._projection_snapshot(db)
        order=_original_order(db,current,work_order_id,operation_id)
        original_result=returns.order_result(db,actor=current,order=order)
        cancellation=db.scalar(select(StockOperationCancellation).where(StockOperationCancellation.operation_id==order.id).execution_options(populate_existing=True))
        cancelled=returns.cancellation_result(db,actor=current,order=order,cancellation=cancellation) if cancellation else None
        rows=tuple(db.scalars(select(StockOperationOutbound).where(StockOperationOutbound.operation_id==order.id)
            .order_by(StockOperationOutbound.created_at,StockOperationOutbound.id).limit(1001).execution_options(populate_existing=True)))
        if len(rows)>1000:_fail('stock_return_outbound_history_limit','发出历史超过完整核验范围，请按准确原请求查询',503)
        items=[];quantities=defaultdict(Decimal);serials=defaultdict(set)
        for row in rows:
            item=facts.outbound_result(db,actor=current,fact=row);items.append(item)
            for line in item.lines:
                identifiers={sn.serial_id for sn in line.selected_serials}
                if serials[line.operation_line_id]&identifiers:facts.invalid()
                serials[line.operation_line_id].update(identifiers)
                quantities[line.operation_line_id]+=Decimal(line.selected_quantity)
        originals=returns.rows(db,order)
        if any(quantities[line.id]>line.quantity for line in originals):facts.invalid()
        status='outbound' if all(quantities[line.id]==line.quantity for line in originals) else 'partially_outbound' if any(quantities.values()) else 'not_outbound'
        if material_audit_cursor(db)!=audit:_fail('stock_return_outbound_history_changed','退料发出历史在读取期间变化，请重新查询')
        inventory._ensure_projection_snapshot_current(db,snapshot);authorize(db,current,'read')
        return StockReturnOutboundHistoryOut(operation_id=order.id,work_order_id=work_order_id,person_id=current.person_id,
            authorization_version=current.authorization_version,queried_at=datetime.now(timezone.utc),outbound_status=status,
            original=original_result,cancellation=cancelled,items=tuple(items))
