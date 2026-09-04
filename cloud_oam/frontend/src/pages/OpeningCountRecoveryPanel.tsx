import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "../ui";
import { createOpeningCountRecoveryAdapter, recoverOpeningCountCommand, type OpeningCountRecoveryAdapter } from "../openingCountRecovery";
import { getOpeningCountRecoveryStore, type OpeningCountRecoveryStore, type OpeningCountSentinelRead } from "../openingCountRecoveryStore";
import type { OpeningStocktakeTaskDetail } from "../formalOpeningStocktake";
import type { OpeningRecountActor } from "../formalOpeningRecountAssignees";

export default function OpeningCountRecoveryPanel({ taskId, actor, revision = 0,
  store = getOpeningCountRecoveryStore(), adapter, canCommit = () => true, onRecovered, onBlockedChange,
}: {
  taskId: string; actor?: OpeningRecountActor; revision?: number;
  store?: OpeningCountRecoveryStore; adapter?: OpeningCountRecoveryAdapter;
  canCommit?: () => boolean; onRecovered: (detail: OpeningStocktakeTaskDetail) => void;
  onBlockedChange?: (blocked: boolean) => void;
}) {
  const [record, setRecord] = useState<OpeningCountSentinelRead>({ kind: "unavailable" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const running = useRef(false);
  const callbacks = useRef({ canCommit, onRecovered, onBlockedChange });
  callbacks.current = { canCommit, onRecovered, onBlockedChange };
  const refresh = useCallback(() => {
    let next: OpeningCountSentinelRead;
    try { next = store.read(taskId); } catch { next = { kind: "unavailable" }; }
    setRecord(next);
    callbacks.current.onBlockedChange?.(next.kind !== "missing");
  }, [store, taskId]);
  useEffect(() => {
    generation.current += 1;
    setError("");
    setBusy(false);
    refresh();
    const changed = () => refresh();
    window.addEventListener("storage", changed);
    return () => { generation.current += 1; window.removeEventListener("storage", changed); };
  }, [refresh, actor?.person_id, actor?.authorization_version]);
  useEffect(() => { refresh(); }, [revision, refresh]);

  const sameActor = record.kind === "valid" && actor?.person_id === record.value.actor_person_id
    && actor?.authorization_version === record.value.actor_authorization_version;
  async function recover(): Promise<void> {
    if (running.current || !sameActor || !actor || record.kind !== "valid") return;
    running.current = true;
    const token = generation.current;
    const live = () => token === generation.current && callbacks.current.canCommit();
    setBusy(true);
    setError("");
    try {
      const result = await store.withTaskLease(taskId, (lease) => recoverOpeningCountCommand(
        lease, record.value, adapter ?? createOpeningCountRecoveryAdapter(actor), live,
      ));
      if (live()) callbacks.current.onRecovered(result.detail);
    } catch {
      // Never render arbitrary transport text or a previous person's details.
      if (live()) setError("原计数尚未完成核验，恢复记录继续保留；请勿重新提交或执行其他写入。");
    } finally {
      running.current = false;
      if (live()) { setBusy(false); refresh(); }
    }
  }
  if (record.kind === "missing") return null;
  return <section className="alert alert-warning" aria-label="盘点计数恢复">
    <div><strong>本任务计数待核验，其他写入已阻止</strong>
      <p>仅核验历史请求与当前任务。查无结果不代表未执行；不重发计数、不自动重试。</p>
      {record.kind === "valid" && sameActor ? <>
        <dl><div><dt>任务</dt><dd>{record.value.task_id}</dd></div>
          <div><dt>原轮次</dt><dd>第 {record.value.round_no} 轮 · {record.value.round_id}</dd></div>
          <div><dt>原范围</dt><dd>{record.value.scope_id}</dd></div>
          <div><dt>核验追踪号</dt><dd>{record.value.trace_request_id}</dd></div></dl>
        <Button tone="secondary" disabled={busy} onClick={() => void recover()}>{busy ? "正在只读核验" : "只读核验原计数"}</Button>
      </> : <p>{record.kind === "valid" ? "登录身份或权限与原请求不同，请联系管理员核验。" : "本地恢复记录损坏或不可用，已停止写入；请联系管理员核验。"}</p>}
      {error && <p role="alert">{error}</p>}
    </div>
  </section>;
}
