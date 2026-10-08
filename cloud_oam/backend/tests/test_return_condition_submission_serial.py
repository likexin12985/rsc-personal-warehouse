"""Actual SN application and generic-entry bypass refusal on candidate SQLite."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.formal_access import load_formal_principal, lock_formal_principal_graph
from app.foundation_models import FileObject
from app.inventory_models import FormalMaterial, StockBalance, SerialCurrentPosition
from app.return_condition_requests import ConditionSubmit
from app.formal_services import inventory_posting as posting, formal_files
from app.formal_services.stock_loss_corrections import return_condition_submission as subject
from app.formal_services.stock_loss_corrections import return_condition_submission_source as preparation
from test_return_condition_submission import db, snapshot
from test_return_condition_submission_source import (
    world,stock,allowed,evidence,regional,headquarters,approved,route,derived,ready,parcel,
    acceptance,prepared,authority_template,regional_opening,reader_tables, context,regional_source,ERRORS,
)
from test_return_condition_evidence import create,finish
from test_formal_files_service import FakeStorage

pytestmark=pytest.mark.parametrize('stock',['serial'],indirect=True)


@pytest.fixture
def command(db,regional_source,monkeypatch):
    c=regional_source
    monkeypatch.setattr(formal_files,'load_formal_principal',load_formal_principal)
    monkeypatch.setattr(formal_files,'lock_formal_principal_graph',lock_formal_principal_graph)
    upload=SimpleNamespace(actor=c.actor,storage=FakeStorage())
    row=db.get(FileObject,create(db,upload).file_id);finish(db,upload,row);db.commit()
    proof=preparation.inspect_submission_source(db,actor=c.actor,inbound_line_id=c.line)
    material=db.get(FormalMaterial,c.source.material_id)
    serials=[dict(serial_id=s['serial_id'],sku_code=material.sku_code,serial_no=s['serial_no'],qr_code=s['qr_code'])
        for s in proof.document['serials']]
    assert len(serials)==1
    result=ConditionSubmit(action='submit_return_condition',inbound_line_id=c.line,
        expected_source_hash=proof.evidence_hash,quantity='1',serial_verifications=serials,evidence_file_ids=(row.id,),
        reason='逐件核实原破损验收SN',request_id=uuid4().hex,idempotency_key=uuid4().hex)
    db.commit();return result


def test_exact_damaged_sn_freezes_and_generic_post_or_disguised_reversal_cannot_bypass(db,regional_source,command):
    c=regional_source;serial=command.serial_verifications[0].serial_id
    result=subject.submit(db,actor=c.actor,request=command);db.commit()
    cases=subject.tables()['stock_condition_cases']
    case=db.execute(select(cases).where(cases.c.id==UUID(result['case_id']))).mappings().one()
    assert case['tracking_mode']=='serial' and case['quantity']==Decimal(1)
    assert db.get(SerialCurrentPosition,serial,populate_existing=True).stock_account_id==case['frozen_account_id']
    assert db.get(StockBalance,case['frozen_account_id']).quantity==Decimal(1)
    rows=subject.tables()['stock_condition_serials']
    assert tuple(db.scalars(select(rows.c.serial_id).where(rows.c.case_id==case['id'])))==(serial,)
    before=snapshot(db)
    # The shared entry itself, not just the permit validator, must fail closed.
    generic=posting.InventoryPostingCommand(transaction_no='COND-GENERIC-'+uuid4().hex,movement_type='unfreeze',
        source_document_type='stock_condition_event',source_document_id=str(uuid4()),posting_key=uuid4().hex,
        effective_at=datetime.now(timezone.utc),movements=(posting.InventoryMovementCommand(
            case['frozen_account_id'],c.source.id,Decimal(1),(serial,)),))
    with pytest.raises(posting.InventoryPostingError) as error:
        posting.post_inventory_transaction(db,actor=c.actor,command=generic,idempotency_key=uuid4().hex,
            request_id=uuid4().hex,permission_resource='stock_operation',permission_action='submit_return_condition')
    assert error.value.code=='condition_posting_authority_invalid'
    db.rollback();assert snapshot(db)==before
    inverse=posting.InventoryReversalCommand(original_transaction_id=case['freeze_transaction_id'],
        transaction_no='COND-INVERSE-'+uuid4().hex,source_document_type='generic_adjustment',
        source_document_id=str(uuid4()),posting_key=uuid4().hex,effective_at=datetime.now(timezone.utc))
    with pytest.raises(posting.InventoryPostingError) as error:
        posting.reverse_inventory_transaction(db,actor=c.actor,command=inverse,idempotency_key=uuid4().hex,request_id=uuid4().hex)
    assert error.value.code=='condition_reversal_requires_command'
    db.rollback();assert snapshot(db)==before


@pytest.mark.parametrize('field',['serial_no','qr_code'])
def test_wrong_physical_serial_proof_does_not_freeze(db,regional_source,command,field):
    proof=command.serial_verifications[0].model_copy(update={field:'wrong-physical-code'})
    command=command.model_copy(update={'serial_verifications':(proof,)})
    before=snapshot(db)
    with pytest.raises(ERRORS) as error:
        subject.submit(db,actor=regional_source.actor,request=command)
    assert error.value.code=='return_condition_serial_mismatch'
    db.rollback();assert snapshot(db)==before


def test_sku_change_after_source_proof_is_rechecked_under_posting_locks(db,regional_source,command,monkeypatch):
    original=preparation.inspect_submission_source; calls=0
    def changing(db,**kwargs):
        nonlocal calls
        result=original(db,**kwargs);calls+=1
        if calls==2:
            material=db.get(FormalMaterial,regional_source.source.material_id)
            material.sku_code='CONDITION-CHANGED-'+uuid4().hex[:12]
            db.flush()
        return result
    monkeypatch.setattr(preparation,'inspect_submission_source',changing)
    before=snapshot(db)
    with pytest.raises(ERRORS) as error:
        subject.submit(db,actor=regional_source.actor,request=command)
    assert calls==2 and error.value.code=='return_condition_serial_mismatch'
    db.rollback();assert snapshot(db)==before
