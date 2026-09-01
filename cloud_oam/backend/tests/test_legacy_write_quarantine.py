import os

import pytest
from pydantic import ValidationError


os.environ.setdefault("OAM_ENVIRONMENT", "test")
os.environ.setdefault(
    "OAM_JWT_SECRET", "legacy-write-test-secret-at-least-thirty-two-characters"
)

from app.config import Settings
from app.main import is_legacy_prototype_write


@pytest.mark.parametrize(
    "path",
    [
        "/api/inventory/adjust",
        "/api/auth/login",
        "/api/auth/miniprogram/password-login",
        "/api/auth/change-password",
        "/api/materials",
        "/api/warehouses",
        "/api/transfers/legacy/receive",
        "/api/work-order-materials/batch",
        "/api/stocktakes/legacy/close",
        "/api/media/transfer/legacy",
        "/api/auth/users",
        "/api/integrations/oam/personnel/legacy/enable",
    ],
)
def test_legacy_write_paths_are_quarantined(path):
    assert is_legacy_prototype_write("POST", path)
    assert not is_legacy_prototype_write("GET", path)


def test_edge_snapshot_ingress_and_passwordless_auth_are_not_quarantined():
    assert not is_legacy_prototype_write(
        "POST", "/api/integrations/oam/edge/snapshots/batches"
    )
    assert not is_legacy_prototype_write("POST", "/api/auth/sms/login")
    assert not is_legacy_prototype_write("POST", "/api/auth/miniprogram/wechat-login")


def test_production_cannot_reenable_legacy_writes():
    with pytest.raises(ValidationError, match="legacy prototype writes"):
        Settings(
            environment="production",
            jwt_secret="x" * 32,
            legacy_prototype_writes_enabled=True,
            sms_login_enabled=True,
            sms_provider="aliyun_pnvs",
            sms_access_key_id="key",
            sms_access_key_secret="secret",
            sms_sign_name="sign",
            sms_template_code="template",
        )
