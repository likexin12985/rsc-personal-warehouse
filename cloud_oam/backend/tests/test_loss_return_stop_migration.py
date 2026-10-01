"""Return-stop migrations require a live catalog in both directions."""
from pathlib import Path
import io
import pytest
from alembic import command
from alembic.config import Config

ROOT=Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('direction',('upgrade','downgrade'))
@pytest.mark.parametrize('url',('postgresql+psycopg://offline:offline@localhost/offline','sqlite+pysqlite:///:memory:'))
def test_return_stop_offline_requires_live_catalog(monkeypatch,direction,url):
    monkeypatch.delenv('OAM_DATABASE_URL',raising=False)
    output=io.StringIO();config=Config(str(ROOT/'alembic.ini'),output_buffer=output)
    config.set_main_option('script_location',str(ROOT/'backend/alembic'));config.set_main_option('sqlalchemy.url',url)
    pair=('20261211_0162','20261212_0163')
    if direction=='downgrade':pair=tuple(reversed(pair))
    with pytest.raises(RuntimeError,match='^0163 online history and catalog verification required$'):
        getattr(command,direction)(config,':'.join(pair),sql=True)
    statements=[line.strip() for line in output.getvalue().splitlines() if line.strip() and not line.lstrip().startswith('--')]
    assert statements in ([],['BEGIN;'])
