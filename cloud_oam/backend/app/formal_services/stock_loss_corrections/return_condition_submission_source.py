"""Regional custodian's exact source preparation, without headquarters access.

The caller supplies only an inbound-line UUID. Scope and the original root are
loaded from persisted links before the internal full historical verifier runs.
Only the selected source evidence is returned, never the headquarters history
view. This ordinary, non-mutating transaction takes opening-proof locks and
must be rolled back promptly. A successful preparation does not permit posting.
"""
from datetime import datetime, timezone
import json

from sqlalchemy import select

from app.inventory_models import StockAccount
from app.stock_operation_models import (
    StockLossDisposition, StockOperationShipment,
    StockOperationReturnInbound, StockOperationReturnInboundLine,
)
from app.formal_services import inventory_query as inventory, stock_loss_sources as sources
from . import return_condition_authority as authority, return_condition_source as source, return_history
from .historical_original import _bound
from .return_condition_reclaim_source import reconcile


def _references(db, admission):
    line = db.get(StockOperationReturnInboundLine, admission.inbound_line_id, populate_existing=True)
    header = db.get(StockOperationReturnInbound, line.inbound_id, populate_existing=True) if line else None
    if (header is None or header.status != 'posted' or line.target_account_id != admission.source_account_id
            or header.target_location_id != admission.location_id
            or header.operator_person_id != admission.custodian_person_id):
        return_history.changed()
    roots = tuple(db.scalars(select(StockLossDisposition).join(StockOperationShipment,
        StockOperationShipment.operation_id == StockLossDisposition.return_operation_id).where(
            StockOperationShipment.id == header.shipment_id,
            StockLossDisposition.disposition == 'return_to_region')
        .limit(2).execution_options(populate_existing=True)))
    if len(roots) != 1:
        source._fail('exception_not_found', '该入库明细没有唯一的原报损退回处置', 404)
    root = roots[0]
    # Capture immutable coordinates before later ORM refreshes can change any
    # of these instances. The complete graph separately proves every edge.
    coordinates = (root.id, root.return_operation_id, header.id, header.shipment_id,
        header.receipt_id, line.id, line.receipt_line_id, line.target_account_id)
    return root, line, header, coordinates


def _issue(quality, line, header):
    selected = tuple(row for row in quality if row.inbound_line_id == line.id)
    if (len(selected) != 1 or selected[0].inbound_id != header.id
            or selected[0].receipt_line_id != line.receipt_line_id
            or selected[0].original_target_account_id != line.target_account_id):
        source._fail('exception_not_found', '所选入库明细不是该原处置的准确历史成色异常', 404)
    return selected[0]


def inspect_submission_source(db, *, actor, inbound_line_id):
    """Prove one authorized source without accepting caller-supplied scope/root.

No correction case, reservation, account, audit, notification or request is
created. The write service must reload and lock all relevant facts at admission
and COMMIT; it must never accept this JSON as independently trusted proof.
"""
    with db.no_autoflush:
        admitted = authority.authorize_submission(db, actor=actor, inbound_line_id=inbound_line_id)
        account = db.get(StockAccount, admitted.source_account_id, populate_existing=True)
        if account is None or source._authorize(db, admitted.actor, account) != admitted.actor:
            return_history.changed()
        root, line, header, coordinates = _references(db, admitted)
        before = _bound(db)
        _, fingerprint, _, quality = return_history._verified_graph(db, root)
        issue = _issue(quality, line, header)
        snapshot = inventory._projection_snapshot(db)
        if snapshot.ledger_cursor != before[0]:
            return_history.changed()
        current, basis = source._current(db, issue=issue, line=line, header=header,
            snapshot=snapshot, actor=admitted.actor)
        basis = reconcile(db, actor=current, inbound_line_id=inbound_line_id, document=basis)
        if current != admitted.actor:
            return_history.changed()
        latest = authority.authorize_submission(db, actor=current, inbound_line_id=inbound_line_id)
        fresh_root, fresh_line, fresh_header, fresh_coordinates = _references(db, latest)
        if latest != admitted or fresh_coordinates != coordinates:
            return_history.changed()
        _, fresh_fingerprint, _, fresh_quality = return_history._verified_graph(db, fresh_root)
        fresh_issue = _issue(fresh_quality, fresh_line, fresh_header)
        if fresh_fingerprint != fingerprint or fresh_issue != issue:
            return_history.changed()
        final_actor, fresh_basis = source._current(db, issue=fresh_issue, line=fresh_line,
            header=fresh_header, snapshot=snapshot, actor=latest.actor)
        fresh_basis = reconcile(db, actor=final_actor, inbound_line_id=inbound_line_id, document=fresh_basis)
        final = authority.authorize_submission(db, actor=final_actor, inbound_line_id=inbound_line_id)
        if final != admitted or fresh_basis != basis or _bound(db) != before:
            return_history.changed()
        inventory._ensure_projection_snapshot_current(db, snapshot)
        selection = source.ConditionSourceSelection(root_disposition_id=coordinates[0],
            inbound_line_id=inbound_line_id, expected_history_fingerprint=fingerprint)
        document = dict(schema_version='return_condition_submission_source/1', stage='source_evidence_only',
            selection=selection.model_dump(mode='json'), actor_user_id=final.actor.user_id,
            actor_person_id=str(final.actor.person_id), authorization_version=final.actor.authorization_version,
            inbound_id=str(coordinates[2]), receipt_line_id=str(issue.receipt_line_id),
            submission_permission_checked=True, **basis)
        return source.ConditionSourceEvidence(json.dumps(document, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')), sources._hash(document), datetime.now(timezone.utc))
