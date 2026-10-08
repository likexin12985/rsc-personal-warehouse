"""Candidate old-command reader with mandatory new-registry collision proof.

Only use after the full candidate schema is installed. The formal 0164 reader
remains unchanged until the new migration and route activation are ready.
There is no missing-table fallback, registration, locking or write permission.
"""
import hashlib
from sqlalchemy.exc import DBAPIError
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from app.formal_services.stock_loss_corrections import bound_recovery as legacy, inverse_recovery
from app.formal_services.stock_loss_corrections.historical_original import _bound
from app.formal_services.stock_loss_corrections.request_contracts import validate
from . import binding_reads, seal_reads
from .lookup_coordinates import keys


def lookup(db, *, actor, request):
    return _lookup(db, actor=actor, request=request, stopped_return=False)


def lookup_unshipped_return(db, *, actor, request):
    return _lookup(db, actor=actor, request=request, stopped_return=True)


def _lookup(db, *, actor, request, stopped_return):
    request = validate(request)
    with db.no_autoflush:
        before = _bound(db)
        handler = legacy.lookup_unshipped_return if stopped_return else legacy.lookup
        result = handler(db, actor=actor, request=request)
        root = db.get(StockLossDisposition, request.root_disposition_id, populate_existing=True)
        order = db.get(StockOperationOrder, root.operation_id, populate_existing=True) if root else None
        if order is None:
            legacy._unknown()
        token = hashlib.sha256(('cloud_oam.loss.correction.key.v1\0' + request.idempotency_key).encode()).hexdigest()
        observed = []
        try:
            for _ in range(2):
                current = inverse_recovery._authorize(db, actor, order)
                observed.append(binding_reads.verify(db, actor=current, request=request,
                    hashes=keys(request), token=token, unknown=legacy._unknown))
                if seal_reads.candidates(db,actor=current,command=request):
                    legacy._unknown()
            inverse_recovery._authorize(db, actor, order)
            if observed[0] != observed[1] or _bound(db) != before:
                legacy._unknown()
        except DBAPIError:
            legacy._unknown()
        return result
