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
