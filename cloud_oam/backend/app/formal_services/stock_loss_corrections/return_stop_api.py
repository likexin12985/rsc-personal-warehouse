"""Authorized query and preview for the dedicated whole unshipped return stop."""
from datetime import datetime,timezone
from sqlalchemy import select,or_
from app.stock_operation_models import StockLossDisposition,StockOperationOrder
from app.stock_loss_return_stop_models import StockLossReturnStop as Stop
from app.stock_loss_return_stop_schemas import ReturnStopSources,ReturnStopPreview
from app.stock_loss_correction_http_schemas import InversePreview,public_preview
from app.formal_services import stock_loss_review_query as reviews,stock_loss_sources as sources
from app.formal_services.work_order_query import _aware
from .correction_models import StockLossDispositionReversal as Inverse
from .historical_original import _bound
from .inverse_recovery import _authorize
from .request_contracts import ReversalPreview,validate
from . import return_history,return_stop,reversal_stock,request_authority


def _stops(db,root):
    rows=tuple(db.scalars(select(Stop).where(or_(Stop.root_disposition_id==root.id,
        Stop.return_operation_id==root.return_operation_id)).order_by(Stop.id).limit(2)
        .execution_options(populate_existing=True)))
    if len(rows)>1:return_history.invalid()
    signature=tuple(tuple(getattr(row,column.key) for column in Stop.__table__.columns) for row in rows)
    return rows,signature


def read(db,*,actor,root_disposition_id):
    with db.no_autoflush:
        current,owners=reviews._scope(db,actor,'headquarters')
        before=_bound(db)
        history=return_history.read(db,actor=current,root_disposition_id=root_disposition_id)
        root=db.get(StockLossDisposition,root_disposition_id,populate_existing=True)
        if root is None:return_history.changed()
        order=db.get(StockOperationOrder,root.operation_id,populate_existing=True)
        if order is None:return_history.changed()
        current=_authorize(db,current,order)
        if len(history.lines)!=1:return_history.invalid()
        line=history.lines[0];serials=tuple(sorted((identifier for share in line.shares for identifier in share.serial_ids),key=str))
        rows,signature=_stops(db,root);stop=None;reference=None
        if rows:
            row=rows[0];inverse=db.get(Inverse,row.reversal_id,populate_existing=True)
            if inverse is None:return_history.invalid()
            return_stop.verify(db,root=root,inverse=inverse)
            stop=dict(stop_id=row.id,reversal_id=inverse.id,posting_transaction_id=inverse.posting_transaction_id,
                stopped_at=_aware(row.created_at),reason=inverse.reason,quantity=inverse.quantity,
                serial_ids=serials,evidence_fingerprint=row.evidence_fingerprint)
            state='stopped'
        elif any(identifiers for _,identifiers in history.coordinates):
            state='downstream_compensation_required'
        else:
            if any(share.quantity for share in line.shares if share.stage!='not_outbound'):
                return_history.invalid()
            state='preview_required'
            reference=dict(root_disposition_id=root.id,expected_root_request_hash=root.request_hash,
                expected_submission_plan_hash=order.plan_hash,reversed_correction_id=None,
                expected_execution_request_hash=root.request_hash)
        result=ReturnStopSources(person_id=current.person_id,authorization_version=current.authorization_version,
            queried_at=datetime.now(timezone.utc),observed_ledger_cursor=history.observed_ledger_cursor,
            root_disposition_id=root.id,report_operation_id=order.id,report_line_id=root.line_id,
            return_operation_id=root.return_operation_id,return_line_id=line.operation_line_id,
            quantity=root.quantity,serial_ids=serials,state=state,preview_reference=reference,stop=stop)
        latest,latest_owners=reviews._scope(db,current,'headquarters');latest=_authorize(db,latest,order)
        if latest!=current or latest_owners!=owners or _bound(db)!=before or _stops(db,root)[1]!=signature or return_history._capture(db,root)[1]!=history.evidence_fingerprint:
            return_history.changed()
        return result


def preview(db,*,actor,request):
    request=validate(request)
    if type(request) is not ReversalPreview:raise ValueError('exact return-stop selection required')
    refs=request_authority.references(db,actor=actor,request=request)
    if refs.execution is not refs.root or refs.root.disposition!='return_to_region':
        sources._fail('loss_return_stop_requires_original_return','本入口仅处理尚未出库的准确原报损退回',412)
    prepared=reversal_stock.prepare(db,actor=refs.actor,request=request)
    boundary=prepared.document['return_boundary']
    generic=public_preview(prepared,InversePreview)
    return ReturnStopPreview(**generic.model_dump(),return_operation_id=boundary['operation_id'],
        return_line_id=boundary['lines'][0]['operation_line_id'])
