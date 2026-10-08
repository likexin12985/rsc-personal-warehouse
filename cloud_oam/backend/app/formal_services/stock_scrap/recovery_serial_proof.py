"""Exact persisted recovery coordinates for terminal SN re-entry.

The full cursor-bound stock plan is proved by recovery_history separately.
This narrow branch never treats a NULL account as general re-entry authority.
"""
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.stock_loss_correction_models import StockLossDispositionReversal
from app.formal_services.serial_ledger import SerialLedgerError
from app.formal_services.inventory_query import InventoryReadError
from . import recovery_authority as authority, recovery_history
from .tables import tables


def require(db, *, movement, previous, original, prior):
    def need(value):
        if not value:
            raise SerialLedgerError('SN 找回缺少独立批准和准确原报废反向事实')
    need(original is not None and prior is not None and previous.lifecycle_status == 'scrapped'
        and prior.lifecycle_status == 'active' and prior.stock_account_id is not None
        and movement['movement_type'] == 'reversal' and movement['source_document_type'] == 'stock_loss_disposition_reversal'
        and original['movement_type'] == 'scrap' and movement['reversed_transaction_id'] == original['transaction_id']
        and movement['ledger_cursor'] > previous.ledger_cursor
        and movement['from_account_id'] is None and original['to_account_id'] is None
        and movement['to_account_id'] == original['from_account_id'] == prior.stock_account_id
        and movement['target_owner_org_id'] == previous.owner_org_id == prior.owner_org_id
        and movement['quantity'] == original['quantity'] and movement['line_no'] == original['line_no'] == 1
        and movement['external_boundary_code'] == original['external_boundary_code'] == 'stock_operation_scrap')
    inverse = db.scalar(select(StockLossDispositionReversal).where(
        StockLossDispositionReversal.posting_transaction_id == movement['transaction_id']).execution_options(populate_existing=True))
    need(inverse is not None and str(inverse.id) == movement['source_document_id']
        and inverse.original_movement_id == original['movement_id'] and inverse.posting_movement_id == movement['movement_id'])
    try:
        from app.stock_scrap_recovery_schemas import ScrapRecoveryExecute
        command = ScrapRecoveryExecute.model_validate(dict(inverse.command_jsonb, idempotency_key='historical-proof-only'))
        source = authority.load_source(db, command.source)
        recovery_history.verify_execution(db, inverse=inverse, source=source)
        sn = tables()['stock_scrap_serials']
        row = db.execute(select(sn).where(sn.c.scrap_line_id == source['line']['id'], sn.c.serial_id == movement['serial_id'])).mappings().one()
        need(row['previous_movement_id'] == prior.last_movement_id and row['admission_movement_id'] == prior.admission_movement_id
            and previous.admission_movement_id == prior.admission_movement_id)
    except SerialLedgerError:
        raise
    except (ValueError, KeyError, TypeError, SQLAlchemyError, InventoryReadError) as error:
        raise SerialLedgerError('SN 找回的持久审批和原报废绑定不完整') from error
