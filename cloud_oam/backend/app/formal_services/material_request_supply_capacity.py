"""Verified remaining planning quantities before any inventory reservation.

Planning is an estimate, separate from allocation and physical stock. Existing
plans can predate allocations and overlap them; keep those original quantities
and expose the overlap instead of inventing an automatic plan consumption.
The 0178 write guard independently enforces this rule. Later fulfillment must
use its own proved release/return facts; no reservation is netted here.
"""
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from app.demand_models import MaterialRequest, MaterialRequestLine, MaterialRequestCommand, SupplyTask
from app.inventory_models import StockAllocation

ZERO = Decimal('0.000')
MAX = Decimal('1000000000000000')


@dataclass(frozen=True, slots=True)
class SupplyQuantityCapacity:
    approved_qty: Decimal
    cancelled_qty: Decimal
    allocated_qty: Decimal
    active_planned_qty: Decimal
    unallocated_qty: Decimal
    existing_overlap_qty: Decimal
    new_plan_qty: Decimal


@dataclass(frozen=True, slots=True)
class SupplyLineCapacity:
    request_line_id: UUID
    quantities: SupplyQuantityCapacity


@dataclass(frozen=True, slots=True)
class SupplyPlanningCapacity:
    request_id: UUID
    request_version: int
    lines: tuple[SupplyLineCapacity, ...]


def quantity_capacity(*, approved, cancelled, allocated, planned):
    """Use verified independent facts; never net a historical release here."""
    values = (approved, cancelled, allocated, planned)
    if any(not isinstance(v, Decimal) or not v.is_finite() or v < ZERO or v >= MAX
           or v != v.quantize(Decimal('.001')) for v in values):
        raise ValueError('supply capacity requires nonnegative decimal(18,3) quantities')
    if cancelled > approved or allocated > approved - cancelled or planned > approved - cancelled:
        raise ValueError('independent supply quantities exceed approved demand')
    unallocated = approved - cancelled - allocated
    return SupplyQuantityCapacity(approved, cancelled, allocated, planned, unallocated,
        max(ZERO, planned - unallocated), max(ZERO, unallocated - planned))


def planning_capacity(db, *, actor, request_id):
    from . import material_request_supply as supply
    from . import material_request_supply_command_status as history
    from .material_request_supply_allocation_history import allocation_history
    with db.no_autoflush:
        current = history._fresh_manager(db, supply._validate_supplied_actor(actor), supply._database_now(db))
        requests = history._rows(db, select(MaterialRequest).where(MaterialRequest.id == request_id))
        if len(requests) != 1:
            supply._fail('material_request_not_found', 'not_found', '需求单不存在')
        request = requests[0]
        supply._require_supply_actor_context(db, current, request=request, now=supply._database_now(db))
        version = request.version
        axes = supply._fulfillment_axes(request)
        later_fulfillment = any(
            axes[key] != supply._NEUTRAL_AXES[key]
            for key in axes if key != 'allocation_status'
        )
        if (request.status not in {'approved', 'partially_approved'}
                or axes['allocation_status'] not in {'not_allocated', 'partially_allocated', 'allocated'}):
            supply._fail('supply_capacity_later_fulfillment_unproved', 'precondition_failed',
                '需求已有占用或后续履约，请按独立释放、入账和退回事实核对；不推断新增计划余量')
        coverage_by_line = None
        if later_fulfillment:
            # This is the authoritative downstream read. It verifies every
            # reservation/release/pick/outbound/shipment/receipt/inbound fact
            # before any later plan capacity is exposed.
            from .material_request_remainder import remaining_fulfillment
            coverage = remaining_fulfillment(db, actor=current, request_id=request.id)
            coverage_by_line = {row.request_line_id: row for row in coverage.lines}
        lines = history._rows(db, select(MaterialRequestLine).where(
            MaterialRequestLine.request_id == request.id,
            MaterialRequestLine.revision_no == request.revision_no).order_by(MaterialRequestLine.line_no))
        commands = history._rows(db, select(MaterialRequestCommand).where(
            MaterialRequestCommand.request_id == request.id,
            MaterialRequestCommand.operation.in_(history._OPERATIONS)).order_by(MaterialRequestCommand.target_version))
        if commands:
            history._verify_current_history(db, request=request, requested_command=commands[-1],
                requested_result=history._command_result(commands[-1]),
                allow_later_fulfillment=later_fulfillment)
        # This proves final approval and every allocation even when no plan has
        # ever existed, and checks the absence of unobserved command versions.
        allocation_history(db, request=request, lines=lines, supply_commands=commands,
                           allow_later_fulfillment=later_fulfillment)
        tasks = history._rows(db, select(SupplyTask).where(SupplyTask.request_line_id.in_([line.id for line in lines])))
        if tasks and not commands:
            history._invalid()
        allocations = history._rows(db, select(StockAllocation).where(StockAllocation.request_id == request.id))
        result = []
        for line in lines:
            allocated = sum((row.allocated_qty for row in allocations if row.request_line_id == line.id), ZERO)
            planned = sum((row.original_equivalent_qty for row in tasks
                if row.request_line_id == line.id and row.status in supply._ACTIVE_TASK_STATUSES), ZERO)
            approved = line.final_approved_qty
            cancelled = line.cancelled_qty
            if coverage_by_line is not None:
                verified = coverage_by_line.get(line.id)
                if verified is None:
                    history._invalid()
                approved = Decimal(verified.approved_qty)
                cancelled = Decimal(verified.cancelled_qty)
            try:
                amounts = quantity_capacity(approved=approved, cancelled=cancelled,
                    allocated=allocated, planned=planned)
            except ValueError:
                history._invalid()
            result.append(SupplyLineCapacity(line.id, amounts))
        history._fresh_manager(db, current, supply._database_now(db))
        fresh = history._rows(db, select(MaterialRequest).where(MaterialRequest.id == request.id))
        if len(fresh) != 1 or fresh[0].version != version:
            history._invalid()
        return SupplyPlanningCapacity(request.id, version, tuple(result))
