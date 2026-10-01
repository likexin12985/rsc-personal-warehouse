"""Exact current chain references after complete history and current scope proof."""
from datetime import datetime,timezone
from uuid import UUID
from app.stock_operation_models import StockLossDisposition,StockOperationOrder
from app.stock_loss_correction_source_schemas import CorrectionSources
from app.formal_services import stock_loss_review_query as reviews,stock_loss_sources as sources
from app.formal_services.work_order_query import _aware
from .historical_original import _bound
from .history_chain import verify_chain
from .history_events import load_event_checked_inventory_history
from .history_inventory import _candidate_rows
from .chain_projection import project,InvalidChain
from .inverse_recovery import _authorize

ACCOUNT_DISPOSITIONS=frozenset({'restore_available','convert_used','convert_damaged'})


def _changed():
    sources._fail('loss_correction_sources_changed','纠正来源或权限在读取期间变化，请刷新',409)


def _signature(root,order,groups):
    # Immutable row identities alone cannot detect a changed request hash in
    # a malformed fixture or a mixed read. Compare every exposed reference.
    return (root.id,root.request_hash,root.disposition,root.operation_id,root.line_id,
            order.id,order.plan_hash,tuple(tuple((row.id,row.request_hash,row.created_at,
            getattr(row,'reversal_id',None),getattr(row,'correction_decision_id',None),
            getattr(row,'disposition',None),row.reason) for row in group) for group in groups))


def read(db,*,actor,root_disposition_id):
    if type(root_disposition_id) is not UUID or root_disposition_id.int==0:
        sources._fail('loss_correction_source_identifier_invalid','请选择准确原处置',400)
    with db.no_autoflush:
        # Require headquarters read scope before disclosing whether a root exists.
        current,owners=reviews._scope(db,actor,'headquarters')
        before=_bound(db)
        root=db.get(StockLossDisposition,root_disposition_id,populate_existing=True)
        order=db.get(StockOperationOrder,root.operation_id,populate_existing=True) if root else None
        if root is None or order is None:
            sources._fail('loss_correction_source_not_found','准确原处置不存在',404)
        current=_authorize(db,current,order)
        try:
            proof=verify_chain(db,root_disposition_id=root.id)
            loaded=load_event_checked_inventory_history(db,root_disposition_id=root.id)
            h=loaded.history;projection=project(h.basis,h.executions,h.reversals,h.decisions)
            groups=_candidate_rows(db,root.id)
            inverses,decisions,corrections=groups
            if sum(len(group) for group in groups)>1000:
                sources._fail('loss_correction_sources_limit','纠正历史超过完整展示上限，请联系总部核验',503)
            if loaded.observed_ledger_cursor!=proof.observed_ledger_cursor or proof.observed_ledger_cursor!=before[0]:
                _changed()
            if {row.id for row in inverses}!={p.reversal_id for p in proof.inverse_proofs if p.root_disposition_id==root.id}:
                raise InvalidChain('inverse proof mismatch')
            if not {row.id for row in decisions}<=proof.proved_decision_ids:
                raise InvalidChain('approval proof mismatch')
            signature=_signature(root,order,groups)
            binding=dict(root_disposition_id=root.id,expected_root_request_hash=root.request_hash,
                         expected_submission_plan_hash=order.plan_hash)
            inverse_reference=approval_reference=None;choices=[]
            if projection.active_execution_id is not None:
                active=next((row for row in (root,*corrections) if row.id==projection.active_execution_id),None)
                if active is None:raise InvalidChain('active execution missing')
                state='active_execution' if active.disposition in ACCOUNT_DISPOSITIONS else 'dedicated_compensation_required'
                if state=='active_execution':
                    inverse_reference=dict(binding,reversed_correction_id=None if active is root else active.id,
                                           expected_execution_request_hash=active.request_hash)
            else:
                inverse=next((row for row in inverses if row.id==projection.pending_reversal_id),None)
                if inverse is None:raise InvalidChain('pending inverse missing')
                approval_reference=dict(binding,reversal_id=inverse.id,expected_reversal_hash=inverse.request_hash)
                for row in decisions:
                    if row.reversal_id!=inverse.id:continue
                    supported=row.disposition in ACCOUNT_DISPOSITIONS
                    choices.append(dict(correction_decision_id=row.id,disposition=row.disposition,reason=row.reason,
                        execution_mode='preview_required' if supported else 'dedicated_flow_required',
                        preview_reference=dict(approval_reference,correction_decision_id=row.id,
                            expected_correction_decision_hash=row.request_hash) if supported else None))
                state='awaiting_execution' if choices else 'awaiting_approval'
            history=[]
            for kind,rows in (('original_execution',(root,)),('inverse',inverses),('approval',decisions),('correction_execution',corrections)):
                for row in rows:
                    history.append(dict(kind=kind,fact_id=row.id,request_hash=row.request_hash,created_at=_aware(row.created_at),
                        posting_transaction_id=getattr(row,'posting_transaction_id',None),
                        disposition=getattr(row,'disposition',None),quantity=getattr(row,'quantity',None)))
            history.sort(key=lambda row:(row['created_at'],str(row['fact_id'])))
            response=CorrectionSources(person_id=current.person_id,authorization_version=current.authorization_version,
                queried_at=datetime.now(timezone.utc),observed_ledger_cursor=proof.observed_ledger_cursor,
                root_disposition_id=root.id,operation_id=root.operation_id,line_id=root.line_id,quantity=root.quantity,
                serial_ids=tuple(sorted(h.basis.serial_ids,key=str)),frozen_share_in_verified_history=projection.frozen_quantity,
                chain_state=state,inverse_preview_reference=inverse_reference,approval_reference=approval_reference,
                approval_choices=tuple(choices),history=tuple(history))
        except InvalidChain:
            sources._fail('loss_correction_sources_unproven','纠正历史证据不完整，暂不能提供操作来源',503)
        latest,latest_owners=reviews._scope(db,current,'headquarters')
        latest=_authorize(db,latest,order)
        fresh_root=db.get(StockLossDisposition,root.id,populate_existing=True)
        fresh_order=db.get(StockOperationOrder,order.id,populate_existing=True)
        if (latest!=current or latest_owners!=owners or _bound(db)!=before or fresh_root is None or fresh_order is None
                or _signature(fresh_root,fresh_order,_candidate_rows(db,root.id))!=signature):
            _changed()
        return response
