"""Re-prove the original approved return without requiring physical write grants."""
from datetime import datetime,timezone
from app.stock_operation_models import StockOperationOrder,StockOperationLine
from app.stock_return_origin_schemas import LossReturnOrigin
from app.stock_return_schemas import StockReturnDestinationOut
from app.loss_return_sender_detail_schemas import LossReturnSenderLineOut,LossReturnSenderDetailOut
from app.formal_services import stock_return_origins as origins,stock_return_facts as facts
from app.formal_services import stock_loss_facts as losses,inventory_query as inventory
from app.formal_services.stock_return_plan import authorize
from app.formal_services.work_order_evidence_snapshot import material_audit_cursor
from app.formal_services.work_order_return_sources import _fail


def read_sender_return(db,*,actor,operation_id):
    current=authorize(db,actor,'read')
    with db.no_autoflush:
        snapshot=inventory._projection_snapshot(db); audit=material_audit_cursor(db)
        order=db.get(StockOperationOrder,operation_id,populate_existing=True)
        origin=origins.verify_return_origin(db,actor=current,order=order)
        if not isinstance(origin,LossReturnOrigin):
            _fail('stock_return_not_found','本人报损派生退回单不存在',404)
        root=db.get(StockOperationOrder,origin.loss_operation_id,populate_existing=True)
        original=losses.submission_evidence(db,order=root)
        loss_line=db.get(StockOperationLine,origin.loss_line_id,populate_existing=True)
        lines=facts.rows(db,order)
        if len(lines)!=1 or not 1<=loss_line.line_no<=len(original.lines):facts.invalid()
        line=lines[0]; selection=original.lines[loss_line.line_no-1]; source=selection.source
        if (line.source_loss_line_id!=loss_line.id or line.material_id!=source.material_id
                or format(line.quantity,'.3f')!=selection.selected_quantity
                or set(facts.serial_ids(db,line))!={sn.serial_id for sn in selection.selected_serials}):facts.invalid()
        result=LossReturnSenderDetailOut(operation_no=order.operation_no,origin=origin,reason=order.reason,
            destination=StockReturnDestinationOut.model_validate(order.plan_jsonb['destination']),
            person_id=current.person_id,authorization_version=current.authorization_version,
            ledger_cursor=snapshot.ledger_cursor,queried_at=datetime.now(timezone.utc),
            loss_operation_no=original.operation_no,loss_submitted_at=original.submitted_at,
            line=LossReturnSenderLineOut(operation_line_id=line.id,source_loss_line_id=loss_line.id,
                material_id=source.material_id,sku_code=source.sku_code,material_name=source.material_name,
                base_unit=source.base_unit,condition_code=line.target_condition,lot_id=source.lot_id,
                lot_no=source.lot_no,return_quantity=selection.selected_quantity,selected_serials=selection.selected_serials))
        if material_audit_cursor(db)!=audit:
            _fail('stock_loss_return_detail_changed','退回记录在读取期间变化，请重新查询',409)
        inventory._ensure_projection_snapshot_current(db,snapshot)
        refreshed=authorize(db,current,'read')
        if refreshed.authorization_version!=current.authorization_version:
            _fail('stock_loss_return_detail_changed','查询权限已变化，请重新查询',409)
        return result
