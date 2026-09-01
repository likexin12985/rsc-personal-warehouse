import { describe, expect, it } from "vitest";

import {
  INVALID_REQUEST_PATH_MESSAGE,
  ISOLATED_ROLE_LABEL,
  blockedClientWriteReason,
  canUseFormalOperationalClient,
  canUseOperationalClient,
  formalRoleLabel,
  hasFormalPermission,
  hasFormalRole,
  isSupportedRole,
  roleLabel,
} from "./clientPolicy";
import type { AccessContext } from "./types";

function accessContext(overrides: Partial<AccessContext> = {}): AccessContext {
  return {
    person_id: "person-1",
    account_status: "active",
    employment_status: "active",
    authorization_version: 3,
    access_mode: "active",
    role_codes: ["technician", "admin"],
    assignments: [],
    permissions: [
      { resource: "role_assignment", action: "manage_provincial", field_code: "" },
    ],
    ...overrides,
  };
}

describe("formal client roles", () => {
  it.each([
    ["admin", "蔚来总部管理员"],
    ["provincial_manager", "区域公司负责人"],
    ["technician", "工程师"],
    ["star_headquarters_approver", "星星总部审批人（外部审批身份）"],
  ])("renders only the supported identity %s", (role, label) => {
    expect(isSupportedRole(role)).toBe(true);
    expect(roleLabel(role)).toBe(label);
  });

  it.each(["auditor", "warehouse_manager", "unknown_legacy_role"])(
    "fails closed for retired or unknown role %s",
    (role) => {
      expect(isSupportedRole(role)).toBe(false);
      expect(canUseOperationalClient(role)).toBe(false);
      expect(roleLabel(role)).toBe(ISOLATED_ROLE_LABEL);
    },
  );

  it("keeps the external approver out of the operational inventory client", () => {
    expect(isSupportedRole("star_headquarters_approver")).toBe(true);
    expect(canUseOperationalClient("star_headquarters_approver")).toBe(false);
  });

  it("uses the formal multi-role context instead of the legacy single role", () => {
    const context = accessContext();
    expect(hasFormalRole(context, "admin")).toBe(true);
    expect(hasFormalPermission(context, "role_assignment", "manage_provincial")).toBe(true);
    expect(formalRoleLabel(context)).toBe("工程师 / 蔚来总部管理员");
    expect(canUseFormalOperationalClient(context)).toBe(true);
  });

  it("fails closed when only restricted handover access remains", () => {
    expect(canUseFormalOperationalClient(accessContext({
      access_mode: "restricted_handover",
      role_codes: ["technician"],
    }))).toBe(false);
  });

  it("fails closed when an unknown role is mixed into a formal context", () => {
    const context = accessContext({ role_codes: ["admin", "unexpected_role"] });
    expect(canUseFormalOperationalClient(context)).toBe(false);
    expect(formalRoleLabel(context)).toBe(ISOLATED_ROLE_LABEL);
  });
});

describe("formal client write quarantine", () => {
  it.each([
    ["/auth/login", "POST"],
    ["/auth/miniprogram/password-login", "POST"],
    ["/auth/change-password", "POST"],
    ["/auth/users", "POST"],
    ["/auth/users/id", "DELETE"],
    ["/inventory/adjust", "POST"],
    ["/materials", "POST"],
    ["/materials/id", "PUT"],
    ["/warehouses", "POST"],
    ["/transfers", "POST"],
    ["/transfers/id/approve", "POST"],
    ["/transfers/id/dispatch", "POST"],
    ["/transfers/id/receive", "POST"],
    ["/transfers/id/cancel", "POST"],
    ["/work-order-materials/batch", "POST"],
    ["/work-order-materials/id/recover", "POST"],
    ["/stocktakes", "POST"],
    ["/stocktakes/id/items/item-id", "PUT"],
    ["/media/transfer/id", "POST"],
    ["/media/stocktake/id", "POST"],
    ["/integrations/oam/personnel/id/enable", "POST"],
    ["/dashboard", "GET"],
    ["/inventory", "GET"],
    ["/materials?limit=500", "GET"],
    ["/transfers?limit=500", "GET"],
    ["/transfers/id", "GET"],
  ])("blocks %s %s before transport", (path, method) => {
    expect(blockedClientWriteReason(path, method)).toContain("已隔离");
  });

  it.each([
    ["/v1/inventory/summary", "GET"],
    ["/v1/inventory/personal/me", "GET"],
    ["/v1/stocktakes/opening", "GET"],
    ["/v1/stocktakes/opening/10000000-0000-4000-8000-000000000001", "GET"],
    ["/v1/stocktakes/opening/10000000-0000-4000-8000-000000000001/rounds/20000000-0000-4000-8000-000000000002/scopes/30000000-0000-4000-8000-000000000003/count", "POST"],
    ["/v1/stocktakes/opening/10000000-0000-4000-8000-000000000001/rounds/20000000-0000-4000-8000-000000000002/reviews/region", "POST"],
    ["/v1/stocktakes/opening/10000000-0000-4000-8000-000000000001/rounds/20000000-0000-4000-8000-000000000002/reviews/headquarters", "POST"],
    ["/v1/stocktakes/opening/10000000-0000-4000-8000-000000000001/rounds/20000000-0000-4000-8000-000000000002/recount", "POST"],
    ["/v1/stocktakes/opening/10000000-0000-4000-8000-000000000001/post", "POST"],
    ["/v1/stocktakes/opening/10000000-0000-4000-8000-000000000001/close", "POST"],
    ["/v1/stocktakes", "GET"],
    ["/v1/stocktakes", "POST"],
    ["/v1/stocktakes/personal", "POST"],
    ["/v1/stocktakes/10000000-0000-4000-8000-000000000001/start", "POST"],
    ["/v1/stocktakes/10000000-0000-4000-8000-000000000001/rounds/20000000-0000-4000-8000-000000000002/scopes/30000000-0000-4000-8000-000000000003/initial-count", "POST"],
    ["/v1/stocktakes/10000000-0000-4000-8000-000000000001/rounds/20000000-0000-4000-8000-000000000002/scopes/30000000-0000-4000-8000-000000000003/recount-count", "POST"],
    ["/v1/stocktakes/10000000-0000-4000-8000-000000000001/rounds/20000000-0000-4000-8000-000000000002/recount", "POST"],
    ["/v1/stocktakes/10000000-0000-4000-8000-000000000001/post-differences", "POST"],
    ["/v1/stocktakes/10000000-0000-4000-8000-000000000001/reconcile", "POST"],
    ["/v1/stocktakes/10000000-0000-4000-8000-000000000001/close", "POST"],
    ["/v1/files/upload-intents", "POST"],
    ["/v1/files/90000000-0000-4000-8000-000000000001/complete", "POST"],
    ["/v1/stocktake-options/assignees?region_org_id=20000000-0000-4000-8000-000000000002&location_id=30000000-0000-4000-8000-000000000003", "GET"],
    ["/v1/reconciliations/opening", "GET"],
    ["/v1/reconciliations/opening/tasks/10000000-0000-4000-8000-000000000001", "POST"],
    ["/v1/reconciliations/opening/20000000-0000-4000-8000-000000000002", "GET"],
    ["/v1/reconciliations/opening/20000000-0000-4000-8000-000000000002/explanations", "POST"],
    ["/v1/reconciliations/opening/20000000-0000-4000-8000-000000000002/approve", "POST"],
    ["/v1/material-requests", "GET"],
    ["/v1/material-requests/10000000-0000-4000-8000-000000000001", "PUT"],
    ["/v1/material-requests/10000000-0000-4000-8000-000000000001/submit", "POST"],
    ["/v1/material-requests/10000000-0000-4000-8000-000000000001/approval-steps/20000000-0000-4000-8000-000000000002/decision", "POST"],
    ["/auth/me", "GET"],
    ["/auth/sessions/id/revoke", "POST"],
    ["/access/provincial-managers/assignments", "POST"],
  ])("allows in-scope request %s %s", (path, method) => {
    expect(blockedClientWriteReason(path, method)).toBeNull();
  });

  it.each([
    "/inventory#client-fragment",
    "/v1/inventory/summary#client-fragment",
    "/v1%2finventory/summary",
    "/v1/%2e%2e/inventory",
    "/v1/../inventory",
    "/v1//inventory",
    "\\inventory",
    "https://example.com/v1/inventory/summary",
    "//example.com/v1/inventory/summary",
  ])("rejects non-canonical request path %s", (path) => {
    expect(blockedClientWriteReason(path, "GET")).toBe(INVALID_REQUEST_PATH_MESSAGE);
  });
});
