from decimal import Decimal
from uuid import uuid4

import pytest

from app.formal_services.work_order_material import (
    WorkOrderMaterialLineInput,
    WorkOrderMaterialPreflightError,
    validate_batch,
    operation_request_hash,
)


def line(**kwargs):
    value = {
        "material_id": uuid4(),
        "stock_account_id": uuid4(),
        "quantity": Decimal("1"),
    }
    value.update(kwargs)
    return WorkOrderMaterialLineInput(**value)


def test_batch_requires_positive_quantity():
    with pytest.raises(WorkOrderMaterialPreflightError, match="大于零"):
        validate_batch((line(quantity=Decimal("0")),))


def test_batch_rejects_duplicate_material_and_serial():
    material = uuid4()
    with pytest.raises(WorkOrderMaterialPreflightError, match="重复提交物料"):
        validate_batch((line(material_id=material), line(material_id=material)))

    serial = uuid4()
    with pytest.raises(WorkOrderMaterialPreflightError, match="序列号"):
        validate_batch((line(serial_ids=(serial,)), line(serial_ids=(serial,))))


def test_batch_accepts_distinct_lines():
    validate_batch((line(), line()))


def test_operation_request_hash_is_stable_and_type_is_strict():
    work_order_id = uuid4()
    operator_id = uuid4()
    value = line()
    first = operation_request_hash(
        operation_type="consume", work_order_id=work_order_id,
        operator_person_id=operator_id, lines=(value,)
    )
    second = operation_request_hash(
        operation_type="consume", work_order_id=work_order_id,
        operator_person_id=operator_id, lines=(value,)
    )
    assert first == second
    with pytest.raises(WorkOrderMaterialPreflightError, match="操作类型"):
        operation_request_hash(
            operation_type="ship", work_order_id=work_order_id,
            operator_person_id=operator_id, lines=(value,)
        )
