import { vi } from "vitest";
import { approvedDetail, identity, access } from "./materialRequestReservationTestFixtures";
import type { RemainingCancellation, RemainingCancellationState } from "./materialRequestRemainingCancellation";
import { createRemainingCancellationStore } from "./materialRequestRemainingCancellationRecovery";
import type { MaterialRequestDetail } from "./formalMaterialRequests";
export const detail: MaterialRequestDetail = approvedDetail();
export const result: RemainingCancellation = { schema_version: "1.0", cancellation_id: "99999999-1111-4111-8111-000000000001",
  request_id: detail.request_id, revision_id: detail.current_revision_id, request_version: detail.request_version,
  cancellation_scope: "all_remaining_unfulfilled", cancelled_at: "2026-10-06T00:00:00Z", evidence_sha256: "ab".repeat(32), replayed: false,
  lines: detail.lines.filter(line => line.final_approved_qty !== "0.000").map(line => ({ request_line_id: line.request_line_id, cancelled_qty: line.final_approved_qty })) };
export const open: RemainingCancellationState = { request_id: detail.request_id, request_version: detail.request_version, cancel_permitted: true, cancellation: null };
export const cancelled: RemainingCancellationState = { ...open, cancel_permitted: false, cancellation: { ...result, replayed: true } };
export const cancelledDetail = { ...detail, allowed_actions: [], lines: detail.lines.map(line => ({ ...line, cancelled_qty: line.final_approved_qty })) };
export const remaining = { schema_version: "1.0" as const, assessment: "remaining_fulfillment_quantities" as const,
  request_id: detail.request_id, revision_id: detail.current_revision_id, request_version: detail.request_version,
  open_supply_tasks: 0, pending_substitutions: 0, lines: detail.lines.map(line => ({ request_line_id: line.request_line_id,
    approved_qty: line.final_approved_qty, cancelled_qty: "0.000", posted_qty: "0.000", unreserved_qty: line.final_approved_qty,
    reserved_unpicked_qty: "0.000", picked_unoutbound_qty: "0.000", outbound_unshipped_qty: "0.000", shipped_unreceived_qty: "0.000",
    accepted_unposted_qty: "0.000", rejected_unsettled_qty: "0.000" })) };
export function props() {
  const adapter = { remainingCancellationState: vi.fn(async (): Promise<RemainingCancellationState> => open),
    cancelRemaining: vi.fn(async () => result), remainingFulfillment: vi.fn(async () => remaining),
    remainingCancellationStatusNoReplay: vi.fn(async () => ({ lookup_status: "confirmed" as const, command: { ...result, replayed: true } })),
    detailNoReplay: vi.fn(async () => cancelledDetail), loadIdentityNoReplay: vi.fn(async () => identity()),
    loadAccessNoReplay: vi.fn(async () => ({ ...access(), schema_version: "1.0" as const })) };
  return { adapter, access: { ...access(), schema_version: "1.0" as const }, detail, store: createRemainingCancellationStore(), onBlocking: vi.fn(), onDetail: vi.fn(), onCancelled: vi.fn() };
}
