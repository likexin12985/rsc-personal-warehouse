"""Add purpose-bound, recipient-owned evidence for receipt exceptions."""
from pathlib import Path
import runpy

from alembic import op

revision = "20260926_0086"
down_revision = "20260925_0085"
branch_labels = depends_on = None
PURPOSE = "receipt_exception_evidence"
OLD_HASH = "c5b754cc9ddf1a180c9f52414b7ec5a9a5ddb9341e42446a95b59e8c985d80a1"
NEW_HASH = "01d934aa197b9dfcd72520f0b776be07abc3b8cedcf9dfc206533a0eac789710"
FUNCTION_HASHES = {
    ("rsc_guard_formal_file_object_0036", ""): (
        "b40aec00c7dda886b556b309adfff1d660c03280295a559a5fc452bb106f4b4d", "76401f3c71eacdbac4c4b4d07e5a49f1159cc2cea878933df36e817c9a85e39f"),
    ("rsc_guard_formal_file_binding_0036", ""): (
        "dc4d335bf59b02c6bff7714f523db54b1a1cd2f92669b34840552967f29dff65", "9ad4d0b6ee9d9a4bab3cd460ca7553adb9df6c8e0040b97480019ca103696a38"),
}
TRIGGER = "trg_receipt_exceptions_evidence_guard_0086"


def _files(expanded=False):
    namespace = runpy.run_path(str(Path(__file__).with_name("20260901_0036_formal_file_runtime_boundary.py")))
    if expanded:
        globals_ = namespace["_postgresql_file_function_sql"].__globals__
        globals_["FORMAL_PURPOSES_SQL"] = globals_["FORMAL_PURPOSES_SQL"].replace("'stocktake_evidence')", f"'stocktake_evidence', '{PURPOSE}')")
    return namespace


def binding_sql(dialect, alias="NEW", current=True):
    m = _files(True)
    prefix = "public." if dialect == "postgresql" else ""
    binding = m[f"_{dialect}_binding_file_sql"](
        file_expression=f"{alias}.evidence_file_id", purpose=PURPOSE,
        user_expression="uploader.id", person_expression="receipt.receiver_person_id",
        bound_at_expression=f"{alias}.created_at", require_current_identity=current,
    )
    return f"""EXISTS (
        SELECT 1 FROM {prefix}receipts receipt
        JOIN {prefix}receipt_lines line ON line.receipt_id = receipt.id
        JOIN {prefix}shipments shipment ON shipment.id = receipt.shipment_id
        JOIN {prefix}shipment_lines source_line ON source_line.id = line.shipment_line_id
            AND source_line.shipment_id = shipment.id
        JOIN {prefix}users uploader ON uploader.person_id = receipt.receiver_person_id
        WHERE receipt.id = {alias}.receipt_id AND line.id = {alias}.receipt_line_id
          AND line.condition = {alias}.exception_type
          AND receipt.status IN ('accepted', 'exception')
          AND shipment.target_person_id = receipt.receiver_person_id
          AND ({binding})
    ) AND NOT EXISTS (
        SELECT 1 FROM {prefix}receipt_exceptions other
        WHERE other.evidence_file_id = {alias}.evidence_file_id
          AND other.receipt_id <> {alias}.receipt_id
    )"""


def source_changes():
    old, new = _files(), _files(True)
    branch = f"""    IF TG_TABLE_NAME = 'receipt_exceptions' THEN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'receipt evidence is append-only';
        END IF;
        IF NEW.evidence_file_id IS NULL THEN
            IF NEW.exception_type <> 'normal' THEN
                RAISE EXCEPTION 'abnormal receipt requires evidence' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
        PERFORM id FROM public.files WHERE id = NEW.evidence_file_id FOR UPDATE;
        IF NOT ({binding_sql('postgresql')}) THEN
            RAISE EXCEPTION 'receipt evidence does not match its recipient and purpose' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
"""
    result = {}
    for name, factory in (("rsc_guard_formal_file_object_0036", "_postgresql_file_function_sql"), ("rsc_guard_formal_file_binding_0036", "_postgresql_binding_function_sql")):
        body = lambda sql: sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
        before, after = body(old[factory]()), body(new[factory]())
        if name == "rsc_guard_formal_file_binding_0036":
            after = after.replace("    RAISE EXCEPTION 'formal file binding target is invalid';", branch + "    RAISE EXCEPTION 'formal file binding target is invalid';")
        result[(name, "")] = ((before, after),)
    return result


def _postgresql(upgrade):
    previous = runpy.run_path(str(Path(__file__).with_name("20260912_0072_outbound_postings.py")))
    replace = previous["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
    for coordinate, pairs in source_changes().items():
        before, after = FUNCTION_HASHES[coordinate]
        replace(signature=f"public.{coordinate[0]}()", expected_hash=before if upgrade else after,
                replacement_hash=after if upgrade else before,
                replacements=pairs if upgrade else tuple((b, a) for a, b in pairs), label="receipt_files_0086")
    replace(signature="public.rsc_oam_runtime_binding_ready_0044()", expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,
            replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),), label="receipt_files_readiness_0086")
    if upgrade:
        op.execute(f"CREATE TRIGGER {TRIGGER} BEFORE INSERT ON public.receipt_exceptions FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_formal_file_binding_0036()")
        op.execute(f"ALTER TABLE public.receipt_exceptions ENABLE ALWAYS TRIGGER {TRIGGER}")
    else:
        op.execute(f"DROP TRIGGER {TRIGGER} ON public.receipt_exceptions")


def _sqlite(upgrade):
    m = _files(upgrade)
    for name in m["_sqlite_trigger_names"]():
        op.execute(f"DROP TRIGGER {name}")
    m["_create_sqlite_triggers"]()
    if upgrade:
        op.execute(f"""CREATE TRIGGER {TRIGGER} BEFORE INSERT ON receipt_exceptions
            WHEN (NEW.evidence_file_id IS NULL AND NEW.exception_type <> 'normal')
              OR (NEW.evidence_file_id IS NOT NULL AND NOT ({binding_sql('sqlite')}))
            BEGIN SELECT RAISE(ABORT, 'receipt evidence does not match its recipient and purpose'); END""")
    else:
        op.execute(f"DROP TRIGGER {TRIGGER}")


def _transition(upgrade):
    db = op.get_bind()
    dialect = db.dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0086 supports PostgreSQL and SQLite")
    if dialect == "sqlite":
        _files()["_ensure_sqlite_migration_transaction"]()
    prefix = "public." if dialect == "postgresql" else ""
    if dialect == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE public.files, public.receipt_exceptions IN ACCESS EXCLUSIVE MODE")
    # No silent relabelling of earlier available legacy files as formal proof.
    predicate = f"EXISTS (SELECT 1 FROM {prefix}receipt_exceptions WHERE evidence_file_id IS NOT NULL OR exception_type <> 'normal')"
    if not upgrade:
        purpose = "metadata_jsonb->>'purpose'" if dialect == "postgresql" else "json_extract(metadata_jsonb, '$.purpose')"
        predicate += f" OR EXISTS (SELECT 1 FROM {prefix}files WHERE {purpose} = '{PURPOSE}')"
    message = "0086 upgrade blocked: existing receipt evidence requires reviewed migration" if upgrade else "0086 downgrade blocked: receipt evidence or upload intents exist"
    if dialect == "postgresql":
        op.execute(f"DO $$ BEGIN IF {predicate} THEN RAISE EXCEPTION '{message}'; END IF; END $$")
        _postgresql(upgrade)
    else:
        if db.exec_driver_sql(f"SELECT {predicate}").scalar():
            raise RuntimeError(message)
        _sqlite(upgrade)


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
