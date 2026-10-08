"""Canonical independent recovery requests and ordered approval history.

These are internal evidence checks, not an authorized lookup endpoint. No
historical reviewer is required to retain today's permissions.
"""
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID
from sqlalchemy import select
from app.foundation_models import FileObject
from app.stock_scrap_recovery_schemas import ScrapRecoveryApply, ScrapRecoveryRegionalReview, ScrapRecoveryHeadquartersReview
from app.formal_services import stock_loss_sources as sources
from app.formal_services.stock_loss_plan import _evidence
from app.formal_services.work_order_query import _aware
from . import recovery_events as events
from .tables import tables

CONTRACTS = dict(apply=ScrapRecoveryApply, regional=ScrapRecoveryRegionalReview, headquarters=ScrapRecoveryHeadquartersReview)
NAMES = dict(apply='stock_scrap_recovery_requests', regional='stock_scrap_recovery_regional_reviews',
    headquarters='stock_scrap_recovery_headquarters_reviews')


def need(value):
    if not value:
        raise ValueError('stock_scrap_recovery_evidence_invalid')


def intent(request):
    value = request.model_dump(mode='json', exclude={'idempotency_key'})
    if 'evidence_file_ids' in value:
        value['evidence_file_ids'].sort()
    return value


def command(row, stage, source):
    parsed = CONTRACTS[stage].model_validate(dict(row['command_jsonb'], idempotency_key='historical-proof-only'))
    need(intent(parsed) == row['command_jsonb'] and sources._hash(intent(parsed)) == row['request_hash']
        and parsed.request_id == row['request_id'] and parsed.reason == row['reason']
        and parsed.source.scrap_line_id == row['scrap_line_id'] == source['line']['id']
        and parsed.source.expected_scrap_request_hash == source['fact'].request_hash
        and row['authorization_version'] > 0 and UUID(row['actor_user_id']).int > 0
        and row['actor_person_id'].int > 0 and len(row['idempotency_key_hash']) == 64
        and _aware(row['created_at']) >= _aware(source['line']['created_at']))
    if stage == 'apply':
        need(row['actor_person_id'] == source['order'].requester_id
            and row['expected_scrap_request_hash'] == parsed.source.expected_scrap_request_hash)
    else:
        need(parsed.recovery_request_id == row['recovery_request_id'] and parsed.decision == row['decision'])
        if stage == 'regional':
            need(parsed.expected_request_hash == row['expected_request_hash'])
        else:
            need(parsed.regional_review_id == row['regional_review_id']
                and parsed.expected_regional_hash == row['expected_regional_hash'] and row['regional_decision'] == 'verified')
    return parsed


def distinct(row, other):
    need(row['actor_user_id'] != other['actor_user_id'] and row['actor_person_id'] != other['actor_person_id'])


def rows(db, stage, *conditions):
    table = tables()[NAMES[stage]]
    return [dict(r) for r in db.execute(select(table).where(*conditions)
        .order_by(table.c.created_at, table.c.id)).mappings()]


def verify_application(db, *, row, source):
    request = command(row, 'apply', source)
    data = _evidence(db, SimpleNamespace(user_id=row['actor_user_id'], person_id=row['actor_person_id'],
        authorization_version=row['authorization_version']), request.evidence_file_ids)
    table = tables()['stock_scrap_recovery_files']
    bound = db.execute(select(table).where(table.c.recovery_request_id == row['id']).order_by(table.c.file_id)).mappings().all()
    need([(r['file_id'], r['metadata_sha256']) for r in bound] == [(UUID(r['file_id']), r['metadata_sha256']) for r in data]
        and all(_aware(r['created_at']) == _aware(row['created_at']) for r in bound))
    for identifier in request.evidence_file_ids:
        file = db.get(FileObject, identifier, populate_existing=True)
        need(_aware(file.created_at) <= _aware(datetime.fromisoformat(file.metadata_jsonb['completion']['verified_at'])) <= _aware(row['created_at']))
    events.verify(db, row=row, stage='apply', recipient=row['actor_person_id'])
    return request


def verify_history(db, *, application, source):
    """Return current approval stage only after proving every earlier decision."""
    verify_application(db, row=application, source=source)
    regional_table, hq_table = (tables()[NAMES[k]] for k in ('regional', 'headquarters'))
    regional = rows(db, 'regional', regional_table.c.recovery_request_id == application['id'])
    headquarters = rows(db, 'headquarters', hq_table.c.recovery_request_id == application['id'])
    by_region = {r['regional_review_id']: r for r in headquarters}
    need(len(by_region) == len(headquarters))
    stage, previous_at, last_region, last_hq = 'awaiting_regional', _aware(application['created_at']), None, None
    consumed = set()
    for row in regional:
        value = command(row, 'regional', source)
        distinct(row, application)
        need(stage == 'awaiting_regional' and _aware(row['created_at']) > previous_at
            and value.expected_request_hash == application['request_hash'])
        events.verify(db, row=row, stage='regional', recipient=application['actor_person_id'])
        stage = events.state(row, 'regional')[1]
        last_region, last_hq, previous_at = row, None, _aware(row['created_at'])
        final = by_region.get(row['id'])
        if final is None:
            continue
        value = command(final, 'headquarters', source)
        distinct(final, application)
        distinct(final, row)
        need(stage == 'awaiting_headquarters' and _aware(final['created_at']) > previous_at
            and value.expected_request_hash == application['request_hash'] and value.expected_regional_hash == row['request_hash'])
        events.verify(db, row=final, stage='headquarters', recipient=application['actor_person_id'])
        stage, last_hq, previous_at = events.state(final, 'headquarters')[1], final, _aware(final['created_at'])
        consumed.add(final['id'])
    need(consumed == {r['id'] for r in headquarters})
    return stage, last_region, last_hq, previous_at
