"""Run as a child in the preserved 0157 checkout, using only its services.

The parent owns a freshly initialized Unix-socket PG16 cluster. No historical
row, plan, hash, trigger or business-service implementation is patched here.
"""
import json
import os
from pathlib import Path
import runpy
import sys


def main():
    old = Path.cwd()
    sys.path[:0] = [str(old/'backend'), str(old/'backend/tests')]
    runpy.run_path(str(old/'backend/tests/conftest.py'))
    from datetime import datetime, timezone
    from decimal import Decimal
    from uuid import uuid4
    from unittest.mock import patch
    from sqlalchemy import create_engine, select, text
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import NullPool
    from app.formal_access import load_formal_principal
    from app.models import User
    from app.foundation_models import FileObject
    from app.inventory_models import StockLocation, StockAccount, InventorySerial
    from app.stock_operation_models import StockOperationOutboundLine, StockLossDisposition
    from app.stock_return_shipment_schemas import StockReturnShipmentPreviewIn, StockReturnShipmentSubmitIn
    from app.stock_return_receipt_schemas import StockReturnReceiptPreviewIn, StockReturnReceiptSubmitIn
    from app.formal_services import formal_files
    from app.formal_services import stock_return_shipment_plan as ship_plan, stock_return_shipment_commands as shipping
    from app.formal_services import stock_return_receipt_plan as receipt_plan, stock_return_receipt_commands as receiving
    from app.formal_services import stock_return_inbound_commands as inbound
    from app.database_security import validate_production_database_security
    from test_formal_files_service import FakeStorage, SECRET
    import pg16_stock_loss_return_receipt_gate as gate
    from pg16_stock_loss_return_shipment_gate import prepare_departures

    engines = {role: create_engine(url, poolclass=NullPool)
        for role, url in json.loads(os.environ['RSC_OWNED_CONDITION_URLS']).items()}
    evidence = {}
    try:
        for role, engine in engines.items():
            if role == 'star_oam_edge':
                continue  # Group role has NOLOGIN; edge_inbox is its login member.
            with engine.connect() as db:
                assert db.execute(text('SELECT current_database(),current_user,inet_server_addr()')).one() == (
                    'rsc_pg16_release_gate', role, None)
        with engines['star_oam_migrator'].connect() as db:
            assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261206_0157'
        validate_production_database_security(engines['star_oam_api'],
            expected_runtime_role='star_oam_api', expected_migration_role='star_oam_migrator')

        def exercise(context):
            world = prepare_departures(context)
            owner, api = world['owner'], world['api']
            departure = world['first']
            with Session(api) as db:
                actor = load_formal_principal(db, context['engineer_id'])
                line = db.scalars(select(StockOperationOutboundLine).where(
                    StockOperationOutboundLine.outbound_id == departure.outbound_id)).one()
                request = StockReturnShipmentPreviewIn(operator_person_id=actor.person_id,
                    carrier='Synthetic', tracking_no='SYNTHETIC-LEGACY-'+uuid4().hex,
                    shipped_at=datetime.now(timezone.utc), reason='Synthetic old contract parcel',
                    lines=(dict(outbound_line_id=line.id, quantity=departure.lines[0].selected_quantity,
                        serial_ids=tuple(s.serial_id for s in departure.lines[0].selected_serials)),))
                checked, _ = ship_plan.preview_shipment(db, actor=actor, work_order_id=None,
                    operation_id=world['order_id'], request=request)
                parcel = shipping.execute_shipment(db, actor=actor, work_order_id=None,
                    operation_id=world['order_id'], request=StockReturnShipmentSubmitIn(**request.model_dump(),
                        expected_plan_hash=checked.plan_hash, request_id=uuid4().hex, idempotency_key=uuid4().hex))
                db.commit()
            with Session(owner) as db:
                location = db.get(StockLocation, parcel.destination.target_location_id)
                receiver = db.scalars(select(User.id).where(User.person_id == location.custodian_person_id)).one()
                root = db.scalars(select(StockLossDisposition).where(
                    StockLossDisposition.return_operation_id == world['order_id'])).one().id
            with Session(api) as db:
                actor = load_formal_principal(db, receiver)
                storage = FakeStorage()
                intent = formal_files.create_file_upload_intent(db, actor=actor,
                    command=formal_files.FileUploadIntentInput(purpose='receipt_exception_evidence',
                        original_filename='synthetic-damage.png', size_bytes=128, mime_type='image/png', sha256='a'*64),
                    idempotency_key=uuid4().hex, idempotency_hmac_secret=SECRET,
                    trace_request_id=uuid4().hex, storage=storage, upload_ttl_seconds=600)
                file = db.get(FileObject, intent.file_id); storage.materialize(file)
                formal_files.complete_file_upload(db, actor=actor, file_id=file.id,
                    trace_request_id=uuid4().hex, storage=storage)
                db.commit()
                _, detail = receipt_plan.authorize(db, actor, parcel.shipment_id)
                original = detail.package.lines[0]
                serials = [db.get(InventorySerial, s.serial_id) for s in original.serials]
                quantity = Decimal(original.shipped_quantity)
                damage = quantity if serials else quantity/2
                value = StockReturnReceiptPreviewIn(operator_person_id=actor.person_id,
                    received_at=datetime.now(timezone.utc), reason='Synthetic legacy damaged acceptance',
                    lines=(dict(shipment_line_id=original.shipment_line_id, accepted_qty=quantity,
                        damaged_qty=damage, damaged_serial_ids=tuple(s.id for s in serials),
                        accepted_serial_verifications=tuple(dict(serial_id=s.id, serial_no=s.serial_no,
                            sku_code=original.sku_code, qr_code=s.qr_code) for s in serials),
                        exceptions=(dict(exception_type='damaged', description='Synthetic damage', evidence_file_id=file.id),)),))
                checked, _ = receipt_plan.preview_receipt(db, actor=actor, shipment_id=parcel.shipment_id, request=value)
                receipt = receiving.execute_receipt(db, actor=actor, shipment_id=parcel.shipment_id,
                    request=StockReturnReceiptSubmitIn(**value.model_dump(), expected_plan_hash=checked.plan_hash,
                        request_id=uuid4().hex, idempotency_key=uuid4().hex))
                db.commit()
                plan = inbound.preview_return_inbound(db, actor=actor, receipt_id=receipt.receipt_id)
                assert plan['schema_version'] == '1.0' and len(plan['lines']) == 1
                assert plan['lines'][0]['condition_code'] == 'new'
                request_id = uuid4().hex
                result = inbound.execute_return_inbound(db, actor=actor, receipt_id=receipt.receipt_id,
                    expected_plan_hash=plan['plan_hash'], request_id=request_id, idempotency_key=uuid4().hex)
                db.commit()
                evidence.update(rootDispositionId=str(root), inboundId=str(result['inbound_id']),
                    receiverUserId=receiver, administratorUserId=context['admin_id'],
                    inboundRequestId=request_id, inboundRequestHash=result['request_hash'],
                    damagedQuantity=format(damage, '.3f'), schemaVersion='1.0')
            return dict(passed=True, actualOldApplication=True, **evidence)

        with patch.object(gate, 'exercise', exercise):
            gate.run_sources(engines, tracking=os.environ['RSC_OWNED_CONDITION_TRACKING'])
        Path(os.environ['RSC_OWNED_CONDITION_OUTPUT']).write_text(json.dumps(evidence, indent=2)+'\n')
        print('preserved 0157 actual damaged receipt and v1 inbound COMMIT PASS', flush=True)
    finally:
        for engine in engines.values():
            engine.dispose()


if __name__ == '__main__':
    main()
