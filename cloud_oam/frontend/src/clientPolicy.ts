import type { AccessContext } from "./types";

export const ROLE_LABELS = {
  admin: "蔚来总部管理员",
  provincial_manager: "区域公司负责人",
  technician: "工程师",
  star_headquarters_approver: "星星总部审批人（外部审批身份）",
} as const;

export type SupportedRole = keyof typeof ROLE_LABELS;

const OPERATIONAL_CLIENT_ROLES = new Set<SupportedRole>([
  "admin",
  "provincial_manager",
  "technician",
]);

export const ISOLATED_ROLE_LABEL = "未授权旧角色（已隔离）";
export const LEGACY_READ_ONLY_LABEL = "v0.9 历史原型/只读";
export const LEGACY_WRITE_BLOCKED_MESSAGE =
  "正式客户端已隔离 v0.9 历史原型接口；请使用正式 V1 业务接口。";
export const INVALID_REQUEST_PATH_MESSAGE =
  "请求路径不是规范的站内相对路径，已停止发送。";

export function isSupportedRole(role: string): role is SupportedRole {
  return Object.prototype.hasOwnProperty.call(ROLE_LABELS, role);
}

export function canUseOperationalClient(role: string): role is Exclude<SupportedRole, "star_headquarters_approver"> {
  return isSupportedRole(role) && OPERATIONAL_CLIENT_ROLES.has(role);
}

export function roleLabel(role: string): string {
  return isSupportedRole(role) ? ROLE_LABELS[role] : ISOLATED_ROLE_LABEL;
}

export function hasFormalRole(context: AccessContext, role: SupportedRole): boolean {
  return context.role_codes.includes(role);
}

export function hasFormalPermission(
  context: AccessContext,
  resource: string,
  action: string,
  fieldCode = "",
): boolean {
  return context.permissions.some((permission) => (
    permission.resource === resource
    && permission.action === action
    && permission.field_code === fieldCode
  ));
}

export function canUseFormalOperationalClient(context: AccessContext): boolean {
  return context.account_status === "active"
    && context.employment_status === "active"
    && Number.isSafeInteger(context.authorization_version)
    && context.authorization_version > 0
    && context.access_mode === "active"
    && context.role_codes.length > 0
    && context.role_codes.every(isSupportedRole)
    && context.role_codes.some((role) => OPERATIONAL_CLIENT_ROLES.has(role));
}

export function formalRoleLabel(context: AccessContext): string {
  if (!context.role_codes.length || !context.role_codes.every(isSupportedRole)) {
    return ISOLATED_ROLE_LABEL;
  }
  const labels = context.role_codes
    .map((role) => ROLE_LABELS[role]);
  return labels.length ? labels.join(" / ") : ISOLATED_ROLE_LABEL;
}

function normalizedPath(path: string): string | null {
  if (!path || path.includes("#") || path.includes("\\")) return null;
  const pathname = path.split("?", 1)[0];
  if (
    !pathname.startsWith("/")
    || pathname.startsWith("//")
    || pathname.includes("//")
    || pathname.includes("%")
    || /[\u0000-\u0020\u007f]/.test(pathname)
    || pathname.split("/").some((segment) => segment === "." || segment === "..")
  ) return null;
  return pathname.replace(/\/+$/, "") || "/";
}

export function blockedClientWriteReason(path: string, method = "GET"): string | null {
  const cleanPath = normalizedPath(path);
  if (cleanPath === null) return INVALID_REQUEST_PATH_MESSAGE;
  const blockedPrefixes = [
    "/auth/login",
    "/auth/change-password",
    "/auth/miniprogram/password-login",
    "/auth/users",
    "/dashboard",
    "/inventory",
    "/materials",
    "/warehouses",
    "/transfers",
    "/work-order-materials",
    "/stocktakes",
    "/media",
    "/integrations/oam/personnel",
    "/integrations/oam/orders",
  ];

  if (blockedPrefixes.some(
    (prefix) => cleanPath === prefix || cleanPath.startsWith(`${prefix}/`),
  )) {
    return LEGACY_WRITE_BLOCKED_MESSAGE;
  }
  return null;
}
