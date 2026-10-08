"""New pin-reader evidence only; no hosted DB/ACL or production claims.

Recording results cover malformed/unknown responses; a synthetic SQLite
catalog executes the actual SELECT to exercise LEFT JOIN semantics. It does
not stand in for the separate PostgreSQL16 role/catalog gate.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql

from app.openbao_pin_reader_candidate import (
    OpenBaoPinReaderCandidateError,
    read_openbao_reviewed_pins_candidate,
)
from app.openbao_registry_candidate import (
    OpenBaoRegistryCandidateError,
    REGISTRY_SCHEMA,
    load_openbao_registry_candidate,
)
from app.openbao_transit_candidate import (
    PROVIDER,
    OpenBaoDecryptResponse,
    OpenBaoKeyCoordinate,
    OpenBaoReviewedPin,
    associated_data_b64,
    context_b64,
)


AUTH = "authentication_idempotency"
CONTACT = "material_request_contact"
ENVIRONMENT = "test"
INSTANCE = "isolated-openbao-2-7-1"
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
SAFE_ERROR = "OpenBao candidate pins are unavailable"
# Opaque synthetic diagnostic marker, deliberately not a credential or URL.
SENSITIVE = "synthetic-driver-diagnostic-must-not-escape"


def coordinate(purpose=AUTH, version=1):
    return OpenBaoKeyCoordinate(purpose, ENVIRONMENT, INSTANCE, version)


def ciphertext(item):
    payload = hashlib.sha256(f"{item.purpose}:{item.application_key_version}".encode()).digest()
    return "vault:v2:" + base64.b64encode((payload * 2)[:60]).decode()


def row(item=None):
    item = item or coordinate()
    digest = hashlib.sha256(ciphertext(item).encode("ascii")).hexdigest()
    return dict(requested_purpose=item.purpose,
        requested_application_key_version=item.application_key_version,
        purpose=item.purpose, environment=item.environment,
        provider_instance_id=item.provider_instance_id, key_path=item.key_path,
        application_key_version=item.application_key_version, transit_key_version=2,
        ciphertext_sha256=digest,
        context_sha256=hashlib.sha256(base64.b64decode(context_b64(item))).hexdigest(),
        associated_data_sha256=hashlib.sha256(base64.b64decode(associated_data_b64(item))).hexdigest(),
        created_at=NOW, claim_purpose=item.purpose,
        claim_application_key_version=item.application_key_version,
        claim_provider=PROVIDER, claim_ciphertext_sha256=digest, claim_created_at=NOW,
        legacy_purpose=None, legacy_application_key_version=None)


class Result:
    def __init__(self, rows, fault=None):
        self.rows = deepcopy(rows)
        self.fault = fault
        self.fetch_sizes = []
        self.closed = False

    def mappings(self):
        if self.fault == "mappings":
            raise RuntimeError(SENSITIVE)
        return self

    def fetchmany(self, size):
        self.fetch_sizes.append(size)
        if self.fault == "fetch":
            raise RuntimeError(SENSITIVE)
        return self.rows[:size]

    def close(self):
        self.closed = True
        if self.fault == "close":
            raise RuntimeError(SENSITIVE)


class Connection:
    def __init__(self, rows=None, fault=None):
        self.result = Result(rows if rows is not None else [row()], fault)
        self.fault = fault
        self.calls = []

    def execute(self, statement, parameters):
        self.calls.append((statement, deepcopy(parameters)))
        if self.fault == "execute":
            raise RuntimeError(SENSITIVE)
        return self.result


def read(db, expected=None, **changes):
    arguments = dict(environment=ENVIRONMENT, provider_instance_id=INSTANCE,
                     expected_coordinates=(coordinate(),) if expected is None else expected)
    arguments.update(changes)
    return read_openbao_reviewed_pins_candidate(db, **arguments)


def assert_safe_error(call):
    with pytest.raises(OpenBaoPinReaderCandidateError) as raised:
        call()
    assert str(raised.value) == SAFE_ERROR
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert SENSITIVE not in repr(raised.value)


def test_one_parameterized_bounded_select_preserves_explicit_historical_order():
    expected = (coordinate(CONTACT, 9), coordinate(AUTH, 1), coordinate(CONTACT, 1), coordinate(AUTH, 7))
    db = Connection([row(item) for item in reversed(expected)])
    pins = read(db, expected)
    assert all(type(pin) is OpenBaoReviewedPin for pin in pins)
    assert tuple(pin.coordinate for pin in pins) == expected
    assert [pin.ciphertext_sha256 for pin in pins] == [row(item)["ciphertext_sha256"] for item in expected]
    assert [pin.context_sha256 for pin in pins] == [row(item)["context_sha256"] for item in expected]
    assert [pin.associated_data_sha256 for pin in pins] == [row(item)["associated_data_sha256"] for item in expected]
    assert all(pin.transit_key_version == 2 for pin in pins)
    assert len(db.calls) == 1
    statement, parameters = db.calls[0]
    sql = str(statement)
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert sql.count("LEFT JOIN public.") == 3
    assert "WITH requested(purpose, application_key_version) AS (VALUES" in sql
    assert "LIMIT :row_limit" in sql
    assert "LIMIT %(row_limit)s" in compiled
    assert "WHERE" not in sql and "DISTINCT" not in sql and "INNER JOIN" not in sql
    assert not any(word in sql for word in ("INSERT", "UPDATE", "DELETE", "FOR SHARE", "FOR UPDATE"))
    assert AUTH not in sql and CONTACT not in sql and INSTANCE not in sql
    assert set(statement._bindparams) == set(parameters)
    assert parameters == {"row_limit": 5, **{
        name: value for index, item in enumerate(expected)
        for name, value in ((f"purpose_{index}", item.purpose), (f"version_{index}", item.application_key_version))
    }}
    assert db.result.fetch_sizes == [5]
    assert db.result.closed


@pytest.mark.parametrize("changes", [
    {"expected_coordinates": ()}, {"expected_coordinates": []},
    {"expected_coordinates": [coordinate()]}, {"expected_coordinates": None},
    {"expected_coordinates": {coordinate()}}, {"expected_coordinates": "latest"},
    {"expected_coordinates": (None,)}, {"expected_coordinates": ({"purpose": AUTH},)},
    {"expected_coordinates": (coordinate(), coordinate())},
    {"expected_coordinates": tuple(coordinate(version=value) for value in range(1, 66))},
    {"expected_coordinates": (replace(coordinate(), environment="production"),)},
    {"expected_coordinates": (replace(coordinate(), provider_instance_id="another-instance"),)},
    {"environment": "unknown"}, {"environment": None}, {"environment": True},
    {"provider_instance_id": ""}, {"provider_instance_id": "../other"},
    {"provider_instance_id": None},
])
def test_invalid_independent_scope_rejected_before_any_sql(changes):
    db = Connection()
    assert_safe_error(lambda: read(db, **changes))
    assert db.calls == []


def test_mutated_coordinate_is_revalidated_before_any_sql():
    item = coordinate()
    object.__setattr__(item, "application_key_version", True)
    db = Connection()
    assert_safe_error(lambda: read(db, (item,)))
    assert db.calls == []


def test_maximum_64_coordinates_reads_at_most_65_rows():
    expected = tuple(coordinate(version=value) for value in range(1, 65))
    db = Connection([row(item) for item in expected])
    assert tuple(pin.coordinate for pin in read(db, expected)) == expected
    assert db.result.fetch_sizes == [65]
    assert db.calls[0][1]["row_limit"] == 65


@pytest.mark.parametrize("changes", [
    {"requested_purpose": CONTACT}, {"requested_purpose": None},
    {"requested_application_key_version": True}, {"requested_application_key_version": "1"},
    {"requested_application_key_version": 2},
    {"purpose": CONTACT}, {"purpose": None}, {"purpose": "other"},
    {"environment": "production"}, {"provider_instance_id": "another-instance"},
    {"key_path": "transit/keys/rsc-material-request-contact"}, {"key_path": None},
    {"key_path": "transit/keys/rsc-authentication-idempotency/"},
    {"application_key_version": 2}, {"application_key_version": True},
    {"transit_key_version": True}, {"transit_key_version": 0}, {"transit_key_version": 2147483648},
    {"ciphertext_sha256": "A" * 64}, {"context_sha256": "a" * 63},
    {"associated_data_sha256": None},
    {"claim_purpose": CONTACT}, {"claim_purpose": None},
    {"claim_application_key_version": 2}, {"claim_application_key_version": True},
    {"claim_provider": "aliyun_kms"}, {"claim_provider": None},
    {"claim_ciphertext_sha256": "a" * 64}, {"claim_ciphertext_sha256": None},
    {"created_at": None}, {"created_at": NOW.replace(tzinfo=None)},
    {"created_at": NOW.isoformat()}, {"claim_created_at": None},
    {"claim_created_at": NOW.replace(tzinfo=None)},
    {"claim_created_at": NOW + timedelta(microseconds=1)},
    {"legacy_purpose": AUTH}, {"legacy_application_key_version": 1},
])
def test_invalid_pin_claim_or_legacy_binding_rejects_entire_read(changes):
    bad = row()
    bad.update(changes)
    db = Connection([bad])
    assert_safe_error(lambda: read(db))
    assert len(db.calls) == 1 and db.result.closed


@pytest.mark.parametrize("mutation", ["missing-row", "missing-field", "extra-field", "duplicate", "unrequested", "wrong-row-type"])
def test_missing_ambiguous_or_unrequested_observation_never_partially_succeeds(mutation):
    rows = [row()]
    if mutation == "missing-row":
        rows = []
    elif mutation == "missing-field":
        rows[0].pop("key_path")
    elif mutation == "extra-field":
        rows[0]["unreviewed"] = SENSITIVE
    elif mutation == "duplicate":
        rows *= 2
    elif mutation == "unrequested":
        rows = [row(coordinate(CONTACT, 2))]
    else:
        rows = [None]
    db = Connection(rows)
    assert_safe_error(lambda: read(db))
    assert db.result.closed


def test_same_count_duplicate_or_duplicate_ciphertext_rejects_all_pins():
    expected = (coordinate(AUTH, 1), coordinate(CONTACT, 1))
    db = Connection([row(expected[0]), row(expected[0])])
    assert_safe_error(lambda: read(db, expected))
    rows = [row(item) for item in expected]
    rows[1]["ciphertext_sha256"] = rows[1]["claim_ciphertext_sha256"] = rows[0]["ciphertext_sha256"]
    assert_safe_error(lambda: read(Connection(rows), expected))


@pytest.mark.parametrize("fault", ["execute", "mappings", "fetch", "close"])
def test_driver_errors_are_fixed_unknown_without_retry_or_exception_chain(fault):
    db = Connection(fault=fault)
    assert_safe_error(lambda: read(db))
    assert len(db.calls) == 1
    assert db.result.closed == (fault != "execute")


def test_same_timestamp_in_another_timezone_is_the_same_database_instant():
    observed = row()
    observed["claim_created_at"] = NOW.astimezone(timezone(timedelta(hours=8)))
    assert read(Connection([observed]))[0].coordinate == coordinate()


class SQLiteResult:
    def __init__(self, result):
        self.result = result

    def mappings(self):
        return self

    def fetchmany(self, size):
        rows = [dict(value) for value in self.result.mappings().fetchmany(size)]
        # Synthetic SQLite stores ISO timestamps as text. PostgreSQL psycopg
        # returns aware datetime directly; this adapter tests SQL joins only.
        for value in rows:
            for name in ("created_at", "claim_created_at"):
                if value[name] is not None:
                    value[name] = datetime.fromisoformat(value[name])
        return rows

    def close(self):
        self.result.close()


class SQLiteConnection:
    def __init__(self, db):
        self.db = db
        self.calls = []

    def execute(self, statement, parameters):
        self.calls.append((statement, parameters))
        return SQLiteResult(self.db.execute(statement, parameters))


@pytest.fixture
def catalog():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as db:
        db.exec_driver_sql("ATTACH DATABASE ':memory:' AS public")
        db.exec_driver_sql("CREATE TABLE public.openbao_data_key_pins (purpose TEXT, environment TEXT, provider_instance_id TEXT, key_path TEXT, application_key_version INTEGER, transit_key_version INTEGER, ciphertext_sha256 TEXT, context_sha256 TEXT, associated_data_sha256 TEXT, created_at TEXT)")
        db.exec_driver_sql("CREATE TABLE public.application_key_version_claims (purpose TEXT, application_key_version INTEGER, provider TEXT, ciphertext_sha256 TEXT, created_at TEXT)")
        db.exec_driver_sql("CREATE TABLE public.kms_data_key_pins (purpose TEXT, application_key_version INTEGER)")
        yield db
    engine.dispose()


def insert_catalog(db, item, *, pin=True, claim=True):
    observed = row(item)
    if pin:
        names = ("purpose", "environment", "provider_instance_id", "key_path", "application_key_version", "transit_key_version", "ciphertext_sha256", "context_sha256", "associated_data_sha256", "created_at")
        values = {name: observed[name] for name in names}
        values["created_at"] = NOW.isoformat()
        db.execute(text("INSERT INTO public.openbao_data_key_pins (" + ",".join(names) + ") VALUES (" + ",".join(":" + name for name in names) + ")"), values)
    if claim:
        db.execute(text("INSERT INTO public.application_key_version_claims VALUES (:purpose,:version,:provider,:digest,:created)"),
                   dict(purpose=item.purpose, version=item.application_key_version, provider=PROVIDER,
                        digest=observed["ciphertext_sha256"], created=NOW.isoformat()))


def test_actual_sql_reads_exact_history_and_ignores_unrequested_versions(catalog):
    expected = (coordinate(CONTACT, 2), coordinate(AUTH, 1))
    for item in (*reversed(expected), coordinate(AUTH, 9)):
        insert_catalog(catalog, item)
    db = SQLiteConnection(catalog)
    assert tuple(pin.coordinate for pin in read(db, expected)) == expected
    assert len(db.calls) == 1


@pytest.mark.parametrize("case", ["missing-pin", "missing-claim", "wrong-provider", "wrong-hash", "wrong-key-path", "legacy-conflict", "duplicate-pin", "duplicate-claim"])
def test_actual_left_joins_preserve_missing_and_conflicting_rows(catalog, case):
    item = coordinate()
    insert_catalog(catalog, item, pin=case != "missing-pin", claim=case != "missing-claim")
    if case == "wrong-provider":
        catalog.exec_driver_sql("UPDATE public.application_key_version_claims SET provider='aliyun_kms'")
    elif case == "wrong-hash":
        catalog.execute(text("UPDATE public.application_key_version_claims SET ciphertext_sha256=:hash"), {"hash": "e" * 64})
    elif case == "wrong-key-path":
        catalog.exec_driver_sql("UPDATE public.openbao_data_key_pins SET key_path='transit/keys/rsc-material-request-contact'")
    elif case == "legacy-conflict":
        catalog.execute(text("INSERT INTO public.kms_data_key_pins VALUES (:purpose,1)"), {"purpose": AUTH})
    elif case == "duplicate-pin":
        insert_catalog(catalog, item, claim=False)
    elif case == "duplicate-claim":
        insert_catalog(catalog, item, pin=False)
    db = SQLiteConnection(catalog)
    assert_safe_error(lambda: read(db))
    assert len(db.calls) == 1


class Transport:
    def __init__(self):
        self.requests = []

    def decrypt(self, *, request, timeout_seconds):
        self.requests.append((request, timeout_seconds))
        return OpenBaoDecryptResponse(200, {"data": {"plaintext": base64.b64encode(b"k" * 32).decode()}})


def registry(tmp_path, expected):
    directory = tmp_path.resolve() / "private-registry"
    directory.mkdir(mode=0o700)
    path = directory / "wrapped-keys.json"
    entries = [dict(purpose=item.purpose, environment=item.environment,
                    provider_instance_id=item.provider_instance_id,
                    application_key_version=item.application_key_version,
                    key_path=item.key_path, transit_key_version=2, ciphertext=ciphertext(item),
                    context_b64=context_b64(item), associated_data_b64=associated_data_b64(item))
               for item in expected]
    path.write_text(json.dumps(dict(schema=REGISTRY_SCHEMA, provider=PROVIDER, entries=entries)))
    os.chmod(path, 0o600)
    return path


def test_db_pins_feed_existing_registry_and_resolve_both_purposes_exact_versions(tmp_path):
    expected = tuple(coordinate(purpose, version) for purpose in (AUTH, CONTACT) for version in (1, 2))
    pins = read(Connection([row(item) for item in expected]), expected)
    transport = Transport()
    candidate = load_openbao_registry_candidate(registry_path=registry(tmp_path, expected),
        environment=ENVIRONMENT, provider_instance_id=INSTANCE, reviewed_pins=pins, transport=transport)
    assert transport.requests == []
    for item in expected:
        assert candidate.resolve(item.purpose, item.application_key_version) == b"k" * 32
    assert len(transport.requests) == 4
    assert all(timeout == 3 for _, timeout in transport.requests)


@pytest.mark.parametrize("field", ["ciphertext_sha256", "context_sha256", "associated_data_sha256"])
def test_db_hashes_are_not_regenerated_from_registry_to_make_pins_match(tmp_path, field):
    observed = row()
    observed[field] = "f" * 64
    if field == "ciphertext_sha256":
        observed["claim_ciphertext_sha256"] = observed[field]
    pins = read(Connection([observed]))
    assert getattr(pins[0], field) == "f" * 64
    transport = Transport()
    with pytest.raises(OpenBaoRegistryCandidateError):
        load_openbao_registry_candidate(registry_path=registry(tmp_path, (coordinate(),)),
            environment=ENVIRONMENT, provider_instance_id=INSTANCE, reviewed_pins=pins, transport=transport)
    assert transport.requests == []
