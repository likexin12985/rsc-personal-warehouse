"""Focused OpenBao production-composition checks; no network or real secrets."""

import base64
import hashlib
import json
from dataclasses import replace

import pytest

from app.formal_services.authentication_key_claims import (
    AUTHENTICATION_PURPOSE,
    AliyunAuthenticationKeyBinding,
    OpenBaoAuthenticationKeyBinding,
)
from app.formal_services.authentication_idempotency import (
    AuthenticationEncryptionKeyUnavailable,
)
from app.openbao_registry_candidate import load_openbao_registry_candidate
from app.openbao_transit_candidate import (
    OpenBaoDecryptResponse,
    OpenBaoKeyCoordinate,
    OpenBaoReviewedPin,
    OpenBaoTransitCandidate,
    OpenBaoWrappedKey,
    associated_data_b64,
    context_b64,
)
from app.production_openbao_composition import (
    OpenBaoProductionSettings,
    OpenBaoRuntimeAttestation,
    assess_openbao_readiness,
    assess_openbao_contact_readiness,
    create_openbao_authentication_composition,
    create_openbao_contact_composition,
)


INSTANCE = "auth-test-instance"
OLD = AliyunAuthenticationKeyBinding(
    7, "kms/auth/history", "version-legacy-7", "a" * 64,
)
COORDINATE = OpenBaoKeyCoordinate(AUTHENTICATION_PURPOSE, "test", INSTANCE, 8)
PIN = OpenBaoReviewedPin(
    COORDINATE,
    2,
    hashlib.sha256(("vault:v2:" + base64.b64encode(bytes(range(60))).decode()).encode()).hexdigest(),
    hashlib.sha256(base64.b64decode(context_b64(COORDINATE))).hexdigest(),
    hashlib.sha256(base64.b64decode(associated_data_b64(COORDINATE))).hexdigest(),
)
CURRENT = OpenBaoAuthenticationKeyBinding(PIN)
CONTACT_COORDINATE = OpenBaoKeyCoordinate("material_request_contact", "test", INSTANCE, 3)
CONTACT_CIPHERTEXT = "vault:v4:" + base64.b64encode(bytes(range(60))).decode()
CONTACT_PIN = OpenBaoReviewedPin(
    CONTACT_COORDINATE,
    4,
    hashlib.sha256(CONTACT_CIPHERTEXT.encode()).hexdigest(),
    hashlib.sha256(base64.b64decode(context_b64(CONTACT_COORDINATE))).hexdigest(),
    hashlib.sha256(base64.b64decode(associated_data_b64(CONTACT_COORDINATE))).hexdigest(),
)


class Transport:
    def __init__(self):
        self.calls = []

    def decrypt(self, *, request, timeout_seconds):
        self.calls.append((request, timeout_seconds))
        return OpenBaoDecryptResponse(
            200,
            {"data": {"plaintext": base64.b64encode(bytes(range(32))).decode()}},
        )


def candidate():
    ciphertext = "vault:v2:" + base64.b64encode(bytes(range(60))).decode()
    entry = OpenBaoWrappedKey(COORDINATE, ciphertext, 2)
    return OpenBaoTransitCandidate(
        environment="test", provider_instance_id=INSTANCE,
        entries=(entry,), reviewed_pins=(PIN,), transport=Transport(),
    )


def contact_candidate(*, coordinate=CONTACT_COORDINATE, pin=CONTACT_PIN, transport=None):
    entry = OpenBaoWrappedKey(coordinate, CONTACT_CIPHERTEXT, 4)
    return OpenBaoTransitCandidate(
        environment="test", provider_instance_id=INSTANCE,
        entries=(entry,), reviewed_pins=(pin,), transport=transport or Transport(),
    )


def contact_settings(**changes):
    return settings(
        purpose="material_request_contact", application_key_versions=(3,),
        active_application_key_version=3, **changes,
    )


def settings(**changes):
    values = dict(
        enabled=True, environment="test", provider_instance_id=INSTANCE,
        registry_path="/run/rsc-openbao/registry.json",
        socket_path="/run/rsc-openbao/socket/openbao.sock",
        token_file="/run/rsc-openbao-token/token",
        api_uid=1001, bao_uid=1002, bao_gid=1003, shared_gid=1004,
        token_projector_uid=1005, application_key_versions=(7, 8),
        active_application_key_version=8,
    )
    values.update(changes)
    return OpenBaoProductionSettings(**values)


class Reader:
    def __init__(self):
        self.calls = []

    def __call__(self, purpose, version):
        self.calls.append((purpose, version))
        return {7: OLD, 8: CURRENT}[version]


class WrongScopeReader(Reader):
    def __call__(self, purpose, version):
        value = super().__call__(purpose, version)
        if version == 8:
            value = replace(value, reviewed_pin=replace(
                value.reviewed_pin,
                coordinate=replace(value.reviewed_pin.coordinate, provider_instance_id="other-instance"),
            ))
        return value


def test_default_settings_are_not_ready_without_any_external_access():
    value = OpenBaoProductionSettings()
    assert value.validation_errors() == ("disabled",)
    result = assess_openbao_readiness(value)
    assert result.release_decision == "not_ready"
    assert result.reason_codes == ("disabled",)


@pytest.mark.parametrize("change", [
    {"provider_instance_id": ""},
    {"application_key_versions": (8, 8)},
    {"application_key_versions": (8,), "active_application_key_version": 7},
    {"token_file": "/run/../token"},
    {"api_uid": 1002},
])
def test_incomplete_coordinates_remain_not_ready(change):
    value = settings(**change)
    assert value.validation_errors()
    assert assess_openbao_readiness(value).release_decision == "not_ready"
    assert create_openbao_authentication_composition(value) is None


def test_factory_reads_only_explicit_versions_and_routes_each_provider_without_fallback():
    reader = Reader()
    result = create_openbao_authentication_composition(
        settings(), claim_reader=reader, candidate=candidate(),
        aliyun_key_loader=lambda binding: bytes(range(32)),
    )
    assert result is not None
    assert reader.calls == [(AUTHENTICATION_PURPOSE, 7), (AUTHENTICATION_PURPOSE, 8)]
    assert result.key_provider.key_for_version(7).version == 7
    assert result.key_provider.current_key().version == 8


def test_factory_wires_pin_registry_and_transport_only_for_explicit_coordinates():
    calls = []
    loaded = candidate()

    def pins(db, **kwargs):
        calls.append(("pins", db, kwargs["expected_coordinates"]))
        assert kwargs["expected_coordinates"] == (COORDINATE,)
        return (PIN,)

    def transport_factory(**kwargs):
        calls.append(("transport", kwargs))
        return Transport()

    def registry_loader(**kwargs):
        calls.append(("registry", kwargs))
        assert kwargs["reviewed_pins"] == (PIN,)
        assert kwargs["environment"] == "test"
        return loaded

    result = create_openbao_authentication_composition(
        settings(application_key_versions=(8,)), claim_reader=lambda _purpose, _version: CURRENT,
        db=object(), pin_reader=pins, transport_factory=transport_factory,
        registry_loader=registry_loader,
    )
    assert result is not None
    assert [item[0] for item in calls] == ["pins", "transport", "registry"]


def test_missing_historical_aliyun_loader_does_not_build_provider():
    result = create_openbao_authentication_composition(
        settings(), claim_reader=Reader(), candidate=candidate(),
    )
    assert result is None


def test_factory_rejects_claim_binding_from_another_openbao_scope():
    result = create_openbao_authentication_composition(
        settings(), claim_reader=WrongScopeReader(), candidate=candidate(),
        aliyun_key_loader=lambda binding: bytes(range(32)),
    )
    assert result is None


def test_openbao_resolution_failure_does_not_try_aliyun_fallback():
    active_reader = Reader()
    transport = Transport()
    broken = OpenBaoTransitCandidate(
        environment="test", provider_instance_id=INSTANCE,
        entries=(OpenBaoWrappedKey(COORDINATE, "vault:v2:" + base64.b64encode(bytes(range(60))).decode(), 2),),
        reviewed_pins=(PIN,),
        transport=type("Failing", (), {"decrypt": lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no fallback"))})(),
    )
    aliyun_calls = []
    result = create_openbao_authentication_composition(
        settings(), claim_reader=active_reader, candidate=broken,
        aliyun_key_loader=lambda binding: aliyun_calls.append(binding) or bytes(range(32)),
    )
    assert result is not None
    with pytest.raises(Exception):
        result.key_provider.current_key()
    assert aliyun_calls == []


def test_readiness_requires_all_external_attestation_bits():
    value = settings()
    composition = create_openbao_authentication_composition(
        value, claim_reader=Reader(), candidate=candidate(),
        aliyun_key_loader=lambda binding: bytes(range(32)),
    )
    incomplete = assess_openbao_readiness(value, composition=composition, attestation=OpenBaoRuntimeAttestation())
    assert incomplete.release_decision == "not_ready"
    complete = assess_openbao_readiness(
        value, composition=composition,
        attestation=OpenBaoRuntimeAttestation(
            database_identity_verified=True, database_acl_verified=True,
            registry_mount_verified=True, token_projection_verified=True,
            transport_identity_verified=True, decrypt_verified=True,
            independent_pin_reviewed=True,
        ),
    )
    assert complete.release_decision == "ready"


def test_readiness_rejects_composition_with_different_explicit_version_set():
    value = settings()
    composition = create_openbao_authentication_composition(
        value, claim_reader=Reader(), candidate=candidate(),
        aliyun_key_loader=lambda binding: bytes(range(32)),
    )
    assert composition is not None
    altered = replace(composition, application_key_versions=(8,))
    result = assess_openbao_readiness(
        value, composition=altered,
        attestation=OpenBaoRuntimeAttestation(
            database_identity_verified=True, database_acl_verified=True,
            registry_mount_verified=True, token_projection_verified=True,
            transport_identity_verified=True, decrypt_verified=True,
            independent_pin_reviewed=True,
        ),
    )
    assert result.release_decision == "not_ready"
    assert "composition_coordinate_mismatch" in result.reason_codes


def test_contact_default_and_missing_coordinates_stay_not_ready():
    assert assess_openbao_contact_readiness(OpenBaoProductionSettings()).release_decision == "not_ready"
    assert create_openbao_contact_composition(contact_settings(registry_path="")) is None


def test_contact_factory_keeps_active_coordinate_exact_and_requires_reviewed_pin():
    value = contact_settings()
    result = create_openbao_contact_composition(
        value, candidate=contact_candidate(), reviewed_pins=(CONTACT_PIN,),
    )
    assert result is not None
    assert result.active_binding.coordinate == CONTACT_COORDINATE
    assert result.cipher.active_key_version() == 3


def test_contact_factory_rejects_candidate_scope_or_entry_version_mismatch():
    wrong_coordinate = replace(CONTACT_COORDINATE, provider_instance_id="other-instance")
    wrong_pin = replace(CONTACT_PIN, coordinate=wrong_coordinate)
    poisoned = contact_candidate()
    poisoned._entries[("material_request_contact", 3)] = OpenBaoWrappedKey(
        wrong_coordinate, CONTACT_CIPHERTEXT, 4,
    )
    assert create_openbao_contact_composition(
        contact_settings(), candidate=poisoned,
        reviewed_pins=(wrong_pin,),
    ) is None


def test_contact_factory_requires_legacy_v1_pair_without_fallback():
    value = contact_settings()
    assert create_openbao_contact_composition(
        value, candidate=contact_candidate(), reviewed_pins=(CONTACT_PIN,),
        legacy_cipher=object(),
    ) is None
    result = create_openbao_contact_composition(
        value, candidate=contact_candidate(), reviewed_pins=(CONTACT_PIN,),
        legacy_cipher=object(), legacy_kms_key_id="kms/contact-v1",
    )
    assert result is not None
    assert assess_openbao_contact_readiness(value, composition=result).release_decision == "not_ready"


def test_contact_readiness_rejects_active_binding_transit_version_drift():
    value = contact_settings()
    composition = create_openbao_contact_composition(
        value, candidate=contact_candidate(), reviewed_pins=(CONTACT_PIN,),
    )
    assert composition is not None
    altered = replace(
        composition,
        active_binding=replace(composition.active_binding, transit_key_version=5),
    )
    result = assess_openbao_contact_readiness(value, composition=altered)
    assert result.release_decision == "not_ready"
    assert "composition_coordinate_mismatch" in result.reason_codes


# Regression evidence for the authentication composition trust boundary.  All
# material below is synthetic; a transport call is itself forbidden on failure.
def _pin_binding_material(coordinate=COORDINATE, *, seed=0, transit_version=2):
    ciphertext = (
        f"vault:v{transit_version}:"
        + base64.b64encode(bytes((seed + value) % 256 for value in range(60))).decode()
    )
    entry = OpenBaoWrappedKey(coordinate, ciphertext, transit_version)
    pin = OpenBaoReviewedPin(
        coordinate, transit_version,
        hashlib.sha256(ciphertext.encode()).hexdigest(),
        hashlib.sha256(base64.b64decode(context_b64(coordinate))).hexdigest(),
        hashlib.sha256(base64.b64decode(associated_data_b64(coordinate))).hexdigest(),
    )
    return entry, pin


def _pin_binding_candidate(*materials, transport):
    return OpenBaoTransitCandidate(
        environment="test", provider_instance_id=INSTANCE,
        entries=tuple(item[0] for item in materials),
        reviewed_pins=tuple(item[1] for item in materials),
        transport=transport,
    )


def _pin_binding_attestation():
    return OpenBaoRuntimeAttestation(
        database_identity_verified=True, database_acl_verified=True,
        registry_mount_verified=True, token_projection_verified=True,
        transport_identity_verified=True, decrypt_verified=True,
        independent_pin_reviewed=True,
    )


def _pin_binding_fixture():
    historical = _pin_binding_material(
        replace(COORDINATE, application_key_version=6), seed=6,
    )
    transport = Transport()
    loaded = _pin_binding_candidate(
        historical, _pin_binding_material(), transport=transport,
    )
    claims = {
        6: OpenBaoAuthenticationKeyBinding(historical[1]), 7: OLD, 8: CURRENT,
    }
    aliyun_calls = []
    value = settings(application_key_versions=(6, 7, 8))
    composition = create_openbao_authentication_composition(
        value, claim_reader=lambda _purpose, version: claims.get(version),
        candidate=loaded,
        aliyun_key_loader=lambda binding: aliyun_calls.append(binding) or bytes(range(32)),
    )
    assert composition is not None
    assert transport.calls == []
    assert aliyun_calls == []
    return value, composition, claims, transport, aliyun_calls, loaded


def test_pin_binding_rejects_self_consistent_candidate_with_another_ciphertext():
    transport = Transport()
    loaded = _pin_binding_candidate(_pin_binding_material(seed=1), transport=transport)
    aliyun_calls = []
    composition = create_openbao_authentication_composition(
        settings(), claim_reader=Reader(), candidate=loaded,
        aliyun_key_loader=lambda binding: aliyun_calls.append(binding) or bytes(range(32)),
    )
    assert composition is None
    assert transport.calls == []
    assert aliyun_calls == []


@pytest.mark.parametrize("field", [
    "ciphertext_sha256", "context_sha256", "associated_data_sha256",
])
def test_pin_binding_factory_rejects_each_claim_digest_mismatch(field):
    transport = Transport()
    loaded = _pin_binding_candidate(_pin_binding_material(), transport=transport)
    changed = OpenBaoAuthenticationKeyBinding(replace(PIN, **{field: "b" * 64}))
    aliyun_calls = []
    composition = create_openbao_authentication_composition(
        settings(),
        claim_reader=lambda _purpose, version: {7: OLD, 8: changed}[version],
        candidate=loaded,
        aliyun_key_loader=lambda binding: aliyun_calls.append(binding) or bytes(range(32)),
    )
    assert composition is None
    assert transport.calls == []
    assert aliyun_calls == []


def test_pin_binding_registry_file_loader_rejects_independent_reader_claim_disagreement(tmp_path):
    # Keep the real strict registry loader in this path: its own pins are valid,
    # but differ from the independently read claim for the same exact version.
    entry, registry_pin = _pin_binding_material(seed=1)
    private = tmp_path.resolve()
    private.chmod(0o700)
    registry_path = private / "registry.json"
    registry_path.write_text(json.dumps({
        "schema": "rsc.openbao.wrapped-data-key-registry.v1",
        "provider": "openbao_transit_v1",
        "entries": [{
            "purpose": COORDINATE.purpose,
            "environment": COORDINATE.environment,
            "provider_instance_id": COORDINATE.provider_instance_id,
            "application_key_version": COORDINATE.application_key_version,
            "key_path": COORDINATE.key_path,
            "transit_key_version": entry.transit_key_version,
            "ciphertext": entry.ciphertext,
            "context_b64": context_b64(COORDINATE),
            "associated_data_b64": associated_data_b64(COORDINATE),
        }],
    }))
    registry_path.chmod(0o600)
    transport = Transport()
    positive_control = load_openbao_registry_candidate(
        registry_path=str(registry_path), environment="test",
        provider_instance_id=INSTANCE, reviewed_pins=(registry_pin,),
        transport=transport,
    )
    assert type(positive_control) is OpenBaoTransitCandidate
    assert positive_control._entries == {(AUTHENTICATION_PURPOSE, 8): entry}
    assert transport.calls == []
    aliyun_calls = []
    pin_calls = []

    def independent_pins(_db, **kwargs):
        pin_calls.append(kwargs["expected_coordinates"])
        return (registry_pin,)

    composition = create_openbao_authentication_composition(
        settings(registry_path=str(registry_path)), db=object(),
        claim_reader=Reader(), pin_reader=independent_pins, transport=transport,
        aliyun_key_loader=lambda binding: aliyun_calls.append(binding) or bytes(range(32)),
    )
    assert pin_calls == [(COORDINATE,)]
    assert composition is None
    assert transport.calls == []
    assert aliyun_calls == []


@pytest.mark.parametrize("shape", ["extra-authentication", "extra-contact", "missing"])
def test_pin_binding_factory_rejects_candidate_coordinate_set_mismatch(shape):
    transport = Transport()
    extra_coordinate = replace(
        COORDINATE,
        purpose="material_request_contact" if shape == "extra-contact" else AUTHENTICATION_PURPOSE,
        application_key_version=9,
    )
    materials = [_pin_binding_material(extra_coordinate, seed=9)]
    if shape != "missing":
        materials.append(_pin_binding_material())
    loaded = _pin_binding_candidate(*materials, transport=transport)
    aliyun_calls = []
    composition = create_openbao_authentication_composition(
        settings(), claim_reader=Reader(), candidate=loaded,
        aliyun_key_loader=lambda binding: aliyun_calls.append(binding) or bytes(range(32)),
    )
    assert composition is None
    assert transport.calls == []
    assert aliyun_calls == []


def _pin_binding_drift_candidate(composition, drift):
    entries = composition.candidate._entries
    index = (AUTHENTICATION_PURPOSE, 8)
    if drift == "missing":
        del entries[index]
    elif drift in {"extra-authentication", "extra-contact"}:
        coordinate = replace(
            COORDINATE,
            purpose="material_request_contact" if drift == "extra-contact" else AUTHENTICATION_PURPOSE,
            application_key_version=9,
        )
        entries[(coordinate.purpose, 9)] = _pin_binding_material(coordinate, seed=9)[0]
    else:
        coordinate = COORDINATE
        if drift == "environment":
            coordinate = replace(coordinate, environment="staging")
        elif drift == "instance":
            coordinate = replace(coordinate, provider_instance_id="another-instance")
        elif drift == "purpose":
            coordinate = replace(coordinate, purpose="material_request_contact")
        entries[index] = _pin_binding_material(
            coordinate, seed=1 if drift == "ciphertext" else 0,
            transit_version=3 if drift == "transit-version" else 2,
        )[0]


@pytest.mark.parametrize("stage", ["readiness", "request"])
@pytest.mark.parametrize("drift", [
    "ciphertext", "transit-version", "environment", "instance", "purpose",
    "missing", "extra-authentication", "extra-contact",
])
def test_pin_binding_candidate_drift_is_rejected_without_provider_calls(stage, drift):
    value, composition, _claims, transport, aliyun_calls, _loaded = _pin_binding_fixture()
    _pin_binding_drift_candidate(composition, drift)
    if stage == "readiness":
        readiness = assess_openbao_readiness(
            value, composition=composition, attestation=_pin_binding_attestation(),
        )
        assert readiness.release_decision == "not_ready"
        assert "composition_coordinate_mismatch" in readiness.reason_codes
    else:
        with pytest.raises(AuthenticationEncryptionKeyUnavailable):
            composition.key_provider.current_key()
    assert transport.calls == []
    assert aliyun_calls == []


@pytest.mark.parametrize("field,value", [
    ("ciphertext_sha256", "b" * 64),
    ("context_sha256", "b" * 64),
    ("associated_data_sha256", "b" * 64),
    ("transit_key_version", 3),
])
def test_pin_binding_readiness_rejects_active_pin_separate_from_bound_claims(field, value):
    config, composition, _claims, transport, aliyun_calls, _loaded = _pin_binding_fixture()
    altered = replace(composition, active_binding=OpenBaoAuthenticationKeyBinding(
        replace(PIN, **{field: value}),
    ))
    readiness = assess_openbao_readiness(
        config, composition=altered, attestation=_pin_binding_attestation(),
    )
    assert readiness.release_decision == "not_ready"
    assert "composition_coordinate_mismatch" in readiness.reason_codes
    assert transport.calls == []
    assert aliyun_calls == []


@pytest.mark.parametrize("drift", [
    "ciphertext_sha256", "context_sha256", "associated_data_sha256",
    "environment", "instance", "transit-version",
])
def test_pin_binding_historical_claim_drift_is_rejected_before_decrypt(drift):
    _config, composition, claims, transport, aliyun_calls, _loaded = _pin_binding_fixture()
    pin = claims[6].reviewed_pin
    if drift in {"environment", "instance"}:
        coordinate = replace(
            pin.coordinate,
            **({"environment": "staging"} if drift == "environment"
               else {"provider_instance_id": "another-instance"}),
        )
        pin = replace(pin, coordinate=coordinate)
    else:
        pin = replace(pin, **({"transit_key_version": 3} if drift == "transit-version"
                             else {drift: "b" * 64}))
    claims[6] = OpenBaoAuthenticationKeyBinding(pin)
    with pytest.raises(AuthenticationEncryptionKeyUnavailable):
        composition.key_provider.key_for_version(6)
    assert transport.calls == []
    assert aliyun_calls == []


@pytest.mark.parametrize("direction", ["openbao-to-aliyun", "aliyun-to-openbao"])
def test_pin_binding_historical_provider_switch_is_rejected_before_either_loader(direction):
    _config, composition, claims, transport, aliyun_calls, _loaded = _pin_binding_fixture()
    if direction == "openbao-to-aliyun":
        version = 6
        claims[version] = replace(OLD, application_key_version=version)
    else:
        version = 7
        entry, pin = _pin_binding_material(replace(COORDINATE, application_key_version=version), seed=7)
        claims[version] = OpenBaoAuthenticationKeyBinding(pin)
        composition.candidate._entries[(AUTHENTICATION_PURPOSE, version)] = entry
    with pytest.raises(AuthenticationEncryptionKeyUnavailable):
        composition.key_provider.key_for_version(version)
    assert transport.calls == []
    assert aliyun_calls == []


def test_pin_binding_undeclared_historical_aliyun_version_never_reaches_loader():
    _config, composition, claims, transport, aliyun_calls, _loaded = _pin_binding_fixture()
    claims[9] = replace(OLD, application_key_version=9)
    with pytest.raises(AuthenticationEncryptionKeyUnavailable):
        composition.key_provider.key_for_version(9)
    assert transport.calls == []
    assert aliyun_calls == []


def test_pin_binding_legitimate_mixed_history_remains_readable_by_exact_provider():
    config, composition, _claims, transport, aliyun_calls, _loaded = _pin_binding_fixture()
    assert assess_openbao_readiness(
        config, composition=composition, attestation=_pin_binding_attestation(),
    ).release_decision == "ready"
    assert transport.calls == []
    assert aliyun_calls == []
    assert composition.key_provider.key_for_version(6).version == 6
    assert composition.key_provider.key_for_version(7).version == 7
    assert composition.key_provider.current_key().version == 8
    assert len(transport.calls) == 2
    assert aliyun_calls == [OLD]


def test_pin_binding_original_candidate_mutation_cannot_replace_composition_snapshot():
    _config, composition, _claims, transport, aliyun_calls, loaded = _pin_binding_fixture()
    loaded._entries[(AUTHENTICATION_PURPOSE, 8)] = _pin_binding_material(seed=99)[0]
    assert composition.key_provider.current_key().version == 8
    assert len(transport.calls) == 1
    assert transport.calls[0][0].ciphertext == _pin_binding_material()[0].ciphertext
    assert aliyun_calls == []
