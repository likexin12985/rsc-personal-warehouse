"""Full application schema with candidate scrap extensions, SQLite only.

No production/migration/PG trigger coverage is implied by this fixture.
"""
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from app.database import Base
from app.stock_scrap_persistence_schema import build_schema


@pytest.fixture
def db():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    metadata, _, _ = build_schema()
    # SQLAlchemy's to_metadata does not copy conditional DDL. Preserve the
    # original dialect dispatch rather than executing PG regex checks on
    # SQLite or removing the corresponding SQLite checks.
    for name, original in Base.metadata.tables.items():
        for constraint in original.constraints:
            rule = constraint._ddl_if
            if rule is None:
                continue
            matches = [c for c in metadata.tables[name].constraints if type(c) is type(constraint)
                and c.name == constraint.name and str(getattr(c, 'sqltext', '')) == str(getattr(constraint, 'sqltext', ''))]
            assert len(matches) == 1, 'exact copied dialect constraint required'
            matches[0].ddl_if(dialect=rule.dialect, callable_=rule.callable_, state=rule.state)
    # Provenance readers have a separate deliberately degraded SQLite fixture
    # for missing/corrupt evidence tests. Native gates always install the full
    # frozen registry, and the complete-schema tests exercise its real FKs.
    metadata.create_all(engine, tables=[table for name, table in metadata.tables.items()
        if name != 'stock_scrap_request_key_bindings'])
    with Session(engine) as session:
        # All writer/history snapshots now see the same ten tables. The
        # registry retains the explicit fault-injection storage used below.
        from scrap_lookup_binding_fixture import create
        create(session)
        yield session
    engine.dispose()
