"""Source-level guardrail for the frozen pilot MVP route boundary."""

import ast
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
ROUTER = ROOT / "backend/app/routers/formal_material_requests.py"


POST_FULFILLMENT_ROUTES = frozenset(
    {
        "formal_material_request_allocation_options",
        "formal_material_request_allocation_command_status",
        "formal_material_request_reservation_options",
        "formal_material_request_reservation_command_status",
        "formal_supply_command_status",
        "formal_material_request_release_options",
        "formal_material_request_remaining_cancellation",
        "formal_material_request_closure",
        "formal_rejection_return_candidates",
        "formal_return_compensation_candidates",
        "formal_material_request_remaining_fulfillment",
        "formal_material_request_fulfillment_preparation",
        "formal_material_request_picking_options",
        "formal_material_request_outbound_options",
        "list_formal_material_request_shipments",
        "list_formal_material_request_shipment_options",
        "list_formal_material_request_shipment_targets",
        "list_formal_material_request_logistics_events",
        "list_formal_material_request_oam_receipt_evidence",
        "create_formal_material_request_receipt",
        "list_formal_material_request_receipts",
        "create_formal_material_request_inbound_order",
        "list_formal_material_request_inbound_orders",
        "post_formal_material_request_inbound_order",
    }
)
TECHNICIAN_ALLOWED_ROUTES = frozenset(
    {
        "list_formal_material_request_my_receiving",
        "read_my_receipt_candidates",
        "create_my_material_request_receipt",
        "my_material_request_receipt_command_status",
        "my_material_request_receipt_trace_status",
        "my_material_request_inbound_candidates",
        "create_my_material_request_inbound",
        "my_material_request_inbound_command_status",
        "my_material_request_inbound_trace_status",
    }
)
TECHNICIAN_STATUS_ROUTES = frozenset(
    {
        "formal_material_request_lifecycle_command_status",
    }
)
COMMAND_STATUS_ROUTES = frozenset(
    {
        "formal_material_request_allocation_command_status",
        "formal_material_request_reservation_command_status",
        "formal_material_request_lifecycle_command_status",
        "formal_supply_command_status",
        "formal_material_request_release_status",
        "formal_material_request_remaining_cancellation_command_status",
        "formal_material_request_close_command_status",
        "formal_rejection_return_command_status",
        "formal_rejection_progress_command_status",
        "formal_return_compensation_command_status",
        "formal_material_request_pick_status",
        "formal_material_request_outbound_status",
        "shipment_command_status",
        "logistics_command_status",
        "my_material_request_receipt_command_status",
        "my_material_request_receipt_trace_status",
        "my_material_request_inbound_command_status",
        "my_material_request_inbound_trace_status",
        "receipt_command_status",
    }
)

MVP_MUTATION_ROUTES = frozenset(
    {
        "create_formal_material_request",
        "replace_formal_material_request_draft",
        "submit_formal_material_request",
        "withdraw_formal_material_request",
        "cancel_formal_material_request",
        "decide_formal_material_request_approval",
        "register_formal_material_request_external_evidence",
        "verify_formal_material_request_external_evidence",
    }
)


def _function_sources() -> dict[str, tuple[int, str]]:
    text = ROUTER.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(ROUTER))
    lines = text.splitlines()
    return {
        node.name: (node.lineno, "\n".join(lines[node.lineno - 1 : node.end_lineno]))
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _post_route_sources() -> dict[str, str]:
    text = ROUTER.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(ROUTER))
    lines = text.splitlines()
    result: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "post"
            for decorator in node.decorator_list
        ):
            result[node.name] = "\n".join(lines[node.lineno - 1 : node.end_lineno])
    return result


def _http_route_sources() -> dict[str, str]:
    text = ROUTER.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(ROUTER))
    lines = text.splitlines()
    result: dict[str, str] = {}
    methods = {"get", "post", "put", "patch", "delete"}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr in methods
            for decorator in node.decorator_list
        ):
            result[node.name] = "\n".join(lines[node.lineno - 1 : node.end_lineno])
    return result


def test_all_post_fulfillment_routes_front_load_backend_capability_guard():
    sources = _function_sources()
    assert set(POST_FULFILLMENT_ROUTES) <= set(sources)
    assert len(POST_FULFILLMENT_ROUTES) == 24
    for name in POST_FULFILLMENT_ROUTES:
        _, source = sources[name]
        guard_line = source.index("_require_backend_fulfillment")
        service_matches = list(re.finditer(r"\b[A-Za-z_]+_service\.", source))
        assert service_matches, name
        assert guard_line < service_matches[0].start(), name


def test_technician_receipt_and_inbound_routes_remain_explicit_exceptions():
    sources = _function_sources()
    assert set(TECHNICIAN_ALLOWED_ROUTES) <= set(sources)
    for name in TECHNICIAN_ALLOWED_ROUTES:
        assert "_require_backend_fulfillment" not in sources[name][1], name


def test_lifecycle_status_route_remains_the_technician_status_view_exception():
    sources = _function_sources()
    assert set(TECHNICIAN_STATUS_ROUTES) <= set(sources)
    for name in TECHNICIAN_STATUS_ROUTES:
        source = sources[name][1]
        assert "_require_backend_fulfillment" not in source, name
        assert "_set_read_no_store" in source, name


def test_all_command_status_routes_front_load_private_cache_boundary():
    sources = _function_sources()
    assert set(COMMAND_STATUS_ROUTES) <= set(sources)
    assert len(COMMAND_STATUS_ROUTES) == 19
    for name in COMMAND_STATUS_ROUTES:
        source = sources[name][1]
        no_store_line = source.index("_set_read_no_store")
        header_line = source.find("_required_safe_header")
        assert header_line < 0 or no_store_line < header_line, name


def test_mvp_request_and_approval_mutations_front_load_private_cache_boundary():
    sources = _function_sources()
    assert set(MVP_MUTATION_ROUTES) <= set(sources)
    for name in MVP_MUTATION_ROUTES:
        source = sources[name][1]
        no_store_line = source.index("_set_read_no_store")
        header_line = source.find("_required_write_headers")
        assert header_line < 0 or no_store_line < header_line, name


def test_every_formal_post_route_sets_private_cache_boundary():
    sources = _post_route_sources()
    assert len(sources) >= 26
    assert all("_set_read_no_store" in source for source in sources.values())


def test_every_formal_http_route_sets_private_cache_boundary():
    sources = _http_route_sources()
    assert len(sources) >= 60
    for name, source in sources.items():
        no_store_line = source.index("_set_read_no_store")
        early_checks = [
            match.start()
            for match in re.finditer(r"\b_required_[A-Za-z_]", source)
        ]
        service_calls = [
            match.start()
            for match in re.finditer(r"\b[A-Za-z_]+_service\.", source)
        ]
        assert all(no_store_line < position for position in early_checks), name
        assert all(no_store_line < position for position in service_calls), name
