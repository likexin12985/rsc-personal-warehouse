"""One complete application model for facts, seals and both key registries.

The immutable Alembic transition never imports this model. Formal Base
registration is an explicit operation, coordinated with revision/readiness.
"""
from sqlalchemy import MetaData
from app import stock_scrap_persistence_schema as facts
from app import stock_scrap_binding_schema as bindings

FACT_NAMES = ('stock_scrap_lines', 'stock_scrap_serials', 'stock_scrap_files',
    'stock_scrap_recovery_requests', 'stock_scrap_recovery_files',
    'stock_scrap_recovery_regional_reviews', 'stock_scrap_recovery_headquarters_reviews',
    'stock_scrap_recovery_executions')
PARENT_NAMES = ('stock_operation_orders', 'stock_loss_dispositions',
    'stock_loss_correction_executions', 'stock_loss_disposition_reversals')
SEAL_NAME = 'stock_scrap_request_seals'
TABLE_NAMES = (*FACT_NAMES, bindings.NAME, SEAL_NAME)
PREDECESSOR_NAMES = (*PARENT_NAMES, 'stock_loss_request_key_bindings')
_previous_parents = None


def clone(metadata, *, exclude=()):
    copied = MetaData()
    for table in metadata.tables.values():
        if table.name not in exclude:
            table.to_metadata(copied)
    copy_conditions(metadata, copied)
    return copied


def copy_conditions(metadata, copied):
    # SQLAlchemy does not propagate conditional dialect DDL to copied tables.
    for name, original in metadata.tables.items():
        if name not in copied.tables:
            continue
        for constraint in original.constraints:
            rule = constraint._ddl_if
            if rule is None:
                continue
            matches = [item for item in copied.tables[name].constraints if type(item) is type(constraint)
                and item.name == constraint.name and str(getattr(item, 'sqltext', '')) == str(getattr(constraint, 'sqltext', ''))]
            if len(matches) != 1:
                raise ValueError('exact copied dialect constraint required')
            matches[0].ddl_if(dialect=rule.dialect, callable_=rule.callable_, state=rule.state)


def register(metadata):
    """Install all ten models together; never accept a partially built set."""
    from app.stock_scrap_seal_schema import define as seals
    if any(name in metadata.tables for name in TABLE_NAMES):
        raise ValueError('complete scrap schema must be registered once')
    from app.database import Base
    global _previous_parents
    if metadata is Base.metadata:
        # Retain only five old table definitions for explicit historical
        # schema-tooling tests. Frozen migrations never use this view.
        _previous_parents = clone(metadata, exclude=set(metadata.tables) - set(PREDECESSOR_NAMES))
    tables = facts.define(metadata, allow_base=True)
    parents = facts.extend_parents(metadata, allow_base=True)
    bindings.extend_legacy(metadata)
    bindings.define(metadata)
    seals(metadata, allow_base=True)
    return tables, parents


def predecessor_schema():
    """Explicit 0164 metadata for historical tooling, never a live fallback."""
    from app import models
    from app.database import Base
    if _previous_parents is None:
        if set(TABLE_NAMES).intersection(Base.metadata.tables):
            raise ValueError('0164 predecessor metadata unavailable')
        return clone(Base.metadata)
    from app.return_condition_application_schema import predecessor_schema as before_condition
    copied = clone(before_condition(), exclude=(*TABLE_NAMES, *PREDECESSOR_NAMES))
    for table in _previous_parents.tables.values():
        table.to_metadata(copied)
    copy_conditions(_previous_parents, copied)
    return copied


def build_schema():
    from app import models
    from app.database import Base
    metadata = clone(Base.metadata)
    present = set(TABLE_NAMES).intersection(metadata.tables)
    if present:
        if present != set(TABLE_NAMES):
            raise ValueError('incomplete live scrap schema')
        return metadata, tuple(metadata.tables[n] for n in FACT_NAMES), tuple(metadata.tables[n] for n in PARENT_NAMES)
    tables, parents = register(metadata)
    return metadata, tables, parents
