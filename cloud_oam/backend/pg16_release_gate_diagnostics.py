"""Test-only, redacted PostgreSQL diagnostics for the PG16 release gate.

Public services deliberately hide database failures behind stable domain errors.
The destructive PostgreSQL 16 release gate still needs enough evidence to locate
an invariant that rejected its synthetic fixture.  This module captures only a
small PostgreSQL diagnostic allowlist while an operation is running.  It never
copies SQL text, bound parameters, connection coordinates, or raw exceptions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import re
import threading
from typing import Any, TypeVar

from sqlalchemy import event
from sqlalchemy.engine import Engine


_T = TypeVar("_T")
UnlinkedDatabaseFailurePredicate = Callable[[BaseException], bool]

_SQLSTATE_RE = re.compile(r"[0-9A-Z]{5}\Z", re.ASCII)
_IDENTIFIER_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_$]{0,127}\Z",
    re.ASCII,
)
_PRIMARY_MESSAGE_ALLOWLIST = {
    "non-opening stocktake start causality is invalid": (
        "non-opening stocktake start causality is invalid"
    ),
    "stocktake difference completion is not canonical": (
        "stocktake difference completion is not canonical"
    ),
}
_PLPGSQL_CONTEXT_RE = re.compile(
    r"^PL/pgSQL function "
    r"(?P<function>"
    r"(?:[A-Za-z_][A-Za-z0-9_$]{0,127}\.)?"
    r"[A-Za-z_][A-Za-z0-9_$]{0,127}"
    r")"
    r"\([A-Za-z0-9_$., \[\]\"]{0,256}\)"
    r" line (?P<line>[1-9][0-9]{0,5}) at "
    r"(?P<operation>"
    r"RAISE|SQL statement|assignment|IF|CASE|LOOP|"
    r"RETURN|RETURN NEXT|RETURN QUERY|PERFORM|EXECUTE|"
    r"CALL|OPEN|FETCH|CLOSE"
    r")$",
    re.ASCII | re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class SanitizedPostgreSQLDiagnostic:
    """The only database-derived values retained by the release gate."""

    sqlstate: str | None = None
    message_primary: str | None = None
    schema_name: str | None = None
    table_name: str | None = None
    column_name: str | None = None
    constraint_name: str | None = None
    plpgsql_function: str | None = None
    plpgsql_line: int | None = None
    plpgsql_operation: str | None = None

    def has_evidence(self) -> bool:
        return any(
            value is not None
            for value in (
                self.sqlstate,
                self.message_primary,
                self.schema_name,
                self.table_name,
                self.column_name,
                self.constraint_name,
                self.plpgsql_function,
                self.plpgsql_line,
                self.plpgsql_operation,
            )
        )


class SanitizedPostgreSQLDiagnosticError(AssertionError):
    """Release-gate assertion containing sanitized database evidence only."""

    def __init__(self, diagnostic: SanitizedPostgreSQLDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(_render_diagnostic(diagnostic))


@dataclass(frozen=True, slots=True)
class _ObservedDatabaseError:
    exceptions: tuple[object, ...]
    diagnostic: SanitizedPostgreSQLDiagnostic | None


def _string_attribute(value: object, attribute: str) -> str | None:
    try:
        candidate = getattr(value, attribute)
    except Exception:
        return None
    return candidate if isinstance(candidate, str) else None


def _safe_primary_message(value: str | None) -> str | None:
    if value is None:
        return None
    return _PRIMARY_MESSAGE_ALLOWLIST.get(value)


def _safe_identifier(value: str | None) -> str | None:
    if value is None or _IDENTIFIER_RE.fullmatch(value) is None:
        return None
    return value


def _parse_plpgsql_context(
    context: str | None,
) -> tuple[str | None, int | None, str | None]:
    if context is None or len(context) > 4096:
        return None, None, None
    match = _PLPGSQL_CONTEXT_RE.search(context)
    if match is None:
        return None, None, None
    return (
        match.group("function"),
        int(match.group("line")),
        match.group("operation"),
    )


def _sanitize_exception_context(
    exception_context: object,
) -> SanitizedPostgreSQLDiagnostic | None:
    try:
        original_exception = getattr(exception_context, "original_exception")
    except Exception:
        return None

    sqlstate = _string_attribute(original_exception, "sqlstate")
    if sqlstate is None or _SQLSTATE_RE.fullmatch(sqlstate) is None:
        sqlstate = None

    try:
        postgres_diagnostic = getattr(original_exception, "diag")
    except Exception:
        postgres_diagnostic = None

    if postgres_diagnostic is None:
        diagnostic = SanitizedPostgreSQLDiagnostic(sqlstate=sqlstate)
        return diagnostic if diagnostic.has_evidence() else None

    function, line, operation = _parse_plpgsql_context(
        _string_attribute(postgres_diagnostic, "context")
    )
    diagnostic = SanitizedPostgreSQLDiagnostic(
        sqlstate=sqlstate,
        message_primary=_safe_primary_message(
            _string_attribute(postgres_diagnostic, "message_primary")
        ),
        schema_name=_safe_identifier(
            _string_attribute(postgres_diagnostic, "schema_name")
        ),
        table_name=_safe_identifier(
            _string_attribute(postgres_diagnostic, "table_name")
        ),
        column_name=_safe_identifier(
            _string_attribute(postgres_diagnostic, "column_name")
        ),
        constraint_name=_safe_identifier(
            _string_attribute(postgres_diagnostic, "constraint_name")
        ),
        plpgsql_function=function,
        plpgsql_line=line,
        plpgsql_operation=operation,
    )
    return diagnostic if diagnostic.has_evidence() else None


def _render_diagnostic(diagnostic: SanitizedPostgreSQLDiagnostic) -> str:
    fields: list[str] = []
    for name in (
        "sqlstate",
        "message_primary",
        "schema_name",
        "table_name",
        "column_name",
        "constraint_name",
        "plpgsql_function",
        "plpgsql_line",
        "plpgsql_operation",
    ):
        value = getattr(diagnostic, name)
        if value is not None:
            fields.append(f"{name}={json.dumps(value, ensure_ascii=True)}")
    if not fields:
        return (
            "postgresql16 sanitized database error: "
            "diagnostic fields unavailable"
        )
    return "postgresql16 sanitized database error: " + ", ".join(fields)


def _linked_database_diagnostic(
    failure: BaseException,
    database_errors: list[_ObservedDatabaseError],
) -> tuple[bool, SanitizedPostgreSQLDiagnostic | None]:
    chain_exceptions: list[BaseException] = []
    current: BaseException | None = failure
    while current is not None and not any(
        current is observed for observed in chain_exceptions
    ):
        chain_exceptions.append(current)
        cause = current.__cause__
        if cause is not None:
            current = cause
        else:
            current = current.__context__
    for database_error in reversed(database_errors):
        if any(
            chained is observed
            for chained in chain_exceptions
            for observed in database_error.exceptions
        ):
            return True, database_error.diagnostic
    return False, None


def _predicate_allows_unlinked_failure(
    predicate: UnlinkedDatabaseFailurePredicate | None,
    failure: BaseException,
) -> bool:
    if predicate is None:
        return False
    try:
        return predicate(failure) is True
    except Exception:
        return False


def run_with_sanitized_database_diagnostics(
    engine: Engine,
    operation: Callable[[], _T],
    *,
    replace_unlinked_database_failure_when: (
        UnlinkedDatabaseFailurePredicate | None
    ),
) -> _T:
    """Run an operation with a temporary, current-thread diagnostic listener.

    If a public service replaces a database exception with a domain exception,
    a caller-supplied predicate must recognize that stable public failure before
    the release gate receives a redacted assertion.  Otherwise replacement
    requires the final exception chain to contain the observed database failure.
    A database event with no safe diagnostic fields becomes a generic redacted
    assertion; when no database event was observed, the operation's exception
    is re-raised.
    """

    if (
        replace_unlinked_database_failure_when is not None
        and not callable(replace_unlinked_database_failure_when)
    ):
        raise TypeError(
            "replace_unlinked_database_failure_when must be callable or None"
        )

    owner_thread_id = threading.get_ident()
    database_errors: list[_ObservedDatabaseError] = []
    selected_diagnostic: SanitizedPostgreSQLDiagnostic | None = None
    replace_failure = False

    def capture_diagnostic(exception_context: Any) -> None:
        if threading.get_ident() != owner_thread_id:
            return
        try:
            if getattr(exception_context, "is_pre_ping", False):
                return
        except Exception:
            return
        exceptions: list[object] = []
        for attribute in ("sqlalchemy_exception", "original_exception"):
            try:
                candidate = getattr(exception_context, attribute)
            except Exception:
                continue
            if candidate is not None and not any(
                candidate is observed for observed in exceptions
            ):
                exceptions.append(candidate)
        database_errors.append(
            _ObservedDatabaseError(
                exceptions=tuple(exceptions),
                diagnostic=_sanitize_exception_context(exception_context),
            )
        )

    event.listen(engine, "handle_error", capture_diagnostic)
    try:
        try:
            return operation()
        except Exception as failure:
            if not database_errors:
                raise
            linked, linked_diagnostic = _linked_database_diagnostic(
                failure,
                database_errors,
            )
            if linked:
                selected_diagnostic = linked_diagnostic
            else:
                if not _predicate_allows_unlinked_failure(
                    replace_unlinked_database_failure_when,
                    failure,
                ):
                    raise
                selected_diagnostic = database_errors[-1].diagnostic
            replace_failure = True
    finally:
        try:
            event.remove(engine, "handle_error", capture_diagnostic)
        finally:
            # Keep exact exception objects alive only long enough to compare
            # identity.  Never retain the raw database failures in the
            # sanitized exception's traceback frame after this boundary exits.
            database_errors.clear()

    if replace_failure:
        # This raise deliberately occurs after leaving the original ``except``
        # block so the sanitized error does not retain it as ``__context__``.
        raise SanitizedPostgreSQLDiagnosticError(
            selected_diagnostic or SanitizedPostgreSQLDiagnostic()
        ) from None
    raise AssertionError("unreachable PostgreSQL diagnostic boundary")
