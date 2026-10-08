"""Explicit exporter only; not part of automatic static test discovery."""
from return_receiving_fixture_export import export
from test_stock_loss_return_receipt import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    route, derived, ready, parcel, acceptance, submit, execute,
)


def test_export(db, stock, acceptance, parcel):
    export(db,stock,acceptance,parcel,submit,execute,origin='loss')
