import { useCallback, useEffect, useMemo, useState } from "react";
import { CircleAlert, PackagePlus, RefreshCw, RotateCcw, Search, Trash2, Wrench } from "lucide-react";
import { api, jsonBody } from "../api";
import { canUseOperationalClient } from "../clientPolicy";
import type { Material, User, Warehouse, WorkOrderMaterial } from "../types";
import { Button, Empty, Field, Loading, Modal, SectionHeader, StatusPill, formatDate, showError } from "../ui";

const MANAGER_ROLES = new Set(["admin", "provincial_manager"]);
const OPERATOR_ROLES = new Set([...MANAGER_ROLES, "technician"]);
const CONDITION: Record<string, string> = { good: "好件", old: "旧件", bad: "坏件" };
type DraftItem = { material_id: string; quantity: number; condition: string };

function OccupyMaterials({ user, onClose, onDone }: { user: User; onClose: () => void; onDone: () => void }) {
  const canManage = MANAGER_ROLES.has(user.role);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [materials, setMaterials] = useState<Material[]>([]);
  const [users, setUsers] = useState<User[]>(canManage ? [] : [user]);
  const [warehouseId, setWarehouseId] = useState("");
  const [holderId, setHolderId] = useState(user.id);
  const [workOrderNumber, setWorkOrderNumber] = useState("");
  const [note, setNote] = useState("");
  const [items, setItems] = useState<DraftItem[]>([{ material_id: "", quantity: 1, condition: "good" }]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const requests: Promise<unknown>[] = [api<Warehouse[]>("/warehouses"), api<Material[]>("/materials?limit=500")];
    if (canManage) requests.push(api<User[]>("/auth/users"));
    Promise.all(requests)
      .then((result) => {
        const networkWarehouses = (result[0] as Warehouse[]).filter((row) => row.warehouse_level === "network");
        setWarehouses(networkWarehouses);
        setWarehouseId(networkWarehouses[0]?.id || "");
        setMaterials(result[1] as Material[]);
        if (canManage) {
          const nextUsers = (result[2] as User[]).filter((row) => canUseOperationalClient(row.role));
          setUsers(nextUsers);
          setHolderId(nextUsers.find((row) => row.role === "technician")?.id || nextUsers[0]?.id || "");
        }
      })
      .catch((err) => setError(showError(err)))
      .finally(() => setLoading(false));
  }, [canManage]);

  function updateItem(index: number, next: Partial<DraftItem>) {
    setItems((current) => current.map((row, i) => i === index ? { ...row, ...next } : row));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (workOrderNumber.trim().length < 3) throw new Error("请填写正确的工单号");
      if (!warehouseId || !holderId) throw new Error("请选择所属仓库和领用人");
      if (items.some((row) => !row.material_id || !Number.isInteger(row.quantity) || row.quantity < 1)) throw new Error("请补齐物料明细");
      const unique = new Set(items.map((row) => `${row.material_id}:${row.condition}`));
      if (unique.size !== items.length) throw new Error("同一物料与状态不能重复");
      await api("/work-order-materials/batch", {
        method: "POST",
        ...jsonBody({ work_order_number: workOrderNumber, warehouse_id: warehouseId, user_id: holderId, note, items }),
      });
      onDone();
      onClose();
    } catch (err) {
      setError(showError(err));
    } finally {
      setBusy(false);
    }
  }

  return <Modal title="登记工单投料" onClose={onClose} wide>
    {loading ? <Loading label="正在加载个人库存" /> : <form className="form-stack" onSubmit={submit}>
      <div className="form-grid three">
        <Field label="工单号"><input value={workOrderNumber} onChange={(e) => setWorkOrderNumber(e.target.value)} placeholder="请输入工单编号" required /></Field>
        <Field label="所属网点仓库"><select value={warehouseId} onChange={(e) => setWarehouseId(e.target.value)} required><option value="">请选择</option>{warehouses.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></Field>
        <Field label="领用人"><select value={holderId} onChange={(e) => setHolderId(e.target.value)} disabled={!canManage} required>{users.map((row) => <option key={row.id} value={row.id}>{row.name}{row.province ? ` · ${row.province}` : ""}</option>)}</select></Field>
      </div>
      <div className="line-items">
        <div className="line-title"><div><strong>占用明细</strong><span className="line-subtitle">从领用人个人可用库存中占用</span></div><Button type="button" tone="secondary" icon={<PackagePlus size={16} />} onClick={() => setItems((current) => [...current, { material_id: "", quantity: 1, condition: "good" }])}>添加物料</Button></div>
        {items.map((row, index) => <div className="work-line-item" key={index}>
          <select value={row.material_id} onChange={(e) => updateItem(index, { material_id: e.target.value })} required><option value="">选择物料</option>{materials.map((material) => <option key={material.id} value={material.id}>{material.code} | {material.name}</option>)}</select>
          <input className="qty-input" type="number" min={1} step={1} value={row.quantity} onChange={(e) => updateItem(index, { quantity: Number(e.target.value) })} aria-label="数量" />
          <select value={row.condition} onChange={(e) => updateItem(index, { condition: e.target.value })} aria-label="类型"><option value="good">好件</option><option value="old">旧件</option></select>
          <button type="button" className="icon-button danger-icon" aria-label="删除物料" disabled={items.length === 1} onClick={() => setItems((current) => current.filter((_, i) => i !== index))}><Trash2 size={18} /></button>
        </div>)}
      </div>
      <Field label="备注"><textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="选填" /></Field>
      <div className="form-notice">确认消耗后会扣减个人库存；回收旧件或坏件后，还需另行发起退回单。</div>
      {error && <div className="form-error">{error}</div>}
      <div className="form-actions"><Button type="button" tone="quiet" onClick={onClose}>取消</Button><Button type="submit" disabled={busy} icon={<Wrench size={17} />}>{busy ? "正在登记" : "确认占用"}</Button></div>
    </form>}
  </Modal>;
}

export default function WorkMaterialsPage({ user }: { user: User }) {
  const [rows, setRows] = useState<WorkOrderMaterial[]>([]);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const canOperate = OPERATOR_ROLES.has(user.role);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setRows(await api<WorkOrderMaterial[]>("/work-order-materials?limit=500"));
    } catch (err) {
      setError(showError(err));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const counts = useMemo(() => rows.reduce<Record<string, number>>((acc, row) => ({ ...acc, [row.status]: (acc[row.status] || 0) + 1 }), {}), [rows]);
  const visibleRows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return rows.filter((row) => (!status || row.status === status) && (!needle || [row.workOrderNumber, row.material.code, row.material.name, row.user.name].some((value) => value.toLowerCase().includes(needle))));
  }, [q, rows, status]);

  async function runAction(row: WorkOrderMaterial, action: "release" | "consume" | "recover", recoveryCondition = "") {
    const prompts: Record<string, string> = {
      release: "确认该工单不再使用此物料，并释放占用？",
      consume: "确认物料已经投入该工单？现有量和占用量都会扣减。",
      recover: `确认已回收为${CONDITION[recoveryCondition]}？回收后还需发起退回单。`,
    };
    if (!window.confirm(prompts[action])) return;
    setBusyId(row.id);
    setError("");
    try {
      await api(`/work-order-materials/${row.id}/${action}`, {
        method: "POST",
        ...(action === "recover" ? jsonBody({ recovery_condition: recoveryCondition, note: "" }) : {}),
      });
      await load();
    } catch (err) {
      setError(showError(err));
    } finally {
      setBusyId("");
    }
  }

  return <>
    <SectionHeader title="工单物料" subtitle="个人库存占用、消耗和回收均绑定具体工单" actions={<><Button tone="secondary" icon={<RefreshCw size={17} />} onClick={load}>刷新</Button>{canOperate && <Button icon={<PackagePlus size={17} />} onClick={() => setCreating(true)}>登记投料</Button>}</>} />
    <section className="work-material-toolbar"><label className="search-box"><Search size={18} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="工单号、物料或领用人" /></label><div className="segmented compact-segmented">{[["", "全部"], ["occupied", "占用中"], ["consumed", "已消耗"], ["recovered", "已回收"], ["released", "已释放"]].map(([value, label]) => <button key={value} className={status === value ? "active" : ""} onClick={() => setStatus(value)}>{label}{value && counts[value] ? <span>{counts[value]}</span> : null}</button>)}</div></section>
    {error && <div className="alert alert-error"><CircleAlert size={18} />{error}</div>}
    {loading ? <Loading /> : visibleRows.length === 0 ? <Empty title="暂无工单物料" detail="投料前先登记占用，才能形成完整流向" /> : <section className="content-section table-section"><div className="table-wrap"><table className="work-material-table"><thead><tr><th>工单号</th><th>物料</th><th>领用人 / 仓库</th><th>库存类型</th><th className="num">数量</th><th>登记时间</th><th>状态</th><th>操作</th></tr></thead><tbody>{visibleRows.map((row) => <tr key={row.id}><td><strong className="mono">{row.workOrderNumber}</strong>{row.note && <span className="cell-subtitle">{row.note}</span>}</td><td><strong className="mono">{row.material.code}</strong><span className="cell-subtitle">{row.material.name}</span></td><td>{row.user.name}<span className="cell-subtitle">{row.warehouse.name}</span></td><td>{CONDITION[row.condition]}{row.recoveryCondition ? ` → ${CONDITION[row.recoveryCondition]}` : ""}</td><td className="num">{row.quantity} {row.material.unit}</td><td>{formatDate(row.createdAt)}</td><td><StatusPill status={row.status} /></td><td><div className="row-actions">{canOperate && row.status === "occupied" && <><button disabled={busyId === row.id} className="table-action" onClick={() => runAction(row, "release")}><RotateCcw size={15} />释放</button><button disabled={busyId === row.id} className="table-action" onClick={() => runAction(row, "consume")}>确认消耗</button></>}{canOperate && row.status === "consumed" && <><button disabled={busyId === row.id} className="table-action" onClick={() => runAction(row, "recover", "old")}>回收旧件</button><button disabled={busyId === row.id} className="table-action danger-text" onClick={() => runAction(row, "recover", "bad")}>回收坏件</button></>}{(!canOperate || !["occupied", "consumed"].includes(row.status)) && "-"}</div></td></tr>)}</tbody></table></div></section>}
    {creating && <OccupyMaterials user={user} onClose={() => setCreating(false)} onDone={load} />}
  </>;
}
