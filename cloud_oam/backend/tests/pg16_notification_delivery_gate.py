"""Persisted delivery evidence on the caller's disposable PostgreSQL 16.

Uses the real API role, dispatcher and delivery services. All adapters are
in-memory fakes. Existing queued fixtures are locked by a separate session so
SKIP LOCKED leaves them intact; no production connection or provider is used.
"""
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.foundation_models import NotificationAttempt, NotificationDelivery, NotificationRecipient
from app.formal_services.notification_delivery import (
    NotificationDeliveryError,
    claim_notification_deliveries,
    record_notification_delivery_result,
    retry_failed_notification_delivery,
)
from app.formal_services.notification_delivery_operations import _is_retryable
from app.formal_services.notification_expansion import expand_notification_event
from app.notification_dispatcher import ProviderResult, dispatch_notification_batch
from pg16_notification_expansion_gate import _event


def assert_notification_delivery_gate(api):
    with api.connect() as db:
        assert db.scalar(text("SELECT current_user")) == "star_oam_api"
        assert db.scalar(text("SELECT current_database()")) == "rsc_pg16_release_gate"
        assert int(db.scalar(text("SHOW server_version_num"))) // 10000 == 16
        assert db.scalar(text(
            "SELECT has_table_privilege(current_user,'notification_attempts','UPDATE')"
        )) is False
        assert db.scalar(text(
            "SELECT has_table_privilege(current_user,'notification_attempts','DELETE')"
        )) is False

    factory = sessionmaker(bind=api, expire_on_commit=False)
    cases = []

    def queued():
        event_id = _event(api)
        with factory() as db:
            assert expand_notification_event(db, event_id=event_id).created_delivery_count == 1
            db.commit()
            return db.scalar(select(NotificationDelivery.id).join(NotificationRecipient).where(
                NotificationRecipient.event_id == event_id
            ))

    def dispatch(identifier, result, *, worker):
        with factory() as db:
            recipient = db.scalar(select(NotificationRecipient.recipient_key).join(
                NotificationDelivery, NotificationDelivery.recipient_id == NotificationRecipient.id
            ).where(NotificationDelivery.id == identifier))
        calls = []

        def send(**kwargs):
            # An older queued fixture must never be mistaken for this request.
            assert kwargs['recipient_key'] == recipient
            calls.append(kwargs)
            return result

        outcome = dispatch_notification_batch(factory, worker_id=worker, adapters={'wechat': send})
        assert outcome.claimed == len(calls) == 1 and not outcome.errors
        return outcome

    def reread_unknown(identifier, *, expected_code=None):
        with factory() as db:
            delivery = db.get(NotificationDelivery, identifier)
            attempt = db.scalars(select(NotificationAttempt).where(
                NotificationAttempt.delivery_id == identifier
            )).one()
            assert delivery.status == 'failed' and delivery.attempts == 1
            assert delivery.provider_message_id is None and delivery.sent_at is None
            assert delivery.locked_by is None and delivery.locked_at is None
            assert attempt.response_code == expected_code
            assert attempt.response_jsonb == {'outcome': 'unknown'}
            assert _is_retryable(delivery, attempt) is False
        with factory() as db:
            try:
                retry_failed_notification_delivery(db, delivery_id=identifier, expected_attempt_no=1)
            except NotificationDeliveryError as error:
                assert 'unknown' in str(error)
                db.rollback()
            else:
                raise AssertionError('unknown notification outcome was requeued')
        with factory() as db:
            assert db.get(NotificationDelivery, identifier).status == 'failed'

    # Other release checks deliberately retain queued notification facts. Hold
    # them on their own connection while the real worker opens fresh sessions.
    # Do not delete, cancel, drain or globally rewrite their evidence to isolate
    # this test. The dispatcher must use its actual SKIP LOCKED query.
    with Session(api) as holder:
        existing = tuple(holder.execute(text(
            "SELECT id, to_jsonb(d) AS evidence FROM notification_deliveries d "
            "WHERE status='queued' ORDER BY id FOR UPDATE"
        )).all())
        try:
            for label, value in (
                ('uncertain-503', ProviderResult('503', None, None, 'unknown', True)),
                ('uncertain-message', ProviderResult('200', None, 'unverified', None, True)),
                ('metadata-unknown', ProviderResult('503', {'outcome': 'unknown'}, None, 'unknown', False)),
            ):
                identifier = queued()
                outcome = dispatch(identifier, value, worker=label)
                assert outcome.unknown == 1 and outcome.sent == outcome.failed == 0
                reread_unknown(identifier)
                cases.append(label)

            for legacy in (False, True):
                identifier = queued()
                with factory() as db:
                    claims = claim_notification_deliveries(db, worker_id='direct')
                    assert len(claims) == 1 and claims[0].delivery_id == identifier
                    db.commit()
                with factory() as db:
                    if legacy:
                        # Insert a historical ambiguous shape through real API
                        # grants; never UPDATE an immutable previous attempt.
                        when = datetime.now(timezone.utc)
                        delivery = db.get(NotificationDelivery, identifier)
                        db.add(NotificationAttempt(
                            delivery_id=identifier, attempt_no=1, request_hash='a' * 64,
                            response_code='503', response_jsonb={'outcome': 'unknown'},
                            error='unknown', attempted_at=when, created_at=when,
                        ))
                        delivery.status = 'failed'
                        delivery.last_error = 'unknown'
                        delivery.locked_at = delivery.locked_by = None
                        delivery.updated_at = when
                    else:
                        record_notification_delivery_result(
                            db, delivery_id=identifier, worker_id='direct', request_hash='a' * 64,
                            response_code='200', response_json={'outcome': 'unknown'},
                            provider_message_id='unverified-direct-message',
                        )
                    db.commit()
                reread_unknown(identifier, expected_code='503' if legacy else None)
                cases.append('historical-ambiguous-preserved' if legacy else 'direct-result-normalized')
                if legacy:
                    with factory() as db:
                        prior = db.execute(text(
                            'SELECT to_jsonb(a) FROM notification_attempts a WHERE delivery_id=:id'
                        ), {'id': identifier}).scalar_one()
                        db.get(NotificationDelivery, identifier).status = 'queued'
                        db.commit()
                    healthy_id = queued()
                    outcome = dispatch(healthy_id, ProviderResult.accepted(
                        response_code='200', provider_message_id='independent-observed'
                    ), worker='historical-queued')
                    assert outcome.sent == 1 and outcome.unknown == outcome.failed == 0
                    reread_unknown(identifier, expected_code='503')
                    with factory() as db:
                        healthy = db.get(NotificationDelivery, healthy_id)
                        assert healthy.status == 'sent' and healthy.attempts == 1
                        assert db.execute(text(
                            'SELECT to_jsonb(a) FROM notification_attempts a WHERE delivery_id=:id'
                        ), {'id': identifier}).scalar_one() == prior
                    cases.append('historical-already-queued-blocked-independent-delivery-progresses')

            identifier = queued()
            outcome = dispatch(identifier, ProviderResult.rejected(
                error='definitely rejected', response_code='503'
            ), worker='definite')
            assert outcome.failed == 1 and outcome.unknown == outcome.sent == 0
            with factory() as db:
                delivery = db.get(NotificationDelivery, identifier)
                first_attempt = db.scalars(select(NotificationAttempt).where(
                    NotificationAttempt.delivery_id == identifier
                )).one()
                assert delivery.status == 'failed' and _is_retryable(delivery, first_attempt)
                retry_failed_notification_delivery(db, delivery_id=identifier, expected_attempt_no=1)
                db.commit()
            with factory() as db:
                assert db.get(NotificationDelivery, identifier).status == 'queued'
                assert db.scalar(select(func.count()).select_from(NotificationAttempt).where(
                    NotificationAttempt.delivery_id == identifier
                )) == 1
            cases.append('definite-rejection-requeues-without-new-send')
            outcome = dispatch(identifier, ProviderResult.accepted(
                response_code='200', provider_message_id='definite-retry-observed'
            ), worker='definite-retry')
            assert outcome.sent == 1 and outcome.failed == outcome.unknown == 0
            with factory() as db:
                delivery = db.get(NotificationDelivery, identifier)
                assert delivery.status == 'sent' and delivery.attempts == 2
                attempts = tuple(db.scalars(select(NotificationAttempt).where(
                    NotificationAttempt.delivery_id == identifier
                ).order_by(NotificationAttempt.attempt_no)))
                assert len(attempts) == 2 and attempts[0].response_code == '503'
                assert attempts[1].response_code == '200'
            cases.append('definite-retry-exactly-one-new-attempt')
            with factory() as db:
                for row in existing:
                    assert db.execute(text(
                        'SELECT to_jsonb(d) FROM notification_deliveries d WHERE id=:id'
                    ), {'id': row.id}).scalar_one() == row.evidence
        finally:
            holder.rollback()
    print(f'PG16 notification delivery: {len(cases)} persisted outcomes PASS; '
          f'{len(existing)} pre-existing queued fixtures unchanged; external provider calls 0', flush=True)
    return tuple(cases)
