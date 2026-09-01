from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import get_settings


class WechatProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class WechatLoginIdentity:
    app_id: str
    openid: str
    unionid: str | None = None


class WechatProvider:
    def exchange_login_code(self, code: str) -> WechatLoginIdentity:
        raise NotImplementedError

    def exchange_phone_code(self, code: str) -> str:
        raise NotImplementedError


class MockWechatProvider(WechatProvider):
    def __init__(self, app_id: str, mobile: str):
        self.app_id = app_id or "mock-wechat-app"
        self.mobile = mobile

    def exchange_login_code(self, code: str) -> WechatLoginIdentity:
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()[:32]
        return WechatLoginIdentity(app_id=self.app_id, openid=f"mock-{digest}")

    def exchange_phone_code(self, code: str) -> str:
        if not code:
            raise WechatProviderError("微信手机号授权凭证无效")
        return self.mobile


class OfficialWechatProvider(WechatProvider):
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = Lock()

    def exchange_login_code(self, code: str) -> WechatLoginIdentity:
        query = urlencode(
            {
                "appid": self.app_id,
                "secret": self.app_secret,
                "js_code": code,
                "grant_type": "authorization_code",
            }
        )
        payload = _request_json(f"https://api.weixin.qq.com/sns/jscode2session?{query}")
        _raise_for_wechat_error(payload, "微信登录凭证校验失败")
        openid = str(payload.get("openid") or "")
        if not openid:
            raise WechatProviderError("微信未返回用户身份")
        return WechatLoginIdentity(
            app_id=self.app_id,
            openid=openid,
            unionid=str(payload["unionid"]) if payload.get("unionid") else None,
        )

    def exchange_phone_code(self, code: str) -> str:
        access_token = self._access_token()
        payload = _request_json(
            "https://api.weixin.qq.com/wxa/business/getuserphonenumber?"
            + urlencode({"access_token": access_token}),
            {"code": code},
        )
        _raise_for_wechat_error(payload, "微信手机号授权失败")
        phone_info = payload.get("phone_info") or {}
        mobile = str(phone_info.get("purePhoneNumber") or phone_info.get("phoneNumber") or "")
        if not mobile:
            raise WechatProviderError("微信未返回手机号")
        return mobile

    def _access_token(self) -> str:
        with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token
            query = urlencode(
                {
                    "grant_type": "client_credential",
                    "appid": self.app_id,
                    "secret": self.app_secret,
                }
            )
            payload = _request_json(f"https://api.weixin.qq.com/cgi-bin/token?{query}")
            _raise_for_wechat_error(payload, "微信服务端凭证获取失败")
            token = str(payload.get("access_token") or "")
            if not token:
                raise WechatProviderError("微信未返回服务端凭证")
            expires_in = max(300, int(payload.get("expires_in") or 7200))
            self._token = token
            self._token_expires_at = time.monotonic() + expires_in - 120
            return token


def _request_json(url: str, body: dict | None = None) -> dict:
    data = json.dumps(body, ensure_ascii=True).encode("utf-8") if body is not None else None
    request = Request(
        url,
        data=data,
        headers={"content-type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise WechatProviderError("微信接口暂时不可用，请改用短信验证码") from exc


def _raise_for_wechat_error(payload: dict, fallback: str) -> None:
    errcode = int(payload.get("errcode") or 0)
    if errcode:
        raise WechatProviderError(f"{fallback}（{errcode}）")


@lru_cache
def get_wechat_provider() -> WechatProvider:
    settings = get_settings()
    if settings.wechat_provider == "mock" and settings.environment != "production":
        return MockWechatProvider(settings.wechat_app_id, settings.wechat_test_mobile)
    if settings.wechat_provider == "wechat":
        return OfficialWechatProvider(settings.wechat_app_id, settings.wechat_app_secret)
    raise WechatProviderError("微信快捷登录尚未启用")
