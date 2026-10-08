"""Exact persisted condition-event attachments, not current read authority.

Call only after scoped admission and as part of full condition history proof.
Never use this helper alone to issue a download URL. It checks original actor
coordinates without reauthorizing a historical uploader or reviewer.
"""
from datetime import datetime, timezone
from functools import lru_cache
from uuid import UUID

from sqlalchemy import select

from app.foundation_models import FileObject
from app.return_condition_schema import build_schema
from formal_file_integrity import _fail
from .return_condition_evidence import completed_evidence


@lru_cache(maxsize=1)
def _tables():
    _, (_, events, _, files), _ = build_schema()
    return events, files


def _invalid():
    _fail('condition_event_evidence_invalid', 'precondition_failed', '纠正事件与原完成上传凭证不一致')


def _aware(value):
    if not isinstance(value, datetime):
        _invalid()
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def read_event_evidence(db, *, event_id):
    """Return immutable evidence snapshots for one exact stored event.

    No inference from a caller-provided actor, file list or rewritten command.
    Current permissions, complete state/ledger history and physical evidence
    remain separate proofs required by the eventual scoped reader.
    """
    if type(event_id) is not UUID or not event_id.int:
        raise ValueError('exact nonzero event UUID required')
    events, files = _tables()
    with db.no_autoflush:
        event = db.execute(select(events).where(events.c.id == event_id)).mappings().one_or_none()
        if event is None:
            _invalid()
        bindings = tuple(db.execute(select(files).where(files.c.event_id == event_id)
            .order_by(files.c.file_id).limit(21)).mappings())
        if len(bindings) > 20 or (event['kind'] in ('submit','supplement','verify_region') and not bindings):
            _invalid()
        expected = [dict(file_id=str(b['file_id']),metadata_sha256=b['metadata_sha256']) for b in bindings]
        if not isinstance(event['command_jsonb'],dict) or event['command_jsonb'].get('evidence') != expected:
            _invalid()
        at = _aware(event['created_at'])
        result = []
        for binding in bindings:
            if _aware(binding['created_at']) != at:
                _invalid()
            row = db.get(FileObject, binding['file_id'], populate_existing=True)
            proof = completed_evidence(row, uploader_user_id=event['actor_user_id'],
                uploader_person_id=event['actor_person_id'], authorization_version=event['authorization_version'],
                provider_code='aliyun_oss_v2', recorded_at=at)
            if proof.metadata_sha256 != binding['metadata_sha256']:
                _invalid()
            result.append(proof)
        return tuple(result)
