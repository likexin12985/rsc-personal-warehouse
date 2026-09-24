"""A changed deployment profile cannot verify or send a persisted old challenge."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func,select

from app.formal_services import authentication_challenge as service
from app.foundation_models import AuditEvent,AuthRefreshToken,LoginChallenge,SmsChallengeDispatch,StateTransitionEvent
from app.models import AuthSession
from app.routers import auth
from test_authentication_challenge import db,_formal_user,_sent_challenge,_prepare,MOBILE_HASH,NOW
from test_formal_auth_api import api_world,_seed_mobile_subject,_request_sms,_login_sms_web,MOBILE


@pytest.mark.parametrize('current',[None,'f'*64,'E'*64,'missing',False])
@pytest.mark.parametrize('status',['accepted','uncertain','sending'])
def test_profile_drift_preserves_challenge_attempt_and_unknown_state(db,current,status):
    user=_formal_user(db);db.commit()
    if status=='accepted':prepared=_sent_challenge(db,user)
    else:
        prepared=_prepare(db,user=user)
        owner=service.claim_dispatch(db,challenge_id=prepared.challenge_id,request_id='profile-binding-owner',lease_seconds=30,now=NOW)
        if status=='uncertain':service.mark_send_uncertain(db,challenge_id=prepared.challenge_id,owner_token=owner.owner_token,request_id='profile-binding-unknown',now=NOW)
    before=db.scalar(select(func.count()).select_from(AuditEvent));db.commit()
    with pytest.raises(service.AuthenticationChallengeError) as error:
        service.begin_verify(db,mobile_hash=MOBILE_HASH,provider='aliyun',client_type='miniprogram',request_id='profile-binding-check',current_dispatch_request_profile_sha256=current,now=NOW+timedelta(seconds=32))
    assert error.value.code=='provider_configuration_changed' and error.value.http_status_code==503
    assert not error.value.mutation_persisted
    db.expire_all()
    challenge=db.get(LoginChallenge,prepared.challenge_id);dispatch=db.get(SmsChallengeDispatch,prepared.challenge_id)
    assert challenge.attempts==0 and challenge.status=='pending' and dispatch.status==status
    assert db.scalar(select(func.count()).select_from(AuditEvent))==before
    recovered=service.begin_verify(db,mobile_hash=MOBILE_HASH,provider='aliyun',client_type='miniprogram',request_id='profile-binding-restored',current_dispatch_request_profile_sha256='e'*64,now=NOW+timedelta(seconds=33))
    assert recovered.challenge_id==prepared.challenge_id and recovered.attempt_no==1


@pytest.mark.parametrize('mode',['local_hash','legacy_unknown'])
def test_historical_modes_never_acquire_provider_verification_authority(db,mode):
    user=_formal_user(db);db.commit();prepared=_sent_challenge(db,user)
    challenge=db.get(LoginChallenge,prepared.challenge_id)
    challenge.verification_mode=mode
    if mode=='local_hash':challenge.code_hash='a'*64
    db.commit()
    with pytest.raises(service.AuthenticationChallengeError) as error:
        service.begin_verify(db,mobile_hash=MOBILE_HASH,provider='aliyun',client_type='miniprogram',request_id='profile-binding-legacy',now=NOW+timedelta(seconds=2))
    assert error.value.code=='challenge_verification_mode_unsupported' and not error.value.mutation_persisted
    db.refresh(challenge)
    assert challenge.verification_mode==mode and challenge.status=='pending' and challenge.attempts==0


@pytest.mark.parametrize('current',[None,'f'*64,'invalid'])
def test_send_drift_quarantines_owner_and_never_authorizes_restore_as_resend(db,current):
    user=_formal_user(db);db.commit();prepared=_prepare(db,user=user)
    owner=service.claim_dispatch(db,challenge_id=prepared.challenge_id,request_id='profile-send-owner',lease_seconds=30,now=NOW)
    args=dict(challenge_id=prepared.challenge_id,owner_token=owner.owner_token,request_id='profile-send-check',minimum_remaining_seconds=15,now=NOW+timedelta(seconds=1))
    assert service.authorize_dispatch_provider_call(db,**args,current_dispatch_request_profile_sha256=current) is False
    db.commit();dispatch=db.get(SmsChallengeDispatch,prepared.challenge_id)
    assert dispatch.status=='uncertain' and dispatch.provider_reference is None
    assert db.scalar(select(StateTransitionEvent).where(StateTransitionEvent.reason=='provider_configuration_changed')) is not None
    assert service.authorize_dispatch_provider_call(db,**args,current_dispatch_request_profile_sha256='e'*64) is False


@pytest.mark.parametrize('stored',[None,'bad','g'*64])
def test_missing_or_invalid_persisted_digest_is_not_treated_as_current(stored):
    import uuid
    assert not service._dispatch_profile_matches(SimpleNamespace(request_sha256=stored,challenge_id=uuid.uuid4()),'e'*64)


@pytest.mark.parametrize('field,value',[('sms_scheme_name','changed-scheme'),('sms_sign_name','changed-sign'),('sms_template_code','SMS_CHANGED'),('sms_code_length',8),('sms_valid_seconds',120),('sms_interval_seconds',90)])
def test_api_checks_actual_provider_profile_before_verification_and_can_recover(api_world,field,value):
    with api_world.session_factory() as db:_seed_mobile_subject(db)
    assert _request_sms(api_world).status_code==200
    original=api_world.sms_provider.settings
    api_world.sms_provider.settings=original.model_copy(update={field:value})
    response=_login_sms_web(api_world)
    assert response.status_code==503 and response.json()['detail']['code']=='provider_configuration_changed'
    assert api_world.sms_provider.verify_calls==[] and len(api_world.sms_provider.send_calls)==1
    with api_world.session_factory() as db:
        challenge=db.scalar(select(LoginChallenge));original_id=challenge.id
        assert challenge.status=='pending' and challenge.attempts==0
        assert db.scalar(select(func.count()).select_from(AuthSession))==0
        assert db.scalar(select(func.count()).select_from(AuthRefreshToken))==0
        failure=db.scalar(select(AuditEvent).where(AuditEvent.action=='authentication.sms.login_failed'))
        assert failure.after_jsonb['reason_code']=='provider_configuration_changed'
    api_world.sms_provider.settings=original
    restored=_login_sms_web(api_world,request_id='profile-restored-login-request',idempotency_key='profile-restored-login-key')
    assert restored.status_code==200 and len(api_world.sms_provider.verify_calls)==1
    assert api_world.sms_provider.verify_calls[0][2]==str(original_id) and len(api_world.sms_provider.send_calls)==1


def test_api_uses_one_exact_provider_even_if_route_settings_or_factory_change(api_world,monkeypatch):
    with api_world.session_factory() as db:_seed_mobile_subject(db)
    assert _request_sms(api_world).status_code==200
    monkeypatch.setattr(auth.settings,'sms_scheme_name','stale-route-setting')
    constructions=[]
    def factory():
        constructions.append(1)
        assert len(constructions)==1
        return api_world.sms_provider
    monkeypatch.setattr(auth,'get_sms_provider',factory)
    assert _login_sms_web(api_world).status_code==200
    assert len(constructions)==1 and len(api_world.sms_provider.verify_calls)==1


def test_api_delayed_send_uses_actual_profile_and_replay_never_resends(api_world):
    with api_world.session_factory() as db:_seed_mobile_subject(db)
    original=api_world.sms_provider.settings
    api_world.sms_provider.settings=original.model_copy(update={'sms_scheme_name':'worker-new-configuration'})
    assert _request_sms(api_world).status_code==200
    assert api_world.sms_provider.send_calls==[]
    with api_world.session_factory() as db:
        dispatch=db.scalar(select(SmsChallengeDispatch));assert dispatch.status=='uncertain'
        assert db.scalar(select(StateTransitionEvent).where(StateTransitionEvent.reason=='provider_configuration_changed')) is not None
    api_world.sms_provider.settings=original
    assert _request_sms(api_world,request_id='profile-send-replay-request').status_code==200
    assert api_world.sms_provider.send_calls==[]


def test_missing_provider_configuration_does_not_fall_back_to_route_settings(api_world):
    with api_world.session_factory() as db:_seed_mobile_subject(db)
    assert _request_sms(api_world).status_code==200
    api_world.sms_provider.settings=None
    assert _login_sms_web(api_world).status_code==502
    assert api_world.sms_provider.verify_calls==[]
    with api_world.session_factory() as db:assert db.scalar(select(LoginChallenge.attempts))==0


@pytest.mark.parametrize('mode',['local_hash','legacy_unknown'])
@pytest.mark.parametrize('entry,status',[('finish_verify','pending'),('finish_verify','verified'),('consume_verified','verified'),('consume_verified','consumed')])
def test_historical_modes_cannot_be_promoted_or_replayed_as_provider_verified(db,mode,entry,status):
    user=_formal_user(db);db.commit();prepared=_sent_challenge(db,user)
    challenge=db.get(LoginChallenge,prepared.challenge_id)
    challenge.verification_mode=mode;challenge.status=status;challenge.attempts=1
    if mode=='local_hash':challenge.code_hash='a'*64
    if status in {'verified','consumed'}:challenge.verified_at=NOW+timedelta(seconds=2)
    if status=='consumed':challenge.consumed_at=NOW+timedelta(seconds=3)
    db.commit()
    before=(challenge.code_hash,challenge.status,challenge.attempts,challenge.verified_at,challenge.consumed_at)
    audit_count=db.scalar(select(func.count()).select_from(AuditEvent))
    args=dict(challenge_id=challenge.id,request_id='historical-mode-lifecycle-reject',now=NOW+timedelta(seconds=4))
    if entry=='finish_verify':args['verified']=True
    with pytest.raises(service.AuthenticationChallengeError) as error:getattr(service,entry)(db,**args)
    assert error.value.code=='challenge_verification_mode_unsupported' and not error.value.mutation_persisted
    db.refresh(challenge)
    assert (challenge.code_hash,challenge.status,challenge.attempts,challenge.verified_at,challenge.consumed_at)==before
    assert db.scalar(select(func.count()).select_from(AuditEvent))==audit_count


@pytest.mark.parametrize('mode',['local_hash','legacy_unknown'])
def test_formal_api_rejects_historical_challenge_without_provider_call_or_session(api_world,mode,monkeypatch):
    with api_world.session_factory() as db:_seed_mobile_subject(db)
    assert _request_sms(api_world).status_code==200
    with api_world.session_factory() as db:
        challenge=db.scalar(select(LoginChallenge));challenge.verification_mode=mode
        if mode=='local_hash':challenge.code_hash='a'*64
        db.commit();db.refresh(challenge)
        historical=(challenge.id,challenge.code_hash,challenge.status,challenge.attempts)
        dispatch=db.get(SmsChallengeDispatch,challenge.id)
        dispatch_evidence=(dispatch.status,dispatch.request_sha256,dispatch.provider_reference,dispatch.accepted_at)
    def pass_if_called(*args):
        api_world.sms_provider.verify_calls.append(args)
        return True
    monkeypatch.setattr(api_world.sms_provider,'verify',pass_if_called)
    response=_login_sms_web(api_world)
    assert response.status_code==503 and response.json()['detail']['code']=='challenge_verification_mode_unsupported'
    assert api_world.sms_provider.verify_calls==[] and len(api_world.sms_provider.send_calls)==1
    with api_world.session_factory() as db:
        challenge=db.scalar(select(LoginChallenge));dispatch=db.get(SmsChallengeDispatch,challenge.id)
        assert (challenge.id,challenge.code_hash,challenge.status,challenge.attempts)==historical
        assert (dispatch.status,dispatch.request_sha256,dispatch.provider_reference,dispatch.accepted_at)==dispatch_evidence
        assert db.scalar(select(func.count()).select_from(AuthSession))==0
        assert db.scalar(select(func.count()).select_from(AuthRefreshToken))==0
        failure=db.scalar(select(AuditEvent).where(AuditEvent.action=='authentication.sms.login_failed'))
        assert failure.after_jsonb['reason_code']=='challenge_verification_mode_unsupported'
