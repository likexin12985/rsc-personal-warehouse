from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Material, User, Warehouse
from .security import hash_password


MATERIALS = [
    ("ADQCPN0090", "BC交流桩主控板V1.5-PCBA", "主控板", "主板"),
    ("ADQCPN0090KG", "BC交流桩主控板V1.5-PCBA（返修件）", "主控板", "主板"),
    ("ADQCPN0096", "交流集成控制板V3.6-PCBA", "集成控制板", "主板"),
    ("ADQCPN0186", "交流集成控制板V3.5.3-PCBA", "集成控制板", "主板"),
    ("ADQCPN1342", "交流桩通用主控板", "通用替代板", "主板"),
    ("ADQCPN1624", "交流桩控制板-PCBA", "", "主板"),
    ("ADQAPG0614", "交流充电枪", "LMGS5508-AM-R", "枪线"),
    ("ADQAPG0646", "交流充电枪", "", "枪线"),
    ("ADQAPG0768", "交流充电枪", "万能通用枪线，带屏蔽层", "枪线"),
    ("ADQCOM0006", "移动物联网卡", "", "通信卡"),
    ("ADQCOM0088", "IC卡", "普通启停卡", "卡片"),
    ("ADQCOM0317", "奔驰桩定制IC卡", "A卡刷卡后转为B卡", "卡片"),
    ("ADQMCB0060", "剩余电流动作微型断路器", "", "电气件"),
    ("ADQSGB0204", "灯板", "", "显示件"),
]

WAREHOUSES = [
    ("SH-SVC-GOOD", "上海市服务商库_新", "上海市", "上海市", "good"),
    ("JS-SVC-GOOD", "江苏省服务商库_新", "江苏省", "南京市", "good"),
    ("ZJ-SVC-GOOD", "浙江省服务商库_新", "浙江省", "杭州市", "good"),
    ("GD-SVC-GOOD", "广东省服务商库_新", "广东省", "广州市", "good"),
]


def seed_initial_data(db: Session) -> None:
    settings = get_settings()
    if not db.scalar(select(User).limit(1)) and settings.admin_initial_password:
        db.add(
            User(
                mobile=settings.admin_mobile,
                name=settings.admin_name,
                password_hash=hash_password(settings.admin_initial_password),
                role="admin",
                require_password_change=True,
            )
        )
    for code, name, specification, category in MATERIALS:
        if not db.scalar(select(Material).where(Material.code == code)):
            db.add(
                Material(
                    code=code,
                    name=name,
                    specification=specification,
                    category=category,
                )
            )
    for code, name, province, city, condition in WAREHOUSES:
        if not db.scalar(select(Warehouse).where(Warehouse.code == code)):
            db.add(
                Warehouse(
                    code=code,
                    name=name,
                    province=province,
                    city=city,
                    condition_scope=condition,
                    warehouse_level="network",
                    ownership_type="regular",
                    position_scope="service_provider",
                )
            )
    if not db.scalar(select(Warehouse).where(Warehouse.code == "HQ-GENERAL")):
        db.add(
            Warehouse(
                code="HQ-GENERAL",
                name="总部仓库",
                province="全国",
                city="",
                warehouse_type="central",
                condition_scope="mixed",
                warehouse_level="headquarters",
                ownership_type="regular",
                position_scope="unrestricted",
            )
        )
    db.commit()
