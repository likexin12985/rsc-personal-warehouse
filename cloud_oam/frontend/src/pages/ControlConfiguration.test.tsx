// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiNoReplay } from "../api";
import { commandDocument, handoffResponse, parseCommand, verifiedResponse } from "../controlConfiguration";
import ControlConfiguration from "./ControlConfiguration";
vi.mock("../api", async (original) => ({ ...await original<typeof import("../api")>(), apiNoReplay: vi.fn() }));
const id = "11111111-1111-4111-8111-111111111111", other = "22222222-2222-4222-8222-222222222222";
const actor = { person_id: id, authorization_version: 3 };
const command = commandDocument({ action: "source_grant", binding_id: id, evidence_file_id: other,
  evidence_sha256: "a".repeat(64), expected_subject_sha256: "b".repeat(64), reason: "核查浙江来源",
  idempotency_key: "configuration-123456", request_id: "request-123456" });
const envelope = (payload: Record<string, unknown>) => ({ payload, key_id: "c".repeat(16), signature: "d".repeat(128) });
function fixtures() {
  const now = Math.floor(Date.now() / 1000);
  const packet = envelope({ schema_version: "rsc.control_configuration_request.v1", handoff_id: id,
    deployment_id: id, database_id: id, actor_user_id: id, actor_person_id: id, auth_session_id: id, audit_event_id: id,
    authorization_version: 3, access_issued_at: now - 30, access_expires_at: now + 600, issued_at: now - 1, expires_at: now + 299,
    command, purpose: "preview", review_sha256: null });
  const result = { review: { schema_version: "rsc.inventory_control_configuration_review.v1", command,
    actor_user_id: id, actor_person_id: id, actor_authorization_version: 3,
    region: { name: "浙江", code: "ZJ" }, source: { code: "oam", mode: "read_only", enabled: true }, catalogue: null,
    evidence_file: { file_id: other, sha256: command.evidence_sha256, size_bytes: 100, mime_type: "application/pdf" },
  }, review_sha256: "e".repeat(64), projection_published: false, start_ready: false };
  const response = envelope({ schema_version: "rsc.control_configuration_response.v1", deployment_id: id, database_id: id,
    issued_at: now, expires_at: now + 300, request: packet, result });
  const view = { result, purpose: "preview", can_execute: true, issued_at: now, expires_at: now + 300 };
  return { packet, response, view };
}
async function upload(label: string, value: unknown) {
  fireEvent.change(screen.getByLabelText(label), { target: { files: [new File([JSON.stringify(value)], "request.json", { type: "application/json" })] } });
  await waitFor(() => expect((screen.getByLabelText(label) as HTMLInputElement).disabled).toBe(false));
}
describe("control configuration handoff", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(cleanup);
  it("requires a verified review and explicit confirmation, uses exact command and never claims a download is success", async () => {
    const f = fixtures();
    vi.mocked(apiNoReplay).mockImplementation(async (path, init) => {
      if (String(path).endsWith("inspect-response")) return f.view;
      const purpose = JSON.parse(String(init?.body)).purpose;
      return { handoff: envelope({ ...f.packet.payload, purpose, review_sha256: purpose === "preview" ? null : "e".repeat(64) }),
        decision_recorded: false, projection_published: false, start_ready: false };
    });
    render(<ControlConfiguration actor={actor} />);
    await upload("导入配置请求", { command, expected_authorization_version: 999 });
    fireEvent.click(screen.getByRole("button", { name: "生成核查交接包" }));
    await screen.findByRole("article", { name: "待交接文件" });
    expect(screen.queryByText(/配置决策已记录/)).toBeNull();
    await upload("导入执行端签名回执", f.response);
    const apply = screen.getByRole("button", { name: "确认并生成授权交接包" }) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("已核对来源、版本与审核证据"));
    fireEvent.click(apply); fireEvent.click(apply);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("授权交接包已生成"));
    const calls = vi.mocked(apiNoReplay).mock.calls.map(([, init]) => JSON.parse(String(init?.body)));
    expect(calls.filter(body => body.purpose === "execute")).toEqual([{ command, expected_authorization_version: 3, purpose: "execute", owner_response: f.response }]);
    expect(apply.disabled).toBe(true);
    expect(screen.queryByText(/配置决策已记录/)).toBeNull();
  });
  it("leaves uncertain issuance locked and offers exact status without replay", async () => {
    const f = fixtures();
    vi.mocked(apiNoReplay).mockImplementation(async path => {
      if (String(path).endsWith("inspect-response")) return f.view;
      throw new Error("timeout");
    });
    render(<ControlConfiguration actor={actor} />);
    await upload("导入配置请求", { command }); await upload("导入执行端签名回执", f.response);
    fireEvent.click(screen.getByLabelText("已核对来源、版本与审核证据"));
    fireEvent.click(screen.getByRole("button", { name: "确认并生成授权交接包" }));
    expect((await screen.findByRole("alert")).textContent).toContain("签发结果未确认");
    expect((screen.getByRole("button", { name: "确认并生成授权交接包" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "生成原结果查询包" })).toBeTruthy();
    expect(apiNoReplay).toHaveBeenCalledTimes(2);
  });
  it("discards late verification after the actor version changes", async () => {
    const f = fixtures(); let resolve!: (value: unknown) => void;
    vi.mocked(apiNoReplay).mockImplementation(() => new Promise(done => { resolve = done; }));
    const page = render(<ControlConfiguration actor={actor} />);
    await upload("导入配置请求", { command });
    fireEvent.change(screen.getByLabelText("导入执行端签名回执"), { target: { files: [new File([JSON.stringify(f.response)], "response.json")] } });
    await waitFor(() => expect(apiNoReplay).toHaveBeenCalledTimes(1));
    page.rerender(<ControlConfiguration actor={{ ...actor, authorization_version: 4 }} />);
    resolve(f.view);
    await waitFor(() => expect(screen.queryByLabelText("已验证核查内容")).toBeNull());
    expect(screen.queryByRole("button", { name: "确认并生成授权交接包" })).toBeNull();
  });
  it("clears previous review when an unverified replacement is imported", async () => {
    const f = fixtures(); vi.mocked(apiNoReplay).mockResolvedValue(f.view);
    render(<ControlConfiguration actor={actor} />);
    await upload("导入配置请求", { command }); await upload("导入执行端签名回执", f.response);
    await upload("导入执行端签名回执", { access_token: "unexpected" });
    expect(screen.queryByLabelText("已验证核查内容")).toBeNull();
    expect(apiNoReplay).toHaveBeenCalledTimes(1);
  });
  it("displays a recorded decision only after its signed response passes server inspection", async () => {
    const f = fixtures(), created = new Date().toISOString();
    const result = { recorded: true, projection_published: false, start_ready: false, execution_audit_event_id: id,
      decision: { decision_id: other, action: command.action, binding_id: command.binding_id, catalog_id: null,
        payload_sha256: "a".repeat(64), audit_event_id: id, created_at: created, valid_from: created, valid_to: null } };
    f.response.payload.request = envelope({ ...f.packet.payload, purpose: "status", review_sha256: "e".repeat(64) });
    f.response.payload.result = result;
    vi.mocked(apiNoReplay).mockResolvedValue({ ...f.view, purpose: "status", can_execute: false, result });
    render(<ControlConfiguration actor={actor} />);
    await upload("导入配置请求", { command });
    expect(screen.queryByText(/配置决策已记录/)).toBeNull();
    await upload("导入执行端签名回执", f.response);
    expect(screen.getByRole("status").textContent).toContain("配置决策已记录。控制账发布与期初启动仍需分别验收");
    expect(screen.getByText(`决策编号：${other}`)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认并生成授权交接包" })).toBeNull();
  });
  it("keeps a historical review available for status while disabling confirmation", async () => {
    const f = fixtures();
    f.packet.payload.authorization_version = 2;
    f.view.result.review.actor_authorization_version = 2;
    f.view.can_execute = false;
    vi.mocked(apiNoReplay).mockResolvedValue(f.view);
    render(<ControlConfiguration actor={actor} />);
    await upload("导入配置请求", { command }); await upload("导入执行端签名回执", f.response);
    expect((screen.getByLabelText("已核对来源、版本与审核证据") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "生成原结果查询包" })).toBeTruthy();
  });
  it.each(["publication", "actor", "command", "version", "expired", "signature", "extra"])("rejects a mismatched issued packet: %s", kind => {
    const f = fixtures();
    const value = { handoff: f.packet, decision_recorded: false, projection_published: false, start_ready: false };
    if (kind === "publication") value.projection_published = true;
    if (kind === "actor") f.packet.payload.actor_person_id = other;
    if (kind === "version") f.packet.payload.authorization_version = 4;
    if (kind === "command") f.packet.payload.command = { ...command, reason: "other" };
    if (kind === "expired") f.packet.payload.expires_at = 1;
    if (kind === "signature") f.packet.signature = "invalid";
    if (kind === "extra") f.packet.payload.access_token = "unexpected";
    expect(() => handoffResponse(value, command, actor, "preview")).toThrow();
  });
  it.each(["publication", "result", "cross_response", "evidence", "expiry"])("rejects a inconsistent inspected response: %s", kind => {
    const f = fixtures();
    if (kind === "publication") f.view.result.projection_published = true;
    if (kind === "result") f.view = { ...f.view, result: { ...f.view.result, review_sha256: "f".repeat(64) } };
    if (kind === "cross_response") f.response.payload.database_id = other;
    if (kind === "evidence") f.view.result.review.evidence_file.file_id = id;
    if (kind === "expiry") f.view.expires_at += 1;
    expect(() => verifiedResponse(f.view, f.response, command, actor)).toThrow();
  });
  it("rejects credential fields in imported request and keeps microsecond timestamps exact", () => {
    expect(() => parseCommand(JSON.stringify({ command, access_token: "unexpected" }))).toThrow();
    expect(() => parseCommand(JSON.stringify({ command: { ...command, secret: "unexpected" } }))).toThrow();
    const f = fixtures();
    const expected = commandDocument({ ...command, valid_from: "2026-09-20T01:00:00.123456Z" });
    f.packet.payload.command = { ...expected, valid_from: "2026-09-20T01:00:00.123456+00:00" };
    const value = { handoff: f.packet, decision_recorded: false, projection_published: false, start_ready: false };
    expect(handoffResponse(value, expected, actor, "preview")).toBe(f.packet);
    f.packet.payload.command = { ...expected, valid_from: "2026-09-20T01:00:00.123457+00:00" };
    expect(() => handoffResponse(value, expected, actor, "preview")).toThrow();
  });
});
