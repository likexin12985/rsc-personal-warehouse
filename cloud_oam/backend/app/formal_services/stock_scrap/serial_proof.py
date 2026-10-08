"""Narrow persisted scrap coordinates for immutable SN reconstruction.

This checks the exact source, parent, order, movement and recorded prior SN.
It is not the whole approval/event/authority proof; the forward SQL guards
and complete business-history verifier must independently prove those facts.
"""
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.formal_services.stock_loss_sources import _hash
from .tables import tables


def require_scrap_serial(db, *, movement, previous):
    from app.formal_services.serial_ledger import SerialLedgerError
    def need(value):
        if not value:
            raise SerialLedgerError('SN 报废没有准确的原处置、报废单和前序流水')
    need(movement['source_document_type'] in ('stock_loss_disposition', 'stock_loss_correction_execution')
        and movement['from_account_id'] is not None and movement['to_account_id'] is None
        and movement['external_boundary_code'] == 'stock_operation_scrap')
    schema = tables()
    line_table = schema['stock_scrap_lines']
    try:
        lines = db.execute(select(line_table).where(line_table.c.posting_transaction_id == movement['transaction_id'])).mappings().all()
    except SQLAlchemyError as error:
        raise SerialLedgerError('SN 报废事实结构不可读，不能证明生命周期') from error
    need(len(lines) == 1)
    line = lines[0]
    table = schema['stock_operation_orders']
    order = db.execute(select(table).where(table.c.id == line['operation_id'])).mappings().one_or_none()
    corrected = line['source_kind'] == 'correction'
    table = schema['stock_loss_correction_executions' if corrected else 'stock_loss_dispositions']
    fact_id = line['correction_execution_id'] if corrected else line['root_disposition_id']
    parent = db.execute(select(table).where(table.c.id == fact_id)).mappings().one_or_none()
    serial_table = schema['stock_scrap_serials']
    serials = db.execute(select(serial_table).where(serial_table.c.scrap_line_id == line['id'])).mappings().all()
    serial = next((s for s in serials if s['serial_id'] == movement['serial_id']), None)
    need(order is not None and parent is not None and serial is not None)
    need(line['source_kind'] in ('original', 'correction') and line['operation_type'] == order['operation_type'] == 'scrap'
        and order['status'] == 'posted' and parent['disposition'] == 'scrap' and parent['target_account_id'] is None
        and parent['scrap_operation_id'] == order['id']
        and order['posting_transaction_id'] == parent['posting_transaction_id'] == movement['transaction_id']
        and parent['posting_movement_id'] == line['posting_movement_id'] == movement['movement_id']
        and parent['source_account_id'] == line['frozen_account_id'] == movement['from_account_id'] == previous.stock_account_id
        and parent['quantity'] == line['quantity'] == movement['quantity'] == len(serials)
        and movement['source_document_id'] == str(fact_id)
        and movement['source_document_type'] == ('stock_loss_correction_execution' if corrected else 'stock_loss_disposition')
        and movement['to_account_id'] is None and movement['external_boundary_code'] == 'stock_operation_scrap'
        and movement['reversed_transaction_id'] is None and movement['line_no'] == 1
        and previous.lifecycle_status == 'active' and previous.admission_movement_id is not None
        and serial['previous_movement_id'] == previous.last_movement_id
        and serial['admission_movement_id'] == previous.admission_movement_id
        and parent['plan_jsonb'] == order['plan_jsonb'] == line['plan_jsonb']
        and _hash(line['plan_jsonb']) == line['plan_hash'] == parent['plan_hash'] == order['plan_hash']
        and parent['command_jsonb'] == order['command_jsonb']
        and _hash(parent['command_jsonb']) == parent['request_hash'] == order['request_hash']
        and sorted(str(s['serial_id']) for s in serials) == line['plan_jsonb']['serial_ids'])
    if corrected:
        need(parent['root_disposition_id'] == line['root_disposition_id']
            and parent['reversal_id'] == line['predecessor_reversal_id']
            and parent['correction_decision_id'] == line['correction_decision_id'] == order['loss_correction_decision_id']
            and line['original_decision_id'] is None and order['loss_headquarters_decision_id'] is None)
    else:
        need(parent['line_id'] == line['loss_line_id']
            and parent['headquarters_decision_id'] == line['original_decision_id'] == order['loss_headquarters_decision_id']
            and line['correction_execution_id'] is None and order['loss_correction_decision_id'] is None)
