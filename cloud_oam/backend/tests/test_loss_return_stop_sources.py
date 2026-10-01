"""Real synthetic stock history and query-only return-stop projection."""
from uuid import UUID
import pytest
from sqlalchemy import select,text
from app.stock_loss_return_stop_models import StockLossReturnStop as Stop
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import return_stop_api,inverse_posting
from app.formal_services.stock_loss_corrections.correction_models import StockLossDispositionReversal as Inverse
from test_loss_return_stop import db,world,stock,allowed,evidence,regional,headquarters,approved,route,execution,prepared,command,snapshot,reader
from test_stock_loss_original_history_recovery import bind_fixture

pytestmark=pytest.mark.parametrize('execution',['return_to_region'],indirect=True)


def test_sources_and_preview_are_readonly_and_stop_preserves_original_history(db,prepared):
    w=prepared;actor=reader(db,w.actor);before=snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        source=return_stop_api.read(db,actor=actor,root_disposition_id=w.root.id)
        preview=return_stop_api.preview(db,actor=actor,request=w.request)
        assert source.state=='preview_required' and source.stop is None
        assert source.preview_reference.expected_root_request_hash==w.root.request_hash
        assert preview.return_operation_id==w.root.return_operation_id
        assert preview.return_line_id==source.return_line_id
        assert preview.quantity==source.quantity==w.root.quantity
        assert preview.stock_effect=='none' and preview.planned_stock_effect=='return_pending_to_original_frozen'
        assert snapshot(db)==before and not db.new and not db.dirty and not db.deleted
    finally:db.execute(text('PRAGMA query_only=OFF'))
    request=command(db,w);posted=inverse_posting.execute_unshipped_return_inverse(db,actor=actor,request=request);db.commit()
    bind_fixture(db,db.get(Inverse,UUID(posted['reversal_id'])),request,'inverse')
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    try:
        stopped=return_stop_api.read(db,actor=actor,root_disposition_id=w.root.id)
        assert stopped.state=='stopped' and stopped.preview_reference is None
        assert str(stopped.stop.reversal_id)==posted['reversal_id']
        assert stopped.stop.quantity==w.root.quantity and stopped.stop.serial_ids==source.serial_ids
        assert stopped.stop.historical_stock_effect=='return_pending_to_original_frozen'
        assert stopped.current_stock_verified is False and stopped.write_authorization_provided is False
        assert snapshot(db)==before and not db.new and not db.dirty and not db.deleted
        public=stopped.model_dump_json()
        assert request.idempotency_key not in public and request.request_id not in public
        assert all(secret not in public for secret in ('command_jsonb','plan_jsonb','key_hash'))
    finally:db.execute(text('PRAGMA query_only=OFF'))


def test_missing_or_forged_stop_is_not_reported_as_available(db,prepared):
    w=prepared;actor=reader(db,w.actor);request=command(db,w)
    posted=inverse_posting.execute_unshipped_return_inverse(db,actor=actor,request=request);db.commit()
    bind_fixture(db,db.get(Inverse,UUID(posted['reversal_id'])),request,'inverse')
    for broken in ('missing','fingerprint'):
        savepoint=db.begin_nested()
        try:
            row=db.scalars(select(Stop)).one()
            if broken=='missing':db.delete(row)
            else:row.evidence_fingerprint='f'*64
            db.flush();before=snapshot(db)
            with pytest.raises(InventoryReadError) as error:
                return_stop_api.read(db,actor=actor,root_disposition_id=w.root.id)
            assert error.value.status_code==503
            assert snapshot(db)==before
        finally:savepoint.rollback();db.expire_all()
