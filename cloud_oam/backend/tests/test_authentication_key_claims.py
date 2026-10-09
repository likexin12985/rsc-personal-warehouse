"""Synthetic C1 provider-routing contracts; no runtime credentials or network."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import socket

import pytest

from app.formal_services.authentication_idempotency import (
    AuthenticationCipherConfigurationError, AuthenticationEncryptionKeyUnavailable,
    AuthenticationIdempotencyError, KmsAuthenticationKeyProvider,
    complete_authentication_failure, create_authentication_response_cipher,
)
from app.formal_services.authentication_key_claims import (
    AUTHENTICATION_PURPOSE, AliyunAuthenticationKeyBinding,
    ClaimRoutedAuthenticationKeyProvider, OpenBaoAuthenticationKeyBinding,
)
from app.openbao_transit_candidate import OpenBaoKeyCoordinate, OpenBaoReviewedPin
from test_authentication_idempotency import (
    ACCESS_TOKEN, NEW_REFRESH_TOKEN, _begin, _complete_success, db,
)


OLD_KEY = bytes(range(32))
OLD = AliyunAuthenticationKeyBinding(7, "kms/auth/history", "version-legacy-7", "a" * 64)
CURRENT = OpenBaoAuthenticationKeyBinding(OpenBaoReviewedPin(
    OpenBaoKeyCoordinate(AUTHENTICATION_PURPOSE, "test", "auth-test-instance", 8),
    2, "b" * 64, "c" * 64, "d" * 64,
))
NEXT = OpenBaoAuthenticationKeyBinding(replace(
    CURRENT.reviewed_pin,
    coordinate=replace(CURRENT.reviewed_pin.coordinate, application_key_version=9),
    transit_key_version=3, ciphertext_sha256="e" * 64,
))
START = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("authentication claim tests cannot access a network")
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)


class IndependentReader:
    """Detached synthetic DB claims/pins fixture, not registry-derived proof."""
    def __init__(self, bindings=(OLD, CURRENT, NEXT)):
        self.rows = {row.application_key_version: row for row in bindings}
        self.calls = []
        self.failure = None

    def __call__(self, purpose, version):
        self.calls.append((purpose, version))
        assert purpose == AUTHENTICATION_PURPOSE
        if self.failure:
            raise self.failure
        return self.rows.get(version)


class Loaders:
    def __init__(self):
        self.calls = []
        self.failure = None

    def aliyun(self, binding):
        self.calls.append(binding)
        assert type(binding) is AliyunAuthenticationKeyBinding
        if self.failure:
            raise self.failure
        assert binding == OLD  # Full expected coordinates reach the adapter.
        return OLD_KEY

    def openbao(self, binding):
        self.calls.append(binding)
        assert type(binding) is OpenBaoAuthenticationKeyBinding
        if self.failure:
            raise self.failure
        assert binding in (CURRENT, NEXT)
        return hashlib.sha256(str(binding.application_key_version).encode()).digest()


def routed(*, active=CURRENT, reader=None, loaders=None):
    reader = reader or IndependentReader()
    loaders = loaders or Loaders()
    provider = ClaimRoutedAuthenticationKeyProvider(
        active_binding=active, claim_reader=reader,
        aliyun_key_loader=loaders.aliyun, openbao_key_loader=loaders.openbao,
    )
    return create_authentication_response_cipher(environment="production", key_provider=provider), reader, loaders


def legacy():
    return create_authentication_response_cipher(
        environment="production", key_provider=KmsAuthenticationKeyProvider(
            kms_key_id=OLD.kms_key_id, active_version=7,
            key_loader=lambda key_id, version: OLD_KEY if (key_id, version) == (OLD.kms_key_id, 7) else None,
        ),
    )


def evidence(operation):
    def timestamp(value):
        # SQLite drops timestamptz's UTC marker on populate_existing; compare
        # the persisted instant rather than SQLAlchemy's pre-refresh object.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")

    return (
        operation.status, operation.response_ciphertext, operation.response_nonce,
        operation.response_sha256, operation.encryption_key_version,
        operation.http_status, timestamp(operation.completed_at), timestamp(operation.expires_at),
    )


def test_old_unexpired_response_replays_with_current_openbao_without_resolving_active(db):
    old = legacy()
    began = _begin(db, old, now=START)
    first = _complete_success(db, old, began, now=START + timedelta(seconds=1))
    before = evidence(began.operation)
    cipher, reader, loaders = routed()
    replay = _begin(db, cipher, now=START + timedelta(seconds=5))
    assert replay.replayed and replay.response.payload == first.payload
    assert reader.calls == [(AUTHENTICATION_PURPOSE, 7)]
    assert loaders.calls == [OLD]
    assert evidence(began.operation) == before


def test_openbao_success_uses_one_preflight_key_and_rotated_active_replays_history(db):
    cipher, reader, loaders = routed()
    assert cipher.active_key_version() == 8
    began = _begin(db, cipher, now=START)
    first = _complete_success(db, cipher, began, now=START + timedelta(seconds=1))
    assert began.operation.encryption_key_version == 8
    assert reader.calls == [(AUTHENTICATION_PURPOSE, 8)]
    assert loaders.calls == [CURRENT]
    assert ACCESS_TOKEN.encode() not in began.operation.response_ciphertext
    assert NEW_REFRESH_TOKEN.encode() not in began.operation.response_ciphertext
    next_cipher, next_reader, next_loaders = routed(active=NEXT)
    replay = _begin(db, next_cipher, now=START + timedelta(seconds=5))
    assert replay.response.payload == first.payload
    assert next_reader.calls == [(AUTHENTICATION_PURPOSE, 8)]
    assert next_loaders.calls == [CURRENT]  # No default/latest/current-v9 fallback.


def test_openbao_controlled_failure_replays_exact_http_status_and_payload(db):
    cipher, _, _ = routed()
    began = _begin(db, cipher, now=START)
    failure = complete_authentication_failure(
        db, begin=began, payload={"detail": "synthetic denied"}, http_status=403,
        cipher=cipher, now=START + timedelta(seconds=1),
    )
    replay, _, _ = routed(active=NEXT)
    result = _begin(db, replay, now=START + timedelta(seconds=2)).response
    assert result.status == "failed" and result.http_status == 403
    assert result.payload == failure.payload and result.replayed


def test_expired_replay_rejected_before_reading_claim_or_resolving_any_key(db):
    old = legacy()
    began = _begin(db, old, now=START)
    _complete_success(db, old, began, now=START + timedelta(seconds=1))
    before = evidence(began.operation)
    cipher, reader, loaders = routed()
    with pytest.raises(AuthenticationIdempotencyError) as failure:
        _begin(db, cipher, now=START + timedelta(seconds=91))
    assert failure.value.code == "authentication_idempotency_expired"
    assert reader.calls == loaders.calls == []
    assert evidence(began.operation) == before


@pytest.mark.parametrize("failure_mode", ["missing", "wrong_version", "ambiguous", "reader_failure", "provider_failure"])
def test_legacy_replay_routing_failure_is_closed_without_overwriting_or_provider_fallback(db, failure_mode):
    old = legacy()
    began = _begin(db, old, now=START)
    _complete_success(db, old, began, now=START + timedelta(seconds=1))
    before = evidence(began.operation)
    reader, loaders = IndependentReader(), Loaders()
    if failure_mode == "missing":
        del reader.rows[7]
    elif failure_mode == "wrong_version":
        reader.rows[7] = CURRENT
    elif failure_mode == "ambiguous":
        reader.rows[7] = (OLD, CURRENT)
    elif failure_mode == "reader_failure":
        reader.failure = RuntimeError("synthetic-reader-secret")
    else:
        loaders.failure = RuntimeError("synthetic-provider-secret")
    cipher, _, _ = routed(reader=reader, loaders=loaders)
    with pytest.raises(AuthenticationIdempotencyError) as failure:
        _begin(db, cipher, now=START + timedelta(seconds=5))
    assert failure.value.code == "authentication_idempotency_response_unavailable"
    assert evidence(began.operation) == before
    assert loaders.calls == ([OLD] if failure_mode == "provider_failure" else [])


def test_active_provider_cannot_rebind_same_application_version():
    reader = IndependentReader()
    reader.rows[8] = replace(OLD, application_key_version=8)
    cipher, _, loaders = routed(reader=reader)
    with pytest.raises(AuthenticationEncryptionKeyUnavailable):
        cipher.active_key_version()
    assert loaders.calls == []


@pytest.mark.parametrize("pin", [
    replace(CURRENT.reviewed_pin, transit_key_version=3),
    replace(CURRENT.reviewed_pin, coordinate=replace(CURRENT.reviewed_pin.coordinate, environment="production")),
    replace(CURRENT.reviewed_pin, coordinate=replace(CURRENT.reviewed_pin.coordinate, provider_instance_id="another-instance")),
    replace(CURRENT.reviewed_pin, ciphertext_sha256="0" * 64),
    replace(CURRENT.reviewed_pin, context_sha256="0" * 64),
    replace(CURRENT.reviewed_pin, associated_data_sha256="0" * 64),
])
def test_active_openbao_entire_independent_pin_must_match_before_loader(pin):
    reader = IndependentReader()
    reader.rows[8] = OpenBaoAuthenticationKeyBinding(pin)
    cipher, _, loaders = routed(reader=reader)
    with pytest.raises(AuthenticationEncryptionKeyUnavailable):
        cipher.active_key_version()
    assert loaders.calls == []


def test_selected_loader_missing_has_no_fallback_or_exception_material():
    provider = ClaimRoutedAuthenticationKeyProvider(
        active_binding=CURRENT, claim_reader=IndependentReader(),
        aliyun_key_loader=lambda binding: pytest.fail("cannot route an OpenBao claim to Aliyun"),
    )
    with pytest.raises(AuthenticationEncryptionKeyUnavailable) as failure:
        provider.current_key()
    assert failure.value.__cause__ is None and failure.value.__context__ is None
    reader = IndependentReader()
    reader.failure = RuntimeError("synthetic-reader-secret")
    provider = ClaimRoutedAuthenticationKeyProvider(active_binding=CURRENT, claim_reader=reader)
    with pytest.raises(AuthenticationEncryptionKeyUnavailable) as failure:
        provider.current_key()
    assert failure.value.__cause__ is None and failure.value.__context__ is None


@pytest.mark.parametrize("value", [True, 0, -1, "8", 2_147_483_648])
def test_invalid_application_version_never_calls_db_reader(value):
    reader = IndependentReader()
    provider = ClaimRoutedAuthenticationKeyProvider(active_binding=CURRENT, claim_reader=reader)
    with pytest.raises(AuthenticationEncryptionKeyUnavailable):
        provider.key_for_version(value)
    assert reader.calls == []


def test_wrong_purpose_and_duck_typed_or_subclass_production_provider_are_rejected():
    contact_pin = replace(CURRENT.reviewed_pin, coordinate=replace(CURRENT.reviewed_pin.coordinate, purpose="material_request_contact"))
    with pytest.raises(AuthenticationEncryptionKeyUnavailable):
        OpenBaoAuthenticationKeyBinding(contact_pin)

    class Subclass(ClaimRoutedAuthenticationKeyProvider):
        pass

    provider = Subclass(active_binding=CURRENT, claim_reader=IndependentReader())
    with pytest.raises(AuthenticationCipherConfigurationError):
        create_authentication_response_cipher(environment="production", key_provider=provider)
    with pytest.raises(AuthenticationCipherConfigurationError):
        create_authentication_response_cipher(environment="production", key_provider=object())
