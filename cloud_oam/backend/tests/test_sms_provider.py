from __future__ import annotations

from types import SimpleNamespace

import pytest
from alibabacloud_dypnsapi20170525 import models as dypns_models

from app.sms import AliyunPnvsProvider, SmsProviderError, SmsSendResult


MOBILE = "13900000831"
OUT_ID = "61000000-0000-4000-8000-000000000001"
BIZ_ID = "PNVS-BIZ-0001"


class _RecordingClient:
    def __init__(self, *, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[object, object]] = []
        self.verify_calls: list[tuple[object, object]] = []

    def send_sms_verify_code_with_options(self, request, runtime_options):
        self.calls.append((request, runtime_options))
        if self.error is not None:
            raise self.error
        return self.response

    def check_sms_verify_code_with_options(self, request, runtime_options):
        self.verify_calls.append((request, runtime_options))
        if self.error is not None:
            raise self.error
        return self.response


def _provider(*, response=None, error: Exception | None = None):
    provider = AliyunPnvsProvider.__new__(AliyunPnvsProvider)
    provider.settings = SimpleNamespace(
        sms_sign_name="RSC个人仓",
        sms_template_code="SMS_FORMAL_TEST",
        sms_valid_seconds=300,
        sms_code_length=6,
        sms_interval_seconds=60,
        sms_scheme_name="RSC个人仓登录",
        sms_sts_credential_ready=lambda **_: True,
    )
    provider.client = _RecordingClient(response=response, error=error)
    return provider


def _response(
    *,
    success: bool = True,
    code: str = "OK",
    biz_id: str | None = BIZ_ID,
    out_id: str | None = OUT_ID,
    include_model: bool = True,
):
    model = (
        dypns_models.SendSmsVerifyCodeResponseBodyModel(
            biz_id=biz_id,
            out_id=out_id,
        )
        if include_model
        else None
    )
    return dypns_models.SendSmsVerifyCodeResponse(
        body=dypns_models.SendSmsVerifyCodeResponseBody(
            success=success,
            code=code,
            model=model,
        )
    )


def _verify_response(
    *,
    success: bool | None = True,
    code: str | None = "OK",
    verify_result: str | None = "PASS",
    out_id: str | None = OUT_ID,
    include_model: bool = True,
):
    model = (
        dypns_models.CheckSmsVerifyCodeResponseBodyModel(
            out_id=out_id,
            verify_result=verify_result,
        )
        if include_model
        else None
    )
    return dypns_models.CheckSmsVerifyCodeResponse(
        body=dypns_models.CheckSmsVerifyCodeResponseBody(
            success=success,
            code=code,
            model=model,
        )
    )


def test_send_disables_provider_and_sdk_retries_and_requires_exact_echo() -> None:
    provider = _provider(response=_response())

    result = provider.send(MOBILE, OUT_ID)

    assert result == SmsSendResult(biz_id=BIZ_ID, out_id=OUT_ID)
    assert len(provider.client.calls) == 1
    request, runtime_options = provider.client.calls[0]
    assert request.phone_number == MOBILE
    assert request.out_id == OUT_ID
    assert request.auto_retry == 0
    assert runtime_options.autoretry is False


@pytest.mark.parametrize(
    "response",
    [
        None,
        dypns_models.SendSmsVerifyCodeResponse(body=None),
        _response(success=False),
        _response(success=None),
        _response(code="SYSTEM_ERROR"),
        _response(code=None),
        _response(include_model=False),
        _response(biz_id=None),
        _response(biz_id=""),
        _response(biz_id="   "),
        _response(out_id=None),
        _response(out_id="different-out-id"),
    ],
    ids=[
        "missing-response",
        "missing-body",
        "unsuccessful-body",
        "missing-success",
        "non-ok-code",
        "missing-code",
        "missing-model",
        "missing-biz-id",
        "empty-biz-id",
        "blank-biz-id",
        "missing-out-id",
        "mismatched-out-id",
    ],
)
def test_send_rejects_incomplete_or_mismatched_provider_acceptance(response) -> None:
    provider = _provider(response=response)

    with pytest.raises(SmsProviderError):
        provider.send(MOBILE, OUT_ID)

    assert len(provider.client.calls) == 1


def test_send_wraps_sdk_exception_without_retrying() -> None:
    secret_error = TimeoutError(f"{MOBILE}|246810|provider accepted before timeout")
    provider = _provider(error=secret_error)

    with pytest.raises(SmsProviderError) as captured:
        provider.send(MOBILE, OUT_ID)

    assert len(provider.client.calls) == 1
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert MOBILE not in repr(captured.value)
    assert "246810" not in repr(captured.value)


def test_verify_disables_sdk_retries_and_binds_exact_out_id() -> None:
    provider = _provider(response=_verify_response())

    assert provider.verify(MOBILE, "246810", OUT_ID) is True

    assert len(provider.client.verify_calls) == 1
    request, runtime_options = provider.client.verify_calls[0]
    assert request.phone_number == MOBILE
    assert request.verify_code == "246810"
    assert request.out_id == OUT_ID
    assert runtime_options.autoretry is False
    assert runtime_options.max_attempts == 1


@pytest.mark.parametrize(
    "response",
    [
        None,
        dypns_models.CheckSmsVerifyCodeResponse(body=None),
        _verify_response(success=False),
        _verify_response(success=None),
        _verify_response(code="SYSTEM_ERROR"),
        _verify_response(code=None),
        _verify_response(include_model=False),
        _verify_response(verify_result=None),
        _verify_response(verify_result="FAIL"),
        _verify_response(out_id=None),
        _verify_response(out_id="different-out-id"),
    ],
    ids=[
        "missing-response",
        "missing-body",
        "unsuccessful-body",
        "missing-success",
        "non-ok-code",
        "missing-code",
        "missing-model",
        "missing-result",
        "unsupported-result",
        "missing-out-id",
        "mismatched-out-id",
    ],
)
def test_verify_rejects_incomplete_or_mismatched_response(response) -> None:
    provider = _provider(response=response)

    with pytest.raises(SmsProviderError):
        provider.verify(MOBILE, "246810", OUT_ID)

    assert len(provider.client.verify_calls) == 1


def test_verify_returns_false_only_for_exact_provider_code_rejection() -> None:
    provider = _provider(response=_verify_response(verify_result="UNKNOWN"))

    assert provider.verify(MOBILE, "246810", OUT_ID) is False

    assert len(provider.client.verify_calls) == 1


def test_verify_wraps_sdk_exception_without_secret_cause_or_retry() -> None:
    provider = _provider(error=TimeoutError(f"{MOBILE}|246810|transport"))

    with pytest.raises(SmsProviderError) as captured:
        provider.verify(MOBILE, "246810", OUT_ID)

    assert len(provider.client.verify_calls) == 1
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert MOBILE not in repr(captured.value)
    assert "246810" not in repr(captured.value)


def test_request_profile_uses_actual_provider_settings_without_global_lookup(monkeypatch) -> None:
    from app import sms
    from app.config import Settings

    sent_settings = Settings(_env_file=None, sms_provider="aliyun_pnvs", sms_scheme_name="sent-scheme")
    expected = sms.sms_dispatch_request_profile_sha256(
        mobile_hash="a" * 64, provider_settings=sent_settings,
    )
    changed = sent_settings.model_copy(update={"sms_scheme_name": "next-scheme"})
    monkeypatch.setattr(sms, "get_settings", lambda: changed)
    assert sms.sms_dispatch_request_profile_sha256(mobile_hash="a" * 64) != expected
    assert sms.sms_dispatch_request_profile_sha256(
        mobile_hash="a" * 64, provider_settings=sent_settings,
    ) == expected


def test_provider_keeps_same_configuration_for_profile_and_actual_call(monkeypatch) -> None:
    from app import sms
    from app.config import Settings

    original = Settings(_env_file=None, sms_provider="aliyun_pnvs", sms_scheme_name="sent-scheme")
    recording = _RecordingClient(response=_verify_response())
    monkeypatch.setattr(sms, "get_settings", lambda: original)
    monkeypatch.setattr(sms, "DypnsClient", lambda _: recording)
    provider = sms.AliyunPnvsProvider()
    profile = sms.sms_dispatch_request_profile_sha256(mobile_hash="a" * 64, provider_settings=provider.settings)
    original.sms_scheme_name = "changed-after-construction"

    assert provider.verify(MOBILE, "246810", OUT_ID) is True
    assert recording.verify_calls[0][0].scheme_name == "sent-scheme"
    assert sms.sms_dispatch_request_profile_sha256(mobile_hash="a" * 64, provider_settings=provider.settings) == profile


def test_request_profile_allows_credential_rotation_without_changing_sms_contract() -> None:
    from app import sms
    from app.config import Settings

    original = Settings(_env_file=None, sms_provider="aliyun_pnvs", sms_scheme_name="same-scheme")
    rotated = original.model_copy(update={"sms_access_key_id": "synthetic-new-id", "sms_access_key_secret": "synthetic-new-secret"})
    assert sms.sms_dispatch_request_profile_sha256(mobile_hash="a" * 64, provider_settings=original) == sms.sms_dispatch_request_profile_sha256(mobile_hash="a" * 64, provider_settings=rotated)


def test_dedicated_sts_triplet_reaches_sdk_without_changing_sms_profile(monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone
    from app import sms
    from app.config import Settings

    expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    settings = Settings(_env_file=None, sms_provider="aliyun_pnvs",
        sms_access_key_id="synthetic-sts-id", sms_access_key_secret="synthetic-sts-secret",
        sms_security_token="synthetic-pnvs-token-" + "t" * 32,
        sms_security_token_expires_at=expiry)
    captured = []
    monkeypatch.setattr(sms, "get_settings", lambda: settings)
    monkeypatch.setattr(sms, "DypnsClient", lambda config: captured.append(config) or _RecordingClient())
    provider = sms.AliyunPnvsProvider()

    assert captured[0].access_key_id == settings.sms_access_key_id
    assert captured[0].access_key_secret == settings.sms_access_key_secret
    assert captured[0].security_token == settings.sms_security_token
    assert provider.settings.sms_sts_credential_ready(minimum_validity_seconds=60)
    rotated = settings.model_copy(update={"sms_access_key_id":"synthetic-next-id",
        "sms_access_key_secret":"synthetic-next-secret",
        "sms_security_token":"synthetic-next-token-" + "n" * 32})
    assert sms.sms_dispatch_request_profile_sha256(mobile_hash="a" * 64, provider_settings=settings) == \
        sms.sms_dispatch_request_profile_sha256(mobile_hash="a" * 64, provider_settings=rotated)


def test_expired_sts_stops_before_paid_send_or_verify(monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone
    from app import sms
    from app.config import Settings

    settings = Settings(_env_file=None, sms_provider="aliyun_pnvs",
        sms_access_key_id="synthetic-sts-id", sms_access_key_secret="synthetic-sts-secret",
        sms_security_token="synthetic-pnvs-token-" + "t" * 32,
        sms_security_token_expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    monkeypatch.setattr(sms, "get_settings", lambda: settings)
    monkeypatch.setattr(sms, "DypnsClient", lambda _: pytest.fail("expired token constructed SDK"))
    with pytest.raises(SmsProviderError) as captured:
        sms.AliyunPnvsProvider()
    assert captured.value.__cause__ is None and captured.value.__context__ is None

    provider = _provider(response=_response())
    provider.settings.sms_sts_credential_ready = lambda **_: False
    with pytest.raises(SmsProviderError):
        provider.send(MOBILE, OUT_ID)
    with pytest.raises(SmsProviderError):
        provider.verify(MOBILE, "246810", OUT_ID)
    assert provider.client.calls == [] and provider.client.verify_calls == []


def test_pnvs_sdk_handler_is_removed_even_when_constructor_fails(monkeypatch) -> None:
    import logging
    from app import sms
    from app.config import Settings

    logger = logging.getLogger("alibabacloud-tea")
    original = (logger.handlers[:], logger.disabled, logger.propagate)
    monkeypatch.setattr(sms, "get_settings", lambda: Settings(_env_file=None))

    def broken_client(_):
        logger.disabled = False
        logger.addHandler(logging.StreamHandler())
        raise RuntimeError("synthetic credential-bearing SDK error")

    monkeypatch.setattr(sms, "DypnsClient", broken_client)
    try:
        with pytest.raises(SmsProviderError) as captured:
            sms.AliyunPnvsProvider()
        assert captured.value.__cause__ is None and captured.value.__context__ is None
        assert logger.disabled and not logger.propagate
        assert len(logger.handlers) == 1 and isinstance(logger.handlers[0], logging.NullHandler)
    finally:
        logger.handlers, logger.disabled, logger.propagate = original
