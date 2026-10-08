"""Scoped observer SQL/selection tests, no live database or prior suite rerun.

The real snapshot()/rows()/SQLAlchemy text path runs against a recording DB
boundary. Synthetic catalog rows include unexpected details and overloads so
the observer cannot silently reduce a future exact-catalog validator's input.
"""

from copy import deepcopy
import re

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import TextClause

from app.stock_scrap_security_probe import snapshot


TABLES = ("openbao_data_key_pins", "application_key_version_claims", "kms_data_key_pins")
FUNCTIONS = (
    "rsc_reject_key_provider_binding_mutation_0180",
    "rsc_guard_application_key_version_claim_0180",
    "rsc_claim_application_key_version_0180",
)
UNRELATED_TABLE = "unrelated_business_table"
UNRELATED_FUNCTION = "unrelated_business_function"


class Result:
    def __init__(self, values):
        self.values = values

    def mappings(self):
        return iter(deepcopy(self.values))


class CatalogConnection:
    def __init__(self):
        self.calls = []
        unexpected_acl = {"grantee": "PUBLIC", "privilege": "UPDATE", "grantable": True}
        self.tables = [
            {"name": name, "owner": "star_oam_migrator", "relkind": "r",
             "relpersistence": "p", "relrowsecurity": False,
             "relforcerowsecurity": False, "relreplident": "d",
             "reloptions": ["unexpected_option=true"], "acl": [unexpected_acl]}
            for name in (*TABLES, UNRELATED_TABLE)
        ]
        self.functions = [
            {"proname": name, "signature": name + "(" + arguments + ")",
             "identity_arguments": arguments, "definition": "synthetic definition",
             "prosrc": "unexpected body retained", "prosecdef": True,
             "provolatile": "v", "proparallel": "u", "proisstrict": False,
             "proleakproof": False, "proconfig": ["search_path=unsafe"],
             "owner": "unexpected_owner", "acl": [{"grantee": "PUBLIC",
                 "privilege": "EXECUTE", "grantable": True}]}
            for name in (*FUNCTIONS, UNRELATED_FUNCTION) for arguments in ("", "text")
        ]
        self.details = {
            "columns": [
                {"name": "purpose", "type": "text", "not_null": True, "default_sql": None,
                 "attidentity": "", "attgenerated": "", "collation": "default", "acl": []},
                {"name": "unexpected_extra_column", "type": "text", "not_null": False,
                 "default_sql": "'unexpected'::text", "attidentity": "", "attgenerated": "",
                 "collation": "default", "acl": [unexpected_acl]},
            ],
            "constraints": [{"name": "unexpected_constraint", "type": "f",
                "definition": "synthetic foreign key", "convalidated": False,
                "condeferrable": True, "condeferred": True}],
            "indexes": [{"name": "unexpected_index", "definition": "synthetic index",
                "indisunique": True, "indisvalid": False, "indisready": False,
                "indimmediate": False, "indisreplident": True}],
            "triggers": [{"name": "unexpected_trigger", "definition": "synthetic trigger",
                "tgenabled": "D", "tgtype": 7, "tgdeferrable": False,
                "tginitdeferred": False, "function_signature": "unexpected_hook(text)"}],
            "fk_guards": [{"constraint_name": "unexpected_constraint", "table_name": name,
                "function_name": "RI_FKey_check_ins", "tgenabled": "D", "tgtype": 5,
                "tgdeferrable": True, "tginitdeferred": True}
                for name in (TABLES[0], "referenced_table_outside_scope")],
        }

    def execute(self, statement, parameters):
        assert isinstance(statement, TextClause)
        sql = " ".join(str(statement).split())
        self.calls.append((statement, sql, deepcopy(parameters)))
        if "FROM pg_proc p WHERE" in sql:
            selected = parameters.get("function_names")
            return Result([row for row in self.functions if selected is None or row["proname"] in selected])
        if "FROM pg_class c WHERE" in sql:
            selected = parameters.get("table_names")
            return Result([row for row in self.tables if selected is None or row["name"] in selected])
        assert set(parameters) == {"table"}
        assert parameters["table"] in {"public." + name for name in (*TABLES, UNRELATED_TABLE)}
        for category, fragment in (
            ("columns", "FROM pg_attribute a"), ("constraints", "FROM pg_constraint WHERE"),
            ("indexes", "FROM pg_index i"), ("triggers", "FROM pg_trigger t"),
            ("fk_guards", "FROM pg_constraint k"),
        ):
            if fragment in sql:
                return Result(self.details[category])
        pytest.fail("unexpected catalog SQL")


INVALID_SELECTORS = [
    (), [], set(), {}, "openbao_data_key_pins", 1, False,
    ("openbao_data_key_pins", "openbao_data_key_pins"),
    ("",), ("UPPERCASE",), ("public.openbao_data_key_pins",),
    ("openbao_data_key_pins ",), ("openbao_data_key_pins\n",),
    ("openbao_data_key_pins' OR true--",), ("a" * 64,),
    (1,), (None,), ([],), ({},), ("legitimate_name", []),
]


@pytest.mark.parametrize("parameter", ["table_names", "function_names"])
@pytest.mark.parametrize("names", INVALID_SELECTORS, ids=[
    "empty_tuple", "list", "set", "dict", "string", "integer", "boolean",
    "duplicate", "empty_name", "uppercase", "schema_qualified", "space", "newline",
    "injection", "overlong", "integer_member", "none_member", "list_member",
    "dict_member", "mixed_unhashable",
])
def test_illegal_selectors_fail_closed_before_any_sql(parameter, names):
    db = CatalogConnection()
    arguments = {"table_names": TABLES, "function_names": FUNCTIONS, parameter: names}
    # Unhashable tuple members currently raise TypeError during duplicate
    # detection; they must still fail before even the first catalog statement.
    with pytest.raises((ValueError, TypeError)):
        snapshot(db, **arguments)
    assert db.calls == []


def test_scoped_snapshot_uses_bound_any_and_keeps_every_object_detail():
    db = CatalogConnection()
    actual = snapshot(db, table_names=TABLES, function_names=FUNCTIONS)
    assert set(actual["tables"]) == set(TABLES)
    assert set(actual["functions"]) == {name + "(" + arguments + ")" for name in FUNCTIONS for arguments in ("", "text")}
    for row in db.functions:
        if row["proname"] in FUNCTIONS:
            assert actual["functions"][row["signature"]] == row
    for row in db.tables:
        if row["name"] in TABLES:
            assert actual["tables"][row["name"]] == {**row, **db.details}

    functions, tables = db.calls[:2]
    for (statement, sql, parameters), bind, values, predicate in (
        (functions, "function_names", FUNCTIONS, "p.proname"),
        (tables, "table_names", TABLES, "c.relname"),
    ):
        assert predicate + " = ANY(:" + bind + ")" in sql
        assert parameters == {bind: list(values)}
        assert set(statement._bindparams) == {bind}
        compiled = str(statement.compile(dialect=postgresql.dialect()))
        assert "ANY(%(" + bind + ")s)" in compiled
        assert all(name not in sql for name in values)
        assert "aclexplode(" in sql and "is_grantable" in sql
        assert "LIMIT" not in sql.upper() and "DISTINCT" not in sql.upper()
    # Function scope is name-only: do not select only the expected zero-arg
    # signature, or an added dangerous overload would disappear from the gate.
    where = functions[1].split(" WHERE ", 1)[1].split(" ORDER BY ", 1)[0]
    assert "proargtypes" not in where and "identity_arguments" not in where
    assert "prokind IN ('f','p')" in where

    assert len(db.calls) == 2 + 5 * len(TABLES)
    detail_sql = [sql for _, sql, _ in db.calls[2:]]
    for statement, sql, parameters in db.calls[2:]:
        assert set(parameters) == {"table"}
        assert set(statement._bindparams) == {"table"}
        assert "to_regclass(:table)" in sql
        assert "ANY(" not in sql and "LIMIT" not in sql.upper()
    columns = next(sql for sql in detail_sql if "FROM pg_attribute a" in sql)
    assert "aclexplode(a.attacl)" in columns
    assert "ORDER BY a.attnum" in columns
    assert re.search(r"a\.attname\s*(?:=|IN)", columns, re.IGNORECASE) is None
    triggers = next(sql for sql in detail_sql if "FROM pg_trigger t" in sql)
    assert "NOT t.tgisinternal" in triggers
    assert re.search(r"t\.tgname\s*(?:=|IN)", triggers, re.IGNORECASE) is None
    fk_guards = next(sql for sql in detail_sql if "FROM pg_constraint k" in sql)
    assert "t.tgisinternal" in fk_guards and "k.contype='f'" in fk_guards
    assert "c.relname =" not in fk_guards


@pytest.mark.parametrize("arguments,table_count,function_count", [
    ({}, 4, 8),
    ({"table_names": TABLES}, 3, 8),
    ({"function_names": FUNCTIONS}, 4, 6),
])
def test_none_or_omitted_selector_preserves_independent_unscoped_side(arguments, table_count, function_count):
    db = CatalogConnection()
    actual = snapshot(db, **arguments)
    assert len(actual["tables"]) == table_count
    assert len(actual["functions"]) == function_count
    for index, name in ((0, "function_names"), (1, "table_names")):
        _, sql, parameters = db.calls[index]
        if name not in arguments:
            assert parameters == {} and "ANY(" not in sql


def test_missing_selected_objects_are_not_fabricated_or_replaced_by_other_objects():
    db = CatalogConnection()
    actual = snapshot(db, table_names=("missing_required_table",), function_names=("missing_required_function",))
    assert actual == {"tables": {}, "functions": {}}
    assert len(db.calls) == 2
    # Observers return exact absences. The owning validator must compare this
    # against its complete expected object set rather than treat it as success.
    assert set(actual["tables"]) != {"missing_required_table"}
