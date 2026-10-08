"""Download authority from an exact immutable event and current regional scope.

An uploader has no unbound-file fallback. Historical actor coordinates prove
the attachment; the current reader's authority separately permits disclosure.
The outer file service locks the file/current principal and audits the grant.
"""
from sqlalchemy import select

from formal_file_integrity import _fail
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from . import return_condition_history as graph
from . import return_condition_history_read as history
from .historical_original import _bound
from .return_condition_event_files import _tables, read_event_evidence


def _binding(db, file_id):
    events, files = _tables()
    # Query the binding before joining so an orphan cannot disappear in a join.
    rows = tuple(db.execute(select(files).where(files.c.file_id == file_id)
        .limit(2)).mappings())
    if not rows:
        _fail('file_purpose_forbidden', 'forbidden', '附件尚未绑定已核验的成色纠正事件')
    if len(rows) != 1:
        _fail('condition_event_evidence_invalid', 'precondition_failed', '纠正附件事件绑定不唯一')
    binding = dict(rows[0])
    event = db.execute(select(events).where(events.c.id == binding['event_id'])).mappings().one_or_none()
    if event is None:
        _fail('condition_event_evidence_invalid', 'precondition_failed', '纠正附件原事件不存在')
    return binding, dict(event)


def authorize_download(db, *, actor, row):
    with db.no_autoflush:
        try:
            binding, event = _binding(db, row.id)
            boundary = _bound(db)
            verified = history.read(db, actor=actor, inbound_line_id=event['inbound_line_id'])
            if event['id'] not in verified.graph.event_ids:
                graph.invalid()
            proofs = read_event_evidence(db, event_id=event['id'])
            matches = [p for p in proofs if p.file_id == row.id]
            if len(matches) != 1 or matches[0].metadata_sha256 != binding['metadata_sha256']:
                graph.invalid()
            fresh_binding, fresh_event = _binding(db, row.id)
            _, digest = graph.capture(db, event['inbound_line_id'])
            current, _, _ = history._scope(db, actor, event['inbound_line_id'])
            if (current != actor or fresh_binding != binding or fresh_event != event
                    or digest != verified.graph.fingerprint or _bound(db) != boundary):
                graph.changed()
        except (InventoryReadError, InventoryPostingError) as exc:
            status = getattr(exc, 'http_status_code', getattr(exc, 'status_code', None))
            category = {403: 'forbidden', 404: 'not_found', 409: 'conflict'}.get(status, 'service_unavailable')
            _fail('condition_file_' + category, category, '无法核验该成色纠正附件的当前查看权限或完整历史')
        return {'binding_type': 'return_condition_event', 'event_id': str(event['id']),
                'case_id': str(event['case_id']), 'inbound_line_id': str(event['inbound_line_id'])}
