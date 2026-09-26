import pytest
from sqlalchemy import select
from app.notification_dispatcher import ProviderResult, dispatch_notification_batch, _adapter_result
from app.foundation_models import NotificationDelivery, NotificationAttempt
from app.formal_services.notification_delivery import retry_failed_notification_delivery, NotificationDeliveryError
from test_material_request_approval_service import approval_db
from test_notification_delivery import _queued_delivery
from test_notification_dispatcher import _Provider, _session_factory, NOW


def test_uncertain_503_must_never_requeue(approval_db):
    delivery = _queued_delivery(approval_db)
    factory = _session_factory(approval_db)
    provider = _Provider(ProviderResult(response_code='503', response_json={'outcome':'unknown'},
        provider_message_id=None, error='provider outcome unknown', uncertain=True))
    outcome = dispatch_notification_batch(factory, worker_id='unknown-proof', adapters={'feishu':provider}, now=NOW)
    assert outcome.unknown == 1 and len(provider.calls) == 1
    with factory() as db:
        assert db.get(NotificationDelivery,delivery.id).status == 'failed'
        with pytest.raises(NotificationDeliveryError,match='unknown'):
            retry_failed_notification_delivery(db, delivery_id=delivery.id, expected_attempt_no=1, now=NOW)


def test_unknown_factory_metadata_cannot_override_outcome():
    result = ProviderResult.unknown(error='no observed result',response_json={'outcome':'accepted'})
    assert result.uncertain is True and result.response_json['outcome']=='unknown'


@pytest.mark.parametrize('uncertain,code,message_id,metadata', [
    (True, '503', None, None),
    (True, '200', 'unverified-message', None),
    (False, '503', None, {'outcome':'unknown'}),
    (False, '200', 'unverified-message', {'outcome':'unknown'}),
    ('false', '503', None, None),
    (0, '503', None, None),
    (None, '503', None, None),
])
def test_unknown_or_invalid_uncertainty_is_canonical(uncertain,code,message_id,metadata):
    from types import SimpleNamespace
    provider = _Provider(ProviderResult(response_code=code,response_json=metadata,
        provider_message_id=message_id,error=None if message_id else 'uncertain',uncertain=uncertain))
    claim = SimpleNamespace(channel='feishu',recipient_key='synthetic',payload={})
    result = _adapter_result(provider,claim)
    assert result.uncertain is True and result.response_code is None and result.provider_message_id is None
    assert result.response_json == {'outcome':'unknown'} and len(provider.calls)==1


def test_direct_result_record_cannot_persist_unknown_as_success(approval_db):
    from app.formal_services.notification_delivery import claim_notification_deliveries, record_notification_delivery_result
    delivery = _queued_delivery(approval_db)
    claim_notification_deliveries(approval_db,worker_id='direct',now=NOW)
    record_notification_delivery_result(approval_db, delivery_id=delivery.id,worker_id='direct',request_hash='f'*64,
        response_code='200',response_json={'outcome':'unknown'},provider_message_id='unverified-message',now=NOW)
    approval_db.commit()
    approval_db.expire_all()
    stored=approval_db.get(NotificationDelivery,delivery.id)
    attempt=approval_db.scalar(select(NotificationAttempt).where(NotificationAttempt.delivery_id==delivery.id))
    assert stored.status=='failed' and stored.provider_message_id is None and stored.sent_at is None
    assert attempt.response_code is None and attempt.response_jsonb['outcome']=='unknown'


@pytest.mark.parametrize('code',['429','500','503','599'])
def test_legacy_ambiguous_attempt_is_not_exposed_or_requeued(approval_db,code):
    from app.formal_services.notification_delivery_operations import _is_retryable
    from test_notification_delivery import _failed_delivery
    delivery=_failed_delivery(approval_db,response_code=code)
    attempt=approval_db.scalar(select(NotificationAttempt).where(NotificationAttempt.delivery_id==delivery.id))
    # Synthetic seed of a historical ambiguous record; production attempts
    # are not edited by the fix and their evidence must remain available.
    attempt.response_jsonb={'outcome':'unknown'}
    approval_db.commit()
    assert _is_retryable(delivery,attempt) is False
    with pytest.raises(NotificationDeliveryError,match='unknown'):
        retry_failed_notification_delivery(approval_db,delivery_id=delivery.id,expected_attempt_no=1,now=NOW)
    approval_db.rollback()
    assert approval_db.get(NotificationDelivery,delivery.id).status=='failed'


@pytest.mark.parametrize('code',['429','503'])
def test_definite_rejection_remains_retryable(approval_db,code):
    delivery=_queued_delivery(approval_db);factory=_session_factory(approval_db)
    provider=_Provider(ProviderResult.rejected(error='definitely rejected',response_code=code))
    outcome=dispatch_notification_batch(factory,worker_id='definite',adapters={'feishu':provider},now=NOW)
    assert outcome.failed==1 and outcome.unknown==0
    with factory() as db:
        assert retry_failed_notification_delivery(db,delivery_id=delivery.id,expected_attempt_no=1,now=NOW).status=='queued'


@pytest.mark.parametrize('kind', ['explicit_unknown', 'missing_code', 'missing_attempt'])
def test_historical_unknown_already_queued_never_calls_provider(approval_db,kind):
    from test_notification_delivery import _failed_delivery
    if kind=='missing_attempt':
        delivery=_queued_delivery(approval_db)
        delivery.attempts=1
    else:
        delivery=_failed_delivery(approval_db,response_code=None if kind=='missing_code' else '503')
        if kind=='explicit_unknown':
            attempt=approval_db.scalar(select(NotificationAttempt).where(NotificationAttempt.delivery_id==delivery.id))
            attempt.response_jsonb={'outcome':'unknown'}
    delivery.status='queued'
    factory=_session_factory(approval_db)
    provider=_Provider(ProviderResult.accepted(response_code='200',provider_message_id='must-not-send'))
    outcome=dispatch_notification_batch(factory,worker_id='after-upgrade',adapters={'feishu':provider},now=NOW)
    assert provider.calls==[] and outcome.claimed==0
    with factory() as db:
        stored=db.get(NotificationDelivery,delivery.id)
        assert stored.status=='failed' and stored.attempts==1
        assert stored.last_error=='previous provider outcome unknown'
        assert stored.locked_by is None and stored.locked_at is None
        previous=db.scalar(select(NotificationAttempt).where(NotificationAttempt.delivery_id==delivery.id))
        assert (previous is None)==(kind=='missing_attempt')
        if kind=='explicit_unknown':
            assert previous.response_code=='503' and previous.response_jsonb=={'outcome':'unknown'}


def test_unknown_queue_entry_does_not_block_an_independent_delivery(approval_db):
    from test_notification_delivery import _failed_delivery
    unknown=_failed_delivery(approval_db,response_code=None)
    unknown.status='queued'
    healthy=_queued_delivery(approval_db)
    factory=_session_factory(approval_db)
    provider=_Provider(ProviderResult.accepted(response_code='200',provider_message_id='new-observed-message'))
    outcome=dispatch_notification_batch(factory,worker_id='independent',adapters={'feishu':provider},now=NOW)
    assert outcome.claimed==outcome.sent==len(provider.calls)==1
    with factory() as db:
        assert db.get(NotificationDelivery,unknown.id).status=='failed'
        assert db.get(NotificationDelivery,unknown.id).attempts==1
        assert db.get(NotificationDelivery,healthy.id).status=='sent'
        assert db.get(NotificationDelivery,healthy.id).attempts==1


def test_definitive_retry_is_claimed_and_sent_once(approval_db):
    from test_notification_delivery import _failed_delivery
    delivery=_failed_delivery(approval_db,response_code='503')
    retry_failed_notification_delivery(approval_db,delivery_id=delivery.id,expected_attempt_no=1,now=NOW)
    factory=_session_factory(approval_db)
    provider=_Provider(ProviderResult.accepted(response_code='200',provider_message_id='definite-retry-message'))
    outcome=dispatch_notification_batch(factory,worker_id='definite-retry',adapters={'feishu':provider},now=NOW)
    assert outcome.claimed==outcome.sent==len(provider.calls)==1
    with factory() as db:
        stored=db.get(NotificationDelivery,delivery.id)
        assert stored.status=='sent' and stored.attempts==2
        attempts=tuple(db.scalars(select(NotificationAttempt).where(NotificationAttempt.delivery_id==delivery.id).order_by(NotificationAttempt.attempt_no)))
        assert len(attempts)==2 and attempts[0].response_code=='503' and attempts[1].response_code=='200'
