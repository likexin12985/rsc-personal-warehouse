from sqlalchemy import text
from app.stock_operation_models import StockOperationOrder
from app.formal_services import stock_return_origins as origins,stock_return_facts as original
from test_stock_return_outbound import db,world,stock,recovered,destination,prepared,snapshot


def test_existing_work_order_origin_and_event_coordinates_remain_exact(db,stock,prepared):
    order=db.get(StockOperationOrder,prepared.order.operation_id)
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    expected=original.order_result(db,actor=stock.actor,order=order)
    current,_,origin=origins.authorize_return_fulfillment(db,actor=stock.actor,operation_id=order.id,action='outbound_return')
    assert origin.origin_kind=='work_order_recovery' and origin.work_order_id==expected.work_order_id
    assert origin.request_hash==expected.request_hash and origin.posting_transaction_id==expected.posting_transaction_id
    assert origins.event_origin(origin)=={'work_order_id':str(expected.work_order_id)}
    assert origins.line_origin(origin,prepared.line)=={'source_recovery_line_id':str(prepared.line.source_recovery_line_id)}
    assert snapshot(db)==before and not db.new and not db.dirty
