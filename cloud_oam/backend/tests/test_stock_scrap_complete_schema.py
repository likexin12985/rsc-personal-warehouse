"""Full model identity and real relational registry constraints; not release proof."""
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, event, select, func
from sqlalchemy.exc import IntegrityError

from app.database import Base
from app.stock_scrap_schema import build_schema, clone, register, predecessor_schema, TABLE_NAMES
from app.stock_scrap_binding_schema import NAME, ALIASES, TYPED
from app.formal_services.stock_scrap import binding_reads, lookup_coordinates, seal_reads
from app.formal_services.stock_scrap.tables import tables


def test_all_write_and_read_handles_share_the_complete_model():
    shared = tables()
    assert set(TABLE_NAMES) <= set(shared)
    assert binding_reads.REGISTRY is shared[NAME]
    assert lookup_coordinates.REGISTRY is shared['stock_loss_request_key_bindings']
    assert seal_reads.table() is shared['stock_scrap_request_seals']
    assert all(shared[name].metadata is binding_reads.REGISTRY.metadata for name in TABLE_NAMES)
    assert {'recovery_key_hash', 'scrap_key_hash'} <= set(lookup_coordinates.REGISTRY.c.keys())
    assert len(binding_reads.REGISTRY.foreign_key_constraints) == 7
    assert set(TABLE_NAMES) <= set(Base.metadata.tables)
    previous = predecessor_schema()
    assert not set(TABLE_NAMES).intersection(previous.tables)
    assert 'scrap_key_hash' not in previous.tables['stock_loss_request_key_bindings'].c


def test_complete_registration_is_not_partial_or_repeated_and_clone_keeps_dialect_guards():
    metadata, _, _ = build_schema()
    with pytest.raises(ValueError, match='registered once'):
        register(metadata)
    copied = clone(metadata)
    for name in (*TABLE_NAMES, 'stock_loss_request_key_bindings'):
        original = {(c.name, str(getattr(c, 'sqltext', '')), c._ddl_if.dialect)
            for c in metadata.tables[name].constraints if c._ddl_if is not None}
        actual = {(c.name, str(getattr(c, 'sqltext', '')), c._ddl_if.dialect)
            for c in copied.tables[name].constraints if c._ddl_if is not None}
        assert actual == original


@pytest.fixture
def relational_registry():
    metadata, _, _ = build_schema()
    registry = metadata.tables[NAME]
    # Minimal real parent keys isolate the registry's own relational contract.
    # Native business gates separately prove approvals, history and provenance.
    parent_columns = {element.column.table.name: element.column for fk in registry.foreign_key_constraints for element in fk.elements}
    stubs = MetaData()
    parents = {name: Table(name, stubs, Column('id', column.type, primary_key=True))
        for name, column in parent_columns.items()}
    ids = {name: str(uuid4()) if name == 'users' else uuid4() for name in parents}
    engine = create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def enforce(dbapi, _):
        dbapi.execute('PRAGMA foreign_keys=ON')
    stubs.create_all(engine)
    registry.create(engine)
    with engine.begin() as db:
        for name, table in parents.items():
            db.execute(table.insert(), dict(id=ids[name]))
    with engine.connect() as db:
        yield db, registry, ids
    engine.dispose()


def record(ids, kind):
    targets = dict(original='stock_loss_dispositions', apply='stock_scrap_recovery_requests',
        regional='stock_scrap_recovery_regional_reviews', headquarters='stock_scrap_recovery_headquarters_reviews')
    identifier = ids[targets[kind]]
    return dict(fact_id=identifier, binding_kind=kind, root_disposition_id=ids['stock_loss_dispositions'],
        actor_user_id=ids['users'], actor_person_id=ids['people'], request_id='schema-' + kind,
        request_hash=sha256(('request:' + kind).encode()).hexdigest(),
        key_token=sha256(('token:' + kind).encode()).hexdigest(),
        **{name: sha256((kind + ':' + name).encode()).hexdigest() for name in ALIASES},
        **{name: identifier if action == kind else None for action, name in TYPED.items()},
        created_at=datetime.now(timezone.utc))


@pytest.mark.parametrize('kind', TYPED)
def test_real_registry_accepts_each_exact_typed_parent(relational_registry, kind):
    db, registry, ids = relational_registry
    row = record(ids, kind)
    db.execute(registry.insert(), row)
    db.commit()
    assert dict(db.execute(select(registry)).mappings().one())['fact_id'] == row['fact_id']


@pytest.mark.parametrize('mutation', ['actor_person_id', 'request_hash', 'request_id', 'typed_parent', 'alias_collision'])
def test_complete_registry_rejects_invalid_foreign_key_shape_and_provenance(relational_registry, mutation):
    db, registry, ids = relational_registry
    row = record(ids, 'original')
    if mutation == 'actor_person_id': row['actor_person_id'] = uuid4()
    elif mutation == 'request_hash': row['request_hash'] = 'z' * 64
    elif mutation == 'request_id': row['request_id'] = 'invalid space'
    elif mutation == 'typed_parent': row['application_id'] = ids['stock_scrap_recovery_requests']
    else: row['scrap_key_hash'] = row['recovery_key_hash']
    with pytest.raises(IntegrityError):
        db.execute(registry.insert(), row)
        db.commit()
    db.rollback()
    assert db.scalar(select(func.count()).select_from(registry)) == 0
