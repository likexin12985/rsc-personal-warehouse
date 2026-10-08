import { vi } from "vitest";
import { approvedDetail, identity, access } from "./materialRequestReservationTestFixtures";
import { closeInputFingerprint, type ClosureResult, type ClosureState } from "./materialRequestClosure";
import { createClosureStore, type ClosureSentinel } from "./materialRequestClosureRecovery";
import type { MaterialRequestDetail } from "./formalMaterialRequests";
export const detail: MaterialRequestDetail = approvedDetail();
export const result: ClosureResult = { schema_version: "1.0", closure_id: "99999999-1111-4111-8111-000000000001",
  request_id: detail.request_id, revision_id: detail.current_revision_id, request_version: detail.request_version,
  business_status: "closed", closed_at: "2026-10-06T00:00:00Z", evidence_sha256: "ab".repeat(32), replayed: false,
  lines: detail.lines.map(line => ({ request_line_id: line.request_line_id, approved_qty: line.final_approved_qty,
    cancelled_qty: "0.000", posted_qty: line.final_approved_qty, remaining_qty: "0.000" })) };
export const open: ClosureState = { request_id: detail.request_id, request_version: detail.request_version, business_status: "open", close_permitted: true, closure: null };
export const closed: ClosureState = { ...open, business_status: "closed", close_permitted: false, closure: { ...result, replayed: true } };
export const coverage = { schema_version: "1.0", request_id: detail.request_id, revision_id: detail.current_revision_id,
  request_version: detail.request_version, assessment: "final_approved_quantity_coverage", quantity_coverage_complete: true,
  pending_inbound_orders: 0, lines: result.lines };
export async function sentinel(): Promise<ClosureSentinel> {
  const input = { expected_request_version: detail.request_version, reason: "全部核对完成" };
  return { v: 1, trace: "close-test-trace-1234", key: "close-test-key-123456", person_id: identity().person_id,
    authorization_version: identity().authorization_version, request_id: detail.request_id, input,
    fingerprint: await closeInputFingerprint(input) };
}
export function props() {
  const adapter = {
    closureState: vi.fn(async (): Promise<ClosureState> => open),
    closeRequest: vi.fn(async () => result),
    closureCommandStatusNoReplay: vi.fn(async () => ({ lookup_status: "confirmed" as const, command: { ...result, replayed: true } })),
    detailNoReplay: vi.fn(async () => detail), loadIdentityNoReplay: vi.fn(async () => identity()),
    loadAccessNoReplay: vi.fn(async () => ({ ...access(), schema_version: "1.0" as const })),
    completionQuantities: vi.fn(async () => coverage),
  };
  return { adapter, access: { ...access(), schema_version: "1.0" as const }, detail, store: createClosureStore(), onBlocking: vi.fn(), onDetail: vi.fn(), onClosed: vi.fn() };
}
