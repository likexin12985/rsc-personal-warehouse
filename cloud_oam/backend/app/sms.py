from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from alibabacloud_dypnsapi20170525 import models as dypns_models
from alibabacloud_dypnsapi20170525.client import Client as DypnsClient
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util import models as util_models

from .config import get_settings


class SmsProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class SmsSendResult:
    biz_id: str = ""


class AliyunPnvsProvider:
    def __init__(self) -> None:
        settings = get_settings()
        config = open_api_models.Config(
            access_key_id=settings.sms_access_key_id,
            access_key_secret=settings.sms_access_key_secret,
            endpoint="dypnsapi.aliyuncs.com",
        )
        self.client = DypnsClient(config)
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
            auto_retry=1,
            return_verify_code=False,
            out_id=out_id,
            scheme_name=self.settings.sms_scheme_name,
        )
        try:
            response = self.client.send_sms_verify_code_with_options(
                request,
                util_models.RuntimeOptions(
                    autoretry=True,
                    max_attempts=2,
                    connect_timeout=5000,
                    read_timeout=8000,
                ),
            )
        except Exception as exc:
            raise SmsProviderError("短信服务暂时不可用") from exc
        body = response.body
        if not body or not body.success or body.code != "OK":
            raise SmsProviderError("短信发送失败")
        biz_id = body.model.biz_id if body.model and body.model.biz_id else ""
        return SmsSendResult(biz_id=biz_id)

    def verify(self, mobile: str, code: str, out_id: str) -> bool:
        request = dypns_models.CheckSmsVerifyCodeRequest(
            phone_number=mobile,
            country_code="86",
            verify_code=code,
            out_id=out_id,
            scheme_name=self.settings.sms_scheme_name,
        )
        try:
            response = self.client.check_sms_verify_code_with_options(
                request,
                util_models.RuntimeOptions(
                    autoretry=True,
                    max_attempts=2,
                    connect_timeout=5000,
                    read_timeout=8000,
                ),
            )
        except Exception as exc:
            raise SmsProviderError("短信服务暂时不可用") from exc
        body = response.body
        return bool(
            body
            and body.success
            and body.code == "OK"
            and body.model
            and body.model.verify_result == "PASS"
        )


class MockSmsProvider:
    def __init__(self) -> None:
        self.settings = get_settings()
        if self.settings.environment == "production":
            raise SmsProviderError("生产环境禁止使用模拟短信服务")

    def send(self, _: str, out_id: str) -> SmsSendResult:
        return SmsSendResult(biz_id=f"mock-{out_id}")

    def verify(self, _: str, code: str, __: str) -> bool:
        return code == self.settings.sms_test_code


def get_sms_provider() -> AliyunPnvsProvider | MockSmsProvider:
    settings = get_settings()
    if not settings.sms_configuration_ready():
        raise SmsProviderError("短信验证码登录尚未启用")
    if settings.sms_provider == "mock":
        return MockSmsProvider()
    return AliyunPnvsProvider()
