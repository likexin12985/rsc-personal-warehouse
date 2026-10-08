"""Run-once pin-reader gate AFTER the existing seven-claim PG16 binding gate.

The caller supplies already verified disposable-cluster engines with finite
connect timeouts; there is no DSN, credential discovery, bootstrap or retry.
Only two new synthetic coordinates are inserted. Every attempted writer is
followed by an exact fresh-connection readback, including an uncertain commit.
An unknown/different result stops the gate without replaying the writer.

Registry and decrypt results here are synthetic/mock evidence, not a real
Transit call, reviewed key provenance or permission to activate a provider.
"""
from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from sqlalchemy import text

from app.openbao_pin_reader_candidate import (
    OpenBaoPinReaderCandidateError,
    read_openbao_reviewed_pins_candidate,
)
from app.openbao_registry_candidate import REGISTRY_SCHEMA, load_openbao_registry_candidate
from app.openbao_transit_candidate import (
    PROVIDER,
    OpenBaoDecryptResponse,
    OpenBaoKeyCoordinate,
    associated_data_b64,
    context_b64,
)


OWNER = "star_oam_migrator"
API = "star_oam_api"
NONREADERS = ("star_oam_projector", "edge_inbox")
TABLES = ("kms_data_key_pins", "openbao_data_key_pins", "application_key_version_claims")
ENVIRONMENT = "test"
INSTANCE = "isolated-openbao-2-7-1"
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
COORDINATES = (
    OpenBaoKeyCoordinate("authentication_idempotency", ENVIRONMENT, INSTANCE, 1_801_001),
    OpenBaoKeyCoordinate("material_request_contact", ENVIRONMENT, INSTANCE, 1_801_002),
)


class OpenBaoPinReaderGateError(RuntimeError):
    """Fixed stage code only; never raw SQL, driver diagnostics or material."""


def _require(condition, stage):
    if not condition:
        raise OpenBaoPinReaderGateError(stage)


def _limits(db):
    db.execute(text("SET LOCAL statement_timeout='10s'"))
    db.execute(text("SET LOCAL lock_timeout='3s'"))
    db.execute(text("SET LOCAL idle_in_transaction_session_timeout='30s'"))


def _ciphertext(coordinate):
    label = "rsc.synthetic.pin-reader." + coordinate.purpose + "." + str(coordinate.application_key_version)
    payload = hashlib.sha256(label.encode("ascii")).digest()
    # Structurally valid nonce + 32-byte DEK + tag, not real Transit encryption.
    return "vault:v2:" + base64.b64encode((payload * 2)[:60]).decode("ascii")


def _pin(coordinate):
    return dict(purpose=coordinate.purpose, environment=coordinate.environment,
        provider_instance_id=coordinate.provider_instance_id, key_path=coordinate.key_path,
        application_key_version=coordinate.application_key_version, transit_key_version=2,
        ciphertext_sha256=hashlib.sha256(_ciphertext(coordinate).encode("ascii")).hexdigest(),
        context_sha256=hashlib.sha256(base64.b64decode(context_b64(coordinate))).hexdigest(),
        associated_data_sha256=hashlib.sha256(base64.b64decode(associated_data_b64(coordinate))).hexdigest(),
        created_at=NOW)


def _snapshot(engine):
    with engine.connect() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        _limits(db)
        result = {table: [dict(row) for row in db.execute(text(
            "SELECT * FROM public." + table + " ORDER BY purpose,application_key_version LIMIT 33"
        )).mappings()] for table in TABLES}
        _require(all(len(rows) <= 32 for rows in result.values()), "fixture_size")
        return result


def _preflight(engines):
    roles = (OWNER, API, *NONREADERS)
    _require(all(role in engines for role in roles), "required_engines")
    for role in roles:
        with engines[role].connect() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            _limits(db)
            observed = db.execute(text("SELECT current_database(),current_user,session_user,"
                "current_setting('server_version_num')::int,current_setting('listen_addresses'),"
                "current_setting('transaction_isolation')")).one()
            _require(tuple(observed[:3]) == ("rsc_pg16_release_gate", role, role)
                and 160000 <= observed[3] < 170000 and tuple(observed[4:]) == ("", "read committed"),
                "direct_isolated_identity")
            flags = db.execute(text("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
                "FROM pg_catalog.pg_roles WHERE rolname=current_user")).one()
            _require(not any(flags), "unprivileged_identity")
    with engines[OWNER].connect() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        _limits(db)
        _require(db.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all()
                 == ["20261229_0180"], "current_head")
    before = _snapshot(engines[OWNER])
    _require(len(before[TABLES[2]]) == 7, "run_after_seven_claim_gate")
    wanted = {(item.purpose, item.application_key_version) for item in COORDINATES}
    _require(all((row["purpose"], row["application_key_version"]) not in wanted
                 for rows in before.values() for row in rows), "new_coordinates_must_be_absent")
    legacy = [row for row in before[TABLES[0]]
              if row["purpose"] == COORDINATES[0].purpose and row["application_key_version"] == 1]
    _require(legacy == [dict(purpose=COORDINATES[0].purpose, application_key_version=1,
        kms_key_id="synthetic-legacy-key", kms_key_version_id="synthetic-legacy-version",
        ciphertext_sha256="a" * 64, created_at=NOW)], "exact_legacy_fixture")
    return before


def _seed_once_and_read_back(owner, before):
    values = [_pin(item) for item in COORDINATES]
    names = tuple(values[0])
    statement = text("INSERT INTO public.openbao_data_key_pins (" + ",".join(names) + ") VALUES "
        + ",".join("(" + ",".join(f":v{index}_{name}" for name in names) + ")"
                   for index in range(len(values))))
    parameters = {f"v{index}_{name}": item[name]
                  for index, item in enumerate(values) for name in names}
    failed = interrupted = False
    try:
        with owner.begin() as db:
            _limits(db)
            db.execute(statement, parameters)  # exactly one write attempt
    except (KeyboardInterrupt, SystemExit):
        failed = interrupted = True
    except Exception:
        failed = True
    # Never infer commit/rollback from a transport response. No retry happens.
    observed = None
    try:
        observed = _snapshot(owner)
    except BaseException:
        pass
    _require(observed is not None, "seed_readback_unknown_no_retry")
    expected = {table: list(rows) for table, rows in before.items()}
    expected[TABLES[1]].extend(values)
    expected[TABLES[2]].extend(dict(purpose=item["purpose"],
        application_key_version=item["application_key_version"], provider=PROVIDER,
        ciphertext_sha256=item["ciphertext_sha256"], created_at=item["created_at"])
        for item in values)
    for rows in expected.values():
        rows.sort(key=lambda row: (row["purpose"], row["application_key_version"]))
    _require(observed == expected, "seed_readback_not_exact_no_retry")
    _require(not interrupted, "seed_interrupted_exact_readback_stop")
    return observed, failed


def _read(engine, coordinates, *, instance=INSTANCE):
    with engine.connect() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        _limits(db)
        return read_openbao_reviewed_pins_candidate(db, environment=ENVIRONMENT,
            provider_instance_id=instance, expected_coordinates=coordinates)


def _denied(engine, coordinates, *, instance=INSTANCE):
    denied = False
    try:
        _read(engine, coordinates, instance=instance)
    except OpenBaoPinReaderCandidateError as error:
        denied = (str(error) == "OpenBao candidate pins are unavailable"
                  and error.__context__ is None and error.__cause__ is None)
    _require(denied, "reader_negative_must_fail_closed")


class _SyntheticTransport:
    def __init__(self):
        self.requests = []

    def decrypt(self, *, request, timeout_seconds):
        matches = [item for item in COORDINATES if _ciphertext(item) == request.ciphertext]
        _require(len(matches) == 1, "mock_ciphertext")
        coordinate = matches[0]
        _require(request.path == coordinate.decrypt_path and request.context == context_b64(coordinate)
            and request.associated_data == associated_data_b64(coordinate) and timeout_seconds == 3,
            "mock_request_binding")
        self.requests.append(coordinate)
        plaintext = hashlib.sha256(("synthetic-dek." + coordinate.purpose).encode("ascii")).digest()
        return OpenBaoDecryptResponse(200, {"data": {"plaintext": base64.b64encode(plaintext).decode("ascii")}})


def _registry_combination(pins):
    entries = [dict(purpose=item.purpose, environment=item.environment,
        provider_instance_id=item.provider_instance_id, application_key_version=item.application_key_version,
        key_path=item.key_path, transit_key_version=2, ciphertext=_ciphertext(item),
        context_b64=context_b64(item), associated_data_b64=associated_data_b64(item)) for item in COORDINATES]
    transport = _SyntheticTransport()
    with tempfile.TemporaryDirectory(prefix="rsc-openbao-pin-reader-") as temporary:
        # This is our own trusted temporary directory, not a caller-supplied
        # path whose symlinks the strict registry reader must reject.
        directory = Path(temporary).resolve(strict=True)
        os.chmod(directory, 0o700)
        path = directory / "synthetic-wrapped-keys.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(dict(schema=REGISTRY_SCHEMA, provider=PROVIDER, entries=entries), stream)
        candidate = load_openbao_registry_candidate(registry_path=path,
            environment=ENVIRONMENT, provider_instance_id=INSTANCE, reviewed_pins=pins, transport=transport)
        _require(transport.requests == [], "registry_load_no_decrypt")
        for item in COORDINATES:
            expected = hashlib.sha256(("synthetic-dek." + item.purpose).encode("ascii")).digest()
            _require(candidate.resolve(item.purpose, item.application_key_version) == expected,
                     "mock_dek_result")
        _require(tuple(transport.requests) == COORDINATES, "exact_mock_decrypt_count")


def run(engines):
    """Run once after pg16_key_provider_binding_gate; caller owns isolation."""
    stage = "preflight"
    report = None
    failure = None
    try:
        before = _preflight(engines)
        stage = "seed"
        seeded, write_error_reread = _seed_once_and_read_back(engines[OWNER], before)
        stage = "api_read"
        pins = _read(engines[API], COORDINATES)
        _require(tuple(pin.coordinate for pin in pins) == COORDINATES, "exact_pin_coordinates")
        stage = "registry_combination"
        _registry_combination(pins)
        stage = "negative_reads"
        other = "another-isolated-instance"
        _denied(engines[API], tuple(replace(item, provider_instance_id=other) for item in COORDINATES), instance=other)
        _denied(engines[API], (replace(COORDINATES[0], application_key_version=1_801_003),))
        _denied(engines[API], (replace(COORDINATES[0], application_key_version=1),))
        for role in NONREADERS:
            _denied(engines[role], COORDINATES)
        stage = "final_readback"
        _require(_snapshot(engines[OWNER]) == seeded, "reads_changed_binding_facts")
        report = dict(schema="rsc.openbao.pin-reader.pg16-candidate.v1", result="passed",
            syntheticOnly=True, productionReady=False, realTransitEvidence=False,
            mockTransportOnly=True, reviewProvenanceProven=False, existingClaimsPreserved=7,
            newPins=2, newClaims=2, finalClaims=9, singleSeedWriteAttempt=True,
            seedExactReadback=True, seedReadbackAfterWriteError=write_error_reread,
            apiPinsRead=2, mockDecrypts=2, wrongInstanceDenied=True,
            missingVersionDenied=True, legacyVersionDenied=True, nonreaderDenials=2,
            allReadsPreservedFacts=True, postgresPermissionEvidence=True, sqliteNotUsed=True)
    except OpenBaoPinReaderGateError as error:
        failure = str(error)
    except BaseException:
        failure = "unexpected_" + stage
    if failure is not None:
        # Outside handlers, so collectors do not retain driver exception chains.
        raise OpenBaoPinReaderGateError(failure)
    return report
