"""Compile an isolated forward structural candidate; never a release migration."""
import hashlib

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import AddConstraint, CreateColumn, CreateIndex, CreateTable, DropConstraint

from app import models  # noqa: F401
from app.database import Base
from app.return_condition_schema import PARENT_NAMES, define
from app.stock_scrap_schema import clone


def compile_structure():
    from app.return_condition_application_schema import predecessor_schema
    metadata = predecessor_schema()
    before = {name: (set(metadata.tables[name].columns.keys()), set(metadata.tables[name].constraints))
              for name in PARENT_NAMES}
    tables, parents = define(metadata)
    pg = dialect()
    statements = [str(CreateTable(t, include_foreign_key_constraints=[]).compile(dialect=pg)) for t in tables]
    foreign_keys = [c for t in tables for c in t.constraints if isinstance(c, ForeignKeyConstraint)]
    for table in tables:
        statements.append('REVOKE ALL ON TABLE public.' + table.name +
            ' FROM PUBLIC, star_oam_api, star_oam_projector, star_oam_edge, edge_inbox')
        statements.extend(str(CreateIndex(index).compile(dialect=pg))
            for index in sorted(table.indexes, key=lambda i: i.name))
    for table in parents:
        columns, constraints = before[table.name]
        for col in table.columns:
            if col.name not in columns:
                statements.append('ALTER TABLE public.' + table.name + ' ADD COLUMN ' +
                                  str(CreateColumn(col).compile(dialect=pg)))
        for constraint in sorted(constraints - table.constraints, key=lambda c: c.name):
            if not isinstance(constraint, CheckConstraint):
                raise ValueError('existing foreign keys and uniqueness must be preserved')
            statements.append(str(DropConstraint(constraint).compile(dialect=pg)))
        for constraint in sorted(table.constraints - constraints,
                key=lambda c: c.name or ','.join(k.name for k in c.columns)):
            if isinstance(constraint, ForeignKeyConstraint):
                foreign_keys.append(constraint)
            else:
                if not isinstance(constraint, (CheckConstraint, UniqueConstraint)):
                    raise ValueError('unexpected structural change')
                statements.append(str(AddConstraint(constraint).compile(dialect=pg)))
    for constraint in sorted(foreign_keys,
            key=lambda c: (c.table.name, c.name or ','.join(k.name for k in c.columns))):
        if constraint.name is None:
            identity = constraint.table.name + '_' + '_'.join(c.name for c in constraint.columns)
            constraint.name = 'fk_cond_' + identity[:35] + '_' + hashlib.sha256(identity.encode()).hexdigest()[:12]
        statements.append(str(AddConstraint(constraint).compile(dialect=pg)))
    return metadata, tables, parents, statements
