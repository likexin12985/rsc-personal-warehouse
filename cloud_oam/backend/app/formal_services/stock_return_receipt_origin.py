"""Select receipt wire contracts from an already verified parcel source."""
from ..stock_return_receiving_schemas import StockReturnReceivingPackageOut, LossReceivingPackage, LossReceivingLine
from ..stock_return_receipt_schemas import (
    StockReturnReceiptLineOut, LossReturnReceiptLineOut,
    StockReturnReceiptPreviewOut, LossReturnReceiptPreviewOut,
    StockReturnReceiptOut, LossReturnReceiptOut,
    StockReturnReceiptSealOut, LossReturnReceiptSealOut,
)


def origin_fields(package, *, json=False):
    if isinstance(package, LossReceivingPackage):
        return {'origin': package.origin.model_dump(mode='json') if json else package.origin}
    return {'work_order_id': str(package.work_order_id) if json else package.work_order_id}


def package_from_snapshot(value):
    # No null-work-order inference: each strict schema requires its own source.
    model = LossReceivingPackage if 'origin' in value else StockReturnReceivingPackageOut
    return model.model_validate(value)


def line_model(original):
    return LossReturnReceiptLineOut if isinstance(original, LossReceivingLine) else StockReturnReceiptLineOut


def output_model(package, kind):
    regular, loss = {
        'preview': (StockReturnReceiptPreviewOut, LossReturnReceiptPreviewOut),
        'receipt': (StockReturnReceiptOut, LossReturnReceiptOut),
        'seal': (StockReturnReceiptSealOut, LossReturnReceiptSealOut),
    }[kind]
    return loss if isinstance(package, LossReceivingPackage) else regular


def work_order_id(package):
    return None if isinstance(package, LossReceivingPackage) else package.work_order_id


def loss_disposition_id(package):
    return package.origin.disposition_id if isinstance(package, LossReceivingPackage) else None
