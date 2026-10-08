import { apiNoReplay } from "./api";
import { id, type PersonalCommand } from "./myFulfillmentContract";
export type PersonalRequester = <T>(path: string, init?: RequestInit) => Promise<T>;
export type MyFulfillmentAdapter = ReturnType<typeof createMyFulfillmentAdapter>;
export function createMyFulfillmentAdapter(requester: PersonalRequester = apiNoReplay) {
  const prefix = (requestId: string) => `/v1/material-requests/${id(requestId)}`;
  const read = (path: string, headers: Record<string, string> = {}) => requester<unknown>(path, { method: "GET", cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache", ...headers } });
  const page = (after: string | null) => `?limit=5${after ? `&after_id=${id(after)}` : ""}`;
  return {
    packages: (requestId: string, after: string | null = null) => read(`${prefix(requestId)}/my-receiving${page(after)}`),
    receiptCandidate: (requestId: string, shipmentId: string) => read(`${prefix(requestId)}/my-receiving/${id(shipmentId)}/candidates`),
    inbounds: (requestId: string, after: string | null = null) => read(`${prefix(requestId)}/my-inbounds/candidates${page(after)}`),
    submit: (requestId: string, command: PersonalCommand, key: string, trace: string) => requester<unknown>(`${prefix(requestId)}/${command.kind === "receipt" ? "my-receipts" : "my-inbounds"}`, { method: "POST", cache: "no-store", headers: { "Idempotency-Key": key, "X-Request-ID": trace }, body: JSON.stringify(command.input) }),
    status: (requestId: string, kind: PersonalCommand["kind"], key: string) => read(`${prefix(requestId)}/${kind === "receipt" ? "my-receipts" : "my-inbounds"}/command-status`, { "Idempotency-Key": key }),
    trace: (requestId: string, kind: PersonalCommand["kind"], trace: string) => read(`${prefix(requestId)}/${kind === "receipt" ? "my-receipts" : "my-inbounds"}/trace-status`, { "X-Original-Request-ID": trace }),
  };
}
