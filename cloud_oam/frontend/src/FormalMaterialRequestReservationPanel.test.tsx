// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import FormalMaterialRequestReservationPanel from "./FormalMaterialRequestReservationPanel";
import { createReservationRecoveryStore } from "./materialRequestReservationRecovery";
import { REQUEST_ID, LINE_ID, SERIAL_ID, SERIAL_2_ID, approvedDetail, access, identity, reservationPage, afterReservation, reservationResult } from "./materialRequestReservationTestFixtures";

afterEach(() => { cleanup(); sessionStorage.clear(); });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
function adapter(overrides: Record<string, unknown> = {}): any {
  return { listReservationOptions: vi.fn().mockResolvedValue(reservationPage()),
    loadIdentityNoReplay: vi.fn().mockResolvedValue(identity()), loadAccessNoReplay: vi.fn().mockResolvedValue(access()),
    detailNoReplay: vi.fn().mockResolvedValueOnce(approvedDetail()).mockResolvedValue(afterReservation()),
    createReservation: vi.fn().mockResolvedValue(reservationResult()),
    reservationCommandStatusNoReplay: vi.fn().mockResolvedValue({ schema_version: "1.0", lookup_status: "not_observed", command: null }),
    loadIdentity: vi.fn(), loadAccess: vi.fn(), detail: vi.fn(), ...overrides };
}
function props(overrides: Record<string, unknown> = {}): any {
  return { adapter: adapter(), access: access(), detail: approvedDetail(), store: createReservationRecoveryStore(),
    otherWriteBusy: false, onBlocking: vi.fn(), onDetail: vi.fn(), ...overrides };
}
async function select() {
  fireEvent.click(screen.getByRole("button", { name: "查看可预留库存" }));
  fireEvent.click(await screen.findByRole("button", { name: "选择并预留" }));
}
async function submit() { await select(); fireEvent.click(screen.getByRole("button", { name: "确认预留" })); }

describe("formal reservation panel", () => {
  it("reads candidates without writing and persists original intent before the one POST", async () => {
    const p = props();
    p.adapter.createReservation.mockImplementation(async (_id: string, body: any) => {
      const saved = p.store.read(); expect(saved.kind).toBe("valid"); expect(saved.value.reserved_qty).toBe(body.reserved_qty);
      expect(saved.value.revision_id).toBe(approvedDetail().current_revision_id); return reservationResult();
    });
    render(<FormalMaterialRequestReservationPanel {...p} />);
    await select(); expect(p.adapter.createReservation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认预留" }));
    await waitFor(() => expect(p.onDetail).toHaveBeenCalledWith(afterReservation()));
    expect(p.store.read().kind).toBe("missing"); expect(p.adapter.createReservation).toHaveBeenCalledTimes(1);
    expect(p.adapter.listReservationOptions).toHaveBeenCalledWith(REQUEST_ID, LINE_ID);
    expect(p.adapter.loadIdentity).not.toHaveBeenCalled(); expect(p.adapter.detail).not.toHaveBeenCalled();
  });
  it.each(["10", "100"])("preserves integer-scale candidate quantity %s", async (amount) => {
    const page = reservationPage(); const item = page.items[0]; item.quantity_scale = 0;
    for (const key of ["quantity", "allocated_qty", "remaining_qty", "reservable_qty"]) item[key] = amount;
    item.reserved_qty = "0";
    const p = props({ adapter: adapter({ listReservationOptions: vi.fn().mockResolvedValue(page) }) });
    render(<FormalMaterialRequestReservationPanel {...p} />); await select();
    expect((screen.getByRole("textbox", { name: "预留数量" }) as HTMLInputElement).value).toBe(amount);
  });
  it("selects SN candidates and checks their exact returned set", async () => {
    const result = { ...reservationResult(), serial_ids: [SERIAL_2_ID, SERIAL_ID] };
    const p = props({ adapter: adapter({ listReservationOptions: vi.fn().mockResolvedValue(reservationPage(true)), createReservation: vi.fn().mockResolvedValue(result) }) });
    render(<FormalMaterialRequestReservationPanel {...p} />); await select();
    expect(screen.queryByRole("textbox", { name: /UUID/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "确认预留" }));
    await waitFor(() => expect(screen.getAllByText(/请从当前候选勾选/).length).toBeGreaterThan(0));
    expect(p.adapter.createReservation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: "预留 SN SN-A" })); fireEvent.click(screen.getByRole("checkbox", { name: "预留 SN SN-B" }));
    fireEvent.click(screen.getByRole("button", { name: "确认预留" }));
    await waitFor(() => expect(p.onDetail).toHaveBeenCalled());
    expect(p.adapter.createReservation.mock.calls[0][1].serial_ids).toEqual([SERIAL_ID, SERIAL_2_ID]);
  });
  it.each(["0", "3", "1.0001"])("blocks invalid or excessive quantity %s before POST", async (value) => {
    const p = props(); render(<FormalMaterialRequestReservationPanel {...p} />); await select();
    fireEvent.change(screen.getByRole("textbox", { name: "预留数量" }), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "确认预留" }));
    await waitFor(() => expect(p.onBlocking).toHaveBeenLastCalledWith(false));
    expect(p.adapter.createReservation).not.toHaveBeenCalled();
  });
  it("does not POST when durable storage fails", async () => {
    const p = props({ store: createReservationRecoveryStore({ getItem: () => null, setItem: () => { throw new Error("quota"); }, removeItem: vi.fn() }) });
    render(<FormalMaterialRequestReservationPanel {...p} />); await submit();
    await waitFor(() => expect(screen.getAllByText(/quota/).length).toBeGreaterThan(0));
    expect(p.adapter.createReservation).not.toHaveBeenCalled();
  });
  it("keeps a partial storage failure blocked without POST", async () => {
    let raw: string | null = null;
    const p = props({ store: createReservationRecoveryStore({ getItem: () => raw, setItem: () => { raw = "invalid"; throw new Error("partial storage failure"); }, removeItem: vi.fn() }) });
    render(<FormalMaterialRequestReservationPanel {...p} />); await submit();
    await waitFor(() => expect(screen.getAllByText(/partial storage failure/).length).toBeGreaterThan(0));
    expect(p.adapter.createReservation).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("corrupt");
    expect(p.onBlocking).toHaveBeenLastCalledWith(true);
  });
  it("blocks POST if the synchronous other-write guard becomes active without a rerender", async () => {
    let blocked = false; const p = props({ otherWriteBlocked: () => blocked });
    render(<FormalMaterialRequestReservationPanel {...p} />); await select(); blocked = true;
    fireEvent.click(screen.getByRole("button", { name: "确认预留" }));
    expect(p.adapter.loadIdentityNoReplay).not.toHaveBeenCalled(); expect(p.adapter.createReservation).not.toHaveBeenCalled();
  });
  it("rechecks the synchronous write guard after a delayed identity preflight without rerender", async () => {
    const pending = deferred<any>(); let blocked = false;
    const store = createReservationRecoveryStore(); const persist = vi.fn((value) => store.persist(value));
    const p = props({ store: { ...store, persist }, otherWriteBlocked: () => blocked,
      adapter: adapter({ loadIdentityNoReplay: vi.fn().mockReturnValue(pending.promise) }) });
    render(<FormalMaterialRequestReservationPanel {...p} />); await submit();
    expect(p.adapter.loadIdentityNoReplay).toHaveBeenCalledTimes(1);
    blocked = true; pending.resolve(identity());
    await waitFor(() => expect(screen.getAllByText(/其他写入正在进行或结果待核验/).length).toBeGreaterThan(0));
    expect(persist).not.toHaveBeenCalled(); expect(p.adapter.createReservation).not.toHaveBeenCalled();
    expect(store.read().kind).toBe("missing");
  });
  it("retains unknown result and never repeats the POST, then recovers with GET evidence", async () => {
    const p = props({ adapter: adapter({ createReservation: vi.fn().mockRejectedValue(new Error("network")) }) });
    render(<FormalMaterialRequestReservationPanel {...p} />); await submit();
    await waitFor(() => expect(screen.getByRole("button", { name: "核验原预留操作" })).toBeTruthy());
    expect(p.store.read().kind).toBe("valid");
    fireEvent.click(screen.getByRole("button", { name: "核验原预留操作" }));
    await waitFor(() => expect(p.adapter.reservationCommandStatusNoReplay).toHaveBeenCalledTimes(1));
    expect(p.store.read().kind).toBe("valid"); expect(p.adapter.createReservation).toHaveBeenCalledTimes(1);
    p.adapter.reservationCommandStatusNoReplay.mockResolvedValue({ schema_version: "1.0", lookup_status: "confirmed", command: { ...reservationResult(), idempotency_replayed: true } });
    await waitFor(() => expect((screen.getByRole("button", { name: "核验原预留操作" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "核验原预留操作" }));
    await waitFor(() => expect(p.store.read().kind).toBe("missing")); expect(p.adapter.createReservation).toHaveBeenCalledTimes(1);
  });
  it.each(["version", "balance", "serial", "axis"])("keeps original coordinate on %s response mismatch", async (kind) => {
    const result = reservationResult();
    if (kind === "version") result.request_version = 5;
    if (kind === "balance") result.source_balance_version = 12;
    if (kind === "serial") result.serial_ids = [SERIAL_ID, SERIAL_2_ID];
    if (kind === "axis") result.state_axes.shipment_status = "shipped";
    const p = props({ adapter: adapter({ createReservation: vi.fn().mockResolvedValue(result) }) });
    render(<FormalMaterialRequestReservationPanel {...p} />); await submit();
    await waitFor(() => expect(p.adapter.createReservation).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(p.onBlocking).toHaveBeenLastCalledWith(true));
    expect(p.store.read().kind).toBe("valid"); expect(p.onDetail).not.toHaveBeenCalled();
  });
  it("rejects a changed source snapshot before persisting or posting", async () => {
    const changed = reservationPage(); changed.items[0].location_name = "另一库位";
    const p = props({ adapter: adapter({ listReservationOptions: vi.fn().mockResolvedValueOnce(reservationPage()).mockResolvedValue(changed) }) });
    render(<FormalMaterialRequestReservationPanel {...p} />); await submit();
    await waitFor(() => expect(screen.getAllByText(/候选已变化/).length).toBeGreaterThan(0));
    expect(p.adapter.createReservation).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
  });
  it("cannot submit after route switch while preflight is awaiting", async () => {
    const pending = deferred<any>(); const p = props({ adapter: adapter({ loadIdentityNoReplay: vi.fn().mockReturnValue(pending.promise) }) });
    const view = render(<FormalMaterialRequestReservationPanel {...p} />); await submit();
    view.rerender(<FormalMaterialRequestReservationPanel {...p} detail={null} />); pending.resolve(identity());
    await waitFor(() => expect(p.adapter.listReservationOptions).toHaveBeenCalledTimes(2));
    expect(p.adapter.createReservation).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
  });
  it("retains a late POST result after unmount for read-only recovery", async () => {
    const pending = deferred<any>(); const p = props({ adapter: adapter({ createReservation: vi.fn().mockReturnValue(pending.promise) }) });
    const view = render(<FormalMaterialRequestReservationPanel {...p} />); await submit();
    await waitFor(() => expect(p.adapter.createReservation).toHaveBeenCalledTimes(1));
    view.unmount(); pending.resolve(reservationResult());
    await waitFor(() => expect(p.adapter.loadIdentityNoReplay).toHaveBeenCalledTimes(2));
    expect(p.store.read().kind).toBe("valid"); expect(p.onDetail).not.toHaveBeenCalled();
  });
});
