"""Material quantity precision measures value, not stored trailing zeros."""
from decimal import Decimal, localcontext
from types import SimpleNamespace

import pytest
from app.formal_services import stocktake_count as count


@pytest.mark.parametrize('raw,scale,fraction', [
    ('2.000',0,False), ('0.000',0,False), ('-0.000',0,False),
    ('1.200',1,True), ('0.010',2,True), ('999999999999999.000',0,False),
])
def test_storage_padding_preserves_allowed_value(raw, scale, fraction):
    value=count._require_quantity(Decimal(raw), positive=False)
    with localcontext() as context:
        # Precision validation must not round the number through normalize().
        context.prec=2
        count._validate_quantity(value, SimpleNamespace(quantity_scale=scale, allow_fraction=fraction), positive=False)


@pytest.mark.parametrize('raw,scale,fraction,positive', [
    ('2.001',0,False,False), ('1.210',1,True,False), ('0.011',2,True,False),
    ('1.100',3,False,False), ('-1.000',0,False,False), ('0.000',0,False,True),
    ('1.001',1,True,False),
])
def test_padding_never_hides_real_fraction_or_sign(raw, scale, fraction, positive):
    with localcontext() as context:
        context.prec=2
        with pytest.raises(count.StocktakeCountError) as failure:
            count._validate_quantity(Decimal(raw), SimpleNamespace(quantity_scale=scale, allow_fraction=fraction), positive=positive)
    assert failure.value.code=='stocktake_count_quantity_policy_invalid'


@pytest.mark.parametrize('raw', ['1.0000','0.0000','NaN','Infinity'])
def test_transport_precision_and_nonfinite_rejection_remain(raw):
    with pytest.raises(count.StocktakeCountError) as failure:
        count._require_quantity(Decimal(raw), positive=False)
    assert failure.value.code=='stocktake_count_quantity_invalid'
