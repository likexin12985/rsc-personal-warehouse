"""Bound loss evidence authorization; URL issuance and audit stay in file service."""
from sqlalchemy import select

from ..stock_operation_models import StockLossFile, StockOperationOrder
from . import stock_loss_facts as facts, stock_loss_review_query as query
from .stock_loss_recovery import authorize_lookup, _cursor
from .stock_loss_review_recovery import _authorize
from .inventory_query import InventoryReadError
from .inventory_posting import InventoryPostingError
from formal_file_integrity import _fail


def authorize_bound_loss_evidence(db, *, actor, row, foreign_bindings_present):
    try:
        return _authorize_bound_loss_evidence(db, actor=actor, row=row,
            foreign_bindings_present=foreign_bindings_present)
    except (InventoryReadError, InventoryPostingError) as error:
        status = error.status_code if isinstance(error, InventoryReadError) else error.http_status_code
        category = {403:'forbidden',404:'not_found',409:'conflict'}.get(status,'service_unavailable')
        _fail('file_loss_evidence_unavailable', category, '当前无法核验报损证据的完整性或查看权限')


def _authorize_bound_loss_evidence(db, *, actor, row, foreign_bindings_present):
    """Return None for an unbound upload; retain the existing uploader policy."""
    bindings = tuple(db.scalars(select(StockLossFile).where(StockLossFile.file_id == row.id).limit(2)
        .execution_options(populate_existing=True)))
    if not bindings: return None
    if foreign_bindings_present or len(bindings) != 1 or bindings[0].operation_type != 'loss_report':
        _fail('file_binding_invalid', 'conflict', '报损附件关联证据不一致')
    before = _cursor(db)
    order = db.get(StockOperationOrder, bindings[0].operation_id, populate_existing=True)
    if order is None: _fail('file_binding_invalid', 'conflict', '报损附件原单不存在')
    if actor.user_id == order.actor_user_id and actor.person_id == order.requester_id:
        current = authorize_lookup(db, actor)
        access = 'requester_current_read'
        stage = None
    else:
        current = None
        for stage in ('regional', 'headquarters'):
            try:
                candidate, owners = query._scope(db, actor, stage)
                if not query._rows(db, candidate, owners, limit=1, operation_id=order.id): continue
                current, _ = _authorize(db, candidate, order, stage)
                break
            except InventoryReadError as error:
                if error.status_code != 403: raise
        if current is None:
            _fail('file_download_forbidden', 'forbidden', '当前账号不能查看该报损审核证据')
        access = stage+'_current_read'
    original = facts.submission_evidence(db, order=order)
    if row.id not in {e.file_id for e in original.evidence}:
        _fail('file_binding_invalid', 'conflict', '附件不属于原报损证据')
    latest = authorize_lookup(db, current) if stage is None else _authorize(db, current, order, stage)[0]
    if latest != current or _cursor(db) != before:
        _fail('file_binding_changed', 'conflict', '报损证据或权限在读取期间变化，请重新申请查看')
    return {'binding_type':'stock_loss_report', 'binding_count':1,
        'operation_id':str(order.id), 'access':access}
