"""Offline candidate contract tests; no network, credentials or database use."""

import ast
import base64
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import socket
import traceback

import pytest

from app.openbao_transit_candidate import (
    OpenBaoCandidateConfigurationError,
    OpenBaoCandidateUnavailable,
    OpenBaoDecryptResponse,
    OpenBaoKeyCoordinate,
    OpenBaoReviewedPin,
    OpenBaoTransitCandidate,
    OpenBaoWrappedKey,
    associated_data_b64,
    context_b64,
)


INSTANCE = "isolated-openbao-2-7-1"
AUTH = "authentication_idempotency"
CONTACT = "material_request_contact"
SECRET_MARKER = "sensitive-transport-marker-must-not-escape"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("network forbidden in OpenBao candidate unit tests")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


def coordinate(purpose=AUTH, version=1, **kwargs):
    return OpenBaoKeyCoordinate(
        purpose=purpose, environment="test", provider_instance_id=INSTANCE,
        application_key_version=version, **kwargs,
    )


def wrapped(purpose=AUTH, version=1):
    # Synthetic nonce + ciphertext + tag: never a production wrapped DEK.
    raw = hashlib.sha512(f"{purpose}/{version}".encode()).digest()[:60]
    ciphertext = "vault:v1:" + base64.b64encode(raw).decode()
    return OpenBaoWrappedKey(coordinate(purpose, version), ciphertext, 1)


def reviewed_pin(entry):
    # Represents a separately reviewed provisioning manifest in unit tests.
    return OpenBaoReviewedPin(
        coordinate=entry.coordinate, transit_key_version=entry.transit_key_version,
        ciphertext_sha256=hashlib.sha256(entry.ciphertext.encode()).hexdigest(),
        context_sha256=hashlib.sha256(base64.b64decode(context_b64(entry.coordinate))).hexdigest(),
        associated_data_sha256=hashlib.sha256(base64.b64decode(associated_data_b64(entry.coordinate))).hexdigest(),
    )


class Transport:
    def __init__(self, response=None, failure=None):
        self.calls = []
        self.response = response or OpenBaoDecryptResponse(
            200, {"data": {"plaintext": base64.b64encode(bytes(range(32))).decode()}}
        )
        self.failure = failure

    def decrypt(self, *, request, timeout_seconds):
        self.calls.append((request, timeout_seconds))
        if self.failure:
            raise self.failure
        return self.response


def candidate(*, entries=None, pins=None, transport=None, **kwargs):
    entries = entries if entries is not None else (wrapped(),)
    return OpenBaoTransitCandidate(
        environment="test", provider_instance_id=INSTANCE,
        entries=entries,
        reviewed_pins=pins if pins is not None else tuple(reviewed_pin(e) for e in entries),
        transport=transport or Transport(), **kwargs,
    )


@pytest.mark.parametrize("purpose,name", [(AUTH, "rsc-authentication-idempotency"), (CONTACT, "rsc-material-request-contact")])
def test_canonical_domains_and_exact_paths(purpose, name):
    coord = coordinate(purpose)
    common = {
        "application": "cloud_oam", "provider": "openbao_transit_v1",
        "environment": "test", "provider_instance_id": INSTANCE,
        "purpose": purpose, "key_path": "transit/keys/" + name,
        "application_key_version": 1,
    }
    for helper, schema in (
        (context_b64, "rsc.openbao.derivation-context.v1"),
        (associated_data_b64, "rsc.openbao.wrap-aad.v1"),
    ):
        expected = json.dumps({**common, "schema": schema}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        assert base64.b64decode(helper(coord), validate=True) == expected
    assert coord.decrypt_path == "/v1/transit/decrypt/" + name
    assert context_b64(coord) != associated_data_b64(coord)


def test_two_purposes_and_retained_versions_resolve_exactly_without_cache():
    entries = (wrapped(), wrapped(version=2), wrapped(CONTACT))
    transport = Transport()
    loader = candidate(entries=entries, transport=transport)
    assert transport.calls == []
    for entry in entries:
        assert loader.resolve(entry.coordinate.purpose, entry.coordinate.application_key_version) == bytes(range(32))
        request, timeout = transport.calls[-1]
        assert request.path == entry.coordinate.decrypt_path
        assert request.ciphertext == entry.ciphertext
        assert request.context == context_b64(entry.coordinate)
        assert request.associated_data == associated_data_b64(entry.coordinate)
        assert timeout == 3.0
    loader.resolve(AUTH, 1)
    assert len(transport.calls) == 4


@pytest.mark.parametrize("field,value", [
    ("purpose", "other"), ("purpose", []),
    ("environment", "TEST"), ("environment", " test"),
    ("provider_instance_id", "https://example.test"),
    ("provider_instance_id", "../instance"),
    ("application_key_version", True), ("application_key_version", 0),
    ("application_key_version", 2_147_483_648),
])
def test_invalid_coordinates_are_rejected(field, value):
    with pytest.raises(OpenBaoCandidateConfigurationError):
        replace(coordinate(), **{field: value})


@pytest.mark.parametrize("ciphertext,version", [
    ("vault:v0:" + "A" * 80, 1),
    ("vault:v01:" + "A" * 80, 1),
    ("bao:v1:" + "A" * 80, 1),
    ("vault:v2:" + "A" * 80, 1),
    ("vault:v1:" + "A" * 79, 1),
    ("vault:v1:" + "A" * 80 + "\n", 1),
    ("vault:v1:" + base64.b64encode(b"short").decode(), 1),
    ("vault:v1:" + "A" * 80, True),
])
def test_wrapped_prefix_and_aes256_shape_are_not_fabricated(ciphertext, version):
    with pytest.raises(OpenBaoCandidateConfigurationError):
        OpenBaoWrappedKey(coordinate(), ciphertext, version)


@pytest.mark.parametrize("field,value", [
    ("transit_key_version", 2),
    ("ciphertext_sha256", "0" * 64),
    ("context_sha256", "0" * 64),
    ("associated_data_sha256", "0" * 64),
    ("coordinate", coordinate(CONTACT)),
])
def test_external_pin_mismatch_blocks_before_transport(field, value):
    entry, transport = wrapped(), Transport()
    pin = replace(reviewed_pin(entry), **{field: value})
    with pytest.raises(OpenBaoCandidateConfigurationError):
        candidate(entries=(entry,), pins=(pin,), transport=transport)
    assert transport.calls == []


@pytest.mark.parametrize("field,value", [("environment", "production"), ("provider_instance_id", "different-instance")])
def test_expected_deployment_scope_is_independent_of_matching_pin(field, value):
    entry = wrapped()
    entry = replace(entry, coordinate=replace(entry.coordinate, **{field: value}))
    with pytest.raises(OpenBaoCandidateConfigurationError):
        candidate(entries=(entry,))


def test_duplicate_application_version_or_ciphertext_or_pin_is_rejected():
    first = wrapped()
    second = wrapped(version=2)
    for entries, pins in (
        ((first, first), (reviewed_pin(first), reviewed_pin(first))),
        ((first, second), (reviewed_pin(first), reviewed_pin(first))),
        ((first, replace(second, ciphertext=first.ciphertext)), None),
    ):
        with pytest.raises(OpenBaoCandidateConfigurationError):
            candidate(entries=entries, pins=pins)


@pytest.mark.parametrize("purpose,version", [(AUTH, 3), (CONTACT, 1), (AUTH, True), (AUTH, 0), ("other", 1)])
def test_missing_history_or_wrong_purpose_has_no_active_key_fallback(purpose, version):
    transport = Transport()
    loader = candidate(transport=transport)
    with pytest.raises(OpenBaoCandidateUnavailable):
        loader.resolve(purpose, version)
    assert transport.calls == []


@pytest.mark.parametrize("response", [
    OpenBaoDecryptResponse(403, {"errors": [SECRET_MARKER]}),
    OpenBaoDecryptResponse(503, {"errors": ["sealed " + SECRET_MARKER]}),
    OpenBaoDecryptResponse(302, {"data": {"plaintext": SECRET_MARKER}}),
    OpenBaoDecryptResponse(True, {"data": {"plaintext": SECRET_MARKER}}),
    OpenBaoDecryptResponse(200, {"data": {"plaintext": SECRET_MARKER}}),
    OpenBaoDecryptResponse(200, {"data": {"plaintext": base64.b64encode(b"short").decode()}}),
    OpenBaoDecryptResponse(200, {"data": {"plaintext": "A" * 42 + "B="}}),
    OpenBaoDecryptResponse(200, {"data": {"plaintext": "A" * 43 + "=", "key_version_id": SECRET_MARKER}}),
    OpenBaoDecryptResponse(200, {"data": {"batch_results": [{"plaintext": SECRET_MARKER}]}}),
    OpenBaoDecryptResponse(200, {"data": {"plaintext": "A" * 43 + "="}, "warnings": [SECRET_MARKER]}),
    OpenBaoDecryptResponse(200, {"data": None}),
])
def test_untrusted_provider_replies_fail_closed_without_leaking(response, caplog, capsys):
    transport = Transport(response=response)
    with pytest.raises(OpenBaoCandidateUnavailable) as caught:
        candidate(transport=transport).resolve(AUTH, 1)
    assert len(transport.calls) == 1
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert SECRET_MARKER not in "".join(traceback.format_exception(caught.value))
    assert SECRET_MARKER not in caplog.text + capsys.readouterr().out


@pytest.mark.parametrize("failure", [TimeoutError(SECRET_MARKER), PermissionError(SECRET_MARKER), RuntimeError(SECRET_MARKER)])
def test_transport_errors_are_unknown_without_retry_or_exception_chain(failure):
    transport = Transport(failure=failure)
    with pytest.raises(OpenBaoCandidateUnavailable) as caught:
        candidate(transport=transport).resolve(AUTH, 1)
    assert str(caught.value) == "OpenBao data key is unavailable"
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert len(transport.calls) == 1


def test_metadata_is_immutable_and_secret_representations_are_hidden():
    entry = wrapped()
    pin = reviewed_pin(entry)
    transport = Transport()
    loader = candidate(entries=(entry,), transport=transport)
    loader.resolve(AUTH, 1)
    request = transport.calls[0][0]
    for value in (entry, pin, entry.coordinate, request, transport.response, loader):
        representation = repr(value)
        assert entry.ciphertext not in representation
        assert base64.b64encode(bytes(range(32))).decode() not in representation
    with pytest.raises(FrozenInstanceError):
        entry.transit_key_version = 2


def test_candidate_has_no_production_registration_or_credential_io():
    root = Path(__file__).resolve().parents[1] / "app"
    for relative in ("config.py", "production_adapters.py", "kms_readiness.py", "kms_pin_gate.py"):
        assert "openbao_transit_candidate" not in (root / relative).read_text()
    source = (root / "openbao_transit_candidate.py").read_text()
    tree = ast.parse(source)
    imported = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not imported.intersection({"os", "socket", "http", "requests", "httpx", "config", "production_adapters"})
    assert "releaseReady" not in source
    assert "kms_key_version_id" not in source
