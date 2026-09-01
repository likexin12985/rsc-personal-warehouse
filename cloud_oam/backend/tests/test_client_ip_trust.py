from __future__ import annotations

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app import dependencies
from app.config import Settings


def make_request(
    peer_host: str | None,
    forwarded_for: str | None = None,
) -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/auth/sms/request",
            "query_string": b"",
            "headers": headers,
            "client": (peer_host, 12345) if peer_host is not None else None,
        }
    )


def settings_with_trusted_proxies(value: str) -> Settings:
    return Settings(_env_file=None, trusted_proxy_ips=value)


def test_default_trusted_proxy_ips_are_loopback_only() -> None:
    field = Settings.model_fields["trusted_proxy_ips"]
    assert field.default == "127.0.0.1,::1"
    assert settings_with_trusted_proxies(field.default).trusted_proxy_ip_set() == {
        "127.0.0.1",
        "::1",
    }


@pytest.mark.parametrize("peer_host", ["127.0.0.1", "::1"])
def test_trusted_peer_uses_first_forwarded_ip(
    monkeypatch: pytest.MonkeyPatch,
    peer_host: str,
) -> None:
    settings = settings_with_trusted_proxies("127.0.0.1, ::1")
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)

    request = make_request(peer_host, " 203.0.113.10, 10.0.0.8 ")

    assert dependencies.client_ip(request) == "203.0.113.10"


def test_untrusted_peer_cannot_spoof_forwarded_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_with_trusted_proxies("127.0.0.1,::1")
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)

    request = make_request("198.51.100.24", "203.0.113.10")

    assert dependencies.client_ip(request) == "198.51.100.24"


@pytest.mark.parametrize("forwarded_for", [None, "", "   ", "not-an-ip"])
def test_trusted_peer_falls_back_when_forwarded_ip_is_absent_or_invalid(
    monkeypatch: pytest.MonkeyPatch,
    forwarded_for: str | None,
) -> None:
    settings = settings_with_trusted_proxies("127.0.0.1")
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)

    request = make_request("127.0.0.1", forwarded_for)

    assert dependencies.client_ip(request) == "127.0.0.1"


def test_forwarded_ip_is_ignored_without_a_network_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_with_trusted_proxies("127.0.0.1")
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)

    assert dependencies.client_ip(make_request(None, "203.0.113.10")) == ""


@pytest.mark.parametrize(
    "trusted_proxy_ips",
    ["0.0.0.0", "::", "224.0.0.1", "proxy.internal", "10.0.0.0/8"],
)
def test_trusted_proxy_configuration_rejects_wildcards_and_non_ip_values(
    trusted_proxy_ips: str,
) -> None:
    with pytest.raises(ValidationError, match="trusted_proxy_ips"):
        settings_with_trusted_proxies(trusted_proxy_ips)
