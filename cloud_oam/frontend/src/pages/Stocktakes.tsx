import { useCallback, useEffect, useMemo, useState } from "react";
import { Camera, CheckCheck, CircleAlert, ClipboardCheck, Eye, Plus, RefreshCw, Save, Send } from "lucide-react";
import { api, jsonBody } from "../api";
import type { Stocktake, StocktakeItem, User, Warehouse } from "../types";
import { Button, Empty, Field, Loading, Modal, SectionHeader, StatusPill, formatDate, showError } from "../ui";

const MANAGER_ROLES = new Set(["admin", "provincial_manager"]);
const CONDITION: Record<string, string> = { good: "好件", old: "旧件", bad: "坏件" };

function NewStocktake({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [warehouse, setWarehouse] = useState("");
  const [assignee, setAssignee] = useState("");
  const [deadline, setDeadline] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { Promise.all([api<Warehouse[]>("/warehouses"), api<User[]>("/auth/users")]).then(([w, u]) => { setWarehouses(w); setUsers(u); }); }, []);
  async function submit(event: React.FormEvent) { event.preventDefault(); setBusy(true); setError(""); try { await api("/stocktakes", { method: "POST", ...jsonBody({ warehouse_id: warehouse, assignee_id: assignee, deadline: deadline ? new Date(deadline).toISOString() : null, note }) }); onCreated(); onClose(); } catch (err) { setError(showError(err)); } finally { setBusy(false); } }
  return <Modal title="新建盘点任务" onClose={onClose}><form className="form-stack" onSubmit={submit}><Field label="盘点仓库"><select value={warehouse} onChange={(e) => setWarehouse(e.target.value)} required><option value="">请选择</option>{warehouses.map((row) => <option key={row.id} value={row.id}>{row.province} · {row.name}</option>)}</select></Field><Field label="盘点人"><select value={assignee} onChange={(e) => setAssignee(e.target.value)} required><option value="">请选择</option>{users.map((row) => <option key={row.id} value={row.id}>{row.name}{row.province ? ` · ${row.province}` : ""}</option>)}</select></Field><Field label="截止时间"><input type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)} /></Field><Field label="任务说明"><textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="选填" /></Field>{error && <div className="form-error">{error}</div>}<div className="form-actions"><Button type="button" tone="quiet" onClick={onClose}>取消</Button><Button type="submit" disabled={busy} icon={<ClipboardCheck size={17} />}>{busy ? "正在创建" : "创建任务"}</Button></div></form></Modal>;
}

function StocktakeDetail({ taskId, user, onClose, onChanged }: { taskId: string; user: User; onClose: () => void; onChanged: () => void }) {
  const [task, setTask] = useState<Stocktake | null>(null);
  const [drafts, setDrafts] = useState<Record<string, { counted: string; remark: string }>>({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const canManage = MANAGER_ROLES.has(user.role);
  const editable = task?.status === "pending" || task?.status === "in_progress";

  const load = useCallback(async () => {
    try {
      const value = await api<Stocktake>(`/stocktakes/${taskId}`);
      setTask(value);
      setDrafts(Object.fromEntries(value.items.map((row) => [row.id, { counted: row.counted === null ? "" : String(row.counted), remark: row.remark }])));
    } catch (err) { setError(showError(err)); }
  }, [taskId]);
  useEffect(() => { load(); }, [load]);

  async function saveItem(item: StocktakeItem, override?: { counted: string; remark: string }) {
    const draft = override || drafts[item.id];
    if (!draft || draft.counted === "") return;
    setBusy(item.id); setError("");
    try { await api(`/stocktakes/${taskId}/items/${item.id}`, { method: "PUT", ...jsonBody({ counted_quantity: Number(draft.counted), remark: draft.remark }) }); await load(); } catch (err) { setError(showError(err)); } finally { setBusy(""); }
  }

  async function markAllEqual() {
    if (!task || !window.confirm("将所有未盘点项按系统数量记为持平？")) return;
    setBusy("all"); setError("");
    try {
      for (const item of task.items.filter((row) => row.counted === null)) {
        await api(`/stocktakes/${taskId}/items/${item.id}`, { method: "PUT", ...jsonBody({ counted_quantity: item.expected, remark: "" }) });
      }
      await load();
    } catch (err) { setError(showError(err)); } finally { setBusy(""); }
  }

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy("upload"); setError("");
    try { for (const file of Array.from(files)) { const form = new FormData(); form.append("file", file); await api(`/media/stocktake/${taskId}`, { method: "POST", body: form }); } await load(); } catch (err) { setError(showError(err)); } finally { setBusy(""); }
  }

  async function transition(action: "submit" | "close") {
    const prompt = action === "submit" ? "确认盘点结果已完整并提交复核？" : "关闭任务后将按实盘数调整系统库存，确认继续？";
    if (!window.confirm(prompt)) return;
    setBusy(action); setError("");
    try { await api(`/stocktakes/${taskId}/${action}`, { method: "POST" }); await load(); onChanged(); } catch (err) { setError(showError(err)); } finally { setBusy(""); }
  }

  if (!task) return <Modal title="盘点任务" onClose={onClose}><Loading /></Modal>;
  return <Modal title="盘点任务" onClose={onClose} wide>
    <div className="detail-heading"><div><strong className="mono detail-number">{task.number}</strong><StatusPill status={task.status} /></div><span>{task.warehouse.name} · {task.assignee.name}</span></div>
    <section className="stocktake-progress"><div><span>盘点进度</span><strong>{task.progress.counted}/{task.progress.total}</strong></div><div className="progress-track"><span style={{ width: `${task.progress.total ? task.progress.counted / task.progress.total * 100 : 0}%` }} /></div><div><span>当前差异</span><strong className={task.difference === 0 ? "positive" : "negative"}>{task.difference > 0 ? "+" : ""}{task.difference}</strong></div><div><span>凭证</span><strong>{task.attachmentCount}</strong></div></section>
    <div className="stocktake-toolbar">{editable && <Button tone="secondary" icon={<CheckCheck size={17} />} disabled={busy === "all"} onClick={markAllEqual}>未盘项全部持平</Button>}<label className="button button-secondary"><Camera size={17} /><span>{busy === "upload" ? "正在上传" : "拍照 / 视频"}</span><input type="file" accept="image/*,video/*" capture="environment" multiple hidden onChange={(e) => upload(e.target.files)} /></label></div>
    <div className="table-wrap"><table className="stocktake-table"><thead><tr><th>物料</th><th>类型</th><th className="num">系统数</th><th className="num">实盘数</th><th className="num">差异</th><th>差异原因</th><th className="actions-col">保存</th></tr></thead><tbody>{task.items.map((item) => { const draft = drafts[item.id] || { counted: "", remark: "" }; const calculated = draft.counted === "" ? null : Number(draft.counted) - item.expected; return <tr key={item.id}><td><strong className="mono">{item.code}</strong><span className="cell-subtitle">{item.name}</span></td><td>{CONDITION[item.condition] || item.condition}</td><td className="num">{item.expected}</td><td className="num"><input className="count-input" type="number" min={0} disabled={!editable} value={draft.counted} onChange={(e) => setDrafts((current) => ({ ...current, [item.id]: { ...draft, counted: e.target.value } }))} /></td><td className={`num ${calculated === 0 ? "positive" : calculated === null ? "" : "negative"}`}>{calculated === null ? "-" : calculated > 0 ? `+${calculated}` : calculated}</td><td><input disabled={!editable} value={draft.remark} onChange={(e) => setDrafts((current) => ({ ...current, [item.id]: { ...draft, remark: e.target.value } }))} placeholder={calculated && calculated !== 0 ? "差异项必填" : "无差异可留空"} /></td><td>{editable && <button className="table-action" disabled={busy === item.id || draft.counted === ""} onClick={() => saveItem(item)}><Save size={16} />保存</button>}</td></tr>; })}</tbody></table></div>
    {error && <div className="form-error">{error}</div>}
    <div className="form-actions">{editable && <Button icon={<Send size={17} />} disabled={!!busy || task.progress.counted !== task.progress.total} onClick={() => transition("submit")}>提交复核</Button>}{canManage && task.status === "submitted" && <Button icon={<CheckCheck size={17} />} disabled={!!busy} onClick={() => transition("close")}>复核并关闭</Button>}</div>
  </Modal>;
}

export default function StocktakesPage({ user }: { user: User }) {
  const [rows, setRows] = useState<Stocktake[]>([]);
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const canManage = MANAGER_ROLES.has(user.role);
  const load = useCallback(async () => { setLoading(true); setError(""); try { setRows(await api<Stocktake[]>(`/stocktakes${status ? `?status=${status}` : ""}`)); } catch (err) { setError(showError(err)); } finally { setLoading(false); } }, [status]);
  useEffect(() => { load(); }, [load]);
  const counts = useMemo(() => rows.reduce<Record<string, number>>((acc, row) => ({ ...acc, [row.status]: (acc[row.status] || 0) + 1 }), {}), [rows]);
  return <>
    <SectionHeader title="盘点任务" subtitle="手机现场录入，差异项必须留痕，复核后再调整库存" actions={<><Button tone="secondary" icon={<RefreshCw size={17} />} onClick={load}>刷新</Button>{canManage && <Button icon={<Plus size={17} />} onClick={() => setCreating(true)}>新建任务</Button>}</>} />
    <div className="segmented">{[["", "全部"], ["pending", "待盘点"], ["in_progress", "盘点中"], ["submitted", "待复核"], ["closed", "已关闭"]].map(([value, label]) => <button key={value} className={status === value ? "active" : ""} onClick={() => setStatus(value)}>{label}{value && counts[value] ? <span>{counts[value]}</span> : null}</button>)}</div>
    {error && <div className="alert alert-error"><CircleAlert size={18} />{error}</div>}
    {loading ? <Loading /> : rows.length === 0 ? <Empty title="暂无盘点任务" detail={canManage ? "可选择仓库创建盘点任务" : "当前没有分配给你的任务"} /> : <section className="task-list">{rows.map((row) => <article className="task-row" key={row.id}><div className="task-status-line"><StatusPill status={row.status} /><span>{row.deadline ? `截止 ${formatDate(row.deadline)}` : "未设截止时间"}</span></div><div className="task-main"><div><strong className="mono">{row.number}</strong><h3>{row.warehouse.name}</h3><p>盘点人：{row.assignee.name}</p></div><div className="task-progress-small"><span><i style={{ width: `${row.progress.total ? row.progress.counted / row.progress.total * 100 : 0}%` }} /></span><strong>{row.progress.counted}/{row.progress.total}</strong></div><div className="task-difference"><span>差异</span><strong className={row.difference === 0 ? "positive" : "negative"}>{row.difference}</strong></div><Button tone="secondary" icon={<Eye size={16} />} onClick={() => setSelected(row.id)}>打开</Button></div></article>)}</section>}
    {creating && <NewStocktake onClose={() => setCreating(false)} onCreated={load} />}
    {selected && <StocktakeDetail taskId={selected} user={user} onClose={() => setSelected(null)} onChanged={load} />}
  </>;
}
