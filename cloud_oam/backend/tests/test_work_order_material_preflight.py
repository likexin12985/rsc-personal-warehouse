from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.formal_services.work_order_material import (
    WorkOrderMaterialLineInput,
    WorkOrderMaterialPreflightError,
    validate_batch,
    operation_request_hash,
    WorkOrderReplacementPairInput,
    validate_serial_quantity,
    expected_posting_movement_type,
)
from app.work_order_material_schemas import WorkOrderMaterialOperationIn, WorkOrderMaterialLineIn


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


def test_replacement_pairs_are_unique_and_part_of_fingerprint():
    value = line()
    pair = WorkOrderReplacementPairInput(uuid4(), uuid4())
    work_order_id, operator_person_id = uuid4(), uuid4()
    first = operation_request_hash(
        operation_type="recover", work_order_id=work_order_id, operator_person_id=operator_person_id,
        lines=(value,), replacement_pairs=(pair,),
    )
    with pytest.raises(WorkOrderMaterialPreflightError, match="不能相同"):
        operation_request_hash(
            operation_type="recover", work_order_id=uuid4(), operator_person_id=uuid4(),
            lines=(value,), replacement_pairs=(WorkOrderReplacementPairInput(pair.installed_serial_id, pair.installed_serial_id),),
        )
    assert first != operation_request_hash(
        operation_type="recover", work_order_id=work_order_id, operator_person_id=operator_person_id, lines=(value,)
    )


def test_serial_quantity_must_match_physical_units():
    with pytest.raises(WorkOrderMaterialPreflightError, match="SN 数量"):
        validate_serial_quantity(Decimal("2"), (uuid4(),))
    validate_serial_quantity(Decimal("2"), (uuid4(), uuid4()))


def test_operation_type_maps_to_matching_inventory_movement():
    assert expected_posting_movement_type("consume") == "consume"
    assert expected_posting_movement_type("recover") == "return"
    with pytest.raises(WorkOrderMaterialPreflightError, match="操作类型"):
        expected_posting_movement_type("ship")


def test_operation_dto_rejects_unknown_operation_type():
    with pytest.raises(ValidationError) as exc:
        WorkOrderMaterialOperationIn(
            operator_person_id=uuid4(), lines=({"material_id": uuid4(), "stock_account_id": uuid4(), "quantity": "1"},), operation_type="ship",
            posting_transaction_id=uuid4(), idempotency_key="k",
        )
    assert [(e["loc"], e["type"]) for e in exc.value.errors()] == [(("operation_type",), "literal_error")]


def test_operation_dto_parses_replacement_pairs():
    material_id, account_id = uuid4(), uuid4()
    value = WorkOrderMaterialOperationIn(
        operator_person_id=uuid4(),
        lines=(WorkOrderMaterialLineIn(material_id=material_id, stock_account_id=account_id, quantity=Decimal("1")),),
        operation_type="recover", posting_transaction_id=uuid4(), idempotency_key="key-1",
        replacement_pairs=({"installed_serial_id": str(uuid4()), "removed_serial_id": str(uuid4())},),
    )
    assert len(value.replacement_pairs) == 1


@pytest.mark.parametrize("quantity", ["NaN", "Infinity", "0.0001", "1000000000000000", "-1"])
def test_dto_and_service_reject_non_inventory_quantities(quantity):
    with pytest.raises(ValidationError):
        WorkOrderMaterialLineIn(material_id=uuid4(), stock_account_id=uuid4(), quantity=quantity)
    with pytest.raises(WorkOrderMaterialPreflightError):
        validate_batch((line(quantity=Decimal(quantity)),))


def test_equivalent_decimal_text_has_same_idempotency_fingerprint():
    from dataclasses import replace
    value = line(quantity=Decimal("1"))
    args = dict(operation_type="consume", work_order_id=uuid4(), operator_person_id=uuid4())
    assert operation_request_hash(**args, lines=(value,)) == operation_request_hash(
        **args, lines=(replace(value, quantity=Decimal("1.000")),))
