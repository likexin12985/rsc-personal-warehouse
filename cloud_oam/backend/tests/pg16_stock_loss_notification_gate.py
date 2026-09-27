"""Project real loss stock events without duplicating their sealed audience.

The caller supplies a fresh owned PG16 disposition fixture. This exercises
ordinary API-role notification writes and COMMIT, never a delivery provider.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.foundation_models import NotificationEvent, NotificationPersonTarget, OutboxEvent
from app.formal_services.inventory_notifications import project_inventory_notification
from app.formal_services.notification_events import target_manifest_hash
from app.formal_services.notification_expansion import expand_notification_event


def run(context):
    from pg16_stock_loss_disposition_gate import snapshot

    owner, api = (context['engines'][name] for name in ('star_oam_migrator', 'star_oam_api'))
    contracts = {
        'stock_loss_submitted': 'stock_operation_order',
        'stock_loss.disposition_posted': 'stock_loss_disposition',
    }
    sources = []
    with Session(api) as db:
        assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
        dedicated = tuple(db.scalars(select(NotificationEvent).where(
            NotificationEvent.event_type.in_(contracts)).order_by(NotificationEvent.id)))
        counts = Counter(row.event_type for row in dedicated)
        assert counts['stock_loss_submitted'] >= 3
        assert counts['stock_loss.disposition_posted'] == 3
        for row in dedicated:
            assert row.business_type == contracts[row.event_type]
            people = tuple(db.scalars(select(NotificationPersonTarget.person_id).where(
                NotificationPersonTarget.event_id == row.id)))
            assert people == (context['person_id'],)
            assert row.target_manifest_sha256 == target_manifest_hash(people)
            tx = UUID(row.payload_jsonb['posting_transaction_id'])
            outbox = db.scalars(select(OutboxEvent).where(
                OutboxEvent.aggregate_type == 'inventory_transaction',
                OutboxEvent.aggregate_id == str(tx),
                OutboxEvent.event_type == 'inventory.transaction.posted')).one()
            assert db.scalar(select(NotificationEvent.id).where(
                NotificationEvent.dedup_key == f'inventory-change:{tx}')) is None
            sources.append((outbox.id, tx))
    assert len({tx for _, tx in sources}) == len(sources)
    before = snapshot(owner)

    def check(db, outbox_id, tx, *, created):
        result = project_inventory_notification(db, outbox_id=outbox_id)
        assert result is not None and result.transaction_id == tx
        assert result.created is created
        assert result.affected_person_count == result.already_notified_person_count == 1
        assert result.recipient_count == 0
        assert not tuple(db.scalars(select(NotificationPersonTarget.id).where(
            NotificationPersonTarget.event_id == result.event_id)))
        event = db.get(NotificationEvent, result.event_id)
        assert event.target_manifest_sha256 == target_manifest_hash(())
        assert expand_notification_event(db, event_id=event.id).created_delivery_count == 0
        return result

    first_id, first_tx = sources[0]
    with Session(api) as db:
        check(db, first_id, first_tx, created=True)
        db.flush()
        db.rollback()
    assert snapshot(owner) == before

    def contender():
        with Session(api) as db:
            db.execute(text("SET LOCAL statement_timeout = '10s'"))
            result = project_inventory_notification(db, outbox_id=first_id)
            db.commit()
            return result

    with Session(api) as db:
        check(db, first_id, first_tx, created=True)
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(contender).result(timeout=15) is None
        db.commit()
    for outbox_id, tx in sources[1:]:
        with Session(api) as db:
            check(db, outbox_id, tx, created=True)
            db.commit()
    for outbox_id, tx in sources:
        with Session(api) as db:
            check(db, outbox_id, tx, created=False)
            db.commit()

    after = snapshot(owner)
    # Only zero-audience generic checkpoints may be added. Original dedicated
    # notifications, targets, recipients, bindings, stock and source evidence
    # remain byte-for-byte unchanged, including after consumer retry/rollback.
    assert set(after) == set(before)
    for table in before:
        if table == 'notification_events':
            assert set(before[table]) <= set(after[table])
            assert len(after[table]) == len(before[table]) + len(sources)
        else:
            assert after[table] == before[table], table
    print('PG16 loss notification ' + context['tracking'] + ': real freeze/disposition targets, '
          'API COMMIT, rollback, concurrent consumer and zero duplicate audience PASS', flush=True)
    return dict(passed=True, apiRole=True, tracking=context['tracking'], sources=len(sources),
        kinds=dict(counts), originalTargetsRetained=True, genericAudienceEmpty=True,
        sameSourceOwnership=True, consumerRollback=True, repeatedExpansionCreatesNoDelivery=True,
        stockAndSourceUnchanged=True, productionAcceptance=False)
