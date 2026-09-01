from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from math import ceil
import re

from alibabacloud_dypnsapi20170525 import models as dypns_models
from alibabacloud_dypnsapi20170525.client import Client as DypnsClient
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util import models as util_models

from .config import get_settings


SMS_PROVIDER_CONNECT_TIMEOUT_MS = 5000
SMS_PROVIDER_READ_TIMEOUT_MS = 8000
# A queued owner must still have this much lease/challenge lifetime before it
# may enter the external call while holding its database object locks.
SMS_PROVIDER_CALL_GUARD_SECONDS = 15


class SmsProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class SmsSendResult:
    biz_id: str = ""
    out_id: str = ""


def sms_dispatch_request_profile_sha256(*, mobile_hash: str) -> str:
    """Hash the exact non-secret PNVS request profile before dispatch exists."""

    if re.fullmatch(r"[0-9a-f]{64}", mobile_hash, re.ASCII) is None:
        raise SmsProviderError("短信请求摘要无效")
    settings = get_settings()
    template_param = (
        f'{{"code":"##code##","min":"{ceil(settings.sms_valid_seconds / 60)}"}}'
    )
    document = {
        "auto_retry": 0,
        "code_length": settings.sms_code_length,
        "code_type": 1,
        "connect_timeout_ms": SMS_PROVIDER_CONNECT_TIMEOUT_MS,
        "country_code": "86",
        "duplicate_policy": 1,
        "interval": settings.sms_interval_seconds,
        "mobile_hash": mobile_hash,
        "provider": settings.sms_provider,
        "return_verify_code": False,
        "read_timeout_ms": SMS_PROVIDER_READ_TIMEOUT_MS,
        "scheme_name": settings.sms_scheme_name,
        "sign_name": settings.sms_sign_name,
        "template_code": settings.sms_template_code,
        "template_param": template_param,
        "valid_time": settings.sms_valid_seconds,
    }
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AliyunPnvsProvider:
    def __init__(self) -> None:
        settings = get_settings()
        client: DypnsClient | None = None
        try:
            config = open_api_models.Config(
                access_key_id=settings.sms_access_key_id,
                access_key_secret=settings.sms_access_key_secret,
                endpoint="dypnsapi.aliyuncs.com",
            )
            client = DypnsClient(config)
        except Exception:
            pass
        # Raise outside the ``except`` suite so neither ``__cause__`` nor
        # ``__context__`` retains an SDK exception containing credentials.
        if client is None:
            raise SmsProviderError("短信服务暂时不可用")
        self.client = client
        self.settings = settings

    def send(self, mobile: str, out_id: str) -> SmsSendResult:
        request = dypns_models.SendSmsVerifyCodeRequest(
            phone_number=mobile,
            country_code="86",
            sign_name=self.settings.sms_sign_name,
            template_code=self.settings.sms_template_code,
            template_param=(
                f'{{"code":"##code##","min":"{ceil(self.settings.sms_valid_seconds / 60)}"}}'
            ),
            code_length=self.settings.sms_code_length,
            code_type=1,
            valid_time=self.settings.sms_valid_seconds,
            interval=self.settings.sms_interval_seconds,
            duplicate_policy=1,
            # Provider-side carrier retry and SDK transport retry both obscure
            # whether one paid side effect occurred.  Keep this boundary one
            # request per persisted dispatch owner.
            auto_retry=0,
            return_verify_code=False,
            out_id=out_id,
            scheme_name=self.settings.sms_scheme_name,
        )
        response = None
        transport_failed = False
        try:
            response = self.client.send_sms_verify_code_with_options(
                request,
                util_models.RuntimeOptions(
                    autoretry=False,
                    max_attempts=1,
                    connect_timeout=SMS_PROVIDER_CONNECT_TIMEOUT_MS,
                    read_timeout=SMS_PROVIDER_READ_TIMEOUT_MS,
                ),
            )
        except Exception:
            transport_failed = True
        if transport_failed:
            raise SmsProviderError("短信服务暂时不可用")
        body = getattr(response, "body", None)
        model = getattr(body, "model", None)
        biz_id = getattr(model, "biz_id", None)
        echoed_out_id = getattr(model, "out_id", None)
        if (
            body is None
            or body.success is not True
            or body.code != "OK"
            or model is None
            or not isinstance(biz_id, str)
            or not biz_id.strip()
            or biz_id != biz_id.strip()
            or echoed_out_id != out_id
        ):
            raise SmsProviderError("短信发送失败")
        return SmsSendResult(biz_id=biz_id, out_id=echoed_out_id)

    def verify(self, mobile: str, code: str, out_id: str) -> bool:
        request = dypns_models.CheckSmsVerifyCodeRequest(
            phone_number=mobile,
            country_code="86",
            verify_code=code,
            out_id=out_id,
            scheme_name=self.settings.sms_scheme_name,
        )
        response = None
        transport_failed = False
        try:
            response = self.client.check_sms_verify_code_with_options(
                request,
                util_models.RuntimeOptions(
                    autoretry=False,
                    max_attempts=1,
                    connect_timeout=SMS_PROVIDER_CONNECT_TIMEOUT_MS,
                    read_timeout=SMS_PROVIDER_READ_TIMEOUT_MS,
                ),
            )
        except Exception:
            transport_failed = True
        if transport_failed:
            raise SmsProviderError("短信服务暂时不可用")
        body = getattr(response, "body", None)
        model = getattr(body, "model", None)
        if (
            body is None
            or body.success is not True
            or body.code != "OK"
            or model is None
            or model.out_id != out_id
            or model.verify_result not in {"PASS", "UNKNOWN"}
        ):
            # An invalid/provider-error response is not evidence that the user
            # entered a wrong code and therefore must not consume an attempt.
            raise SmsProviderError("短信校验服务暂时不可用")
        return model.verify_result == "PASS"


class MockSmsProvider:
    def __init__(self) -> None:
        self.settings = get_settings()
        if self.settings.environment == "production":
            raise SmsProviderError("生产环境禁止使用模拟短信服务")

    def send(self, _: str, out_id: str) -> SmsSendResult:
        return SmsSendResult(biz_id=f"mock-{out_id}", out_id=out_id)

    def verify(self, _: str, code: str, __: str) -> bool:
        return code == self.settings.sms_test_code


def get_sms_provider() -> AliyunPnvsProvider | MockSmsProvider:
    settings = get_settings()
    if not settings.sms_configuration_ready():
        raise SmsProviderError("短信验证码登录尚未启用")
    if settings.sms_provider == "mock":
        return MockSmsProvider()
    return AliyunPnvsProvider()
