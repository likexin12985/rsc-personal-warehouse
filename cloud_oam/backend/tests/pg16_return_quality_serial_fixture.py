"""Expand owned synthetic stock to two serials without bypassing any service.

The original fixture functions remain immutable. Exact-anchor substitutions
only change the synthetic cardinality and its expected count; all authorization,
opening, ledger, approval and native database checks still execute unchanged.
"""
from contextlib import contextmanager
from datetime import datetime,timezone
import inspect
from uuid import uuid4
from unittest.mock import patch
from sqlalchemy.orm import Session
from app.inventory_models import InventorySerial
import pg16_stock_loss_sources_gate as sources
import pg16_stock_loss_return_preview_gate as preview
import pg16_stock_loss_return_shipment_gate as shipment


def modified(function,changes,globals=None):
    code=inspect.getsource(function)
    for old,new in changes:
        if code.count(old)!=1:raise AssertionError(('fixture anchor drift',function.__name__,old))
        code=code.replace(old,new)
    namespace={**function.__globals__,**(globals or {})}
    exec(compile(code,'<owned-two-serial-fixture-'+function.__name__+'>','exec'),namespace)
    return namespace[function.__name__]


@contextmanager
def two_serials(business):
    original=sources.prepare_stocktake_inventory
    def prepare(owner,edge,**kwargs):
        assert kwargs['control_material']=='serial'
        fixture=original(owner,edge,**kwargs)
        with Session(owner) as db:
            old=db.get(InventorySerial,fixture['concurrency_serial_id'])
            now=datetime.now(timezone.utc)
            extra=InventorySerial(id=uuid4(),material_id=old.material_id,lot_id=old.lot_id,
                serial_no='QUALITY-MIXED-'+uuid4().hex,qr_code='QUALITY-MIXED-QR-'+uuid4().hex,
                lifecycle_status='active',created_at=now,updated_at=now)
            db.add(extra);db.flush()
            proofs=tuple(dict(serial_id=sn.id,sku_code=fixture['concurrency_material_sku_code'],
                serial_no=sn.serial_no,qr_code=sn.qr_code) for sn in (old,extra))
            db.commit()
        return dict(fixture,quality_serial_proofs=proofs,
            selected_serial_nos=tuple(p['serial_no'] for p in proofs),physical_count=2)
    source_fn=modified(sources.run,[
        ("choices.items[0].quantity=='1.000'","choices.items[0].quantity=='2.000'"),
        ("quantity='1' if tracking=='serial' else '0.250'","quantity='2' if tracking=='serial' else '0.250'"),
        ("serial_verifications=(dict(serial_id=fixture['concurrency_serial_id'], sku_code=fixture['concurrency_material_sku_code'], serial_no=fixture['concurrency_serial_no'], qr_code=fixture['concurrency_serial_qr_code']),) if tracking=='serial' else ()",
         "serial_verifications=fixture['quality_serial_proofs'] if tracking=='serial' else ()"),
        ("('1.000' if tracking=='serial' else '0.250')","('2.000' if tracking=='serial' else '0.250')"),
        ("len(preview.lines[0].selected_serials)==int(tracking=='serial')","len(preview.lines[0].selected_serials)==2*int(tracking=='serial')"),
    ],{'prepare_stocktake_inventory':lambda *a,**kw:sources.prepare_stocktake_inventory(*a,**kw)})
    preview_fn=modified(preview.run,[
        ("('1.000' if context['tracking'] == 'serial' else '0.250')","('2.000' if context['tracking'] == 'serial' else '0.250')"),
        ("len(first['serial_ids']) == int(context['tracking'] == 'serial')","len(first['serial_ids']) == 2*int(context['tracking'] == 'serial')"),
    ])
    sender_fn=modified(shipment.sender_read_gate,[
        ("len(detail['line']['selected_serials']) == int(context['tracking'] == 'serial')",
         "len(detail['line']['selected_serials']) == 2*int(context['tracking'] == 'serial')"),
    ])
    departure_fn=modified(shipment.prepare_departures,[
        ("first_quantity = Decimal('1') if context['tracking']=='serial' else Decimal('.100')", "first_quantity = Decimal('2') if context['tracking']=='serial' else Decimal('.100')"),
    ],{'preview':preview_fn,'sender_read_gate':sender_fn})
    with patch.object(sources,'prepare_stocktake_inventory',prepare),patch.object(sources,'run',source_fn), \
            patch.object(business,'prepare_departures',departure_fn):
        yield
