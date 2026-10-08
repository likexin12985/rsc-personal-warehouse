"""All condition facts, original inputs and both permanent request closures.

Formal registration preserves mapped parent table identity. Frozen migrations
never import these definitions; historical tools request the explicit 0166 view.
"""
from app.stock_scrap_schema import clone, copy_conditions
from app import return_condition_schema as facts
from app import return_condition_request_schema as submission
from app import return_condition_key_schema as keys
from app import return_condition_seal_schema as seals
from app import return_condition_settlement_input_schema as settlement
from app import return_condition_decision_seal_schema as decisions

TABLE_NAMES = (*facts.TABLE_NAMES, submission.NAME, keys.NAME, seals.NAME,
               settlement.NAME, settlement.SCANS, decisions.NAME)
PARENT_NAMES = facts.PARENT_NAMES
_previous_parents = None


def _define(metadata, *, allow_base=False):
    tables, parents = facts.define(metadata, allow_base=allow_base)
    tables = (*tables, submission.define(metadata, allow_base=allow_base),
              keys.define(metadata, allow_base=allow_base), seals.define(metadata, allow_base=allow_base),
              *settlement.define(metadata, allow_base=allow_base),
              decisions.define(metadata, allow_base=allow_base))
    return tables, parents


def register(metadata):
    """Validate the whole predecessor before changing shared mapped tables."""
    if set(TABLE_NAMES).intersection(metadata.tables):
        raise ValueError('complete condition schema must be registered once')
    _define(clone(metadata))
    from app.database import Base
    global _previous_parents
    saved = clone(metadata, exclude=set(metadata.tables) - set(PARENT_NAMES))
    result = _define(metadata, allow_base=True)
    if metadata is Base.metadata:
        _previous_parents = saved
    return result


def predecessor_schema():
    """Explicit schema-tooling snapshot; never a fallback for missing live facts."""
    from app import models  # noqa: F401
    from app.database import Base
    present = set(TABLE_NAMES).intersection(Base.metadata.tables)
    if _previous_parents is None:
        if present:
            raise ValueError('0166 predecessor metadata unavailable')
        return clone(Base.metadata)
    if present != set(TABLE_NAMES):
        raise ValueError('incomplete live condition schema')
    copied = clone(Base.metadata, exclude=(*TABLE_NAMES, *PARENT_NAMES))
    for table in _previous_parents.tables.values():
        table.to_metadata(copied)
    copy_conditions(_previous_parents, copied)
    return copied


def build_schema():
    from app import models  # noqa: F401
    from app.database import Base
    metadata = clone(Base.metadata)
    present = set(TABLE_NAMES).intersection(metadata.tables)
    if present:
        if present != set(TABLE_NAMES):
            raise ValueError('incomplete live condition schema')
        return metadata, tuple(metadata.tables[n] for n in TABLE_NAMES), tuple(metadata.tables[n] for n in PARENT_NAMES)
    tables, parents = _define(metadata)
    return metadata, tables, parents
