"""Browser-only first acceptance and first posting, followed by actual DB readback."""
from decimal import Decimal
from uuid import UUID
import json
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.models import User
from app.formal_access import load_formal_principal
from app.inventory_models import StockAccount, StockBalance, StockLocation, InventorySerial, InventoryTransaction, SerialCurrentPosition
from app.material_request_rejection_return_schema import returns
from app.material_request_rejection_inbound_schema import inbounds
from app.formal_services import material_request_rejection_warehouse as warehouse
from pg16_stock_scrap_structure_gate import original_columns, facts
from pg16_fulfillment_browser import serve


def run(mounted, cloud, *, engines, directory, return_id, timeout_seconds):
    api, owner = engines['star_oam_api'], engines['star_oam_migrator']
    return_id = UUID(return_id)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        parent = db.execute(select(returns).where(returns.c.id == return_id)).mappings().one()
        account = db.get(StockAccount, parent['return_source_account_id'])
        location = db.get(StockLocation, account.location_id)
        actor_id = db.scalar(select(User.id).where(User.person_id == location.custodian_person_id))
        current = warehouse.detail(db, actor=load_formal_principal(db, actor_id), return_id=return_id)
        assert current.receive_permitted and current.receipts == () and current.posted_qty == '0.000'
        # return_source_account identifies the return destination warehouse;
        # the stock actually leaving during inbound belongs to in_transit.
        source_id = parent['in_transit_account_id']
        before_balance = db.get(StockBalance, source_id).quantity
        # These are synthetic test labels to scan, not session/auth credentials.
        labels = []
        for sn in current.source.serials:
            item = db.get(InventorySerial, sn.serial_id)
            labels.append(dict(serial_id=str(item.id), sku_code=current.source.sku_code, serial_no=item.serial_no, qr_code=item.qr_code))
        (directory/'browser-warehouse-labels.json').write_text(json.dumps(dict(
            synthetic=True, source=current.model_dump(mode='json'), scanLabels=labels), ensure_ascii=False, indent=2)+'\n')
    with owner.connect() as db:
        columns = {name: cols for name, cols in original_columns(db).items()
            if name.startswith(('material_request', 'approval_', 'shipment', 'receipt', 'outbound', 'stock_reserv', 'stock_allocation'))
            and not name.startswith(('material_request_rejection_receipt', 'material_request_rejection_inbound'))}
        original = facts(db, columns)
    result = serve(mounted, cloud, api=api, admin=actor_id, directory=directory,
        request_id=current.source.request_id, warehouse_return_id=return_id,
        timeout_seconds=timeout_seconds, serial_ids=tuple(sn.serial_id for sn in current.source.serials))
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        final = warehouse.detail(db, actor=load_formal_principal(db, actor_id), return_id=return_id)
        assert len(final.receipts) == 1 and final.receipts[0].inbound is not None
        assert final.accepted_qty == final.posted_qty == current.source.quantity
        assert final.unconfirmed_qty == final.pending_inbound_qty == final.rejected_qty == final.damaged_qty == '0.000'
        assert db.get(StockBalance, source_id).quantity == before_balance - Decimal(final.posted_qty)
        posted = final.receipts[0].inbound
        row = db.execute(select(inbounds).where(inbounds.c.id == posted.inbound_id)).mappings().one()
        plan = row['plan_jsonb']
        for part in plan['parts']:
            old = (part['target_balance'] or {}).get('quantity', '0')
            assert db.get(StockBalance, UUID(part['target_account_id'])).quantity == Decimal(old) + Decimal(part['quantity'])
            for serial_id in part['serial_ids']:
                assert db.get(SerialCurrentPosition, UUID(serial_id)).stock_account_id == UUID(part['target_account_id'])
        assert db.get(InventoryTransaction, posted.inventory_transaction_id).status == 'posted'
        result.update(readback=final.model_dump(mode='json'), actualInventoryPosted=True,
            readOnlyDatabaseVerification=True, openingOrAuthorityBypass=False,
            syntheticIdentityAndLabels=True, productionAcceptance=False)
    with owner.connect() as db:
        assert facts(db, columns) == original
    result['originalBusinessUnchanged'] = True
    (directory/'browser-warehouse-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result
