import { useLayoutEffect, useRef, useState } from 'react';
import { Button, Field } from '../ui';
import { createOpeningStartPorts, getOpeningStartStore } from '../openingStartClient';
import { recoverOpeningStart, sealOpeningStart, START_SEAL_CONFIRMATION, startErrorMessage, submitOpeningStart, type StartActor, type StartPorts, type StartScope, type StartStore } from '../openingStartCore';
import type { ControlBatch } from '../formalOpeningControlDirectory';

export type DraftStartScope = Readonly<{ scope: StartScope; owner: string; location: string; person: string }>;
export default function OpeningStartWorkflow({ actor, region, selectedScope, batch, enabled, onOpen,
  store = getOpeningStartStore(), ports = createOpeningStartPorts(),
}: { actor: StartActor; region: string; selectedScope?: DraftStartScope; batch?: ControlBatch; enabled: boolean;
  onOpen?: (task: string) => void; store?: StartStore; ports?: StartPorts }) {
  const [scopes, setScopes] = useState<readonly DraftStartScope[]>([]);
  const [taskNo, setTaskNo] = useState(''), [note, setNote] = useState(''), [deadline, setDeadline] = useState('');
  const [blind, setBlind] = useState(true), [freeze, setFreeze] = useState<StartScope['freeze_mode']>('hard');
  const [record, setRecord] = useState(() => store.read(region));
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [found, setFound] = useState<string | null>(null);
  const active = useRef(false), running = useRef(false), revision = useRef(0);
  const identity = `${actor.person_id}:${actor.authorization_version}:${region}`;
  const currentIdentity = useRef(identity); currentIdentity.current = identity;
  useLayoutEffect(() => { active.current = true; return () => { active.current = false; revision.current++; }; }, []);
  useLayoutEffect(() => {
    revision.current++; setScopes([]); setTaskNo(''); setNote(''); setDeadline('');
    setFound(null); setMessage(''); setRecord(store.read(region));
  }, [identity, store]);
  const observe = () => setRecord(store.read(region));
  useLayoutEffect(() => {
    // Another tab may finish or create an uncertain request while this one is idle.
    const read = () => { if (!running.current) observe(); };
    const visibility = () => {
      if (document.visibilityState === 'hidden') { revision.current++; setFound(null); setMessage(''); }
      else read();
    };
    window.addEventListener('storage', read); window.addEventListener('focus', read);
    document.addEventListener('visibilitychange', visibility);
    return () => { window.removeEventListener('storage', read); window.removeEventListener('focus', read); document.removeEventListener('visibilitychange', visibility); };
  }, [store, region]);
  async function run(recovery: boolean | "seal") {
    if (running.current) return;
    running.current = true; setBusy(true); setMessage(''); setFound(null);
    const epoch = ++revision.current;
    const live = () => active.current && document.visibilityState !== 'hidden' && epoch === revision.current && identity === currentIdentity.current;
    try {
      const result = recovery === "seal" ? await sealOpeningStart({ actor, region, store, ports, canContinue: live, confirm: async () => window.confirm(START_SEAL_CONFIRMATION) })
        : recovery ? await recoverOpeningStart({ actor, region, store, ports, canContinue: live })
        : await submitOpeningStart({ actor, store, ports, enabled, canContinue: live, input: {
          publication_id: batch?.publicationId ?? '', region_org_id: region, task_no: taskNo.trim(),
          scopes: scopes.map((row) => row.scope), blind_count: blind, deadline: deadline ? new Date(deadline).toISOString() : null, note: note.trim(),
        } });
      if (!live()) return;
      setFound(result.result?.task_id ?? null); setScopes([]); setTaskNo(''); setNote(''); setDeadline('');
      setMessage(result.seal ? '原启动请求已永久终结并核验。请重新选择范围和批次后另行发起。' : result.recovered ? '已核验原启动任务，请打开查看当前进度。' : '盘点任务已创建并核验，请打开任务下发实盘。');
    } catch (error) { if (live()) setMessage(startErrorMessage(error)); }
    finally {
      // Release UI busy only after the original lease settles, even if hidden.
      // A hidden or obsolete invocation must never clear the durable marker.
      running.current = false;
      if (active.current) { setBusy(false); if (identity === currentIdentity.current) observe(); }
    }
  }
  const pending = record.kind !== 'missing';
  const canAdd = enabled && selectedScope && !busy && !pending && !scopes.some((row) => row.scope.owner_org_id === selectedScope.scope.owner_org_id && row.scope.location_id === selectedScope.scope.location_id);
  if (!enabled && !pending && !message) return null;
  return <section className="opening-detail-section" aria-label="期初启动与结果恢复">
    {pending && <div className="alert alert-warning" role="status">
      {record.kind === 'valid' ? '本区域有启动请求待核验，已停止新提交。请核验原任务结果。' : '本区域启动恢复记录不可用，已停止新提交，请保留现有记录。'}
    </div>}
    {record.kind === 'valid' && <>
      <Button tone="secondary" disabled={busy} onClick={() => void run(true)}>核验原启动结果</Button>
      <Button tone="quiet" disabled={busy} onClick={() => void run('seal')}>终结原启动请求</Button>
    </>}
    {enabled && !pending && <>
      <h3>建立期初盘点任务</h3>
      <p>添加全部实际盘点范围和执行人，再确认所选控制批次。控制数量由服务器读取。</p>
      <Field label="新增范围的冻结方式"><select aria-label="新增范围的冻结方式" value={freeze} disabled={busy} onChange={(event) => setFreeze(event.target.value as StartScope['freeze_mode'])}>
        <option value="hard">整范围冻结</option><option value="cutoff_replay">按截止流水回算</option>
      </select></Field>
      <Button tone="secondary" disabled={!canAdd} onClick={() => {
        if (canAdd && selectedScope) setScopes((old) => [...old, { ...selectedScope, scope: { ...selectedScope.scope, freeze_mode: freeze } }]);
      }}>加入盘点范围</Button>
      <ul>{scopes.map((row, index) => <li key={`${row.scope.owner_org_id}:${row.scope.location_id}`}>
        {row.owner} · {row.location} · {row.person} · {row.scope.freeze_mode === 'hard' ? '整范围冻结' : '截止流水回算'}
        <Button tone="quiet" disabled={busy} onClick={() => setScopes((old) => old.filter((_, i) => i !== index))}>移除范围 {index + 1}</Button>
      </li>)}</ul>
      <Field label="期初任务编号"><input aria-label="期初任务编号" value={taskNo} maxLength={100} disabled={busy} onChange={(event) => setTaskNo(event.target.value)} /></Field>
      <Field label="盘点方式"><select aria-label="盘点方式" value={blind ? 'blind' : 'visible'} disabled={busy} onChange={(event) => setBlind(event.target.value === 'blind')}><option value="blind">盲盘</option><option value="visible">明盘</option></select></Field>
      <Field label="盘点截止时间（选填）"><input type="datetime-local" aria-label="盘点截止时间（选填）" value={deadline} disabled={busy} onChange={(event) => setDeadline(event.target.value)} /></Field>
      <Field label="期初盘点备注"><textarea aria-label="期初盘点备注" value={note} maxLength={10000} disabled={busy} onChange={(event) => setNote(event.target.value)} /></Field>
      <p>{batch ? `已选择：${batch.sourceName}，采集于 ${new Date(batch.capturedAt).toLocaleString('zh-CN')}；${scopes.length} 个盘点范围。` : '请先选择已发布控制批次。'}</p>
      <Button disabled={busy || !scopes.length || !taskNo.trim() || !batch?.isLatest || Date.parse(batch.validUntil) <= Date.now()}
        onClick={() => void run(false)}>{busy ? '正在核验启动结果' : '确认范围并启动期初盘点'}</Button>
    </>}
    {message && <p role="status">{message}</p>}
    {found && onOpen && <Button tone="secondary" disabled={busy} onClick={() => onOpen(found)}>打开已核验任务</Button>}
  </section>;
}
