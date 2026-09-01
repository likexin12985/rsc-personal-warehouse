import { useCallback, useEffect, useState } from "react";
import { LogOut, MonitorSmartphone, RefreshCw } from "lucide-react";
import { api, mutationHeaders } from "../api";
import { roleLabel } from "../clientPolicy";
import type { AuthSession, Material, User, Warehouse } from "../types";
import { Button, Empty, Loading, SectionHeader, showError } from "../ui";

const WAREHOUSE_TYPE_LABELS: Record<string, string> = {
  central: "中心仓",
  service_backpack: "服务商背包",
  personal_backpack: "个人背包",
};
const CONDITION_SCOPE_LABELS: Record<string, string> = { good: "好件", old: "旧件", bad: "坏件", mixed: "综合" };
const WAREHOUSE_LEVEL_LABELS: Record<string, string> = { headquarters: "总部仓库", network: "网点仓库" };
const OWNERSHIP_LABELS: Record<string, string> = { regular: "自有库存", customer_supplied: "客供库存" };
const POSITION_SCOPE_LABELS: Record<string, string> = { unrestricted: "通用仓位", employee: "员工仓位", service_provider: "服务商仓位" };

type View = "users" | "warehouses" | "materials" | "sessions";

export default function SettingsPage() {
  const [view, setView] = useState<View>("users");
  const [users, setUsers] = useState<User[]>([]);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [materials, setMaterials] = useState<Material[]>([]);
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [nextUsers, nextWarehouses, nextMaterials, nextSessions] = await Promise.all([
        api<User[]>("/auth/users"),
        api<Warehouse[]>("/warehouses"),
        api<Material[]>("/materials?limit=500"),
        api<AuthSession[]>("/auth/sessions"),
      ]);
      setUsers(nextUsers);
      setWarehouses(nextWarehouses);
      setMaterials(nextMaterials);
      setSessions(nextSessions);
    } catch (err) {
      setError(showError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return <>
    <SectionHeader
      title="系统设置"
      subtitle="v0.9 账号、仓库和 OAM SKU 仅供迁移核对；本地维护入口已隔离"
      actions={<Button tone="secondary" icon={<RefreshCw size={17} />} onClick={load}>刷新</Button>}
    />
    <div className="segmented">
      <button className={view === "users" ? "active" : ""} onClick={() => setView("users")}>账号<span>{users.length}</span></button>
      <button className={view === "warehouses" ? "active" : ""} onClick={() => setView("warehouses")}>仓库<span>{warehouses.length}</span></button>
      <button className={view === "materials" ? "active" : ""} onClick={() => setView("materials")}>物料<span>{materials.length}</span></button>
      <button className={view === "sessions" ? "active" : ""} onClick={() => setView("sessions")}>登录设备<span>{sessions.length}</span></button>
    </div>
    {error && <div className="alert alert-error">{error}</div>}
    {loading ? <Loading /> : view === "users" ? <UsersTable rows={users} /> : view === "warehouses" ? <WarehousesTable rows={warehouses} /> : view === "materials" ? <MaterialsTable rows={materials} /> : <SessionsTable rows={sessions} onRevoked={load} />}
  </>;
}

function SessionsTable({ rows, onRevoked }: { rows: AuthSession[]; onRevoked: () => void }) {
  const [revoking, setRevoking] = useState("");
  if (!rows.length) return <Empty title="暂无在线设备" />;

  async function revoke(row: AuthSession) {
    if (!window.confirm(`确认让 ${row.user_name} 的“${row.device_name}”退出登录？`)) return;
    setRevoking(row.id);
    try {
      await api(`/auth/sessions/${row.id}/revoke`, {
        method: "POST",
        ...mutationHeaders("auth-session-revoke"),
      });
      onRevoked();
    } catch (error) {
      window.alert(showError(error));
    } finally {
      setRevoking("");
    }
  }

  return <section className="content-section table-section"><div className="table-wrap"><table><thead><tr><th>账号</th><th>登录端</th><th>最近使用</th><th>登录时间</th><th>有效期至</th><th>IP</th><th>操作</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td><strong>{row.user_name}</strong><span className="cell-subtitle mono">{row.mobile}</span></td><td><span className="session-device"><MonitorSmartphone size={16} />{row.device_name}</span><span className="cell-subtitle">{row.client_type === "miniprogram" ? "微信小程序" : "PC网页"}</span></td><td>{formatDate(row.last_seen_at)}</td><td>{formatDate(row.created_at)}</td><td>{formatDate(row.expires_at)}</td><td className="mono">{row.ip_address || "-"}</td><td>{row.is_current ? <span className="status status-success">当前设备</span> : <Button tone="secondary" icon={<LogOut size={16} />} disabled={revoking === row.id} onClick={() => revoke(row)}>{revoking === row.id ? "处理中" : "强制下线"}</Button>}</td></tr>)}</tbody></table></div></section>;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function UsersTable({ rows }: { rows: User[] }) {
  if (!rows.length) return <Empty title="暂无账号" />;
  return <><div className="alert alert-info">正式客户端仅支持微信或手机验证码登录；账号开通请通过唯一 OAM 人员映射。</div><section className="content-section table-section"><div className="table-wrap"><table><thead><tr><th>姓名</th><th>手机号</th><th>角色</th><th>省份</th><th>登录方式</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td><strong>{row.name}</strong></td><td className="mono">{row.mobile}</td><td>{roleLabel(row.role)}</td><td>{row.province || "全国"}</td><td>微信 / 手机验证码</td></tr>)}</tbody></table></div></section></>;
}

function WarehousesTable({ rows }: { rows: Warehouse[] }) {
  if (!rows.length) return <Empty title="暂无仓库" />;
  const byId = new Map(rows.map((row) => [row.id, row.name]));
  return <section className="content-section table-section"><div className="table-wrap"><table><thead><tr><th>仓库编码</th><th>仓库名称</th><th>层级 / 上级</th><th>省份 / 城市</th><th>物权</th><th>仓位属性</th><th>库存范围</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td className="mono">{row.code}</td><td><strong>{row.name}</strong><span className="cell-subtitle">{WAREHOUSE_TYPE_LABELS[row.warehouse_type] || row.warehouse_type}</span></td><td>{WAREHOUSE_LEVEL_LABELS[row.warehouse_level] || row.warehouse_level}<span className="cell-subtitle">{row.parent_warehouse_id ? byId.get(row.parent_warehouse_id) || "上级仓库" : "无上级"}</span></td><td>{row.province}<span className="cell-subtitle">{row.city || "未填写城市"}</span></td><td>{OWNERSHIP_LABELS[row.ownership_type] || row.ownership_type}</td><td>{POSITION_SCOPE_LABELS[row.position_scope] || row.position_scope}</td><td>{CONDITION_SCOPE_LABELS[row.condition_scope] || row.condition_scope}</td></tr>)}</tbody></table></div></section>;
}

function MaterialsTable({ rows }: { rows: Material[] }) {
  if (!rows.length) return <Empty title="暂无物料" />;
  return <><div className="alert alert-info">OAM SKU 为只读投影，正式客户端禁止本地新增或覆盖。</div><section className="content-section table-section"><div className="table-wrap"><table><thead><tr><th>物料编码</th><th>物料名称</th><th>规格型号</th><th>分类</th><th>单位</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td className="mono"><strong>{row.code}</strong></td><td>{row.name}</td><td>{row.specification || "-"}</td><td>{row.category}</td><td>{row.unit}</td></tr>)}</tbody></table></div></section></>;
}
