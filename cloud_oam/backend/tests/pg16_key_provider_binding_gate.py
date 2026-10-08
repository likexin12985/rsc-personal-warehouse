"""C1 synthetic PG16 gate over caller-owned, already verified test engines.

No DSN, bootstrap, credentials, environment fallback or automatic retry exists.
The caller owns the disposable cluster and finite connection timeouts. This
entry requires its exact initial fixture; after an interrupted run the caller
must inspect that owned database, never blindly rerun this writer.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from threading import Event
import time

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


HEAD = "20261229_0180"
DATABASE = "rsc_pg16_release_gate"
AUTH = "authentication_idempotency"
CONTACT = "material_request_contact"
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
OWNER = "star_oam_migrator"
ROLES = (OWNER, "star_oam_api", "star_oam_backup", "star_oam_projector", "edge_inbox")
TABLES = ("kms_data_key_pins", "openbao_data_key_pins", "application_key_version_claims")
FUNCTIONS = (
    "rsc_reject_key_provider_binding_mutation_0180",
    "rsc_guard_application_key_version_claim_0180",
    "rsc_claim_application_key_version_0180",
)


class KeyProviderBindingGateError(RuntimeError):
    """Only fixed non-sensitive stage identifiers cross the gate boundary."""


def _require(condition, stage):
    if not condition:
        raise KeyProviderBindingGateError(stage) from None


def _limits(db, *, racing=False):
    db.execute(text("SET LOCAL statement_timeout='15s'"))
    db.execute(text("SET LOCAL lock_timeout='12s'" if racing else "SET LOCAL lock_timeout='4s'"))
    db.execute(text("SET LOCAL idle_in_transaction_session_timeout='45s'"))


def _digest(label):
    return sha256(("rsc.synthetic.key-provider.0180." + label).encode("ascii")).hexdigest()


def _pin(provider, version, label, *, purpose=AUTH):
    value = dict(purpose=purpose, application_key_version=version,
        ciphertext_sha256=_digest(label), created_at=NOW)
    if provider == "aliyun_kms":
        value.update(kms_key_id="synthetic-reviewed-aliyun-key", kms_key_version_id="synthetic-reviewed-aliyun-version")
        return TABLES[0], value
    _require(provider == "openbao_transit_v1", "internal_provider")
    value.update(environment="test", provider_instance_id="isolated-openbao-2-7-1",
        key_path="transit/keys/" + ("rsc-authentication-idempotency" if purpose == AUTH else "rsc-material-request-contact"),
        transit_key_version=1, context_sha256=_digest("context-" + label),
        associated_data_sha256=_digest("aad-" + label))
    return TABLES[1], value


def _insert_statement(table, values):
    _require(table in TABLES, "internal_table")
    return text("INSERT INTO public." + table + " (" + ",".join(values) + ") VALUES (" + ",".join(":" + key for key in values) + ")")


def _insert(db, pin):
    table, values = pin
    db.execute(_insert_statement(table, values), values)


def _failure(db, statement, parameters=None, *, codes, stage):
    """An expected failure is isolated and always followed by a usable tx."""
    savepoint = db.begin_nested()
    try:
        db.execute(statement, parameters or {})
    except DBAPIError as error:
        code = getattr(error.orig, "sqlstate", None)
        savepoint.rollback()
        _require(code in codes, stage)
    except BaseException:
        savepoint.rollback()
        raise
    else:
        # Even an unexpected permission success must never be committed.
        savepoint.rollback()
        raise KeyProviderBindingGateError(stage)
    _require(db.scalar(text("SELECT 1")) == 1, "savepoint_recovery")


def _snapshot(engine):
    with engine.connect() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        _limits(db)
        return {table: [dict(row) for row in db.execute(text("SELECT * FROM public." + table + " ORDER BY purpose,application_key_version")).mappings()] for table in TABLES}


def _identity(engines):
    _require(all(role in engines for role in ROLES), "required_engines")
    for role in ROLES:
        with engines[role].connect() as db:
            _limits(db)
            row = db.execute(text("SELECT current_database(),current_user,session_user,current_setting('server_version_num')::int,current_setting('listen_addresses'),current_setting('transaction_isolation')")).one()
            _require(tuple(row[:3]) == (DATABASE, role, role) and 160000 <= row[3] < 170000 and row[4:] == ("", "read committed"), "direct_isolated_identity")
            flags = db.execute(text("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=current_user")).one()
            _require(not any(flags), "unprivileged_identity")
    with engines[OWNER].connect() as db:
        _limits(db)
        _require(db.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all() == [HEAD], "current_head")
        _require(db.scalar(text("SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE rolname='star_oam_edge'")) is False, "edge_nologin")
    initial = _snapshot(engines[OWNER])
    expected_seed = dict(purpose=AUTH, kms_key_id="synthetic-legacy-key", application_key_version=1,
        kms_key_version_id="synthetic-legacy-version", ciphertext_sha256="a" * 64, created_at=NOW)
    _require(initial[TABLES[0]] == [expected_seed] and initial[TABLES[1]] == [], "exact_initial_pin_fixture")
    expected_claim = {key: value for key, value in expected_seed.items() if key not in {"kms_key_id", "kms_key_version_id"}}
    expected_claim["provider"] = "aliyun_kms"
    _require(initial[TABLES[2]] == [expected_claim], "exact_initial_claim_fixture")


def _acl_gate(engines):
    mutation_denials = function_denials = select_denials = select_successes = 0
    for role in ROLES[1:]:
        with engines[role].begin() as db:
            _limits(db)
            for table in TABLES:
                query = text("SELECT count(*) FROM public." + table)
                if role in {"star_oam_api", "star_oam_backup"}:
                    _require(db.scalar(query) >= 1, "reader_select")
                    select_successes += 1
                else:
                    _failure(db, query, codes={"42501"}, stage="nonreader_select_denied")
                    select_denials += 1
                if table == TABLES[2]:
                    values = dict(purpose=AUTH, application_key_version=1, provider="aliyun_kms", ciphertext_sha256="a" * 64, created_at=NOW)
                else:
                    _, values = _pin("aliyun_kms" if table == TABLES[0] else "openbao_transit_v1", 1_800_090, "acl")
                for statement, parameters in (
                    (_insert_statement(table, values), values),
                    (text("UPDATE public." + table + " SET ciphertext_sha256=ciphertext_sha256"), {}),
                    (text("DELETE FROM public." + table), {}),
                    (text("TRUNCATE public." + table), {}),
                ):
                    _failure(db, statement, parameters, codes={"42501"}, stage="runtime_mutation_denied")
                    mutation_denials += 1
            for function in FUNCTIONS:
                _require(db.scalar(text("SELECT has_function_privilege(current_user,:signature,'EXECUTE')"), {"signature": "public." + function + "()"}) is False, "runtime_function_acl")
                _failure(db, text("SELECT public." + function + "()"), codes={"42501"}, stage="runtime_function_execute_denied")
                function_denials += 1
            _failure(db, text("SET LOCAL ROLE star_oam_migrator"), codes={"42501"}, stage="migration_role_escalation_denied")
    with engines[OWNER].begin() as db:
        _limits(db)
        for table in TABLES:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                _require(db.scalar(text("SELECT has_table_privilege('star_oam_edge',:relation,:privilege)"), {"relation": "public." + table, "privilege": privilege}) is False, "edge_table_acl")
        for function in FUNCTIONS:
            _require(db.scalar(text("SELECT has_function_privilege('star_oam_edge',:signature,'EXECUTE')"), {"signature": "public." + function + "()"}) is False, "edge_function_acl")
        _failure(db, text("SET LOCAL session_replication_role='replica'"), codes={"42501"}, stage="replication_bypass_denied")
        # Do not grant role memberships just to manufacture impersonation.
        _failure(db, text("SET LOCAL ROLE star_oam_api"), codes={"42501"}, stage="owner_role_switch_denied")
    return dict(runtimeMutationDenials=mutation_denials, runtimeFunctionDenials=function_denials,
        nonreaderSelectDenials=select_denials, readerSelectSuccesses=select_successes,
        edgeNoLoginAndNoPrivileges=True, roleEscalationDenied=True, replicationBypassDenied=True)


def _deterministic_gate(engines):
    owner = engines[OWNER]
    # Valid OpenBao facts cover both purposes and show purpose-local versions.
    with owner.begin() as db:
        _limits(db)
        _insert(db, _pin("openbao_transit_v1", 1_800_001, "valid-auth"))
        _insert(db, _pin("openbao_transit_v1", 1, "valid-contact", purpose=CONTACT))
    before = _snapshot(owner)
    invalid_changes = (
        {"purpose": "other"}, {"environment": "other"},
        {"provider_instance_id": " Abc"}, {"provider_instance_id": "-ab"},
        {"provider_instance_id": "Abc"}, {"provider_instance_id": "界abc"},
        {"application_key_version": 0}, {"transit_key_version": 0},
        {"key_path": "transit/keys/rsc-material-request-contact"},
        {"ciphertext_sha256": "A" * 64}, {"context_sha256": "a" * 63},
        {"associated_data_sha256": "b" * 63 + "z"},
    )
    with owner.begin() as db:
        _limits(db)
        for index, changes in enumerate(invalid_changes):
            table, values = _pin("openbao_transit_v1", 1_800_030 + index, "invalid-" + str(index))
            values.update(changes)
            _failure(db, _insert_statement(table, values), values, codes={"23514"}, stage="invalid_pin_rejected")
        for provider in ("aliyun_kms", "openbao_transit_v1"):
            values = dict(purpose=AUTH, application_key_version=1_800_080, provider=provider,
                ciphertext_sha256=_digest("orphan"), created_at=NOW)
            _failure(db, _insert_statement(TABLES[2], values), values, codes={"23514"}, stage="orphan_claim_rejected")
        for pin in (_pin("openbao_transit_v1", 1, "legacy-conflict"), _pin("aliyun_kms", 1_800_001, "openbao-conflict")):
            table, values = pin
            _failure(db, _insert_statement(table, values), values, codes={"23505"}, stage="cross_provider_unique_rejected")
        for table in TABLES:
            for command in ("UPDATE public." + table + " SET ciphertext_sha256=ciphertext_sha256", "DELETE FROM public." + table, "TRUNCATE public." + table):
                _failure(db, text(command), codes={"55000"}, stage="owner_mutation_rejected")
    _require(_snapshot(owner) == before, "failed_operations_changed_facts")
    return dict(invalidCoordinatesRejected=len(invalid_changes), orphanClaimsRejected=2,
        sequentialProviderConflictsRejected=2, ownerImmutableDenials=9,
        failedOperationsPreserveAllFacts=True, bothPurposesBound=True)


def _race(owner, *, first, commit, version):
    second = "openbao_transit_v1" if first == "aliyun_kms" else "aliyun_kms"
    label = first + ("-commit" if commit else "-rollback")
    first_pin = _pin(first, version, "winner-" + label)
    second_pin = _pin(second, version, "waiter-" + label)
    before = _snapshot(owner)
    started = Event()
    shared = {}

    def waiter():
        try:
            with owner.connect() as db:
                transaction = db.begin()
                _limits(db, racing=True)
                shared["pid"] = db.scalar(text("SELECT pg_backend_pid()"))
                started.set()
                savepoint = db.begin_nested()
                try:
                    _insert(db, second_pin)  # exactly one attempt
                except DBAPIError as error:
                    code = getattr(error.orig, "sqlstate", None)
                    savepoint.rollback()
                    _require(db.scalar(text("SELECT 1")) == 1, "race_savepoint_recovery")
                    transaction.rollback()
                    _require(commit and code == "23505", "race_waiter_unexpected_failure")
                    return "unique_conflict"
                else:
                    savepoint.commit()
                    if commit:
                        transaction.rollback()
                        raise KeyProviderBindingGateError("race_loser_unexpected_success")
                    transaction.commit()
                    return "committed"
        except KeyProviderBindingGateError as error:
            raise KeyProviderBindingGateError(str(error)) from None
        except BaseException:
            raise KeyProviderBindingGateError("race_waiter_unavailable") from None

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="key-provider-gate")
    future = None
    observed = False
    try:
        with owner.connect() as winner:
            transaction = winner.begin()
            _limits(winner, racing=True)
            winner_pid = winner.scalar(text("SELECT pg_backend_pid()"))
            try:
                _insert(winner, first_pin)  # exactly one attempt
                future = pool.submit(waiter)
                _require(started.wait(timeout=10), "race_waiter_start_timeout")
                _require(shared["pid"] != winner_pid, "race_distinct_connections")
                deadline = time.monotonic() + 8
                with owner.connect() as observer:
                    _limits(observer)
                    while time.monotonic() < deadline:
                        if future.done():
                            future.result()
                            raise KeyProviderBindingGateError("race_waiter_did_not_block")
                        proof = observer.execute(text("SELECT :winner=ANY(pg_catalog.pg_blocking_pids(:waiter)), EXISTS (SELECT 1 FROM pg_catalog.pg_locks WHERE pid=:waiter AND relation='public.application_key_version_claims'::regclass AND mode='RowExclusiveLock' AND granted)"), {"winner": winner_pid, "waiter": shared["pid"]}).one()
                        if tuple(proof) == (True, True):
                            observed = True
                            break
                        time.sleep(0.02)
                _require(observed, "shared_primary_key_wait_not_observed")
                if commit:
                    transaction.commit()
                else:
                    transaction.rollback()
            finally:
                if transaction.is_active:
                    transaction.rollback()
        result = future.result(timeout=20)
        _require(result == ("unique_conflict" if commit else "committed"), "race_terminal_result")
    finally:
        # The winner lock is released before joining the bounded waiter. The
        # caller-provided engines must also have a finite connect timeout.
        pool.shutdown(wait=True, cancel_futures=True)
    after = _snapshot(owner)
    surviving = first_pin if commit else second_pin
    table, values = surviving
    expected = {name: list(rows) for name, rows in before.items()}
    expected[table].append(values)
    claim = {key: values[key] for key in ("purpose", "application_key_version", "ciphertext_sha256", "created_at")}
    claim["provider"] = first if commit else second
    expected[TABLES[2]].append(claim)
    for rows in expected.values():
        rows.sort(key=lambda row: (row["purpose"], row["application_key_version"]))
    _require(after == expected, "race_atomic_readback")
    return dict(waitObserved=True, exactlyOneDurableProvider=True,
        eachParticipantAttemptedOnce=True, failedOrRolledBackParticipantLeftNoPin=True)


def _exact_mirror(owner):
    with owner.connect() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        _limits(db)
        differences = db.scalar(text("""WITH pins AS (
 SELECT purpose,application_key_version,'aliyun_kms'::text AS provider,ciphertext_sha256,created_at FROM public.kms_data_key_pins
 UNION ALL SELECT purpose,application_key_version,'openbao_transit_v1'::text,ciphertext_sha256,created_at FROM public.openbao_data_key_pins
), mismatch AS (
 (SELECT purpose,application_key_version,provider,ciphertext_sha256,created_at FROM public.application_key_version_claims EXCEPT SELECT * FROM pins)
 UNION ALL (SELECT * FROM pins EXCEPT SELECT purpose,application_key_version,provider,ciphertext_sha256,created_at FROM public.application_key_version_claims)
) SELECT count(*) FROM mismatch"""))
        _require(differences == 0, "pins_claims_bijection")
        _require(db.scalar(text("""SELECT count(*) FROM (
 SELECT purpose,application_key_version FROM (SELECT purpose,application_key_version FROM public.kms_data_key_pins UNION ALL SELECT purpose,application_key_version FROM public.openbao_data_key_pins) p
 GROUP BY purpose,application_key_version HAVING count(*)<>1) duplicates""")) == 0, "global_version_unique")
        rows = db.execute(text("SELECT tgname,tgenabled FROM pg_catalog.pg_trigger WHERE tgname IN ('trg_openbao_data_key_pins_immutable_0180','trg_openbao_data_key_pins_no_truncate_0180','trg_application_key_version_claims_immutable_0180','trg_application_key_version_claims_no_truncate_0180','trg_application_key_version_claims_insert_0180','trg_openbao_data_key_pins_claim_0180','trg_kms_data_key_pins_claim_0180')")).all()
        _require(len(rows) == 7 and all(row[1] == "A" for row in rows), "always_binding_guards")
        return db.scalar(text("SELECT count(*) FROM public.application_key_version_claims"))


def run(engines):
    """Run once on the caller's owned 0180 synthetic fixture; return safe facts."""
    stage = "identity"
    try:
        _identity(engines)
        stage = "deterministic"
        report = _deterministic_gate(engines)
        stage = "acl"
        report.update(_acl_gate(engines))
        stage = "concurrency"
        for index, (first, commit) in enumerate((
            ("aliyun_kms", True), ("openbao_transit_v1", True),
            ("aliyun_kms", False), ("openbao_transit_v1", False),
        )):
            _race(engines[OWNER], first=first, commit=commit, version=1_800_100 + index)
        stage = "readback"
        count = _exact_mirror(engines[OWNER])
        _require(count == 7, "final_claim_count")
        report.update(schema="rsc.key-provider.binding.pg16.v1", result="passed", syntheticOnly=True,
            productionReady=False, claims=count, sharedUniqueIndexWaitsObserved=4,
            winnerCommitRaces=2, winnerRollbackRaces=2, raceInsertAttempts=8,
            allClaimsMatchExactlyOnePin=True, immutableAlwaysTriggers=7,
            postgresPermissionEvidence=True, sqliteNotUsed=True)
        return report
    except KeyProviderBindingGateError as error:
        raise KeyProviderBindingGateError(str(error)) from None
    except BaseException:
        # Neither SQLAlchemy SQL/parameters nor raw PostgreSQL diagnostics are
        # part of the receipt, even for a previously unseen failure.
        raise KeyProviderBindingGateError("unexpected_" + stage) from None
