"""Read-only PostgreSQL proof for the 0044 OAM sync row boundary.

The edge receiver and formal projector have different table ACLs, but both
must see the same migration-owned FORCE RLS graph.  This verifier deliberately
derives identity from the database session and never accepts a caller-set
scope value.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection


RLS_REVISION = "20260902_0044"
MIGRATION_ROLE = "star_oam_migrator"
PROJECTOR_ROLE = "star_oam_projector"
EDGE_ROLE = "edge_inbox"
API_ROLE = "star_oam_api"
BACKUP_ROLE = "star_oam_backup"

RLS_TABLES = (
    "oam_sync_scope_bindings",
    "external_sync_snapshots",
    "external_sync_snapshot_batches",
    "external_sync_snapshot_records",
    "external_sync_current_records",
    "audit_logs",
    "source_systems",
    "sync_runs",
    "sync_batches",
    "sync_inbox_events",
    "external_objects",
    "external_object_versions",
    "external_object_mappings",
    "sync_conflicts",
    "organizations",
    "people",
    "oam_work_orders",
)

_API_SELECT_TABLES = (
    "source_systems",
    "sync_runs",
    "sync_batches",
    "sync_inbox_events",
    "external_objects",
    "external_object_versions",
    "organizations",
    "people",
    "oam_work_orders",
)
_PROJECTOR_SELECT_TABLES = (
    "external_sync_snapshots",
    "external_sync_snapshot_batches",
    "external_sync_snapshot_records",
    "external_sync_current_records",
    "source_systems",
    "sync_runs",
    "sync_batches",
    "sync_inbox_events",
    "external_objects",
    "external_object_versions",
    "external_object_mappings",
    "sync_conflicts",
    "organizations",
    "people",
    "oam_work_orders",
)
_PROJECTOR_WRITE_TABLES = (
    "sync_runs",
    "sync_batches",
    "sync_inbox_events",
    "external_objects",
    "external_object_versions",
    "sync_conflicts",
    "oam_work_orders",
)
_EDGE_STAGING_TABLES = (
    "external_sync_snapshots",
    "external_sync_snapshot_batches",
    "external_sync_snapshot_records",
    "external_sync_current_records",
)


def _runtime_policy_expression(table_name: str, command: str) -> str:
    return (
        "rsc_oam_rls_check_0044("
        f"'{table_name}'::text, '{command}'::text, "
        f"to_jsonb({table_name}.*))"
    )


def _expected_policies(
) -> tuple[tuple[str, str, str, str, str | None, str | None], ...]:
    policies: list[
        tuple[str, str, str, str, str | None, str | None]
    ] = []
    for table_name in RLS_TABLES:
        policies.append(
            (
                table_name,
                f"{table_name}_migrator_0044",
                "*",
                MIGRATION_ROLE,
                "true",
                "true",
            )
        )
        policies.append(
            (
                table_name,
                f"{table_name}_backup_select_0044",
                "r",
                BACKUP_ROLE,
                "true",
                None,
            )
        )
    for table_name in _API_SELECT_TABLES:
        policies.append(
            (
                table_name,
                f"{table_name}_api_select_0044",
                "r",
                API_ROLE,
                "true",
                None,
            )
        )
    for table_name in _PROJECTOR_SELECT_TABLES:
        policies.append(
            (
                table_name,
                f"{table_name}_projector_select_0044",
                "r",
                PROJECTOR_ROLE,
                _runtime_policy_expression(table_name, "select"),
                None,
            )
        )
    for table_name in _PROJECTOR_WRITE_TABLES:
        policies.append(
            (
                table_name,
                f"{table_name}_projector_insert_0044",
                "a",
                PROJECTOR_ROLE,
                None,
                _runtime_policy_expression(table_name, "insert"),
            )
        )
    for table_name in _PROJECTOR_WRITE_TABLES:
        update_using_command = (
            "update_old"
            if table_name in {
                "external_objects",
                "external_object_versions",
                "oam_work_orders",
            }
            else "update"
        )
        update_check_command = (
            "update_new"
            if table_name in {
                "external_objects",
                "external_object_versions",
                "oam_work_orders",
            }
            else "update"
        )
        policies.append(
            (
                table_name,
                f"{table_name}_projector_update_0044",
                "w",
                PROJECTOR_ROLE,
                _runtime_policy_expression(
                    table_name, update_using_command
                ),
                _runtime_policy_expression(
                    table_name, update_check_command
                ),
            )
        )
    for table_name in _EDGE_STAGING_TABLES:
        policies.extend(
            (
                (
                    table_name,
                    f"{table_name}_edge_select_0044",
                    "r",
                    EDGE_ROLE,
                    _runtime_policy_expression(table_name, "select"),
                    None,
                ),
                (
                    table_name,
                    f"{table_name}_edge_insert_0044",
                    "a",
                    EDGE_ROLE,
                    None,
                    _runtime_policy_expression(table_name, "insert"),
                ),
            )
        )
    for table_name in (
        "external_sync_snapshots",
        "external_sync_current_records",
    ):
        update_using_command = (
            "select"
            if table_name == "external_sync_current_records"
            else "update"
        )
        update_check_command = (
            "insert"
            if table_name == "external_sync_current_records"
            else "update"
        )
        policies.append(
            (
                table_name,
                f"{table_name}_edge_update_0044",
                "w",
                EDGE_ROLE,
                _runtime_policy_expression(table_name, update_using_command),
                _runtime_policy_expression(table_name, update_check_command),
            )
        )
    policies.extend(
        (
            (
                "external_sync_current_records",
                "external_sync_current_records_edge_delete_0044",
                "d",
                EDGE_ROLE,
                _runtime_policy_expression(
                    "external_sync_current_records", "delete"
                ),
                None,
            ),
            (
                "audit_logs",
                "audit_logs_edge_insert_0044",
                "a",
                EDGE_ROLE,
                None,
                _runtime_policy_expression("audit_logs", "insert"),
            ),
        )
    )
    return tuple(policies)


EXPECTED_POLICIES = _expected_policies()


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_nullable_literal(value: str | None) -> str:
    return "NULL" if value is None else _sql_literal(value)


RECEIPT_TABLES = ("shipments", "oam_receipt_evidence", "oam_receipt_sync_scope_bindings")


def _receipt_policies() -> tuple[tuple[str, str, str, str, str | None, str | None], ...]:
    policies: list[tuple[str, str, str, str, str | None, str | None]] = []

    def add(table: str, command: str, capability: str) -> None:
        expression = (
            "rsc_oam_receipt_rls_check_0082("
            f"'{command}'::text, '{capability}'::text, '{table}'::text, to_jsonb({table}.*))"
        )
        policies.append((
            table, f"{table}_projector_{command}_receipt_0082",
            {"select": "r", "insert": "a", "update": "w"}[command], PROJECTOR_ROLE,
            None if command == "insert" else expression,
            None if command == "select" else expression,
        ))

    for table in (
        "external_sync_snapshots", "external_sync_current_records", "source_systems",
        "sync_runs", "external_objects", "external_object_mappings", "shipments",
        "oam_receipt_evidence",
    ):
        add(table, "select", "projector_read")
    add("sync_runs", "insert", "projector_write")
    add("sync_runs", "update", "projector_write")
    add("oam_receipt_evidence", "insert", "projector_write")
    for table in RECEIPT_TABLES:
        migrator_name = (
            "oam_receipt_sync_scope_bindings_migrator_0082"
            if table == "oam_receipt_sync_scope_bindings"
            else f"{table}_migrator_receipt_0082"
        )
        policies.append((table, migrator_name, "*", MIGRATION_ROLE, "true", "true"))
        policies.append((
            table, f"{table}_backup_select_receipt_0082", "r", BACKUP_ROLE, "true", None,
        ))
    for table in ("shipments", "oam_receipt_evidence"):
        suffix = "receipt_0082" if table == "shipments" else "0082"
        policies.append((table, f"{table}_api_select_{suffix}", "r", API_ROLE, "true", None))
        if table == "shipments":
            policies.append((table, f"{table}_api_insert_{suffix}", "a", API_ROLE, None, "true"))
    return tuple(policies)


RECEIPT_POLICIES = _receipt_policies()
_POLICY_VALUES = ",\n        ".join(
    "(" + ", ".join(_sql_nullable_literal(value) for value in policy) + ")"
    for policy in (*EXPECTED_POLICIES, *RECEIPT_POLICIES)
)
_TABLE_VALUES = ",\n        ".join(
    f"({_sql_literal(table_name)})" for table_name in (*RLS_TABLES, *RECEIPT_TABLES)
)


OAM_SYNC_FUNCTION_MANIFEST_0044 = {
    "rsc_oam_formal_scope_key_0044(text,text)": (
        True, "i", "sql", "text", True, "s",
        "520cc03d146ea720b367b3e074df9e2a846c00021bb5f25eab2b7cafba1ae60b",
    ),
    "rsc_oam_binding_allowed_0044(text,text,text,text,text,text,text,text)": (
        True, "s", "sql", "boolean", False, "u",
        "3a734c09e6ea4f16237b5f621c22ca4cbc40aff63e9cec827019e6cc88849a08",
    ),
    "rsc_oam_run_snapshot_allowed_0044(text,uuid,text,text,text,text,text)": (
        True, "s", "sql", "boolean", False, "u",
        "9d7fae157669187e5d174489127bd8c872047577bf6b674f1d1aa3358a705f54",
    ),
    (
        "rsc_oam_inbox_allowed_0044("
        "text,text,uuid,uuid,text,text,text,text,timestamptz,jsonb,text,text)"
    ): (
        True, "s", "sql", "boolean", False, "u",
        "515d3f8d675c1dcd2b7c07433fb3332163043af4a3fde90d6927f1681939239e",
    ),
    (
        "rsc_oam_external_object_allowed_0044("
        "text,text,uuid,uuid,text,text,uuid)"
    ): (
        True, "s", "sql", "boolean", False, "u",
        "2bdd9e5e37f7274daaf4a04f9cd3a72756c20d3638e471748d442f2d2762ddfa",
    ),
    (
        "rsc_oam_version_allowed_0044("
        "text,text,uuid,uuid,text,timestamptz,timestamptz,timestamptz,"
        "jsonb,text,boolean,timestamptz)"
    ): (
        True, "s", "sql", "boolean", False, "u",
        "72a73eafa76564fa99dd2d65ed74cd5ee5a3df15876f0fc40cf1d1cbf1a5af13",
    ),
    "rsc_oam_conflict_allowed_0044(text,uuid,uuid,uuid)": (
        True, "s", "sql", "boolean", False, "u",
        "69b00a149292d720adf476abef5a5d746c3c3c400bf1e1c60b4e21484eeb872d",
    ),
    "rsc_oam_organization_allowed_0044(uuid)": (
        True, "s", "sql", "boolean", False, "u",
        "b19827272bb5965b5c6ae78b0eae7953b36f0e5dbe98933d5931069cdbc95f9f",
    ),
    "rsc_oam_person_allowed_0044(uuid)": (
        True, "s", "sql", "boolean", False, "u",
        "f9af10110d558983855424b345d4cb16249693bc45c4d08f2558f7775ee26719",
    ),
    (
        "rsc_oam_work_order_allowed_0044("
        "text,text,uuid,text,uuid,uuid,text,"
        "timestamptz,timestamptz,timestamptz)"
    ): (
        True, "s", "sql", "boolean", False, "u",
        "13193c0619a3ce7deb34020c477b096af80d0a50eea62dc0d5c508cb6633c3bf",
    ),
    "rsc_oam_snapshot_transition_guard_0044()": (
        True, "v", "plpgsql", "trigger", False, "u",
        "c02173d12a88317f594c4d9e5d48681a8aaa30b6e19af09a7b9e007ccad04ca6",
    ),
    "rsc_oam_projection_chain_guard_0044()": (
        True, "v", "plpgsql", "trigger", False, "u",
        "9c32efa94e7b2589f26b295db6daae03818e781efe90c10bdaa47c6b8ff5c3f4",
    ),
    "rsc_oam_rls_check_0044(text,text,jsonb)": (
        False, "s", "plpgsql", "boolean", False, "u",
        "4c8edd83a081ed7c4257bd5c00e884bdb1e2af5e643170fcd425fb7598909973",
    ),
    "rsc_oam_runtime_binding_ready_0044()": (
        False, "s", "sql", "boolean", False, "u",
        "bb4b5e75c5ae91b8750d7086ebd03bbdbd2495763acffa5a0db5660324dc3364",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0045 = {
    **OAM_SYNC_FUNCTION_MANIFEST_0044,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_0044[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "f443316e40a66352f057bcc4fcc973facf58c86de7fcac79b3f4591a20b911b5",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0046 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0045,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0045[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "fe40e4f623bd42ea270b1177718cf26e3bca5eb331339f078e085bc965b3b459",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0047 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0046,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0046[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "51be9786e60dbafdf324db7a6b1f187e8b5839ba81e30178b170b525b525638b",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0048 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0047,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0047[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "b397b6d0662c60450810756eed05c240d16a5220a415d3bc9aa7d0e068cc02a3",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0049 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0048,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0048[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "fe87de8114ddd176dc5684333fdbc25a88300d564fd3813db1abbb01711b7539",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0050 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0049,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0049[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "5acd1d32b8f9db6e861224524da518515636fd802d4d33387a1b82b712c9c7a1",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0051 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0050,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0050[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "8764e8b910dff314b5a3e0478043c1145e228cbbd9ec70a1715426cd8ad714c3",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0052 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0051,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0051[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "7b87874563d011de3c6c02e598892700d4400dd0cccfee38c7f573068c319f12",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0053 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0052,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0052[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "f40e55742d5b70d0050beb09a1ef367bfd50e02f28b4c3e60d879bec6143b78d",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0054 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0053,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0053[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "9be94cc48bd9d15f84d237177f29b929126593426c3cba7b1ad1947a1e66c011",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0055 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0054,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0054[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "3f6b6b7a849746154cbdd54ff8c1d3153aa24faa3cf081b6e0d3d6ef832c7f70",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0056 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0055,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0055[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "449f0f8526292713d6344d07d25d35654b0c9967183ea83559e989dc982b04c7",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0057 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0056,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0056[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "9b97c355d0fcbb5ee4dfcf1e90fd76339b2eafd64f5f5b287a21aa3d364cfdc5",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0058 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0057,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0057[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "194c166aa7eeca78e70b9ab9376f060457d7e9715fd9bead34b3cf1703f32907",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0059 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0058,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0058[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "21859de4675a39652ffe7cda14887c2d644b05d208d32e361ecfad7f9dd4b963",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0060 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0059,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0059[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "47ede12ff8b812e78aa0bb325ce6445b3dd41f6d1fd512ee86055c7418e00bf3",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0061 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0060,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0060[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "900dd22c6ff47e807dddd6dd6110449e433dde3bc410057ad03472e7216bc53c",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0062 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0061,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0061[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "d019a572ad39a3570ca175479ee50a4a7987eff3a0655c5c910c5b83744ea04d",
    ),
}

# 0064 is a forward-only migration which advances the migration-owned
# readiness function while installing the finalizer organization lock.  Keep
# the historical 0062 manifest for downgrade/readback checks, but make the
# runtime manifest describe the actual head function body; otherwise the
# projector/edge RLS boundary proof rejects an otherwise valid 0064 catalog.
OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0064 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0062,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0062[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "b75bb3c37c279a2a406a36be9049a187e9cef61dfd0892a48a2db0e8aef551c0",
    ),
}

# 0063 is intentionally a linear child of the already released 0064
# migration.  Preserve the 0064 manifest for downgrade/readback checks while
# making the generic runtime manifest describe the actual 0063 head.
OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0063 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0064,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0064[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "dd43dabe3a816b44b888b4cda1fdcbc84eeefef8b659323b031fb80a55c82cc4",
    ),
}

# 0065-0067 add append-only stocktake capabilities without changing the
# readiness function.  0068 creates the first allocation facts and advances
# the migration-owned readiness marker atomically with those tables.
OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0068 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0063,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0063[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "b248454938a77c33684cc39652d8d709abfb3408d4a96d19036010fb0730292f",
    ),
}

# 0069 adds reservation facts and advances the migration-owned readiness
# marker.  The OAM sync function itself is unchanged; only its exact source
# revision coordinate moves forward.
OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0069 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0068,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0068[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "eee63dbc90cfe728d461f43c0ef824840fa20078723cf58776debb4750373e36",
    ),
}

OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0070 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0069,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0069["rsc_oam_runtime_binding_ready_0044()"][:6],
        "5c9734c3b500a4b1794fef3a66fa5cdeb38f98e6d7e7130209cf6f6f87a07bfa",
    ),
}
OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0071 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0070,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0070["rsc_oam_runtime_binding_ready_0044()"][:6],
        "6c20025dc2ca9d473724c743dae394f3526e24cdb73fa8689f99d8d053dc6a01",
    ),
}
OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0072 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0071,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0071["rsc_oam_runtime_binding_ready_0044()"][:6],
        "7bc4ca43bc203431db82c14473ec0d177831ff31ccf9c4711b142a74e6a1cd43",
    ),
}
OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0079 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0072,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0072[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "9dbb698d8c5f6f01883c8692a8a1e87204137a326948d840f3a191d26f40e3c0",
    ),
}
OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0080 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0079,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0079[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "39c99f7b25ea5cbb22befa6176e8ea6b0237ace130e3848792d2aa864edd3583",
    ),
}
OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0081 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0080,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0080[
            "rsc_oam_runtime_binding_ready_0044()"
        ][:6],
        "efb632b66d584cd8fe26414abd5420ddb59d7fbf97ad50e13864bb73237b14b6",
    ),
}
RECEIPT_RLS_SIGNATURE = "rsc_oam_receipt_rls_check_0082(text,text,text,jsonb)"
OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0082 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0081,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0081["rsc_oam_runtime_binding_ready_0044()"][:6],
        "c6318534d12f800c067ad8ced6c2700018545c95c50e8b5078c56f3037b06210",
    ),
    "rsc_guard_oam_receipt_evidence_immutable_0081()": (
        True, "v", "plpgsql", "trigger", False, "u",
        "297ec85aaea40a667a0bed41be94f58f374bbe554b1d857c910a69d67d9405c9",
    ),
    RECEIPT_RLS_SIGNATURE: (
        False, "s", "plpgsql", "boolean", False, "u",
        "80116140d0b4514b7c1ba4c16668d243fac03d51db86ed8788510b925224aa06",
    ),
}
OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0083 = {
    **OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0082,
    "rsc_oam_runtime_binding_ready_0044()": (
        *OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0082["rsc_oam_runtime_binding_ready_0044()"][:6],
        "88d751f169702542808c0d9def764b608b93774821e54bfbcaba177c236da838",
    ),
}
OAM_SYNC_FUNCTION_MANIFEST = OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0083

EXPECTED_TRIGGERS = (
    (
        "external_sync_snapshots",
        "trg_external_sync_snapshot_transition_0044",
        "rsc_oam_snapshot_transition_guard_0044()",
        19,
        False,
        False,
        False,
    ),
    (
        "external_objects",
        "trg_external_objects_chain_0044",
        "rsc_oam_projection_chain_guard_0044()",
        21,
        True,
        True,
        True,
    ),
    (
        "external_object_versions",
        "trg_external_object_versions_chain_0044",
        "rsc_oam_projection_chain_guard_0044()",
        21,
        True,
        True,
        True,
    ),
)

RECEIPT_TRIGGERS = (
    ("oam_receipt_evidence", "trg_oam_receipt_evidence_immutable_0081",
     "rsc_guard_oam_receipt_evidence_immutable_0081()", 27, False, False, False),
)

_FUNCTION_VALUES = ",\n        ".join(
    "(" + ", ".join(
        (
            _sql_literal(signature),
            str(definition[0]).upper(),
            _sql_literal(definition[1]),
            _sql_literal(definition[2]),
            _sql_literal(definition[3]),
            str(definition[4]).upper(),
            _sql_literal(definition[5]),
            _sql_literal(definition[6]),
        )
    ) + ")"
    for signature, definition in OAM_SYNC_FUNCTION_MANIFEST.items()
)
_TRIGGER_VALUES = ",\n        ".join(
    "(" + ", ".join(
        (
            _sql_literal(table_name),
            _sql_literal(trigger_name),
            _sql_literal(function_signature),
            str(trigger_type),
            str(is_constraint).upper(),
            str(is_deferrable).upper(),
            str(is_initially_deferred).upper(),
        )
    ) + ")"
    for (
        table_name,
        trigger_name,
        function_signature,
        trigger_type,
        is_constraint,
        is_deferrable,
        is_initially_deferred,
    ) in (*EXPECTED_TRIGGERS, *RECEIPT_TRIGGERS)
)
_TRIGGER_TABLE_VALUES = ",\n        ".join(
    f"({_sql_literal(table_name)})"
    for table_name in sorted({row[0] for row in (*EXPECTED_TRIGGERS, *RECEIPT_TRIGGERS)})
)


RECEIPT_BINDING_CONSTRAINTS = {
    "ck_oam_receipt_binding_capability_0082": (
        "CHECK ((capability = ANY (ARRAY['projector_read'::text, 'projector_write'::text])))"
    ),
    "ck_oam_receipt_binding_entity_0082": (
        "CHECK ((entity_type = 'oam_receipt'::text))"
    ),
    "ck_oam_receipt_binding_principal_0082": (
        "CHECK ((principal_name = 'star_oam_projector'::text))"
    ),
    "ck_oam_receipt_binding_scope_0082": (
        "CHECK ((scope_key ~ '^oam-receipts:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'::text))"
    ),
    "ck_oam_receipt_binding_source_0082": (
        "CHECK ((source_system = 'starcharge_oam'::text))"
    ),
    "oam_receipt_sync_scope_bindings_pkey": (
        'PRIMARY KEY (id)'
    ),
    "uq_oam_receipt_binding_coordinate_0082": (
        'UNIQUE (principal_name, capability, source_system, source_instance, scope_key, company_id, org_code, entity_type)'
    ),
    "ck_oam_receipt_binding_company_0082": (
        'CHECK (((length(TRIM(BOTH FROM company_id)) >= 1) AND (length(TRIM(BOTH FROM company_id)) <= 80)))'
    ),
    "ck_oam_receipt_binding_instance_0082": (
        'CHECK (((length(TRIM(BOTH FROM source_instance)) >= 1) AND (length(TRIM(BOTH FROM source_instance)) <= 128)))'
    ),
    "ck_oam_receipt_binding_org_0082": (
        'CHECK (((length(TRIM(BOTH FROM org_code)) >= 1) AND (length(TRIM(BOTH FROM org_code)) <= 80)))'
    ),
}
_RECEIPT_CONSTRAINT_VALUES = ",\n".join(
    f"({_sql_literal(name)}, {_sql_literal(definition)})"
    for name, definition in RECEIPT_BINDING_CONSTRAINTS.items()
)

_RLS_BOUNDARY_SQL = text(
    f"""
WITH
required_tables(table_name) AS (
    VALUES
        {_TABLE_VALUES}
),
expected_policies(
    table_name,
    policy_name,
    command_code,
    role_name,
    using_expression,
    check_expression
) AS (
    VALUES
        {_POLICY_VALUES}
),
actual_policies AS (
    SELECT
        table_row.relname AS table_name,
        policy.polname AS policy_name,
        policy.polcmd AS command_code,
        policy.polpermissive AS is_permissive,
        ARRAY(
            SELECT pg_catalog.pg_get_userbyid(role_oid)
              FROM pg_catalog.unnest(policy.polroles) AS role_row(role_oid)
             ORDER BY 1
        ) AS role_names,
        pg_catalog.pg_get_expr(policy.polqual, policy.polrelid)
            AS using_expression,
        pg_catalog.pg_get_expr(policy.polwithcheck, policy.polrelid)
            AS check_expression
      FROM pg_catalog.pg_policy AS policy
      JOIN pg_catalog.pg_class AS table_row ON table_row.oid = policy.polrelid
      JOIN pg_catalog.pg_namespace AS schema_row
        ON schema_row.oid = table_row.relnamespace
     WHERE schema_row.nspname = 'public'
       AND table_row.relname IN (SELECT table_name FROM required_tables)
),
expected_functions(
    signature,
    is_private,
    volatility,
    language_name,
    result_type,
    is_strict,
    parallel_safety,
    source_sha256
) AS (
    VALUES
        {_FUNCTION_VALUES}
),
required_trigger_tables(table_name) AS (
    VALUES
        {_TRIGGER_TABLE_VALUES}
),
expected_triggers(
    table_name,
    trigger_name,
    function_signature,
    trigger_type,
    is_constraint,
    is_deferrable,
    is_initially_deferred
) AS (
    VALUES
        {_TRIGGER_VALUES}
),
actual_oam_functions AS (
    SELECT function_row.oid
      FROM pg_catalog.pg_proc AS function_row
      JOIN pg_catalog.pg_namespace AS schema_row
        ON schema_row.oid = function_row.pronamespace
     WHERE schema_row.nspname = 'public'
       AND (function_row.proname ~ '^rsc_oam_[[:alnum:]_]+_0044$'
            OR function_row.proname IN ('rsc_oam_receipt_rls_check_0082',
                'rsc_guard_oam_receipt_evidence_immutable_0081'))
),
actual_runtime_triggers AS (
    SELECT
        table_row.relname AS table_name,
        trigger_row.tgname AS trigger_name,
        trigger_row.tgfoid AS function_id,
        trigger_row.tgenabled AS enabled,
        trigger_row.tgtype::integer AS trigger_type,
        trigger_row.tgconstraint <> 0 AS is_constraint,
        trigger_row.tgdeferrable AS is_deferrable,
        trigger_row.tginitdeferred AS is_initially_deferred,
        trigger_row.tgisinternal AS is_internal,
        trigger_row.tgnargs AS argument_count,
        trigger_row.tgqual IS NOT NULL AS has_when_clause,
        trigger_row.tgattr::text <> '' AS has_column_filter,
        trigger_row.tgparentid <> 0 AS has_parent,
        trigger_row.tgoldtable IS NOT NULL AS has_old_transition_table,
        trigger_row.tgnewtable IS NOT NULL AS has_new_transition_table
      FROM pg_catalog.pg_trigger AS trigger_row
      JOIN pg_catalog.pg_class AS table_row
        ON table_row.oid = trigger_row.tgrelid
      JOIN pg_catalog.pg_namespace AS schema_row
        ON schema_row.oid = table_row.relnamespace
     WHERE schema_row.nspname = 'public'
       AND table_row.relname IN (
           SELECT table_name FROM required_trigger_tables
       )
       AND NOT trigger_row.tgisinternal
),
receipt_binding_constraints AS (
    SELECT NOT EXISTS (
        SELECT 1 FROM (VALUES {_RECEIPT_CONSTRAINT_VALUES}) expected(name, definition)
        FULL JOIN (
            SELECT conname, pg_catalog.pg_get_constraintdef(oid) definition, convalidated
              FROM pg_catalog.pg_constraint
             WHERE conrelid = pg_catalog.to_regclass('public.oam_receipt_sync_scope_bindings')
        ) actual ON actual.conname = expected.name
        WHERE actual.conname IS NULL OR expected.name IS NULL
           OR actual.definition IS DISTINCT FROM expected.definition
           OR actual.convalidated IS NOT TRUE
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles role_row
         WHERE role_row.rolname NOT IN (:migration_role, 'pg_write_all_data') AND NOT role_row.rolsuper
           AND (pg_catalog.has_any_column_privilege(role_row.oid,
                   'public.oam_receipt_sync_scope_bindings', 'INSERT,UPDATE,REFERENCES')
                OR pg_catalog.has_table_privilege(role_row.oid,
                   'public.oam_receipt_sync_scope_bindings', 'DELETE,TRUNCATE,TRIGGER'))
    ) AS passed
),
table_boundary AS (
    SELECT NOT EXISTS (
        SELECT 1
          FROM required_tables AS required
          LEFT JOIN pg_catalog.pg_class AS table_row
            ON table_row.oid = pg_catalog.to_regclass(
                pg_catalog.format('public.%I', required.table_name)
            )
         WHERE table_row.oid IS NULL
            OR table_row.relkind NOT IN ('r', 'p')
            OR NOT table_row.relrowsecurity
            OR NOT table_row.relforcerowsecurity
            OR pg_catalog.pg_get_userbyid(table_row.relowner)
               <> :migration_role
    ) AS passed
),
policy_boundary AS (
    SELECT NOT EXISTS (
        SELECT 1
          FROM expected_policies AS expected
          FULL JOIN actual_policies AS actual
            ON actual.table_name = expected.table_name
           AND actual.policy_name = expected.policy_name
         WHERE expected.policy_name IS NULL
            OR actual.policy_name IS NULL
            OR actual.command_code <> expected.command_code
            OR actual.is_permissive IS NOT TRUE
            OR actual.role_names <> ARRAY[expected.role_name]::name[]
            OR actual.using_expression
               IS DISTINCT FROM expected.using_expression
            OR actual.check_expression
               IS DISTINCT FROM expected.check_expression
    ) AS passed
),
function_boundary AS (
    SELECT NOT EXISTS (
        SELECT 1
          FROM expected_functions AS expected
          LEFT JOIN pg_catalog.pg_proc AS function_row
            ON function_row.oid = pg_catalog.to_regprocedure(
                'public.' || expected.signature
            )
          LEFT JOIN pg_catalog.pg_language AS language_row
            ON language_row.oid = function_row.prolang
         WHERE function_row.oid IS NULL
            OR pg_catalog.pg_get_userbyid(function_row.proowner)
               <> :migration_role
            OR function_row.prokind <> 'f'
            OR NOT function_row.prosecdef
            OR function_row.provolatile <> expected.volatility
            OR function_row.proleakproof
            OR language_row.lanname <> expected.language_name
            OR pg_catalog.format_type(function_row.prorettype, NULL)
               <> expected.result_type
            OR function_row.proretset
            OR function_row.proargmodes IS NOT NULL
            OR function_row.pronargdefaults <> 0
            OR function_row.proisstrict IS DISTINCT FROM expected.is_strict
            OR function_row.proparallel <> expected.parallel_safety
            OR function_row.proconfig IS DISTINCT FROM
               ARRAY['search_path=pg_catalog']::text[]
            OR pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                   ),
                   'hex'
               ) <> expected.source_sha256
            OR pg_catalog.has_function_privilege(
                '{API_ROLE}', function_row.oid, 'EXECUTE'
            )
            OR pg_catalog.has_function_privilege(
                '{BACKUP_ROLE}', function_row.oid, 'EXECUTE'
            )
            OR pg_catalog.has_function_privilege(
                '{PROJECTOR_ROLE}', function_row.oid, 'EXECUTE'
            ) IS DISTINCT FROM NOT expected.is_private
            OR pg_catalog.has_function_privilege(
                '{EDGE_ROLE}', function_row.oid, 'EXECUTE'
            ) IS DISTINCT FROM (NOT expected.is_private
                AND expected.signature <> '{RECEIPT_RLS_SIGNATURE}')
            OR EXISTS (
                SELECT 1
                  FROM pg_catalog.aclexplode(
                      COALESCE(
                          function_row.proacl,
                          pg_catalog.acldefault('f', function_row.proowner)
                      )
                  ) AS function_acl
                 WHERE function_acl.grantee = 0
                    OR function_acl.privilege_type <> 'EXECUTE'
                    OR (
                        function_acl.grantee <> function_row.proowner
                        AND function_acl.is_grantable
                    )
                    OR NOT (
                        function_acl.grantee = function_row.proowner
                        OR (
                            NOT expected.is_private
                            AND EXISTS (
                                SELECT 1
                                  FROM pg_catalog.pg_roles AS allowed_role
                                 WHERE allowed_role.oid = function_acl.grantee
                                   AND allowed_role.rolname IN (
                                       '{EDGE_ROLE}', '{PROJECTOR_ROLE}'
                                   )
                                   AND (allowed_role.rolname <> '{EDGE_ROLE}'
                                        OR expected.signature <> '{RECEIPT_RLS_SIGNATURE}')
                            )
                        )
                    )
            )
            OR (
                SELECT pg_catalog.count(*)
                  FROM pg_catalog.aclexplode(
                      COALESCE(
                          function_row.proacl,
                          pg_catalog.acldefault('f', function_row.proowner)
                      )
                  ) AS exact_function_acl
                 WHERE exact_function_acl.privilege_type = 'EXECUTE'
            ) <> CASE WHEN expected.is_private THEN 1
                     WHEN expected.signature = '{RECEIPT_RLS_SIGNATURE}' THEN 2 ELSE 3 END
    ) AS passed
),
function_roster_boundary AS (
    SELECT NOT EXISTS (
        SELECT 1
          FROM expected_functions AS expected
          FULL JOIN actual_oam_functions AS actual
            ON actual.oid = pg_catalog.to_regprocedure(
                'public.' || expected.signature
            )
         WHERE expected.signature IS NULL
            OR actual.oid IS NULL
    ) AS passed
),
runtime_execute_boundary AS (
    SELECT NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = function_row.pronamespace
         WHERE schema_row.nspname = 'public'
           AND (
               pg_catalog.has_function_privilege(
                   '{EDGE_ROLE}', function_row.oid, 'EXECUTE'
               )
               OR pg_catalog.has_function_privilege(
                   '{PROJECTOR_ROLE}', function_row.oid, 'EXECUTE'
               )
           )
           AND function_row.oid NOT IN (
               pg_catalog.to_regprocedure(
                   'public.rsc_oam_rls_check_0044(text,text,jsonb)'
               ),
               pg_catalog.to_regprocedure(
                   'public.rsc_oam_runtime_binding_ready_0044()'
               ),
               pg_catalog.to_regprocedure(
                   'public.rsc_oam_receipt_rls_check_0082(text,text,text,jsonb)'
               )
           )
    ) AS passed
),
trigger_boundary AS (
    SELECT NOT EXISTS (
        SELECT 1
          FROM expected_triggers AS expected
          FULL JOIN actual_runtime_triggers AS actual
            ON actual.table_name = expected.table_name
           AND actual.trigger_name = expected.trigger_name
         WHERE expected.trigger_name IS NULL
            OR actual.trigger_name IS NULL
            OR actual.function_id IS DISTINCT FROM
               pg_catalog.to_regprocedure(
                   'public.' || expected.function_signature
               )::oid
            OR actual.enabled <> CASE WHEN expected.table_name = 'oam_receipt_evidence' THEN 'A' ELSE 'O' END
            OR actual.trigger_type <> expected.trigger_type
            OR actual.is_constraint IS DISTINCT FROM expected.is_constraint
            OR actual.is_deferrable IS DISTINCT FROM expected.is_deferrable
            OR actual.is_initially_deferred IS DISTINCT FROM
               expected.is_initially_deferred
            OR actual.is_internal
            OR actual.argument_count <> 0
            OR actual.has_when_clause
            OR actual.has_column_filter
            OR actual.has_parent
            OR actual.has_old_transition_table
            OR actual.has_new_transition_table
    ) AS passed
),
session_boundary AS (
    SELECT
        current_user = :runtime_role
        AND session_user = :runtime_role
        AND :runtime_role IN ('{EDGE_ROLE}', '{PROJECTOR_ROLE}')
        AND pg_catalog.current_setting('row_security') = 'on'
        AND NOT pg_catalog.has_table_privilege(
            current_user,
            'public.oam_sync_scope_bindings',
            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
        )
        AND NOT pg_catalog.has_any_column_privilege(
            current_user,
            'public.oam_sync_scope_bindings',
            'SELECT,INSERT,UPDATE,REFERENCES'
        )
        AS passed
),
revision_and_binding_boundary AS (
    SELECT public.rsc_oam_runtime_binding_ready_0044() AS passed
),
check_results(check_name, passed) AS (
    SELECT 'forced_tables', passed FROM table_boundary
    UNION ALL SELECT 'receipt_binding_constraints', passed FROM receipt_binding_constraints
    UNION ALL SELECT 'policy_closure', passed FROM policy_boundary
    UNION ALL SELECT 'function_closure', passed FROM function_boundary
    UNION ALL SELECT 'function_roster', passed FROM function_roster_boundary
    UNION ALL SELECT 'runtime_execute', passed FROM runtime_execute_boundary
    UNION ALL SELECT 'trigger_closure', passed FROM trigger_boundary
    UNION ALL SELECT 'session_binding', passed FROM session_boundary
    UNION ALL SELECT 'revision_and_binding', passed
      FROM revision_and_binding_boundary
)
SELECT
    COALESCE(pg_catalog.bool_and(passed), FALSE) AS boundary_ok,
    COALESCE(
        pg_catalog.string_agg(check_name, ',' ORDER BY check_name)
            FILTER (WHERE NOT COALESCE(passed, FALSE)),
        ''
    ) AS boundary_failures
FROM check_results
"""
)


def read_oam_sync_scope_boundary(
    connection: Connection,
    *,
    expected_role: str,
    expected_migration_role: str = MIGRATION_ROLE,
) -> Mapping[str, object] | None:
    """Return one fail-closed catalog proof row without changing database state."""

    return connection.execute(
        _RLS_BOUNDARY_SQL,
        {
            "runtime_role": expected_role,
            "migration_role": expected_migration_role,
        },
    ).mappings().one_or_none()


def oam_sync_scope_boundary_passed(
    row: Mapping[str, object] | None,
) -> bool:
    return bool(
        row is not None
        and row.get("boundary_ok") is True
        and row.get("boundary_failures") == ""
    )


__all__ = [
    "EXPECTED_POLICIES",
    "EXPECTED_TRIGGERS",
    "MIGRATION_ROLE",
    "OAM_SYNC_FUNCTION_MANIFEST",
    "OAM_SYNC_FUNCTION_MANIFEST_0044",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0045",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0046",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0047",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0048",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0049",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0050",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0051",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0052",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0053",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0054",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0055",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0056",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0057",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0058",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0059",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0060",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0061",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0062",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0064",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0063",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0068",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0069",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0070",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0071",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0072",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0079",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0080",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0081",
    "OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0082",
    "RLS_REVISION",
    "RLS_TABLES",
    "_RLS_BOUNDARY_SQL",
    "oam_sync_scope_boundary_passed",
    "read_oam_sync_scope_boundary",
]
