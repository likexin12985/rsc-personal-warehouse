from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import DateTime, bindparam, create_engine, event, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.foundation_models import KmsDataKeyPin
from app.key_provider_models import ApplicationKeyVersionClaim, OpenBaoDataKeyPin
import app.production_adapters as adapters
from app.openbao_transit_candidate import OpenBaoKeyCoordinate, OpenBaoReviewedPin
from app.persisted_key_references import (
    AliyunPersistedKeyReference,
    PersistedKeyReferenceCatalog,
    PersistedKeyReferenceUnavailable,
)
from app.production_adapters import (
    AUTHENTICATION_PURPOSE,
    MATERIAL_REQUEST_CONTACT_PURPOSE,
    AliyunKmsEnvelopeKeyLoader,
    KmsDecryptResult,
    ProductionAdapterConfigurationError,
    ProductionAdapterUnavailable,
    REGISTRY_SCHEMA,
    create_production_authentication_cipher,
    create_production_material_request_contact_cipher,
    validate_persisted_kms_key_references,
    validate_production_adapter_installation,
)


AUTH_KEY_ID = "kms-rsc-auth-data-key"
CONTACT_KEY_ID = "kms-rsc-contact-data-key"
AUTH_KMS_VERSION = "12345678-auth-kms-version"
CONTACT_KMS_VERSION = "12345678-contact-kms-version"
AUTH_CIPHERTEXT = "Q2lwaGVydGV4dEJsb2JBdXRoMTIzNA=="
CONTACT_CIPHERTEXT = "Q2lwaGVydGV4dEJsb2JDb250YWN0NTY3OA=="
AUTH_KEY = bytes(range(32))
CONTACT_KEY = bytes(reversed(range(32)))
RETIRED_CONTACT_KEY_ID = "kms-rsc-contact-data-key-retired"
RETIRED_CONTACT_KMS_VERSION = "12345678-retired-contact-version"
RETIRED_CONTACT_CIPHERTEXT = "Q2lwaGVydGV4dEJsb2JSZXRpcmVkOTAxMg=="
RETIRED_CONTACT_KEY = bytes((value + 17) % 256 for value in range(32))
RETIRED_CONTACT_APPLICATION_VERSION = 2


def _context(purpose: str, key_id: str, version: int) -> dict[str, str]:
    return {
        "application": "cloud_oam",
        "purpose": purpose,
        "kms_key_id": key_id,
        "application_key_version": str(version),
    }


def _entry(
    *,
    purpose: str,
    key_id: str,
    kms_version: str,
    ciphertext: str,
    version: int = 1,
) -> dict[str, object]:
    return {
        "purpose": purpose,
        "kms_key_id": key_id,
        "application_key_version": version,
        "kms_key_version_id": kms_version,
        "ciphertext_blob": ciphertext,
        "encryption_context": _context(purpose, key_id, version),
    }


def _registry_document() -> dict[str, object]:
    return {
        "schema": REGISTRY_SCHEMA,
        "entries": [
            _entry(
                purpose=AUTHENTICATION_PURPOSE,
                key_id=AUTH_KEY_ID,
                kms_version=AUTH_KMS_VERSION,
                ciphertext=AUTH_CIPHERTEXT,
            ),
            _entry(
                purpose=MATERIAL_REQUEST_CONTACT_PURPOSE,
                key_id=CONTACT_KEY_ID,
                kms_version=CONTACT_KMS_VERSION,
                ciphertext=CONTACT_CIPHERTEXT,
            ),
        ],
    }


def _write_registry(tmp_path, document: dict[str, object] | None = None):
    path = tmp_path / "encrypted-data-keys.json"
    path.write_text(
        json.dumps(document or _registry_document(), sort_keys=True),
        encoding="utf-8",
    )
    return path


class FakeKmsClient:
    def __init__(self, results: dict[str, KmsDecryptResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, dict[str, str]]] = []

    def decrypt(self, *, ciphertext_blob: str, encryption_context):
        self.calls.append((ciphertext_blob, dict(encryption_context)))
        result = self.results.get(ciphertext_blob)
        if result is None:
            raise RuntimeError("fake KMS unavailable")
        return result


class FakePersistedReferenceSession:
    """Adapter-only double; the provider-aware scanner has real-DB tests.

    A detached catalog is supplied independently of the later DB-pin read so
    these tests also exercise drift between those two proof boundaries.
    """

    def __init__(self, *, catalog, pin_rows=()) -> None:
        self.catalog = catalog
        self.pin_rows = list(pin_rows)
        self.execute_calls = 0
        self.scan_calls = []
        self.no_autoflush_entries = 0
        self._no_autoflush = False

    @property
    @contextmanager
    def no_autoflush(self):
        assert not self._no_autoflush
        self._no_autoflush = True
        self.no_autoflush_entries += 1
        try:
            yield
        finally:
            self._no_autoflush = False

    def execute(self, statement):
        assert self._no_autoflush, "pin inspection must not flush pending writes"
        assert statement.is_select
        assert {table.name for table in statement.get_final_froms()} == {"kms_data_key_pins"}
        self.execute_calls += 1
        rows = self.pin_rows

        class _Result:
            def all(self):
                return list(rows)

        return _Result()

    def flush(self):
        pytest.fail("persisted-reference inspection must not flush")

    commit = rollback = flush


@pytest.fixture
def stub_persisted_catalog(monkeypatch):
    """Mock only the scanner boundary; leave registry and DB-pin proof real."""
    def scan(db, *, environment, now):
        assert isinstance(db, FakePersistedReferenceSession)
        assert environment == "production"
        db.scan_calls.append((environment, now))
        if isinstance(db.catalog, Exception):
            raise db.catalog
        return db.catalog

    monkeypatch.setattr(adapters, "scan_persisted_key_references", scan)


def _persisted_catalog(loader, coordinates=None):
    selected = set(coordinates) if coordinates is not None else None
    return PersistedKeyReferenceCatalog(aliyun=tuple(
        AliyunPersistedKeyReference(purpose, version, key_id, pin.kms_key_version_id, pin.ciphertext_sha256)
        for (purpose, key_id, version), pin in loader.pin_manifest().items()
        if selected is None or (purpose, key_id, version) in selected
    ))


def _fake_client() -> FakeKmsClient:
    return FakeKmsClient(
        {
            AUTH_CIPHERTEXT: KmsDecryptResult(
                plaintext_b64=base64.b64encode(AUTH_KEY).decode("ascii"),
                key_id=AUTH_KEY_ID,
                key_version_id=AUTH_KMS_VERSION,
            ),
            CONTACT_CIPHERTEXT: KmsDecryptResult(
                plaintext_b64=base64.b64encode(CONTACT_KEY).decode("ascii"),
                key_id=CONTACT_KEY_ID,
                key_version_id=CONTACT_KMS_VERSION,
            ),
        }
    )


def _loader(tmp_path, client: FakeKmsClient | None = None):
    selected = client or _fake_client()
    loader = AliyunKmsEnvelopeKeyLoader(
        endpoint="kms.cn-hangzhou.aliyuncs.com",
        region="cn-hangzhou",
        registry_path=str(_write_registry(tmp_path)),
        client_factory=lambda **_: selected,
    )
    return loader, selected


def _pin_rows(loader: AliyunKmsEnvelopeKeyLoader):
    return [
        (
            purpose,
            key_id,
            version,
            pin.kms_key_version_id,
            pin.ciphertext_sha256,
        )
        for (purpose, key_id, version), pin in loader.pin_manifest().items()
    ]


def _production_settings(registry_path: str) -> Settings:
    return Settings(
        _env_file=None,
        environment="production",
        database_url="postgresql+psycopg://star_oam_api:test@db/test",
        database_schema_mode="alembic",
        legacy_prototype_writes_enabled=False,
        admin_mobile="",
        admin_name="",
        admin_initial_password=None,
        password_login_enabled=False,
        edge_sync_enabled=False,
        edge_sync_secret="",
        edge_sync_allowed_sources="",
        edge_sync_legacy_batches_enabled=False,
        edge_sync_legacy_personnel_projection_enabled=False,
        auth_idempotency_encryption_provider="aliyun_kms",
        auth_idempotency_kms_key_id=AUTH_KEY_ID,
        auth_idempotency_encryption_key_version=1,
        kms_endpoint="kms.cn-hangzhou.aliyuncs.com",
        kms_region="cn-hangzhou",
        kms_encrypted_data_key_registry_path=registry_path,
        material_request_writes_enabled=True,
        material_request_contact_encryption_provider="aliyun_kms",
        material_request_contact_kms_key_id=CONTACT_KEY_ID,
        material_request_contact_encryption_key_version=1,
    )


def test_loader_unwraps_only_exact_purpose_key_and_version(tmp_path) -> None:
    loader, client = _loader(tmp_path)

    assert loader(AUTHENTICATION_PURPOSE, AUTH_KEY_ID, 1) == AUTH_KEY
    assert loader(MATERIAL_REQUEST_CONTACT_PURPOSE, CONTACT_KEY_ID, 1) == CONTACT_KEY
    assert client.calls == [
        (AUTH_CIPHERTEXT, _context(AUTHENTICATION_PURPOSE, AUTH_KEY_ID, 1)),
        (
            CONTACT_CIPHERTEXT,
            _context(MATERIAL_REQUEST_CONTACT_PURPOSE, CONTACT_KEY_ID, 1),
        ),
    ]

    with pytest.raises(ProductionAdapterUnavailable):
        loader(AUTHENTICATION_PURPOSE, AUTH_KEY_ID, 2)
    with pytest.raises(ProductionAdapterUnavailable):
        loader(AUTHENTICATION_PURPOSE, CONTACT_KEY_ID, 1)


def test_readiness_probe_reuses_one_synchronized_default_credential_chain(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import alibabacloud_credentials.client as credential_module

    credential_instances: list[object] = []
    supplied_credentials: list[object] = []

    class _Credential:
        def __init__(self):
            credential_instances.append(self)

        def get_credential(self):
            return object()

        def get_type(self):
            return "default"

    monkeypatch.setattr(credential_module, "Client", _Credential)
    sdk_loggers = [
        logging.getLogger("credentials"),
        logging.getLogger("alibabacloud-tea"),
    ]
    original_states = [
        (
            logger.disabled,
            logger.propagate,
            list(logger.handlers),
        )
        for logger in sdk_loggers
    ]
    for logger in sdk_loggers:
        logger.disabled = False
        logger.propagate = True
        logger.handlers[:] = [logging.StreamHandler()]
    selected = _fake_client()
    loader = AliyunKmsEnvelopeKeyLoader(
        endpoint="kms.cn-hangzhou.aliyuncs.com",
        region="cn-hangzhou",
        registry_path=str(_write_registry(tmp_path)),
    )

    def client_factory(**arguments):
        supplied_credentials.append(arguments["credential"])
        return selected

    # Preserve the default-factory marker while replacing only the SDK client
    # construction seam; no network or real credential lookup occurs.
    loader._client_factory = client_factory

    try:
        assert loader.probe(AUTHENTICATION_PURPOSE, AUTH_KEY_ID, 1) == AUTH_KEY
        assert loader.probe(
            MATERIAL_REQUEST_CONTACT_PURPOSE,
            CONTACT_KEY_ID,
            1,
        ) == CONTACT_KEY

        assert len(credential_instances) == 1
        assert len(supplied_credentials) == 2
        assert supplied_credentials[0] is supplied_credentials[1]
        for logger in sdk_loggers:
            assert logger.disabled is True
            assert logger.propagate is False
            assert len(logger.handlers) == 1
            assert isinstance(logger.handlers[0], logging.NullHandler)
    finally:
        for logger, (disabled, propagate, handlers) in zip(
            sdk_loggers,
            original_states,
            strict=True,
        ):
            logger.disabled = disabled
            logger.propagate = propagate
            logger.handlers[:] = handlers


def test_sdk_wire_debug_cannot_emit_sensitive_headers(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DEBUG", "sdk")
    tea_logger = logging.getLogger("alibabacloud-tea")
    original_state = (
        tea_logger.disabled,
        tea_logger.propagate,
        tea_logger.level,
        list(tea_logger.handlers),
    )
    secret = "Bearer must-never-reach-stderr"
    try:
        tea_logger.disabled = False
        tea_logger.propagate = True
        tea_logger.setLevel(logging.DEBUG)
        tea_logger.handlers[:] = [logging.StreamHandler()]

        # The dependency may add a handler while importing Tea.core.  The
        # adapter repeats the boundary immediately after SDK imports and before
        # every KMS request.
        adapters._silence_aliyun_sdk_loggers()
        from Tea.core import TeaCore

        tea_logger.disabled = False
        tea_logger.handlers.append(logging.StreamHandler())
        adapters._silence_aliyun_sdk_loggers()
        TeaCore._do_http_debug(
            SimpleNamespace(
                url="https://kms.cn-hangzhou.aliyuncs.com/decrypt",
                method="POST",
                headers={
                    "Authorization": secret,
                    "x-acs-security-token": "temporary-token-must-not-leak",
                },
            ),
            SimpleNamespace(status_code=200, headers={"x-acs-request-id": "id"}),
        )

        captured = capfd.readouterr()
        assert captured.err == ""
        assert secret not in captured.out
        assert "temporary-token-must-not-leak" not in captured.out
        assert tea_logger.disabled is True
        assert tea_logger.propagate is False
        assert len(tea_logger.handlers) == 1
        assert isinstance(tea_logger.handlers[0], logging.NullHandler)
    finally:
        disabled, propagate, level, handlers = original_state
        tea_logger.disabled = disabled
        tea_logger.propagate = propagate
        tea_logger.setLevel(level)
        tea_logger.handlers[:] = handlers


def test_registry_rejects_cross_purpose_ciphertext_reuse_and_context_drift(
    tmp_path,
) -> None:
    duplicated = _registry_document()
    duplicated["entries"][1]["ciphertext_blob"] = AUTH_CIPHERTEXT
    duplicate_path = _write_registry(tmp_path, duplicated)
    with pytest.raises(
        ProductionAdapterConfigurationError,
        match="duplicate material",
    ):
        AliyunKmsEnvelopeKeyLoader(
            endpoint="kms.cn-hangzhou.aliyuncs.com",
            region="cn-hangzhou",
            registry_path=str(duplicate_path),
            client_factory=lambda **_: _fake_client(),
        )

    drifted = _registry_document()
    drifted["entries"][0]["encryption_context"]["purpose"] = (
        MATERIAL_REQUEST_CONTACT_PURPOSE
    )
    drifted_path = _write_registry(tmp_path, drifted)
    with pytest.raises(
        ProductionAdapterConfigurationError,
        match="encryption context",
    ):
        AliyunKmsEnvelopeKeyLoader(
            endpoint="kms.cn-hangzhou.aliyuncs.com",
            region="cn-hangzhou",
            registry_path=str(drifted_path),
            client_factory=lambda **_: _fake_client(),
        )


def test_registry_rejects_application_version_reuse_across_key_ids(
    tmp_path,
) -> None:
    ambiguous = _registry_document()
    ambiguous["entries"].append(
        _entry(
            purpose=MATERIAL_REQUEST_CONTACT_PURPOSE,
            key_id=RETIRED_CONTACT_KEY_ID,
            kms_version=RETIRED_CONTACT_KMS_VERSION,
            ciphertext=RETIRED_CONTACT_CIPHERTEXT,
            version=1,
        )
    )

    with pytest.raises(
        ProductionAdapterConfigurationError,
        match="duplicate material",
    ):
        AliyunKmsEnvelopeKeyLoader(
            endpoint="kms.cn-hangzhou.aliyuncs.com",
            region="cn-hangzhou",
            registry_path=str(_write_registry(tmp_path, ambiguous)),
            client_factory=lambda **_: _fake_client(),
        )


def test_registry_rejects_kms_key_reuse_across_current_or_historical_purposes(
    tmp_path,
) -> None:
    shared_key = _registry_document()
    shared_key["entries"][1]["kms_key_id"] = AUTH_KEY_ID
    shared_key["entries"][1]["encryption_context"] = _context(
        MATERIAL_REQUEST_CONTACT_PURPOSE,
        AUTH_KEY_ID,
        1,
    )

    with pytest.raises(
        ProductionAdapterConfigurationError,
        match="distinct KMS keys",
    ):
        AliyunKmsEnvelopeKeyLoader(
            endpoint="kms.cn-hangzhou.aliyuncs.com",
            region="cn-hangzhou",
            registry_path=str(_write_registry(tmp_path, shared_key)),
            client_factory=lambda **_: _fake_client(),
        )


@pytest.mark.parametrize("duplicate_scope", ["top", "entry", "context"])
def test_registry_rejects_duplicate_json_keys_at_every_level(
    tmp_path,
    duplicate_scope: str,
) -> None:
    raw = json.dumps(_registry_document())
    if duplicate_scope == "top":
        raw = raw.replace(
            '{"schema":',
            '{"schema": "wrong", "schema":',
            1,
        )
    elif duplicate_scope == "entry":
        raw = raw.replace(
            '"purpose": "authentication_idempotency"',
            '"purpose": "material_request_contact", '
            '"purpose": "authentication_idempotency"',
            1,
        )
    else:
        marker = '"purpose": "authentication_idempotency"'
        first = raw.index(marker)
        second = raw.index(marker, first + len(marker))
        raw = (
            raw[:second]
            + '"purpose": "material_request_contact", '
            + raw[second:]
        )
    path = tmp_path / "duplicate-keys.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(
        ProductionAdapterConfigurationError,
        match="unreadable",
    ):
        AliyunKmsEnvelopeKeyLoader(
            endpoint="kms.cn-hangzhou.aliyuncs.com",
            region="cn-hangzhou",
            registry_path=str(path),
            client_factory=lambda **_: _fake_client(),
        )


@pytest.mark.parametrize(
    "result",
    [
        KmsDecryptResult(
            plaintext_b64=base64.b64encode(AUTH_KEY[:-1]).decode("ascii"),
            key_id=AUTH_KEY_ID,
            key_version_id=AUTH_KMS_VERSION,
        ),
        KmsDecryptResult(
            plaintext_b64=base64.b64encode(AUTH_KEY).decode("ascii"),
            key_id=CONTACT_KEY_ID,
            key_version_id=AUTH_KMS_VERSION,
        ),
        KmsDecryptResult(
            plaintext_b64=base64.b64encode(AUTH_KEY).decode("ascii"),
            key_id=AUTH_KEY_ID,
            key_version_id=CONTACT_KMS_VERSION,
        ),
    ],
)
def test_loader_rejects_wrong_key_length_or_kms_identity(tmp_path, result) -> None:
    client = FakeKmsClient({AUTH_CIPHERTEXT: result})
    loader, _ = _loader(tmp_path, client)
    with pytest.raises(ProductionAdapterUnavailable):
        loader(AUTHENTICATION_PURPOSE, AUTH_KEY_ID, 1)


def test_composition_creates_separate_request_scoped_ciphers(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader, client = _loader(tmp_path)
    settings = _production_settings(
        str(tmp_path / "encrypted-data-keys.json")
    )
    monkeypatch.setattr(adapters, "get_configured_kms_loader", lambda _: loader)

    first = create_production_authentication_cipher(settings)
    second = create_production_authentication_cipher(settings)
    contact = create_production_material_request_contact_cipher(settings)
    assert first is not second and first is not contact
    assert first.active_key_version() == second.active_key_version() == 1
    assert contact.active_key_version() == 1
    assert [call[0] for call in client.calls] == [
        AUTH_CIPHERTEXT,
        AUTH_CIPHERTEXT,
        CONTACT_CIPHERTEXT,
    ]
    # Reuse is request-local: the same cipher does not unwrap twice.
    assert first.active_key_version() == 1
    assert len(client.calls) == 3


def test_startup_validation_is_network_free_but_requires_every_enabled_entry(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader, client = _loader(tmp_path)
    settings = _production_settings(
        str(tmp_path / "encrypted-data-keys.json")
    )
    monkeypatch.setattr(adapters, "get_configured_kms_loader", lambda _: loader)

    validate_production_adapter_installation(settings)
    assert client.calls == []

    missing_document = _registry_document()
    missing_document["entries"] = missing_document["entries"][:1]
    missing_path = _write_registry(tmp_path, missing_document)
    missing = AliyunKmsEnvelopeKeyLoader(
        endpoint="kms.cn-hangzhou.aliyuncs.com",
        region="cn-hangzhou",
        registry_path=str(missing_path),
        client_factory=lambda **_: client,
    )
    monkeypatch.setattr(adapters, "get_configured_kms_loader", lambda _: missing)
    with pytest.raises(
        ProductionAdapterConfigurationError,
        match="required KMS",
    ):
        validate_production_adapter_installation(settings)


def test_loader_configuration_rejects_noncanonical_endpoint_or_registry(tmp_path) -> None:
    path = _write_registry(tmp_path)
    for endpoint in (
        "http://kms.cn-hangzhou.aliyuncs.com",
        "kms.example.com",
        "kms..cn-hangzhou.aliyuncs.com",
    ):
        with pytest.raises(ProductionAdapterConfigurationError):
            AliyunKmsEnvelopeKeyLoader(
                endpoint=endpoint,
                region="cn-hangzhou",
                registry_path=str(path),
                client_factory=lambda **_: _fake_client(),
            )
    with pytest.raises(ProductionAdapterConfigurationError):
        AliyunKmsEnvelopeKeyLoader(
            endpoint="kms.cn-hangzhou.aliyuncs.com",
            region="cn-hangzhou",
            registry_path=str(tmp_path / "missing.json"),
            client_factory=lambda **_: _fake_client(),
        )

    path.chmod(0o666)
    with pytest.raises(
        ProductionAdapterConfigurationError,
        match="permissions",
    ):
        AliyunKmsEnvelopeKeyLoader(
            endpoint="kms.cn-hangzhou.aliyuncs.com",
            region="cn-hangzhou",
            registry_path=str(path),
            client_factory=lambda **_: _fake_client(),
        )


def test_persisted_key_gate_requires_all_live_auth_and_durable_contact_versions(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    stub_persisted_catalog,
) -> None:
    loader, client = _loader(tmp_path)
    settings = _production_settings(str(tmp_path / "encrypted-data-keys.json"))
    monkeypatch.setattr(adapters, "get_configured_kms_loader", lambda _: loader)
    session = FakePersistedReferenceSession(
        catalog=_persisted_catalog(loader),
        pin_rows=_pin_rows(loader),
    )

    now = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)
    required = validate_persisted_kms_key_references(session, settings, now=now)

    assert required == frozenset(
        {
            (AUTHENTICATION_PURPOSE, AUTH_KEY_ID, 1),
            (MATERIAL_REQUEST_CONTACT_PURPOSE, CONTACT_KEY_ID, 1),
        }
    )
    assert session.scan_calls == [("production", now)]
    assert session.no_autoflush_entries == session.execute_calls == 1
    assert client.calls == []

    missing_old_auth = FakePersistedReferenceSession(
        catalog=PersistedKeyReferenceCatalog(aliyun=(
            *session.catalog.aliyun,
            replace(session.catalog.aliyun[0], application_key_version=2),
        )),
        pin_rows=_pin_rows(loader),
    )
    with pytest.raises(
        ProductionAdapterConfigurationError,
        match="persisted KMS",
    ):
        validate_persisted_kms_key_references(missing_old_auth, settings)

    wrong_contact_key = FakePersistedReferenceSession(
        catalog=PersistedKeyReferenceCatalog(aliyun=tuple(
            replace(pin, kms_key_id="kms-retired-contact-key")
            if pin.purpose == MATERIAL_REQUEST_CONTACT_PURPOSE else pin
            for pin in session.catalog.aliyun
        )),
        pin_rows=_pin_rows(loader),
    )
    with pytest.raises(
        ProductionAdapterConfigurationError,
        match="persisted KMS",
    ):
        validate_persisted_kms_key_references(wrong_contact_key, settings)
    assert missing_old_auth.execute_calls == wrong_contact_key.execute_calls == 0
    assert client.calls == []


def test_persisted_key_gate_rejects_missing_or_rebound_database_pin(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    stub_persisted_catalog,
) -> None:
    loader, client = _loader(tmp_path)
    settings = _production_settings(str(tmp_path / "encrypted-data-keys.json"))
    monkeypatch.setattr(adapters, "get_configured_kms_loader", lambda _: loader)

    missing = FakePersistedReferenceSession(
        catalog=_persisted_catalog(loader),
        pin_rows=[],
    )
    with pytest.raises(
        ProductionAdapterConfigurationError,
        match="pins are incomplete",
    ):
        validate_persisted_kms_key_references(missing, settings)

    rebound_rows = _pin_rows(loader)
    rebound_rows[0] = (*rebound_rows[0][:-1], "0" * 64)
    rebound = FakePersistedReferenceSession(
        catalog=_persisted_catalog(loader),
        pin_rows=rebound_rows,
    )
    with pytest.raises(
        ProductionAdapterConfigurationError,
        match="does not match",
    ):
        validate_persisted_kms_key_references(rebound, settings)

    ambiguous_rows = _pin_rows(loader)
    ambiguous_rows.append(
        (
            AUTHENTICATION_PURPOSE,
            "kms-retired-auth-data-key",
            1,
            "12345678-retired-auth-version",
            "f" * 64,
        )
    )
    ambiguous = FakePersistedReferenceSession(
        catalog=_persisted_catalog(loader),
        pin_rows=ambiguous_rows,
    )
    with pytest.raises(
        ProductionAdapterConfigurationError,
        match="pin is invalid",
    ):
        validate_persisted_kms_key_references(ambiguous, settings)

    cross_purpose_rows = _pin_rows(loader)
    cross_purpose_rows[1] = (
        MATERIAL_REQUEST_CONTACT_PURPOSE,
        AUTH_KEY_ID,
        cross_purpose_rows[1][2],
        cross_purpose_rows[1][3],
        cross_purpose_rows[1][4],
    )
    cross_purpose = FakePersistedReferenceSession(
        catalog=_persisted_catalog(loader),
        pin_rows=cross_purpose_rows,
    )
    with pytest.raises(
        ProductionAdapterConfigurationError,
        match="pin is invalid",
    ):
        validate_persisted_kms_key_references(cross_purpose, settings)
    assert client.calls == []
    assert all(item.no_autoflush_entries == item.execute_calls == 1
               for item in (missing, rebound, ambiguous, cross_purpose))


def test_historical_contact_cmk_remains_exactly_decryptable_after_rotation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    stub_persisted_catalog,
) -> None:
    document = _registry_document()
    document["entries"].append(
        _entry(
            purpose=MATERIAL_REQUEST_CONTACT_PURPOSE,
            key_id=RETIRED_CONTACT_KEY_ID,
            kms_version=RETIRED_CONTACT_KMS_VERSION,
            ciphertext=RETIRED_CONTACT_CIPHERTEXT,
            version=RETIRED_CONTACT_APPLICATION_VERSION,
        )
    )
    client = _fake_client()
    client.results[RETIRED_CONTACT_CIPHERTEXT] = KmsDecryptResult(
        plaintext_b64=base64.b64encode(RETIRED_CONTACT_KEY).decode("ascii"),
        key_id=RETIRED_CONTACT_KEY_ID,
        key_version_id=RETIRED_CONTACT_KMS_VERSION,
    )
    loader = AliyunKmsEnvelopeKeyLoader(
        endpoint="kms.cn-hangzhou.aliyuncs.com",
        region="cn-hangzhou",
        registry_path=str(_write_registry(tmp_path, document)),
        client_factory=lambda **_: client,
    )
    settings = _production_settings(str(tmp_path / "encrypted-data-keys.json"))
    monkeypatch.setattr(adapters, "get_configured_kms_loader", lambda _: loader)
    session = FakePersistedReferenceSession(
        catalog=_persisted_catalog(loader, {
            (AUTHENTICATION_PURPOSE, AUTH_KEY_ID, 1),
            (MATERIAL_REQUEST_CONTACT_PURPOSE, RETIRED_CONTACT_KEY_ID,
             RETIRED_CONTACT_APPLICATION_VERSION),
        }),
        pin_rows=_pin_rows(loader),
    )

    required = validate_persisted_kms_key_references(session, settings)
    assert (
        MATERIAL_REQUEST_CONTACT_PURPOSE,
        RETIRED_CONTACT_KEY_ID,
        RETIRED_CONTACT_APPLICATION_VERSION,
    ) in required
    assert session.no_autoflush_entries == session.execute_calls == 1
    assert client.calls == []

    cipher = create_production_material_request_contact_cipher(settings)
    nonce = b"0123456789ab"
    aad = b"historical-contact-binding"
    plaintext = b'{"mobile":"13860013800","name":"engineer"}'
    ciphertext = AESGCM(RETIRED_CONTACT_KEY).encrypt(nonce, plaintext, aad)

    assert cipher.decrypt_for_kms_key_id(
        RETIRED_CONTACT_KEY_ID,
        ciphertext,
        nonce=nonce,
        aad=aad,
        key_version=RETIRED_CONTACT_APPLICATION_VERSION,
    ) == plaintext
    assert client.calls[-1][0] == RETIRED_CONTACT_CIPHERTEXT


@pytest.mark.parametrize("purpose", [AUTHENTICATION_PURPOSE, MATERIAL_REQUEST_CONTACT_PURPOSE])
@pytest.mark.parametrize("field,value", [
    ("kms_key_version_id", "12345678-other-reviewed-version"),
    ("ciphertext_sha256", "f" * 64),
])
def test_persisted_key_gate_compares_complete_independent_pins(
    tmp_path, monkeypatch, stub_persisted_catalog, purpose, field, value,
):
    loader, client = _loader(tmp_path)
    settings = _production_settings(str(tmp_path / "encrypted-data-keys.json"))
    monkeypatch.setattr(adapters, "get_configured_kms_loader", lambda _: loader)
    catalog = _persisted_catalog(loader)
    session = FakePersistedReferenceSession(
        catalog=PersistedKeyReferenceCatalog(aliyun=tuple(
            replace(pin, **{field: value}) if pin.purpose == purpose else pin
            for pin in catalog.aliyun
        )),
        pin_rows=_pin_rows(loader),
    )
    with pytest.raises(ProductionAdapterConfigurationError, match="persisted KMS"):
        validate_persisted_kms_key_references(session, settings)
    assert session.execute_calls == session.no_autoflush_entries == 0
    assert client.calls == []


@pytest.mark.parametrize("purpose", [AUTHENTICATION_PURPOSE, MATERIAL_REQUEST_CONTACT_PURPOSE])
def test_persisted_key_gate_never_enables_openbao_from_a_catalog(
    tmp_path, monkeypatch, stub_persisted_catalog, purpose,
):
    settings = _production_settings(str(tmp_path / "encrypted-data-keys.json"))
    session = FakePersistedReferenceSession(catalog=PersistedKeyReferenceCatalog(openbao=(
        OpenBaoReviewedPin(OpenBaoKeyCoordinate(purpose, "production", "pilot-bao", 2),
                           3, "1" * 64, "2" * 64, "3" * 64),
    )))
    monkeypatch.setattr(adapters, "get_configured_kms_loader",
                        lambda _: pytest.fail("unsupported provider must be rejected before any loader"))
    with pytest.raises(ProductionAdapterConfigurationError, match="persisted KMS"):
        validate_persisted_kms_key_references(session, settings)
    assert session.scan_calls == [("production", None)]
    assert session.execute_calls == session.no_autoflush_entries == 0


def test_persisted_key_gate_rejects_scanner_failure_without_driver_context(
    tmp_path, monkeypatch, stub_persisted_catalog,
):
    settings = _production_settings(str(tmp_path / "encrypted-data-keys.json"))
    session = FakePersistedReferenceSession(catalog=PersistedKeyReferenceUnavailable("private-driver-marker"))
    monkeypatch.setattr(adapters, "get_configured_kms_loader",
                        lambda _: pytest.fail("failed scan must not reach a loader"))
    with pytest.raises(ProductionAdapterConfigurationError, match="persisted KMS") as failure:
        validate_persisted_kms_key_references(session, settings)
    assert str(failure.value) == "persisted KMS key references are unavailable"
    assert failure.value.__cause__ is failure.value.__context__ is None
    assert session.execute_calls == 0


def test_persisted_key_gate_retains_active_coordinates_with_empty_history(
    tmp_path, monkeypatch, stub_persisted_catalog,
):
    loader, client = _loader(tmp_path)
    settings = _production_settings(str(tmp_path / "encrypted-data-keys.json"))
    monkeypatch.setattr(adapters, "get_configured_kms_loader", lambda _: loader)
    session = FakePersistedReferenceSession(catalog=PersistedKeyReferenceCatalog(), pin_rows=_pin_rows(loader))
    assert validate_persisted_kms_key_references(session, settings) == frozenset(loader.pin_manifest())
    assert session.no_autoflush_entries == session.execute_calls == 1
    assert client.calls == []


def test_persisted_key_gate_does_not_admit_a_historical_auth_cmk_without_request_routing(
    tmp_path, monkeypatch, stub_persisted_catalog,
):
    document = _registry_document()
    old_key_id = "kms-rsc-auth-retired"
    document["entries"].append(_entry(
        purpose=AUTHENTICATION_PURPOSE, key_id=old_key_id,
        kms_version="12345678-retired-auth-version", version=2,
        ciphertext=base64.b64encode(b"distinct-retired-auth-wrapped-key").decode("ascii"),
    ))
    client = _fake_client()
    loader = AliyunKmsEnvelopeKeyLoader(
        endpoint="kms.cn-hangzhou.aliyuncs.com", region="cn-hangzhou",
        registry_path=str(_write_registry(tmp_path, document)), client_factory=lambda **_: client,
    )
    settings = _production_settings(str(tmp_path / "encrypted-data-keys.json"))
    monkeypatch.setattr(adapters, "get_configured_kms_loader", lambda _: loader)
    session = FakePersistedReferenceSession(catalog=_persisted_catalog(loader), pin_rows=_pin_rows(loader))
    assert loader.has_entry(AUTHENTICATION_PURPOSE, old_key_id, 2)
    with pytest.raises(ProductionAdapterConfigurationError, match="persisted KMS"):
        validate_persisted_kms_key_references(session, settings)
    assert session.execute_calls == 0
    assert client.calls == []


@pytest.mark.parametrize("failure", ["missing", "rebound"])
def test_persisted_key_gate_checks_mounted_pins_even_when_not_referenced(
    tmp_path, monkeypatch, stub_persisted_catalog, failure,
):
    loader, client = _loader(tmp_path)
    settings = _production_settings(str(tmp_path / "encrypted-data-keys.json"))
    settings.material_request_writes_enabled = False
    monkeypatch.setattr(adapters, "get_configured_kms_loader", lambda _: loader)
    rows = _pin_rows(loader)
    if failure == "missing":
        rows = [row for row in rows if row[0] != MATERIAL_REQUEST_CONTACT_PURPOSE]
    else:
        rows = [(*row[:-1], "e" * 64) if row[0] == MATERIAL_REQUEST_CONTACT_PURPOSE else row for row in rows]
    session = FakePersistedReferenceSession(catalog=PersistedKeyReferenceCatalog(), pin_rows=rows)
    with pytest.raises(ProductionAdapterConfigurationError, match="pins are incomplete|does not match"):
        validate_persisted_kms_key_references(session, settings)
    assert session.no_autoflush_entries == session.execute_calls == 1
    assert client.calls == []


@pytest.mark.parametrize("scenario", ["valid", "missing_claim", "claim_drift", "openbao_history"])
def test_persisted_key_gate_real_scanner_registry_and_pin_integration(
    tmp_path, monkeypatch, scenario,
):
    """Real SQLite SELECTs join the scanner to the deployed Aliyun boundary.

    This is metadata/registry integration, not PG16 or online decrypt proof.
    """
    loader, client = _loader(tmp_path)
    settings = _production_settings(str(tmp_path / "encrypted-data-keys.json"))
    settings.material_request_writes_enabled = False
    monkeypatch.setattr(adapters, "get_configured_kms_loader", lambda _: loader)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    now = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)
    try:
        for model in (KmsDataKeyPin, OpenBaoDataKeyPin, ApplicationKeyVersionClaim):
            model.__table__.create(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE auth_idempotency_operations (status TEXT, expires_at DATETIME, encryption_key_version INTEGER)")
            for name in ("material_requests", "material_request_revisions"):
                connection.exec_driver_sql(f"CREATE TABLE {name} (contact_snapshot_jsonb JSON)")
            for purpose, key_id, version, kms_version, digest in _pin_rows(loader):
                common = {"purpose": purpose, "application_key_version": version,
                          "ciphertext_sha256": digest, "created_at": now}
                connection.execute(KmsDataKeyPin.__table__.insert(), common | {
                    "kms_key_id": key_id, "kms_key_version_id": kms_version,
                })
                if scenario == "missing_claim" and purpose == MATERIAL_REQUEST_CONTACT_PURPOSE:
                    continue
                connection.execute(ApplicationKeyVersionClaim.__table__.insert(), common | {
                    "provider": "aliyun_kms",
                    "ciphertext_sha256": "f" * 64 if scenario == "claim_drift" and purpose == MATERIAL_REQUEST_CONTACT_PURPOSE else digest,
                })
            connection.execute(text(
                "INSERT INTO auth_idempotency_operations VALUES ('completed', :expires, 1)"
            ).bindparams(bindparam("expires", type_=DateTime(timezone=True))), {
                "expires": datetime(2026, 10, 10, 0, 0, tzinfo=timezone.utc),
            })
            contact = {"schema": "rsc.material_request_contact.v1", "provider": "aliyun_kms",
                       "kms_key_id": CONTACT_KEY_ID, "key_version": 1}
            if scenario == "openbao_history":
                contact = {"schema": "rsc.material_request_contact.v2", "provider": "openbao_transit_v1",
                           "purpose": MATERIAL_REQUEST_CONTACT_PURPOSE, "environment": "production",
                           "provider_instance_id": "pilot-bao", "key_path": "transit/keys/rsc-material-request-contact",
                           "application_key_version": 2, "transit_key_version": 1}
            # Only historical contact remains, and writes are disabled: the
            # corresponding key is still required by the real scan.
            connection.execute(text("INSERT INTO material_request_revisions VALUES (:contact)"),
                               {"contact": json.dumps(contact)})
        queries = []
        event.listen(engine, "before_cursor_execute", lambda conn, cursor, statement, params, context, many: queries.append(statement))
        with Session(engine) as db:
            monkeypatch.setattr(db, "flush", lambda *args, **kwargs: pytest.fail("reference gate must not flush"))
            if scenario == "valid":
                assert validate_persisted_kms_key_references(db, settings, now=now) == frozenset(loader.pin_manifest())
            else:
                with pytest.raises(ProductionAdapterConfigurationError, match="persisted KMS") as failure:
                    validate_persisted_kms_key_references(db, settings, now=now)
                assert failure.value.__cause__ is failure.value.__context__ is None
        assert queries and all(query.lstrip().upper().startswith("SELECT") for query in queries)
        assert client.calls == []
    finally:
        engine.dispose()
