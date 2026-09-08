import { afterReservation } from "./materialRequestReservationTestFixtures";
import { releasePage } from "./materialRequestReleaseTestFixtures";
import { type FulfillmentPreparation } from "./materialRequestFulfillmentPreparation";

export function preparationPage(serial = false): FulfillmentPreparation {
  const release = releasePage(serial), original = release.items[0];
  const { target_stock_account_id: _target, releasable_qty, ...item } = original;
  return { ...release, scope: "authorized_sources", material_id: afterReservation().lines[0].material_id,
    ledger_cursor: 10, projected_at: "2026-09-08T08:00:00+08:00",
    items: [{ ...item, remaining_reserved_qty: releasable_qty, verified_held_qty: releasable_qty, preparation_status: "ready_for_review", blockers: [] }] };
}
