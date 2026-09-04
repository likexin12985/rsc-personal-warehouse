import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BarChart3,
  Boxes,
  ClipboardCheck,
  ClipboardList,
  LogOut,
  Menu,
  MessageSquareText,
  Scale,
  Smartphone,
  UserCog,
  X,
} from "lucide-react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import {
  api,
  ApiError,
  jsonBody,
  mutationHeaders,
  subscribeAuthenticationEstablished,
  subscribeAuthenticationTerminalLogout,
} from "./api";
import {
  canUseFormalOperationalClient,
  formalRoleLabel,
  hasFormalPermission,
  isSupportedRole,
  roleLabel,
} from "./clientPolicy";
import { validateInventorySummary } from "./formalInventory";
import { createFormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
import { createFormalStocktakeAdapter } from "./formalStocktakeAdapter";
import ProvincialManagersPage from "./pages/ProvincialManagers";
import FormalInventoryPage from "./pages/FormalInventory";
import FormalMaterialRequestsPage from "./pages/FormalMaterialRequests";
import FormalOpeningReconciliationsPage from "./pages/FormalOpeningReconciliations";
import FormalOpeningStocktakesPage from "./pages/FormalOpeningStocktakes";
import FormalStocktakesPage from "./pages/FormalStocktakes";
import type { AccessContext, AuthenticatedUser, FormalUser, InventorySummary } from "./types";
import { Button, Field, Loading, showError } from "./ui";

const BRAND_ICON_SRC = "/brand/rsc-personal-warehouse-blue.png";

function Logo() {
  return <img className="brand-mark" src={BRAND_ICON_SRC} alt="RSC个人仓" />;
}

function BrandAvatar({ className = "account-avatar", label }: { className?: string; label: string }) {
  return <img className={className} src={BRAND_ICON_SRC} alt={label} />;
}

function normalizeAuthenticatedUser(value: AuthenticatedUser): FormalUser {
  if (
    !Array.isArray(value.role_codes)
    || value.role_codes.length === 0
    || value.role_codes.some((role) => !isSupportedRole(role))
  ) {
    throw new ApiError(403, "登录响应包含未授权角色");
  }
  const formalRole = ["admin", "provincial_manager", "technician", "star_headquarters_approver"]
    .find((role) => value.role_codes?.includes(role));
  const id = value.person_id || "";
  const authorizationVersion = value.authorization_version;
  if (
    !id
    || !value.name
    || !Number.isSafeInteger(authorizationVersion)
    || (authorizationVersion || 0) <= 0
    || !value.account_status
    || !value.employment_status
    || !value.access_mode
    || !formalRole
  ) throw new ApiError(500, "登录响应缺少正式人员身份或授权版本");
  return {
    id,
    mobile: value.mobile || "",
    name: value.name,
    role: formalRole,
    province: value.province ?? null,
    require_password_change: value.require_password_change ?? false,
    employee_no: value.employee_no,
    organization_code: value.organization_code,
    organization_name: value.organization_name,
    account_status: value.account_status,
    employment_status: value.employment_status,
    access_mode: value.access_mode,
    authorization_version: authorizationVersion as number,
    role_codes: [...value.role_codes],
  };
}

export function Login({ onLogin }: { onLogin: (user: FormalUser) => void }) {
  const [options, setOptions] = useState({
    sms_enabled: false,
    sms_interval_seconds: 60,
  });
  const [mobile, setMobile] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [countdown, setCountdown] = useState(0);

  useEffect(() => {
    api<typeof options>("/auth/login-options")
      .then(setOptions)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (countdown <= 0) return undefined;
    const timer = window.setTimeout(() => setCountdown((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [countdown]);

  async function sendCode() {
    if (!options.sms_enabled) {
      setError("手机验证码登录尚未启用，正式客户端已关闭密码登录。请联系管理员。");
      return;
    }
    const normalizedMobile = mobile.trim();
    if (!/^1[3-9]\d{9}$/.test(normalizedMobile)) {
      setError("请输入正确的11位手机号");
      return;
    }
    setSending(true);
    setError("");
    setNotice("");
    try {
      const result = await api<{ message: string; retry_after: number }>("/auth/sms/request", {
        method: "POST",
        ...mutationHeaders("sms-request"),
        ...jsonBody({ mobile: normalizedMobile }),
      });
      setNotice(result.message);
      setCountdown(result.retry_after || options.sms_interval_seconds);
    } catch (err) {
      setError(showError(err));
    } finally {
      setSending(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!options.sms_enabled) {
      setError("手机验证码登录尚未启用，正式客户端已关闭密码登录。请联系管理员。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const authenticated = await api<AuthenticatedUser>("/auth/sms/login", {
        method: "POST",
        ...mutationHeaders("sms-login"),
        ...jsonBody({ mobile: mobile.trim(), code }),
      });
      onLogin(normalizeAuthenticatedUser(authenticated));
    } catch (err) {
      setError(showError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-panel">
        <div className="login-brand"><Logo /><div><strong>RSC个人仓</strong><span>仓储与流转协同</span></div></div>
        <h1>登录工作台</h1>
        <p>使用已完成唯一身份映射的手机号验证码登录</p>
        <form onSubmit={submit}>
          <Field label="手机号"><input inputMode="tel" autoComplete="username" value={mobile} onChange={(e) => setMobile(e.target.value)} placeholder="请输入手机号" required /></Field>
          <div className="otp-row">
            <Field label="验证码"><input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 8))} placeholder="请输入验证码" minLength={4} maxLength={8} required /></Field>
            <Button type="button" tone="secondary" className="otp-send-button" icon={<MessageSquareText size={17} />} disabled={sending || countdown > 0} onClick={sendCode}>{countdown > 0 ? `${countdown}秒后重发` : sending ? "正在发送" : "获取验证码"}</Button>
          </div>
          {!options.sms_enabled && <div className="alert alert-error">当前环境未启用手机验证码登录，密码入口已按正式版基线隔离。</div>}
          {notice && <div className="form-notice">{notice}</div>}
          {error && <div className="form-error">{error}</div>}
          <Button type="submit" disabled={busy || !options.sms_enabled} className="button-full" icon={<Smartphone size={18} />}>{busy ? "正在登录" : "验证码登录"}</Button>
        </form>
        <small className="login-footnote">正式环境不提供密码登录或改密入口。验证码只发送至已登记手机号。</small>
      </section>
      <aside className="login-context"><div><span>面向全国服务团队</span><strong>一个入口，查清每一件物料</strong><p>当前仅开放已通过正式安全边界的查询能力，业务写模块将按 V1.0 阶段验收后逐项开放。</p></div></aside>
    </main>
  );
}

function IsolatedIdentity({
  user,
  access,
  accessError,
  logoutError,
  logoutPending,
  onLogout,
}: {
  user: FormalUser;
  access: AccessContext | null;
  accessError: string;
  logoutError: string;
  logoutPending: boolean;
  onLogout: () => Promise<void>;
}) {
  const externalApprover = access
    ? access.role_codes.includes("star_headquarters_approver")
    : user.role === "star_headquarters_approver";
  const title = externalApprover ? "外部审批身份已受限" : "旧角色已隔离";
  const detail = accessError || (externalApprover
    ? "该身份未同时具备正式业务端所需的内部角色，已停止进入；三级审批只能通过已验收的正式需求接口处理，不会回退 v0.9 Transfer。"
    : "该账号不是正式版允许的业务身份。为避免继承旧原型权限，客户端已拒绝进入库存与审批功能。");
  return <main className="access-block-page">
    <section className="access-block-card">
      <Logo />
      <span className="access-block-kicker">{access ? formalRoleLabel(access) : isSupportedRole(user.role) ? roleLabel(user.role) : "身份校验失败"}</span>
      <h1>{title}</h1>
      <p>{detail}</p>
      <div className="alert alert-info">v0.9 历史原型仅可作为只读历史，不授予任何库存或审批写权限。</div>
      {logoutError && <div className="alert alert-error" role="alert">{logoutError}</div>}
      <Button tone="secondary" icon={<LogOut size={18} />} disabled={logoutPending} onClick={onLogout}>
        {logoutPending ? "正在确认退出" : "退出登录"}
      </Button>
    </section>
  </main>;
}

function projectionTimeLabel(value: string | null): string {
  if (!value) return "尚无已过账流水";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function FormalHome({ user, access }: { user: FormalUser; access: AccessContext }) {
  const [inventory, setInventory] = useState<InventorySummary | null>(null);
  const [inventoryError, setInventoryError] = useState("");
  const [inventoryLoading, setInventoryLoading] = useState(false);
  const canReadInventory = hasFormalPermission(access, "inventory", "read");

  useEffect(() => {
    if (!canReadInventory) {
      setInventory(null);
      setInventoryError("");
      setInventoryLoading(false);
      return undefined;
    }
    let cancelled = false;
    setInventory(null);
    setInventoryError("");
    setInventoryLoading(true);
    api<unknown>("/v1/inventory/summary")
      .then(validateInventorySummary)
      .then((result) => { if (!cancelled) setInventory(result); })
      .catch((error) => {
        if (!cancelled) {
          setInventory(null);
          setInventoryError(showError(error));
        }
      })
      .finally(() => { if (!cancelled) setInventoryLoading(false); });
    return () => { cancelled = true; };
  }, [canReadInventory, access.authorization_version, access.person_id]);

  const openingEstablished = inventory?.opening_balance_status === "established";
  return <section>
    <div className="section-header">
      <div><h1>{user.name}，欢迎进入 RSC 个人仓</h1><p>正式身份已验证；仅显示当前已经通过 P0 验收的能力。</p></div>
    </div>
    <div className="metric-grid">
      <article className="metric"><div><span>当前角色</span><strong>{formalRoleLabel(access)}</strong></div></article>
      <article className="metric"><div><span>所属组织</span><strong>{user.organization_name || "正式组织"}</strong></div></article>
      <article className="metric"><div><span>权限版本</span><strong>v{access.authorization_version}</strong></div></article>
    </div>
    <section className="content-section formal-inventory-status" aria-label="正式库存账状态">
      <div className="content-title">
        <div><h2>正式库存账</h2><p>只读 V1 账本投影；不读取 v0.9 余额，不将 OAM 控制数计入个人仓</p></div>
        {inventory && <span className={`status ${openingEstablished ? "status-approved" : "status-pending"}`}>
          {openingEstablished ? "期初已建立" : "期初未建立"}
        </span>}
      </div>
      {!canReadInventory && <div className="alert alert-info">当前正式权限不包含库存只读能力，未发起库存请求。</div>}
      {inventoryLoading && <Loading label="正在读取正式库存账" />}
      {inventoryError && <div className="alert alert-error" role="alert">正式库存响应未通过校验：{inventoryError}</div>}
      {inventory && <>
        <div className="formal-inventory-facts">
          <div><span>账本游标</span><strong>{inventory.ledger_cursor}</strong></div>
          <div><span>最近过账</span><strong>{projectionTimeLabel(inventory.projected_at)}</strong></div>
          <div><span>授权范围</span><strong>{inventory.scopes.length} 个</strong></div>
        </div>
        <div className={`alert ${openingEstablished ? "alert-info" : "alert-warning"}`}>
          {openingEstablished
            ? "期初状态已由服务端确认。不同 SKU 和计量单位必须按物料维度读取，本页不做数量求和。"
            : "单笔 opening 流水不能证明期初完整建立。首次盘点、区域负责人复核和蔚来总部管理员复核完成前，全部库存数量保持空值。"}
        </div>
      </>}
    </section>
    <div className="alert alert-info">
      正式模块仅使用 V1 权限与接口。审批、分配、占用、出库、发货、物流签收、OAM 收货、个人仓入库、通知送达和对账同步保持独立；控制账对账只消费云端已校验快照，不借用 v0.9 页面或回写 OAM。
    </div>
  </section>;
}

function Shell({
  user,
  access,
  logoutError,
  logoutPending,
  onLogout,
  children,
}: {
  user: FormalUser;
  access: AccessContext;
  logoutError: string;
  logoutPending: boolean;
  onLogout: () => Promise<void>;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);
  const nav = useMemo(() => [
    { to: "/dashboard", label: "首页", icon: BarChart3 },
    ...(hasFormalPermission(access, "inventory", "read") ? [{ to: "/inventory", label: "库存账户", icon: Boxes }] : []),
    ...(hasFormalPermission(access, "material_request", "read") ? [{ to: "/material-requests", label: "需求提报", icon: ClipboardList }] : []),
    ...(hasFormalPermission(access, "stocktake", "read") ? [{ to: "/stocktakes", label: "日常盘点", icon: ClipboardCheck }] : []),
    ...(hasFormalPermission(access, "stocktake", "read") ? [{ to: "/opening-stocktakes", label: "盘点中心", icon: ClipboardCheck }] : []),
    ...(hasFormalPermission(access, "reconciliation", "read") ? [{ to: "/opening-reconciliations", label: "控制账对账", icon: Scale }] : []),
    ...(hasFormalPermission(access, "role_assignment", "manage_provincial") ? [{ to: "/provincial-managers", label: "省负责人", icon: UserCog }] : []),
  ], [access]);

  return <div className="app-shell">
    <header className="mobile-header"><button className="icon-button" aria-label="菜单" onClick={() => setOpen(true)}><Menu size={22} /></button><div className="mobile-brand"><Logo /><strong>RSC个人仓</strong></div><BrandAvatar className="mobile-account-avatar" label={`${user.name}账号`} /></header>
    <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
      <div className="sidebar-brand"><Logo /><div><strong>RSC个人仓</strong><span>仓储与流转协同</span></div><button className="sidebar-close" onClick={() => setOpen(false)} aria-label="关闭菜单"><X size={20} /></button></div>
      <nav className="side-nav">{nav.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => isActive ? "active" : ""}><Icon size={19} /><span>{label}</span></NavLink>)}</nav>
      <div className="sidebar-spacer" />
      <div className="account-block"><BrandAvatar label={`${user.name}账号`} /><div><strong>{user.name}</strong><span>{formalRoleLabel(access)}</span></div></div>
      <button className="logout-button" disabled={logoutPending} onClick={onLogout}><LogOut size={18} /><span>{logoutPending ? "正在确认退出" : "退出登录"}</span></button>
    </aside>
    {open && <button className="sidebar-mask" aria-label="关闭菜单" onClick={() => setOpen(false)} />}
    <div className="main-column"><main className="main-content">
      {logoutError && <div className="alert alert-error" role="alert">{logoutError}</div>}
      {children}
    </main></div>
    <nav className="bottom-nav">{nav.slice(0, 5).map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => isActive ? "active" : ""}><Icon size={21} /><span>{label}</span></NavLink>)}</nav>
  </div>;
}

function FormalMaterialRequestsRoute({ access }: { access: AccessContext }) {
  const adapter = useMemo(() => createFormalMaterialRequestAdapter({
    person_id: access.person_id,
    authorization_version: access.authorization_version,
  }), [access.person_id, access.authorization_version]);
  return <FormalMaterialRequestsPage adapter={adapter} />;
}

function FormalStocktakesRoute({ access }: { access: AccessContext }) {
  const adapter = useMemo(() => createFormalStocktakeAdapter({
    person_id: access.person_id,
    authorization_version: access.authorization_version,
  }), [access.person_id, access.authorization_version]);
  return <FormalStocktakesPage adapter={adapter} />;
}

export default function App() {
  const [user, setUser] = useState<FormalUser | null>(null);
  const [access, setAccess] = useState<AccessContext | null>(null);
  const [accessLoading, setAccessLoading] = useState(true);
  const [accessError, setAccessError] = useState("");
  const [loading, setLoading] = useState(true);
  const [logoutError, setLogoutError] = useState("");
  const [logoutPending, setLogoutPending] = useState(false);
  const logoutInFlight = useRef(false);

  const refreshUser = useCallback(async () => {
    try {
      const nextUser = normalizeAuthenticatedUser(await api<AuthenticatedUser>("/auth/me"));
      setAccessLoading(true);
      setUser(nextUser);
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) console.error(error);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refreshUser(); }, [refreshUser]);

  useEffect(() => subscribeAuthenticationTerminalLogout(() => {
    setAccess(null);
    setUser(null);
    setLogoutError("");
    setLogoutPending(false);
    logoutInFlight.current = false;
  }), []);

  useEffect(() => subscribeAuthenticationEstablished(() => {
    // Another tab established new credentials. Remove every old-user projection before any
    // request can run with the new cookies, then rebuild identity and access from the server.
    setAccess(null);
    setUser(null);
    setAccessError("");
    setAccessLoading(true);
    setLogoutError("");
    setLogoutPending(false);
    logoutInFlight.current = false;
    setLoading(true);
    void refreshUser();
  }), [refreshUser]);

  useEffect(() => {
    if (!user) {
      setAccess(null);
      setAccessLoading(false);
      setAccessError("");
      return undefined;
    }
    let cancelled = false;
    setAccessLoading(true);
    setAccessError("");
    api<AccessContext>("/access/context")
      .then((result) => {
        if (cancelled) return;
        const sameRoles = [...result.role_codes].sort().join("|")
          === [...user.role_codes].sort().join("|");
        if (
          result.person_id !== user.id
          || result.authorization_version !== user.authorization_version
          || result.account_status !== user.account_status
          || result.employment_status !== user.employment_status
          || result.access_mode !== user.access_mode
          || !sameRoles
        ) {
          setAccess(null);
          setAccessError("登录身份与正式访问上下文不一致，已失败关闭");
          return;
        }
        setAccess(result);
      })
      .catch((error) => {
        if (!cancelled) {
          setAccess(null);
          setAccessError(showError(error));
        }
      })
      .finally(() => { if (!cancelled) setAccessLoading(false); });
    return () => { cancelled = true; };
  }, [user]);

  async function logout() {
    if (logoutInFlight.current) return;
    logoutInFlight.current = true;
    setLogoutPending(true);
    setLogoutError("");
    try {
      await api("/auth/logout", {
        method: "POST",
        ...mutationHeaders("auth-logout"),
      });
      setAccess(null);
      setUser(null);
    } catch (error) {
      if (error instanceof ApiError && error.responseReceived && error.credentialsCleared) {
        setAccess(null);
        setUser(null);
        return;
      }
      setLogoutError("退出未得到服务端清除凭据的确认，当前会话仍保持登录。请检查网络后重试。");
    } finally {
      logoutInFlight.current = false;
      setLogoutPending(false);
    }
  }

  function acceptLogin(nextUser: FormalUser) {
    setAccess(null);
    setAccessLoading(true);
    setUser(nextUser);
  }

  if (loading) return <Loading label="正在连接云端系统" />;
  if (!user) return <Login onLogin={acceptLogin} />;
  if (accessLoading) return <Loading label="正在校验正式角色与数据范围" />;
  if (!access || !canUseFormalOperationalClient(access)) return <IsolatedIdentity
    user={user}
    access={access}
    accessError={accessError}
    logoutError={logoutError}
    logoutPending={logoutPending}
    onLogout={logout}
  />;

  const canManageProvincial = hasFormalPermission(access, "role_assignment", "manage_provincial");
  const canReadInventory = hasFormalPermission(access, "inventory", "read");
  const canReadMaterialRequests = hasFormalPermission(access, "material_request", "read");
  const canReadStocktake = hasFormalPermission(access, "stocktake", "read");
  const canReadReconciliation = hasFormalPermission(access, "reconciliation", "read");
  const canCreateOpeningReconciliation = canReadStocktake
    && hasFormalPermission(access, "reconciliation", "create_opening");

  return <Shell
    user={user}
    access={access}
    logoutError={logoutError}
    logoutPending={logoutPending}
    onLogout={logout}
  >
    <Routes>
      <Route path="/dashboard" element={<FormalHome user={user} access={access} />} />
      <Route path="/inventory" element={canReadInventory ? <FormalInventoryPage /> : <Navigate to="/dashboard" replace />} />
      <Route path="/material-requests" element={canReadMaterialRequests ? <FormalMaterialRequestsRoute access={access} /> : <Navigate to="/dashboard" replace />} />
      <Route path="/stocktakes" element={canReadStocktake ? <FormalStocktakesRoute access={access} /> : <Navigate to="/dashboard" replace />} />
      <Route path="/opening-stocktakes" element={canReadStocktake ? <FormalOpeningStocktakesPage
        key={`${access.person_id}:${access.authorization_version}`}
        actor={{ person_id: access.person_id, authorization_version: access.authorization_version }}
      /> : <Navigate to="/dashboard" replace />} />
      <Route path="/opening-reconciliations" element={canReadReconciliation ? <FormalOpeningReconciliationsPage canCreate={canCreateOpeningReconciliation} /> : <Navigate to="/dashboard" replace />} />
      <Route path="/provincial-managers" element={canManageProvincial ? <ProvincialManagersPage /> : <Navigate to="/dashboard" replace />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  </Shell>;
}
