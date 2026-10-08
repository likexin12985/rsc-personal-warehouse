"""Explicit synthetic full-ledger export for native candidate SQL checks."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from uuid import UUID
import pytest
from sqlalchemy import select
from app.formal_services.stock_scrap.tables import tables
from test_stock_scrap_recovery_generations import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, found, ready_to_restore,
    exercise_generations as exercise, protect_other_report,
)


def encoded(value):
    if isinstance(value, datetime):
        return {'__rsc_type__':'datetime','value':value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()}
    if isinstance(value,(UUID,Decimal)):
        return {'__rsc_type__':type(value).__name__,'value':str(value)}
    raise TypeError(type(value).__name__)


@pytest.mark.parametrize('shared', [False, True], ids=['single', 'shared'])
def test_export_two_generations_from_actual_services(db,ready_to_restore,allowed,regional,monkeypatch,shared):
    assert db.get_bind().dialect.name=='sqlite' and db.get_bind().url.database==':memory:'
    target=Path(os.environ['RSC_TEST_SCRAP_LEDGER_OUTPUT']).resolve(strict=True)
    cloud=Path(__file__).resolve().parents[2]
    assert target.is_relative_to((cloud/'artifacts').resolve())
    protected=protect_other_report(db,ready_to_restore,allowed,regional,monkeypatch) if shared else None
    exercise(db,ready_to_restore,monkeypatch,protected=protected)
    mode='serial' if ready_to_restore.checked.document['serial_ids'] else 'quantity'
    rows={name:[dict(r) for r in db.execute(select(table)).mappings()] for name,table in tables().items()}
    rows={name:values for name,values in rows.items() if values}
    assert len(rows['stock_scrap_lines'])==len(rows['stock_scrap_recovery_executions'])==2
    with (target/(mode+('-shared' if shared else '')+'.json')).open('x') as output:
        json.dump(dict(provenance='synthetic SQLite real services; two scrap/recovery generations',
            tracking=mode,shared=shared,rows=rows),output,ensure_ascii=False,default=encoded)
