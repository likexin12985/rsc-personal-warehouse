"""A historical migration probe must first unwind every newer catalog change."""
from pathlib import Path

from alembic.script import ScriptDirectory

from pg16_work_order_reversal_boundary_gate import _reversal_successor_migrations


def test_legacy_reversal_probe_unwinds_current_head_in_exact_parent_order():
    scripts = ScriptDirectory(str(Path(__file__).parents[1] / 'alembic'))
    successors = _reversal_successor_migrations()
    assert successors and successors[0]['revision'] == scripts.get_current_head()
    for current, parent in zip(successors, successors[1:]):
        assert current['down_revision'] == parent['revision']
    assert successors[-1]['down_revision'] == '20261007_0097'
    assert '20261014_0104' in {row['revision'] for row in successors}
    assert all(callable(row['downgrade']) for row in successors)
