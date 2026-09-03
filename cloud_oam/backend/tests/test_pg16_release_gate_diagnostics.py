from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace
import threading

import pytest

import pg16_release_gate_diagnostics as diagnostics


_SECRET_DETAIL = "SENTINEL-DETAIL-7f31"
_SECRET_HINT = "SENTINEL-HINT-c8d2"
_SECRET_QUERY = "SENTINEL-INTERNAL-QUERY-23ac"
_SECRET_STATEMENT = "SENTINEL-STATEMENT-625b"
_SECRET_PARAMETERS = "SENTINEL-PARAMETERS-a925"
_SECRET_DSN = "SENTINEL-DSN-c10e"


def _fake_events(monkeypatch):
    installed: list[tuple[object, str, object]] = []
    removed: list[tuple[object, str, object]] = []

    def listen(target, identifier, listener):
        installed.append((target, identifier, listener))

    def remove(target, identifier, listener):
        removed.append((target, identifier, listener))

    monkeypatch.setattr(diagnostics.event, "listen", listen)
    monkeypatch.setattr(diagnostics.event, "remove", remove)
    return installed, removed


def test_success_returns_value_and_always_removes_listener(monkeypatch) -> None:
    installed, removed = _fake_events(monkeypatch)
    engine = object()

    result = diagnostics.run_with_sanitized_database_diagnostics(
        engine,
        lambda: {"status": "complete"},
        replace_unlinked_database_failure_when=lambda _failure: True,
    )

    assert result == {"status": "complete"}
    assert len(installed) == 1
    assert removed == installed
    assert installed[0][0:2] == (engine, "handle_error")


def test_unlinked_replacement_mode_must_be_a_predicate(monkeypatch) -> None:
    installed, removed = _fake_events(monkeypatch)

    with pytest.raises(
        TypeError,
        match="replace_unlinked_database_failure_when must be callable or None",
    ):
        diagnostics.run_with_sanitized_database_diagnostics(
            object(),
            lambda: None,
            replace_unlinked_database_failure_when=True,  # type: ignore[arg-type]
        )

    assert installed == []
    assert removed == []


def test_failure_exposes_only_allowlisted_postgresql_fields(monkeypatch) -> None:
    installed, removed = _fake_events(monkeypatch)
    engine = object()

    postgres_diagnostic = SimpleNamespace(
        message_primary="stocktake difference completion is not canonical",
        schema_name="public",
        table_name="stocktake_difference_set_completions",
        column_name="authorization_sha256",
        constraint_name="ck_stocktake_difference_authorization_0031",
        context=(
            "PL/pgSQL function "
            "rsc_validate_stocktake_difference_set_completion_0031() "
            "line 34 at RAISE"
        ),
        message_detail=_SECRET_DETAIL,
        message_hint=_SECRET_HINT,
        internal_query=_SECRET_QUERY,
    )
    original_exception = SimpleNamespace(
        sqlstate="23514",
        diag=postgres_diagnostic,
        detail=_SECRET_DETAIL,
        connection=SimpleNamespace(dsn=_SECRET_DSN),
    )

    class PublicDomainError(RuntimeError):
        pass

    def operation():
        listener = installed[0][2]
        listener(
            SimpleNamespace(
                original_exception=original_exception,
                is_pre_ping=False,
                statement=_SECRET_STATEMENT,
                parameters={"secret": _SECRET_PARAMETERS},
                engine=SimpleNamespace(url=_SECRET_DSN),
            )
        )
        raise PublicDomainError("public-domain-error-must-not-be-copied")

    with pytest.raises(
        diagnostics.SanitizedPostgreSQLDiagnosticError
    ) as captured:
        diagnostics.run_with_sanitized_database_diagnostics(
            engine,
            operation,
            replace_unlinked_database_failure_when=lambda _failure: True,
        )

    message = str(captured.value)
    assert "sqlstate=\"23514\"" in message
    assert (
        "message_primary=\"stocktake difference completion is not canonical\""
        in message
    )
    assert "schema_name=\"public\"" in message
    assert "table_name=\"stocktake_difference_set_completions\"" in message
    assert "column_name=\"authorization_sha256\"" in message
    assert (
        "constraint_name=\"ck_stocktake_difference_authorization_0031\""
        in message
    )
    assert (
        "plpgsql_function="
        "\"rsc_validate_stocktake_difference_set_completion_0031\""
        in message
    )
    assert "plpgsql_line=34" in message
    assert "plpgsql_operation=\"RAISE\"" in message
    for secret in (
        _SECRET_DETAIL,
        _SECRET_HINT,
        _SECRET_QUERY,
        _SECRET_STATEMENT,
        _SECRET_PARAMETERS,
        _SECRET_DSN,
        "public-domain-error-must-not-be-copied",
    ):
        assert secret not in message
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert removed == installed
    assert {field.name for field in fields(captured.value.diagnostic)} == {
        "sqlstate",
        "message_primary",
        "schema_name",
        "table_name",
        "column_name",
        "constraint_name",
        "plpgsql_function",
        "plpgsql_line",
        "plpgsql_operation",
    }


def test_non_database_exception_is_reraised_unchanged(monkeypatch) -> None:
    installed, removed = _fake_events(monkeypatch)
    engine = object()
    failure = ValueError("ordinary failure")

    def operation():
        raise failure

    with pytest.raises(ValueError) as captured:
        diagnostics.run_with_sanitized_database_diagnostics(
            engine,
            operation,
            replace_unlinked_database_failure_when=lambda _failure: True,
        )

    assert captured.value is failure
    assert removed == installed


def test_other_thread_and_pre_ping_diagnostics_are_ignored(monkeypatch) -> None:
    installed, removed = _fake_events(monkeypatch)
    engine = object()
    failure = RuntimeError("unrelated failure")
    leaked_diagnostic = SimpleNamespace(
        message_primary="must be ignored",
        schema_name=None,
        table_name=None,
        column_name=None,
        constraint_name=None,
        context=None,
    )
    original_exception = SimpleNamespace(
        sqlstate="XX000",
        diag=leaked_diagnostic,
    )

    def operation():
        listener = installed[0][2]
        worker = threading.Thread(
            target=lambda: listener(
                SimpleNamespace(
                    original_exception=original_exception,
                    is_pre_ping=False,
                )
            )
        )
        worker.start()
        worker.join()
        listener(
            SimpleNamespace(
                original_exception=original_exception,
                is_pre_ping=True,
            )
        )
        raise failure

    with pytest.raises(RuntimeError) as captured:
        diagnostics.run_with_sanitized_database_diagnostics(
            engine,
            operation,
            replace_unlinked_database_failure_when=lambda _failure: True,
        )

    assert captured.value is failure
    assert removed == installed


def test_malformed_context_and_identifiers_are_not_rendered(monkeypatch) -> None:
    installed, _removed = _fake_events(monkeypatch)
    engine = object()
    postgres_diagnostic = SimpleNamespace(
        message_primary="safe primary",
        schema_name="public; secret",
        table_name="table\nsecret",
        column_name="valid_column",
        constraint_name="constraint with spaces",
        context=(
            "PL/pgSQL function permitted_name() line 2 at RAISE; "
            "SENTINEL-CONTEXT"
        ),
    )
    original_exception = SimpleNamespace(
        sqlstate="invalid-state",
        diag=postgres_diagnostic,
    )

    def operation():
        installed[0][2](
            SimpleNamespace(
                original_exception=original_exception,
                is_pre_ping=False,
            )
        )
        raise RuntimeError("hidden")

    with pytest.raises(
        diagnostics.SanitizedPostgreSQLDiagnosticError
    ) as captured:
        diagnostics.run_with_sanitized_database_diagnostics(
            engine,
            operation,
            replace_unlinked_database_failure_when=lambda _failure: True,
        )

    message = str(captured.value)
    assert "message_primary=" not in message
    assert "safe primary" not in message
    assert "column_name=\"valid_column\"" in message
    assert "sqlstate=" not in message
    assert "schema_name=" not in message
    assert "table_name=" not in message
    assert "constraint_name=" not in message
    assert "plpgsql_" not in message
    assert "SENTINEL-CONTEXT" not in message


def test_unknown_primary_message_is_never_copied(monkeypatch) -> None:
    installed, removed = _fake_events(monkeypatch)
    engine = object()
    secret_primary = "SENTINEL-PRIMARY-SECRET-9d43"
    original_exception = SimpleNamespace(
        sqlstate="P0001",
        diag=SimpleNamespace(
            message_primary=secret_primary,
            schema_name=None,
            table_name=None,
            column_name=None,
            constraint_name=None,
            context=None,
        ),
    )

    def operation():
        installed[0][2](
            SimpleNamespace(
                original_exception=original_exception,
                is_pre_ping=False,
            )
        )
        raise RuntimeError("hidden public failure")

    with pytest.raises(
        diagnostics.SanitizedPostgreSQLDiagnosticError
    ) as captured:
        diagnostics.run_with_sanitized_database_diagnostics(
            engine,
            operation,
            replace_unlinked_database_failure_when=lambda _failure: True,
        )

    message = str(captured.value)
    assert "sqlstate=\"P0001\"" in message
    assert "message_primary=" not in message
    assert secret_primary not in message
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert removed == installed


def test_database_event_without_diagnostic_never_reraises_raw_failure(
    monkeypatch,
) -> None:
    installed, removed = _fake_events(monkeypatch)
    engine = object()
    original_exception = SimpleNamespace(
        args=(
            _SECRET_STATEMENT,
            _SECRET_PARAMETERS,
            _SECRET_DSN,
        ),
        statement=_SECRET_STATEMENT,
        parameters=_SECRET_PARAMETERS,
        dsn=_SECRET_DSN,
    )

    def operation():
        installed[0][2](
            SimpleNamespace(
                original_exception=original_exception,
                is_pre_ping=False,
                statement=_SECRET_STATEMENT,
                parameters=_SECRET_PARAMETERS,
                engine=SimpleNamespace(url=_SECRET_DSN),
            )
        )
        raise RuntimeError(
            f"raw failure {_SECRET_STATEMENT} "
            f"{_SECRET_PARAMETERS} {_SECRET_DSN}"
        )

    with pytest.raises(
        diagnostics.SanitizedPostgreSQLDiagnosticError
    ) as captured:
        diagnostics.run_with_sanitized_database_diagnostics(
            engine,
            operation,
            replace_unlinked_database_failure_when=lambda _failure: True,
        )

    assert str(captured.value) == (
        "postgresql16 sanitized database error: diagnostic fields unavailable"
    )
    for secret in (
        _SECRET_STATEMENT,
        _SECRET_PARAMETERS,
        _SECRET_DSN,
    ):
        assert secret not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert removed == installed


def test_latest_database_event_replaces_stale_safe_diagnostic(monkeypatch) -> None:
    installed, removed = _fake_events(monkeypatch)
    engine = object()
    first_failure = RuntimeError("first handled database failure")
    last_failure = RuntimeError(
        f"last raw failure {_SECRET_STATEMENT} {_SECRET_PARAMETERS} {_SECRET_DSN}"
    )

    def operation():
        listener = installed[0][2]
        listener(
            SimpleNamespace(
                original_exception=SimpleNamespace(
                    sqlstate="P0001",
                    diag=SimpleNamespace(
                        message_primary=(
                            "stocktake difference completion is not canonical"
                        ),
                        schema_name=None,
                        table_name=None,
                        column_name=None,
                        constraint_name=None,
                        context=None,
                    ),
                ),
                sqlalchemy_exception=first_failure,
                is_pre_ping=False,
            )
        )
        listener(
            SimpleNamespace(
                original_exception=last_failure,
                sqlalchemy_exception=last_failure,
                is_pre_ping=False,
                statement=_SECRET_STATEMENT,
                parameters=_SECRET_PARAMETERS,
                engine=SimpleNamespace(url=_SECRET_DSN),
            )
        )
        raise last_failure

    with pytest.raises(
        diagnostics.SanitizedPostgreSQLDiagnosticError
    ) as captured:
        diagnostics.run_with_sanitized_database_diagnostics(
            engine,
            operation,
            replace_unlinked_database_failure_when=None,
        )

    assert str(captured.value) == (
        "postgresql16 sanitized database error: diagnostic fields unavailable"
    )
    assert "P0001" not in str(captured.value)
    assert "stocktake difference completion is not canonical" not in str(
        captured.value
    )
    for secret in (_SECRET_STATEMENT, _SECRET_PARAMETERS, _SECRET_DSN):
        assert secret not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert removed == installed


def test_linked_failure_uses_its_own_database_event(monkeypatch) -> None:
    installed, removed = _fake_events(monkeypatch)
    engine = object()
    linked_failure = RuntimeError("linked database failure")
    later_handled_failure = RuntimeError("later handled database failure")

    def operation():
        listener = installed[0][2]
        listener(
            SimpleNamespace(
                original_exception=SimpleNamespace(
                    sqlstate="P0001",
                    diag=SimpleNamespace(
                        message_primary=(
                            "stocktake difference completion is not canonical"
                        ),
                        schema_name=None,
                        table_name=None,
                        column_name=None,
                        constraint_name=None,
                        context=None,
                    ),
                ),
                sqlalchemy_exception=linked_failure,
                is_pre_ping=False,
            )
        )
        listener(
            SimpleNamespace(
                original_exception=SimpleNamespace(
                    sqlstate="23505",
                    diag=None,
                ),
                sqlalchemy_exception=later_handled_failure,
                is_pre_ping=False,
            )
        )
        raise linked_failure

    with pytest.raises(
        diagnostics.SanitizedPostgreSQLDiagnosticError
    ) as captured:
        diagnostics.run_with_sanitized_database_diagnostics(
            engine,
            operation,
            replace_unlinked_database_failure_when=None,
        )

    message = str(captured.value)
    assert "sqlstate=\"P0001\"" in message
    assert "stocktake difference completion is not canonical" in message
    assert "23505" not in message
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert removed == installed


def test_unlinked_failure_is_not_misattributed_when_predicate_rejects(
    monkeypatch,
) -> None:
    installed, removed = _fake_events(monkeypatch)
    engine = object()
    unrelated_failure = RuntimeError("later unrelated failure")
    database_failure = RuntimeError("handled database failure")
    evaluated_failures: list[BaseException] = []

    def reject_unrelated(failure: BaseException) -> bool:
        evaluated_failures.append(failure)
        return False

    def operation():
        installed[0][2](
            SimpleNamespace(
                original_exception=database_failure,
                sqlalchemy_exception=database_failure,
                is_pre_ping=False,
            )
        )
        raise unrelated_failure

    with pytest.raises(RuntimeError) as captured:
        diagnostics.run_with_sanitized_database_diagnostics(
            engine,
            operation,
            replace_unlinked_database_failure_when=reject_unrelated,
        )

    assert captured.value is unrelated_failure
    assert evaluated_failures == [unrelated_failure]
    assert removed == installed


def test_recycled_numeric_identity_cannot_link_unrelated_failure(
    monkeypatch,
) -> None:
    installed, removed = _fake_events(monkeypatch)
    engine = object()
    database_failure = RuntimeError("handled database failure")
    unrelated_failure = RuntimeError("later unrelated failure")

    # Simulate CPython recycling a released exception's numeric ``id`` for a
    # later exception.  A diagnostic link must compare live objects with
    # ``is``; retaining only numeric ids would deterministically misattribute
    # the unrelated failure below.
    monkeypatch.setattr(diagnostics, "id", lambda _value: 1, raising=False)

    def operation():
        installed[0][2](
            SimpleNamespace(
                original_exception=database_failure,
                sqlalchemy_exception=database_failure,
                is_pre_ping=False,
            )
        )
        raise unrelated_failure

    with pytest.raises(RuntimeError) as captured:
        diagnostics.run_with_sanitized_database_diagnostics(
            engine,
            operation,
            replace_unlinked_database_failure_when=None,
        )

    assert captured.value is unrelated_failure
    assert removed == installed


def test_linked_database_failure_is_sanitized_without_unlinked_mode(
    monkeypatch,
) -> None:
    installed, removed = _fake_events(monkeypatch)
    engine = object()
    database_failure = RuntimeError(
        f"database wrapper {_SECRET_STATEMENT} {_SECRET_PARAMETERS} {_SECRET_DSN}"
    )

    def operation():
        installed[0][2](
            SimpleNamespace(
                original_exception=database_failure,
                sqlalchemy_exception=database_failure,
                is_pre_ping=False,
            )
        )
        raise database_failure

    with pytest.raises(
        diagnostics.SanitizedPostgreSQLDiagnosticError
    ) as captured:
        diagnostics.run_with_sanitized_database_diagnostics(
            engine,
            operation,
            replace_unlinked_database_failure_when=None,
        )

    assert str(captured.value) == (
        "postgresql16 sanitized database error: diagnostic fields unavailable"
    )
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert removed == installed
