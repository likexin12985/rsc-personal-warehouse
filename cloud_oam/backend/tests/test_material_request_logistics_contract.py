from datetime import datetime, timezone
import pytest

from app.formal_services.material_request_logistics import LogisticsEventError, _ensure_after_shipping


def test_logistics_event_cannot_predate_shipment():
    shipped = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
    with pytest.raises(LogisticsEventError, match="交运时间"):
        _ensure_after_shipping(shipped, datetime(2026, 9, 9, 9, 59, tzinfo=timezone.utc))


def test_logistics_event_accepts_same_or_later_time():
    shipped = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
    _ensure_after_shipping(shipped, shipped)
    _ensure_after_shipping(shipped, datetime(2026, 9, 9, 10, 1, tzinfo=timezone.utc))
