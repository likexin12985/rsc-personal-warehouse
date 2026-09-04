// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import OpeningCountRecoveryPanel from "./OpeningCountRecoveryPanel";
import { createOpeningCountRecoveryAdapter } from "../openingCountRecovery";
import { createOpeningCountRecoveryStore, type OpeningCountLockManager, type OpeningCountSentinel } from "../openingCountRecoveryStore";

const TASK = "10000000-0000-4000-8000-000000000001";
const ROUND = "20000000-0000-4000-8000-000000000002";
const SCOPE = "30000000-0000-4000-8000-000000000003";
const PERSON = "40000000-0000-4000-8000-000000000004";
const NEXT = "50000000-0000-4000-8000-000000000005";
const WHEN = "2026-09-05T01:00:00Z";
const actor = { person_id: PERSON, authorization_version: 7 };
const sentinel: OpeningCountSentinel = { v: 1, kind: "opening_scope_count", task_id: TASK,
  round_id: ROUND, round_no: 1, scope_id: SCOPE, actor_person_id: PERSON,
  actor_authorization_version: 7, trace_request_id: "test-panel-count-0001" };
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
function detail(later = false): any {
  return { schema_version: "1.0", task_id: TASK, task_no: "OPENING-PANEL", region_org_id: NEXT,
    status: later ? "counting" : "submitted", blind_count: true, task_version: later ? 8 : 4,
    deadline: null, cutoff_at: "2026-09-04T00:00:00Z",
    current_round: { round_id: later ? NEXT : ROUND, round_no: later ? 2 : 1,
      round_type: later ? "recount" : "initial", status: later ? "counting" : "submitted",
      started_at: later ? "2026-09-05T02:00:00Z" : "2026-09-05T00:00:00Z", submitted_at: later ? null : WHEN },
    evidence_status: later ? "counting_hidden" : "sealed",
    scopes: [{ scope_id: SCOPE, scope_no: 1, location_id: NEXT, owner_org_id: NEXT, assigned_to_me: true,
      completion_status: later ? "pending" : "completed", completed_at: later ? null : WHEN,
      zero_confirmed: later ? null : true, count_line_count: later ? null : 0,
      observation_line_count: later ? null : 0, serial_count: later ? null : 0, total_counted_qty: later ? null : "0.000" }],
    observations: [], differences: [], reviews: [], allowed_actions: later ? ["count"] : ["review_region"] };
}
function status(confirmed = true) {
  return { schema_version: "1.0", task_id: TASK, round_id: ROUND, scope_id: SCOPE,
    actor_person_id: PERSON, actor_authorization_version: 7, trace_request_id: sentinel.trace_request_id,
    lookup_status: confirmed ? "confirmed" : "not_observed", command: confirmed
      ? { completion_id: NEXT, completed_at: WHEN, round_no: 1, scope_completed: true, caused_round_submission: true } : null };
}
async function fixture(options: { seed?: boolean; result?: unknown; later?: boolean } = {}) {
  const values = new Map<string, string>();
  const storage = { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } };
  const held = new Set<string>();
  const locks: OpeningCountLockManager = { async request(name, _options, work) {
    if (held.has(name)) return work(null);
    held.add(name);
    try { return await work({}); } finally { held.delete(name); }
  } };
  const store = createOpeningCountRecoveryStore({ storage, locks });
  if (options.seed !== false) await store.withTaskLease(TASK, async (lease) => { lease.persist(sentinel); });
  const requester = vi.fn(async (path: string, _init?: RequestInit): Promise<unknown> => {
    if (path === "/auth/me") return { ...actor, name: "禁止展示的人员姓名", employee_no: "PRIVATE-EMP",
      organization_code: "ORG", organization_name: "测试区域", account_status: "active", employment_status: "active", access_mode: "active", role_codes: ["technician"] };
    if (path === "/access/context") return { ...actor, account_status: "active", employment_status: "active",
      access_mode: "active", role_codes: ["technician"], assignments: [{ assignment_id: NEXT,
        role_code: "technician", scope_type: "person", scope_id: PERSON, valid_from: "2026-09-01T00:00:00Z", valid_to: null }],
      permissions: [{ resource: "stocktake", action: "read", field_code: "" }, { resource: "stocktake", action: "count", field_code: "" }] };
    if (path.includes("count-command-status")) return options.result === undefined ? status() : await options.result;
    if (path === `/v1/stocktakes/opening/${TASK}`) return detail(options.later);
    throw new Error("unexpected endpoint");
  });
  const adapter = createOpeningCountRecoveryAdapter(actor, requester);
  const onRecovered = vi.fn();
  return { store, storage, values, requester, adapter, onRecovered,
    props: { taskId: TASK, actor, store, adapter, onRecovered } };
}
afterEach(cleanup);

describe("opening count durable recovery panel", () => {
  it("does no network work on mount, shows only safe coordinates, and explicitly recovers through GETs", async () => {
    const f = await fixture();
    render(<OpeningCountRecoveryPanel {...f.props} />);
    expect(f.requester).not.toHaveBeenCalled();
    expect(screen.getByText(sentinel.trace_request_id)).toBeTruthy();
    expect(document.body.textContent).not.toContain(PERSON);
    fireEvent.click(screen.getByRole("button", { name: "只读核验原计数" }));
    await waitFor(() => expect(f.onRecovered).toHaveBeenCalledWith(detail()));
    expect(f.store.read(TASK)).toEqual({ kind: "missing" });
    expect(f.requester).toHaveBeenCalledTimes(6);
    for (const [, init] of f.requester.mock.calls) {
      expect(init?.method ?? "GET").toBe("GET"); expect(init?.cache).toBe("no-store");
      expect(init?.body).toBeUndefined(); expect(new Headers(init?.headers).has("Idempotency-Key")).toBe(false);
    }
    expect(document.body.textContent).not.toContain("禁止展示的人员姓名");
  });

  it("keeps not_observed durable and every explicit retry stays GET-only", async () => {
    const f = await fixture({ result: status(false) });
    render(<OpeningCountRecoveryPanel {...f.props} />);
    for (let attempt = 0; attempt < 2; attempt += 1) {
      fireEvent.click(screen.getByRole("button", { name: "只读核验原计数" }));
      await screen.findByRole("alert");
      expect(f.store.read(TASK).kind).toBe("valid");
    }
    expect(f.onRecovered).not.toHaveBeenCalled();
    expect(f.requester.mock.calls.every(([, init]) => (init?.method ?? "GET") === "GET")).toBe(true);
  });

  it("does not label a pending later round completed when historical evidence confirms", async () => {
    const f = await fixture({ later: true });
    render(<OpeningCountRecoveryPanel {...f.props} />);
    fireEvent.click(screen.getByRole("button", { name: "只读核验原计数" }));
    await waitFor(() => expect(f.onRecovered).toHaveBeenCalledWith(detail(true)));
    expect(f.onRecovered.mock.calls[0][0].scopes[0].completion_status).toBe("pending");
  });

  it.each(["unmount", "identity", "view"])("keeps the marker when a late result follows %s", async (change) => {
    const pending = deferred<unknown>();
    const f = await fixture({ result: pending.promise });
    let live = true;
    const view = render(<OpeningCountRecoveryPanel {...f.props} canCommit={() => live} />);
    fireEvent.click(screen.getByRole("button", { name: "只读核验原计数" }));
    await waitFor(() => expect(f.requester.mock.calls.some(([path]) => path.includes("count-command-status"))).toBe(true));
    if (change === "unmount") view.unmount();
    else if (change === "identity") view.rerender(<OpeningCountRecoveryPanel {...f.props} actor={{ ...actor, authorization_version: 8 }} />);
    else live = false;
    await act(async () => { pending.resolve(status()); await pending.promise; });
    expect(f.onRecovered).not.toHaveBeenCalled();
    expect(f.store.read(TASK).kind).toBe("valid");
  });

  it("hides old actor coordinates and makes no request under a different identity", async () => {
    const f = await fixture();
    render(<OpeningCountRecoveryPanel {...f.props} actor={{ ...actor, person_id: NEXT }} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.body.textContent).not.toContain(sentinel.trace_request_id);
    expect(document.body.textContent).not.toContain(PERSON);
    expect(f.requester).not.toHaveBeenCalled();
    expect(f.store.read(TASK).kind).toBe("valid");
  });

  it("allows another explicit GET after switching away and back to the original identity", async () => {
    const pending = deferred<unknown>();
    const f = await fixture({ result: pending.promise });
    const view = render(<OpeningCountRecoveryPanel {...f.props} />);
    fireEvent.click(screen.getByRole("button", { name: "只读核验原计数" }));
    await waitFor(() => expect(f.requester.mock.calls.some(([path]) => path.includes("count-command-status"))).toBe(true));
    view.rerender(<OpeningCountRecoveryPanel {...f.props} actor={{ ...actor, authorization_version: 8 }} />);
    await act(async () => { pending.resolve(status()); await pending.promise; });
    expect(f.onRecovered).not.toHaveBeenCalled();
    expect(f.store.read(TASK).kind).toBe("valid");
    view.rerender(<OpeningCountRecoveryPanel {...f.props} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "只读核验原计数" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "只读核验原计数" }));
    await waitFor(() => expect(f.onRecovered).toHaveBeenCalledTimes(1));
    expect(f.store.read(TASK).kind).toBe("missing");
  });

  it("refreshes a new cross-tab marker on storage events without starting recovery", async () => {
    const f = await fixture({ seed: false });
    const blocked = vi.fn();
    render(<OpeningCountRecoveryPanel {...f.props} onBlockedChange={blocked} />);
    expect(screen.queryByLabelText("盘点计数恢复")).toBeNull();
    await f.store.withTaskLease(TASK, async (lease) => { lease.persist(sentinel); });
    fireEvent(window, new Event("storage"));
    expect(screen.getByLabelText("盘点计数恢复")).toBeTruthy();
    expect(blocked).toHaveBeenLastCalledWith(true);
    expect(f.requester).not.toHaveBeenCalled();
  });

  it.each(["corrupt", "unavailable"])("blocks a %s record without deleting or querying", async (kind) => {
    const f = await fixture();
    if (kind === "corrupt") f.values.set([...f.values.keys()][0], "{broken");
    const store = kind === "unavailable" ? createOpeningCountRecoveryStore({ storage: null, locks: null }) : f.store;
    render(<OpeningCountRecoveryPanel {...f.props} store={store} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/本地恢复记录损坏或不可用/)).toBeTruthy();
    expect(f.requester).not.toHaveBeenCalled();
    expect(f.values.size).toBe(1);
  });

  it("keeps history on transport failure without showing raw error payloads", async () => {
    const f = await fixture();
    f.requester.mockRejectedValue(new Error("SECRET-KEY forbidden server payload"));
    render(<OpeningCountRecoveryPanel {...f.props} />);
    fireEvent.click(screen.getByRole("button", { name: "只读核验原计数" }));
    await screen.findByRole("alert");
    expect(document.body.textContent).not.toContain("SECRET-KEY");
    expect(f.store.read(TASK).kind).toBe("valid");
    expect(f.onRecovered).not.toHaveBeenCalled();
  });
});
