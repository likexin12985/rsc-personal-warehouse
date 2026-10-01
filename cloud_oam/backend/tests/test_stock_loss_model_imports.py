"""Cold-process imports must not make model registration depend on services."""
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("entry", [
    "app.formal_access",
    "app.inventory_control_configuration",
    "app.models",
    "app.formal_services.stock_loss_corrections.correction_models",
])
def test_loss_models_register_without_import_order_dependency(entry):
    script = f"""
import importlib
import warnings
from sqlalchemy.exc import SAWarning
warnings.simplefilter('error', SAWarning)
importlib.import_module({entry!r})
from app import models
from app.database import Base
from app import stock_loss_correction_models as canonical
from app.formal_services.stock_loss_corrections import correction_models, seal_model
assert correction_models.StockLossDispositionReversal is canonical.StockLossDispositionReversal
assert seal_model.StockLossInverseRequestSeal is canonical.StockLossInverseRequestSeal
for table in (
    'stock_loss_disposition_reversals', 'stock_loss_correction_decisions',
    'stock_loss_correction_executions', 'stock_loss_inverse_request_seals',
):
    assert table in Base.metadata.tables, table
    for fk in Base.metadata.tables[table].foreign_keys:
        assert fk.column is not None
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
