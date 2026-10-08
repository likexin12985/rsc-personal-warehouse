"""Explicit exporter only; not part of automatic static test discovery."""
from return_receiving_fixture_export import export
from test_stock_return_receipt import (
    db, world, stock, recovered, destination, prepared, parcel, incoming,
    acceptance, submit, execute,
)


def test_export(db, stock, acceptance, parcel):
    export(db,stock,acceptance,parcel,submit,execute,origin='work-order')
