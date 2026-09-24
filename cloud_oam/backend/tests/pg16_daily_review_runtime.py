"""Shared CI/native entry: provision restricted readers and preserve facts.

The caller owns a disposable PG16 database and its migration command. This
module never selects a host, accepts an external DSN, disables guards or drops
existing data. Readers must be absent at first entry, and are retained after it.
"""
import hashlib
import json
import secrets

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.daily_reconciliation.capture_provisioning import provision_capture_roles
from app.daily_reconciliation.capture_role_contract import ROLES
from app.database_security import validate_production_database_security
from pg16_daily_review_gate import run


def facts(owner):
    with owner.connect() as connection:
        tables=connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).all()
        return {name:hashlib.sha256(json.dumps(connection.scalar(text(
            'SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),\'[]\'::jsonb) FROM public.'+
            connection.dialect.identifier_preparer.quote(name)+' t')),sort_keys=True,default=str).encode()).hexdigest() for name in tables}


def run_with_capture_roles(engines, administrator, migrate):
    owner=engines['star_oam_migrator'];api=engines['star_oam_api']
    passwords={role:secrets.token_urlsafe(40) for role in ROLES}
    with administrator.begin() as connection:
        observed=provision_capture_roles(connection,database='rsc_pg16_release_gate',apply=False)
        assert observed['configured'] is False, 'daily runtime requires previously absent capture principals'
        configured=provision_capture_roles(connection,database='rsc_pg16_release_gate',passwords=passwords,apply=True)
        assert configured['configured'] and configured['changed']
    readers={role:create_engine(owner.url.set(username=role,password=passwords[role]),
        poolclass=NullPool,hide_parameters=True) for role in ROLES}
    def retain(destination, blocker):
        before=facts(owner)
        output=migrate(destination)
        assert blocker in output, 'daily migration refused at an unexpected layer'
        assert facts(owner)==before, 'failed daily downgrade changed committed facts'
        validate_production_database_security(api,expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    try:
        result=run(engines,readers,check_retention=retain)
        assert len(result['cases'])==12
        return result
    finally:
        for engine in readers.values():engine.dispose()
