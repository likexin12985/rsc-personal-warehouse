from pathlib import Path

ROUTER = Path(__file__).parents[1] / "app" / "routers" / "formal_material_requests.py"

def test_fulfillment_write_routes_use_dedicated_permission():
    source = ROUTER.read_text(encoding="utf-8")
    names = (
        "create_formal_material_request_shipment",
        "create_formal_material_request_receipt",
        "create_formal_material_request_inbound_order",
        "post_formal_material_request_inbound_order",
    )
    for name in names:
        start = source.index(f"def {name}")
        end = source.find("\n@router.", start)
        block = source[start:] if end < 0 else source[start:end]
        assert 'require_permission("material_request", "fulfill")' in block
