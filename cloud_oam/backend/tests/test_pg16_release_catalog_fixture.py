"""Offline regression of the hosted catalog mutation/restoration fixture.

Exercise the real column-ACL validator with an in-memory catalog. This verifies
the fixture's current-HEAD mutation choices, not PostgreSQL GRANT semantics.
"""
from contextlib import contextmanager
import re

import pytest

import app.database_security as security
from pg16_reservation_release_gate import _assert_release_catalog


class ColumnCatalog:
    def __init__(self):
        self.grants = {
            (table, column)
            for table, columns in security.RUNTIME_UPDATE_COLUMNS.items()
            for column in columns
        }
        self.original = self.grants.copy()
        self.statements = []

    @contextmanager
    def begin(self):
        yield self

    def execute(self, statement):
        sql = str(statement)
        match = re.fullmatch(
            r"(GRANT|REVOKE) UPDATE \((\w+)\) ON TABLE public.material_requests "
            r"(TO|FROM) star_oam_api", sql,
        )
        assert match, sql
        action, column, direction = match.groups()
        assert direction == ("TO" if action == "GRANT" else "FROM")
        self.statements.append((action, column))
        coordinate = ("material_requests", column)
        if action == "GRANT":
            self.grants.add(coordinate)
        else:
            self.grants.discard(coordinate)

    def validate(self):
        rows = [
            dict(table_name=table, column_name=column,
                 grantee_name="star_oam_api", privilege_type="UPDATE",
                 is_grantable=False)
            for table, column in sorted(self.grants)
        ]
        security._assert_runtime_column_acl(rows, expected_runtime_role="star_oam_api")


def _install_column_catalog(monkeypatch, *, fail_at=None):
    catalog = ColumnCatalog()
    # Isolate column mutations; trigger/function probes are not changed here.
    monkeypatch.setattr(security, "EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS", {})
    monkeypatch.setattr(security, "MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256", {})
    observations = []

    def validate(engine, *, expected_runtime_role, expected_migration_role):
        assert engine is catalog
        assert expected_runtime_role == "star_oam_api"
        assert expected_migration_role == "star_oam_migrator"
        observations.append(catalog.grants.copy())
        if len(observations) == fail_at:
            raise RuntimeError("injected catalog read failure")
        catalog.validate()

    monkeypatch.setattr(security, "validate_production_database_security", validate)
    return catalog, observations


def test_head_catalog_rejects_missing_projection_and_excess_identity_grants(monkeypatch):
    catalog, observations = _install_column_catalog(monkeypatch)
    assert ("material_requests", "shipment_status") in catalog.original
    assert ("material_requests", "requester_person_id") not in catalog.original

    _assert_release_catalog(catalog, catalog, "_0071")

    assert catalog.statements == [
        ("REVOKE", "outbound_status"), ("GRANT", "outbound_status"),
        ("REVOKE", "shipment_status"), ("GRANT", "shipment_status"),
        ("GRANT", "requester_person_id"), ("REVOKE", "requester_person_id"),
    ]
    assert len(observations) == 6
    for removed in ("outbound_status", "shipment_status"):
        index = 0 if removed == "outbound_status" else 2
        assert observations[index] == catalog.original - {("material_requests", removed)}
    assert observations[4] == catalog.original | {("material_requests", "requester_person_id")}
    assert all(observations[index] == catalog.original for index in (1, 3, 5))
    assert catalog.grants == catalog.original


@pytest.mark.parametrize("fail_at", [1, 3, 5])
def test_catalog_mutations_restore_exact_grants_on_unexpected_validation_failure(monkeypatch, fail_at):
    catalog, _ = _install_column_catalog(monkeypatch, fail_at=fail_at)
    with pytest.raises(RuntimeError, match="injected catalog read failure"):
        _assert_release_catalog(catalog, catalog, "_0071")
    assert catalog.grants == catalog.original
    catalog.validate()
