"""Route contract checks for formal work-order material fulfillment."""
from app.routers.formal_work_order_material import router


def test_recover_route_is_registered_on_formal_router():
    routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }
    assert ("/v1/work-orders/{work_order_id}/material-operations/recover", "POST") in routes
