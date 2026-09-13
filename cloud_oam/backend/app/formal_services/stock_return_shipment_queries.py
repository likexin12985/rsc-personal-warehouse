"""Exact return-departure parcel choices and separately verified history."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from sqlalchemy import select
from ..inventory_models import StockBalance, FormalMaterial, InventoryLot, InventorySerial, SerialCurrentPosition
from ..stock_operation_models import StockOperationLine, StockOperationOutbound, StockOperationOutboundLine, StockOperationShipment
from ..stock_return_shipment_schemas import StockReturnShipmentOptionLineOut, StockReturnShipmentOptionsOut, StockReturnShipmentHistoryOut
from . import inventory_query as inventory, stock_return_facts as returns, stock_return_outbound_facts as departures, stock_return_shipment_facts as facts
from .stock_return_shipment_plan import shipment_context
from .stock_return_outbound_plan import original
from .stock_return_outbound_queries import outbound_history
from .stock_return_plan import authorize
from .stock_return_recovery import _original_order
from .work_order_evidence_snapshot import material_audit_cursor
from .work_order_query import _aware
from .work_order_return_sources import _fail


def _options_basis(db, actor, order):
    pairs=tuple(db.execute(select(StockOperationOutboundLine,StockOperationOutbound)
        .join(StockOperationOutbound,StockOperationOutbound.id==StockOperationOutboundLine.outbound_id)
        .where(StockOperationOutbound.operation_id==order.id).order_by(StockOperationOutboundLine.id)
        .limit(10001).execution_options(populate_existing=True)))
    if len(pairs)>10000:_fail('stock_return_shipment_history_limit','原发出明细超过完整核验范围，请按原请求核验',503)
    rows={};parents={}
    for row,parent in pairs:
        if parent.id not in parents:
            departures.outbound_result(db,actor=actor,fact=parent);parents[parent.id]=parent
        rows[row.id]=row
    snapshot,at,route,accounts,quantities,prior_serials,assigned,policies,fingerprint=shipment_context(db,actor,order,rows)
    options=[]
    for row in rows.values():
        account=accounts[row.transit_stock_account_id];policy=policies[account.material_id]
        balance=db.get(StockBalance,account.id,populate_existing=True)
        held=balance.quantity if balance else Decimal(0);remaining=row.quantity-quantities[row.id];unassigned=held-assigned[account.id]
        if remaining<0 or unassigned<0:facts.invalid()
        sku=db.get(FormalMaterial,account.material_id,populate_existing=True)
        lot=db.get(InventoryLot,account.lot_id,populate_existing=True) if account.lot_id else None
        if (policy.tracking_mode in {'lot','lot_and_serial'}) != (lot is not None):
            _fail('stock_return_shipment_lot_policy','原发出明细批次与当前策略不一致，请核验原记录')
        ids=set(departures.serial_ids(db,row))-prior_serials[row.id];serials=[]
        for identifier in sorted(ids,key=str):
            sn=db.get(InventorySerial,identifier,populate_existing=True);position=db.get(SerialCurrentPosition,identifier,populate_existing=True)
            if (sn is None or position is None or position.stock_account_id!=account.id or sn.lifecycle_status!='active'
                    or sn.material_id!=account.material_id or sn.lot_id!=account.lot_id):
                _fail('stock_return_shipment_serial_position','尚未装包的 SN 不在原发出对应的在途账户，请核验原记录')
            serials.append(dict(serial_id=sn.id,serial_no=sn.serial_no))
        tracked=policy.tracking_mode in {'serial','lot_and_serial'}
        if (tracked and Decimal(len(serials))!=remaining) or (not tracked and serials):
            _fail('stock_return_shipment_serial_policy','原发出 SN 数量与当前追踪策略不一致，请核验原记录')
        scale=policy.quantity_scale if policy.allow_fraction and not tracked else 0
        selectable=min(remaining,unassigned).quantize(Decimal(1).scaleb(-scale),rounding=ROUND_DOWN)
        origin=db.get(StockOperationLine,row.operation_line_id,populate_existing=True)
        options.append(StockReturnShipmentOptionLineOut(outbound_id=row.outbound_id,outbound_no=parents[row.outbound_id].outbound_no,
            outbound_at=_aware(parents[row.outbound_id].outbound_at),outbound_line_id=row.id,operation_line_id=row.operation_line_id,
            source_recovery_line_id=origin.source_recovery_line_id,transit_stock_account_id=account.id,material_id=account.material_id,
            sku_code=sku.sku_code,material_name=sku.name,base_unit=sku.base_unit,condition_code=account.condition_code,
            lot_id=account.lot_id,lot_no=lot.lot_no if lot else None,tracking_mode=policy.tracking_mode,quantity_scale=policy.quantity_scale,
            allow_fraction=policy.allow_fraction,outbound_quantity=format(row.quantity,'.3f'),shipped_quantity=format(quantities[row.id],'.3f'),
            unshipped_quantity=format(remaining,'.3f'),in_transit_quantity=format(held,'.3f'),unassigned_quantity=format(unassigned,'.3f'),
            selectable_quantity=format(selectable,'.3f'),serials=tuple(serials)))
    inventory._ensure_projection_snapshot_current(db,snapshot)
    return snapshot,route,tuple(options),fingerprint


def shipment_options(db,*,actor,work_order_id,operation_id):
    current=authorize(db,actor,'ship_return')
    with db.no_autoflush:
        audit=material_audit_cursor(db)
        order,original_result=original(db,current,operation_id,work_order_id)
        first=_options_basis(db,current,order);latest=_options_basis(db,current,order)
        if first!=latest or material_audit_cursor(db)!=audit or returns.order_result(db,actor=current,order=order)!=original_result:
            _fail('stock_return_shipment_options_changed','发出、分包、库存或接收责任在读取期间变化，请刷新后再选择')
        snapshot,route,lines,_policies=latest
        inventory._ensure_projection_snapshot_current(db,snapshot);authorize(db,current,'ship_return')
        return StockReturnShipmentOptionsOut(operation_id=order.id,operation_no=order.operation_no,work_order_id=work_order_id,
            person_id=current.person_id,authorization_version=current.authorization_version,ledger_cursor=snapshot.ledger_cursor,
            queried_at=datetime.now(timezone.utc),destination=route,lines=lines)


def shipment_history(db,*,actor,work_order_id,operation_id):
    current=authorize(db,actor,'read')
    with db.no_autoflush:
        audit=material_audit_cursor(db);snapshot=inventory._projection_snapshot(db)
        order=_original_order(db,current,work_order_id,operation_id)
        source=outbound_history(db,actor=current,work_order_id=work_order_id,operation_id=operation_id)
        rows=tuple(db.scalars(select(StockOperationShipment).where(StockOperationShipment.operation_id==order.id)
            .order_by(StockOperationShipment.audit_version).limit(1001).execution_options(populate_existing=True)))
        if len(rows)>1000:_fail('stock_return_shipment_history_limit','分包历史超过完整核验范围，请按准确原请求查询',503)
        items=[];quantities=defaultdict(Decimal);serials=set()
        for row in rows:
            item=facts.shipment_result(db,actor=current,fact=row);items.append(item)
            for line in item.lines:
                identifiers={sn.serial_id for sn in line.selected_serials}
                if serials&identifiers:facts.invalid()
                serials.update(identifiers);quantities[line.operation_line_id]+=Decimal(line.selected_quantity)
        originals=returns.rows(db,order)
        if any(quantities[line.id]>line.quantity for line in originals) or (source.cancellation and items):facts.invalid()
        status='shipped' if all(quantities[line.id]==line.quantity for line in originals) else 'partially_shipped' if items else 'not_shipped'
        if material_audit_cursor(db)!=audit:_fail('stock_return_shipment_history_changed','退回运单历史在读取期间变化，请重新查询')
        inventory._ensure_projection_snapshot_current(db,snapshot);authorize(db,current,'read')
        return StockReturnShipmentHistoryOut(operation_id=order.id,work_order_id=work_order_id,person_id=current.person_id,
            authorization_version=current.authorization_version,queried_at=datetime.now(timezone.utc),shipment_status=status,departures=source,items=tuple(items))
