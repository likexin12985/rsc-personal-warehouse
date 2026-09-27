"""Current whole-command preview; never a submission, freeze or approval.

The eventual submit path must reload this proof under posting locks, create
real FK attachment bindings and prove the complete atomic command at COMMIT.
"""
from datetime import datetime, timezone

from ..foundation_models import FileObject
from ..stock_loss_schemas import (
    StockLossEvidenceOut, StockLossPreviewIn, StockLossPreviewOut, StockLossSelectionIn,
)
from . import stock_loss_sources as sources, inventory_query as inventory
from .formal_files import is_available_formal_file_for_purpose
from .stock_loss_evidence import PURPOSE
from .work_order_query import _aware


def intent(request):
    clean = StockLossPreviewIn.model_validate(request.model_dump(include=set(StockLossPreviewIn.model_fields)))
    value = clean.model_dump(mode='json')
    value['evidence_file_ids'].sort()
    value['lines'] = []
    for row in sorted(clean.lines, key=lambda item: str(item.stock_account_id)):
        line = row.model_dump(mode='json')
        line['quantity'] = format(row.quantity, '.3f')
        line['serial_verifications'].sort(key=lambda proof: proof['serial_id'])
        value['lines'].append(line)
    return dict(operation_type='loss_report', **value)


def _evidence(db, actor, identifiers):
    result = []
    for identifier in sorted(identifiers, key=str):
        row = db.get(FileObject, identifier, populate_existing=True)
        if (not is_available_formal_file_for_purpose(row, purpose=PURPOSE, uploader_user_id=actor.user_id)
                or row.metadata_jsonb.get('provider') != 'aliyun_oss_v2'
                or row.metadata_jsonb.get('uploader_person_id') != str(actor.person_id)
                or row.metadata_jsonb.get('authorization_version') != actor.authorization_version
                or not _aware(row.created_at) <= datetime.fromisoformat(
                    row.metadata_jsonb['completion']['verified_at']) <= datetime.now(timezone.utc)):
            sources._fail('stock_loss_evidence_invalid', '报损证据必须是本人当前权限下已完成上传的专用文件')
        result.append(dict(file_id=str(row.id), original_filename=row.original_filename,
            sha256=row.sha256, size_bytes=row.size_bytes, mime_type=row.mime_type,
            metadata_sha256=sources._hash(row.metadata_jsonb)))
    return result


def preview_loss(db, *, actor, request):
    request = StockLossPreviewIn.model_validate(request.model_dump(include=set(StockLossPreviewIn.model_fields)))
    current = sources.authorize(db, actor)
    value = intent(request)
    selection_request = StockLossSelectionIn(operator_person_id=request.operator_person_id, lines=request.lines)
    with db.no_autoflush:
        selected = sources.preview_selection(db, actor=current, request=selection_request)
        evidence = _evidence(db, current, request.evidence_file_ids)
        # Source and file reads can observe different READ COMMITTED snapshots.
        # Compare both again after traversing the other half of the command.
        latest = sources.preview_selection(db, actor=current, request=selection_request)
        latest_evidence = _evidence(db, current, request.evidence_file_ids)
        if (latest.basis_hash != selected.basis_hash or latest_evidence != evidence
                or latest.authorization_version != selected.authorization_version):
            sources._fail('stock_loss_plan_changed', '报损库存、证据或权限在预检期间变化，请重新核验整批内容')
        inventory._ensure_projection_snapshot_current(db, inventory._ProjectionSnapshot(selected.ledger_cursor, None))
        sources.authorize(db, current)
        document = dict(intent=value, authorization_version=current.authorization_version,
            location_id=str(selected.location_id), ledger_cursor=selected.ledger_cursor,
            source_basis_hash=selected.basis_hash, evidence=evidence,
            lines=[row.model_dump(mode='json') for row in selected.lines])
        return StockLossPreviewOut(operator_person_id=current.person_id,
            authorization_version=current.authorization_version, location_id=selected.location_id,
            ledger_cursor=selected.ledger_cursor, checked_at=datetime.now(timezone.utc),
            reason=request.reason, request_hash=sources._hash(value), plan_hash=sources._hash(document),
            lines=selected.lines, evidence=tuple(StockLossEvidenceOut(**row) for row in evidence)), document
