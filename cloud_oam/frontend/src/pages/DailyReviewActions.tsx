import { useEffect, useRef, useState } from 'react';
import { subscribeAuthenticationEstablished, subscribeAuthenticationTerminalLogout } from '../api';
import { hasFormalPermission } from '../clientPolicy';
import FormalFileUploadField, { type AvailableFormalFile } from '../FormalFileUploadField';
import { operationLabels, reviewLabels, type DailyDetail, type loadDailyItems } from '../formalDailyReconciliation';
import { prepareDailyCommand, type DailyOperation, type DailyRecovery } from '../dailyReviewProtocol';
import { getDailyReviewRecoveryStore, type DailyReviewRecoveryStore, type DailyReviewSentinel } from '../dailyReviewRecoveryStore';
import { recoverDailyCommand, submitDailyCommand } from '../dailyReviewCommands';
import type { AccessContext } from '../types';
import { Button, Field } from '../ui';

type Item = Awaited<ReturnType<typeof loadDailyItems>>['items'][number];
type Draft = { version: number; selected: number[]; comment: string; rows: Record<number, { explanation: string; file?: AvailableFormalFile; blocking?: boolean }> };
function resultMessage(result: DailyRecovery) {
  if (result.outcome === 'not_observed') return '暂未查到原提交结果，记录继续保留。可稍后查询，或明确终结原请求。';
  if (result.outcome === 'sealed') return '原请求已永久终结，不会再执行。请刷新核对后重新准备操作。';
  return `原提交已确认：第 ${result.receipt.version} 版，${reviewLabels[result.receipt.review_status]}。当前对账状态请以刷新后的详情为准。`;
}
function can(access: AccessContext, detail: DailyDetail, operation: DailyOperation) {
  const action = operation === 'open' ? 'create_daily' : operation === 'explain' ? 'explain_daily' : 'approve_daily';
  return detail.action_context?.person_id === access.person_id
    && detail.action_context?.authorization_version === access.authorization_version
    && Array.isArray(detail.allowed_actions) && detail.allowed_actions.includes(operation)
    && access.account_status === 'active' && access.employment_status === 'active' && access.access_mode === 'active'
    && detail.review_status !== 'approved' && (operation === 'open' ? detail.review_version === 0 : detail.review_version > 0)
    && (operation !== 'approve' || detail.review_status === 'pending_review')
    && hasFormalPermission(access, 'reconciliation', action) && access.assignments.some(a =>
    Date.parse(a.valid_from) <= Date.now() && (a.valid_to === null || Date.parse(a.valid_to) > Date.now()) &&
    ((operation !== 'explain' && a.role_code === 'admin' && a.scope_type === 'national' && a.scope_id === '*') ||
     (['open', 'explain'].includes(operation) && a.role_code === 'provincial_manager' && a.scope_type === 'organization' && a.scope_id === detail.region_org_id)));
}
function canReturn(item: Item) {
  return item.status === 'difference' && !!item.review.explanation.trim() && !item.review.revision_requested;
}

/** Stays mounted across detail reloads so drafts never vanish after a 409. */
export default function DailyReviewActions({ access, detail, items, selected, readBusy, onChanged,
  store = getDailyReviewRecoveryStore() }: { access: AccessContext; detail?: DailyDetail; items: Item[]; selected: string;
  readBusy: boolean; onChanged: (id: string) => void; store?: DailyReviewRecoveryStore }) {
  const [pending, setPending] = useState(() => store.readPending());
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [usable, setUsable] = useState(true);
  const [messageCutoff, setMessageCutoff] = useState('');
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
  const live = useRef(true), actionBusy = useRef(false), view = useRef({ access, selected });
  view.current = { access, selected };
  useEffect(() => {
    live.current = true;
    const refresh = () => setPending(store.readPending());
    const revoke = () => { live.current = false; setUsable(false); setDrafts({}); setConfirmed({}); setMessageCutoff(''); setMessage('身份已变化，请重新进入日终对账'); };
    const logout = subscribeAuthenticationTerminalLogout(revoke), login = subscribeAuthenticationEstablished(revoke);
    window.addEventListener('storage', refresh); window.addEventListener('focus', refresh);
    refresh();
    return () => { live.current = false; logout(); login(); window.removeEventListener('storage', refresh); window.removeEventListener('focus', refresh); };
  }, [store]);
  useEffect(() => {
    if (detail) setDrafts(prior => prior[detail.cutoff_id] ? prior : { ...prior, [detail.cutoff_id]: { version: detail.review_version, selected: [], comment: '', rows: {} } });
  }, [detail?.cutoff_id, detail?.review_version]);
  const draft = detail && drafts[detail.cutoff_id];
  const markers = pending.kind === 'valid' ? pending.values || [] : [];
  const blocked = !usable || readBusy || busy || ['unavailable', 'corrupt'].includes(pending.kind)
    || markers.some(v => v.cutoff_id === selected);
  const stale = !!detail && !!draft && draft.version !== detail.review_version;
  const selectedItems = draft ? items.filter(i => draft.selected.includes(i.ordinal)) : [];
  const selectedDifferences = selectedItems.filter(i => i.status === 'difference');
  const invalidReturnSelection = selectedItems.length === 0 || selectedItems.some(i => !canReturn(i));
  const guard = (id?: string) => () => live.current && view.current.access.person_id === access.person_id
    && view.current.access.authorization_version === access.authorization_version && (!id || view.current.selected === id);
  function edit(update: (d: Draft) => Draft) {
    if (!detail || !draft) return;
    setDrafts(prior => ({ ...prior, [detail.cutoff_id]: update(prior[detail.cutoff_id]) }));
  }
  async function execute(work: () => Promise<DailyRecovery>, id: string, submitted = false) {
    if (actionBusy.current || !live.current) return;
    actionBusy.current = true; setBusy(true); setMessageCutoff(id); setMessage('');
    try {
      const result = await work();
      if (!live.current) return;
      setMessage(resultMessage(result));
      if (result.outcome !== 'not_observed') {
        setConfirmed({});
        if (submitted) setDrafts(prior => { const copy = { ...prior }; delete copy[id]; return copy; });
        onChanged(id);
      }
    } catch {
      if (live.current) setMessage('操作未确认。原请求记录和草稿已保留；请查询原提交结果，勿重复提交。身份或版本变化时先刷新核对。');
    } finally {
      actionBusy.current = false;
      if (live.current) { setBusy(false); setPending(store.readPending()); }
    }
  }
  function submit(operation: DailyOperation) {
    if (!detail || !draft || blocked || stale || !can(access, detail, operation)) return;
    const chosen = selectedDifferences;
    if (operation === 'request_changes' && invalidReturnSelection) {
      setMessageCutoff(detail.cutoff_id); setMessage('请只选择已有解释且尚未退回的差异；本次未提交。'); return;
    }
    let prepared;
    try {
      prepared = prepareDailyCommand(detail, access, { operation, comment: draft.comment, ordinals: operation === 'request_changes' ? chosen.map(i => i.ordinal) : [],
        items: operation === 'explain' ? chosen.map(i => {
          const row = draft.rows[i.ordinal];
          if (!row?.file || row.blocking || row.file.purpose !== 'daily_reconciliation_evidence') throw new Error('附件未完成');
          return { ordinal: i.ordinal, expected_item_version: i.review.version, explanation: row.explanation,
            evidence_file_id: row.file.file_id, evidence_sha256: row.file.sha256 };
        }) : undefined });
    } catch { setMessageCutoff(detail.cutoff_id); setMessage('请选取本页差异，填写至少四字的完整说明，并等待每项证据上传确认。'); return; }
    void execute(() => submitDailyCommand(store, prepared, access, guard(detail.cutoff_id)), detail.cutoff_id, true);
  }
  function recover(marker: DailyReviewSentinel, seal: boolean) {
    if (!usable || marker.actor_person_id !== access.person_id || (seal && !confirmed[marker.trace_request_id])) return;
    void execute(() => recoverDailyCommand(store, marker, access, seal, guard()), marker.cutoff_id);
  }
  return <section className="content-section" aria-label="日终审核操作">
    <h2>审核操作与原请求核验</h2>
    {message && (!selected || !messageCutoff || selected === messageCutoff) && <p role="status">{message}</p>}
    {['corrupt', 'unavailable'].includes(pending.kind) && <p role="alert">浏览器恢复记录不可用或已损坏，已停止新提交。请保留浏览器数据并联系管理员核验。</p>}
    {markers.map(marker => <article key={marker.trace_request_id} aria-label={`待核验 ${marker.cutoff_id}`}>
      <p>待核验：{operationLabels[marker.operation]} · 对账 <span className="mono">{marker.cutoff_id}</span> · 原第 {marker.original_review_version} 版</p>
      {marker.actor_person_id !== access.person_id ? <p>此记录属于其他人员，请使用原账号核验，不能覆盖或清除。</p> : <>
        <Button tone="secondary" disabled={busy || !usable} onClick={() => recover(marker, false)}>查询原提交结果</Button>
        <label><input type="checkbox" checked={!!confirmed[marker.trace_request_id]} disabled={busy || !usable}
          onChange={e => setConfirmed({ ...confirmed, [marker.trace_request_id]: e.target.checked })} />确认终结原请求；已提交的操作仍保留原回执</label>
        <Button tone="quiet" disabled={busy || !usable || !confirmed[marker.trace_request_id]} onClick={() => recover(marker, true)}>终结原请求</Button>
      </>}
    </article>)}
    {detail && draft && <>
      <p>对账日期 {detail.business_date}，当前第 {detail.review_version} 版。提交期间不自动重试。</p>
      {stale && <div role="alert"><p>审核版本已变化，草稿仍保留。请先核对最新明细，再采用当前版本；原勾选会清空。</p>
        <Button tone="secondary" disabled={blocked} onClick={() => edit(d => ({ ...d, version: detail.review_version, selected: [] }))}>核对后采用当前版本</Button></div>}
      {detail.review_status === 'approved' ? <p>这份对账已审核，原数量与差异解释保持。</p> : detail.review_version === 0 ?
        can(access, detail, 'open') ? <Button disabled={blocked || stale} onClick={() => submit('open')}>开启审核</Button> : <p>当前没有可执行的审核操作。</p> : <>
        {!['explain', 'approve', 'request_changes'].some(operation => can(access, detail, operation as DailyOperation)) && <p>当前没有可执行的审核操作。</p>}
        {(can(access, detail, 'explain') || can(access, detail, 'request_changes')) && items.filter(i => i.status === 'difference').map(item => {
          const row = draft.rows[item.ordinal], selectedRow = draft.selected.includes(item.ordinal);
          const selectable = can(access, detail, 'explain') || canReturn(item);
          const update = (change: Partial<NonNullable<typeof row>>) => edit(d => ({ ...d, rows: { ...d.rows, [item.ordinal]: { ...(d.rows[item.ordinal] || { explanation: '' }), ...change } } }));
          return <article key={item.ordinal} className="formal-stocktake-observation-row">
            <label><input aria-label={`选择差异 ${item.ordinal}`} type="checkbox" checked={selectedRow} disabled={blocked || stale || (!selectable && !selectedRow)}
              onChange={e => edit(d => ({ ...d, selected: e.target.checked ? [...d.selected, item.ordinal] : d.selected.filter(i => i !== item.ordinal) }))} />第 {item.ordinal} 项 · {item.warehouse_code} · {item.material_id}</label>
            {selectedRow && can(access, detail, 'explain') && <>
              <Field label={`第 ${item.ordinal} 项解释`}><textarea aria-label={`第 ${item.ordinal} 项解释`} maxLength={2000} value={row?.explanation || ''} disabled={blocked || stale}
                onChange={e => update({ explanation: e.target.value })} /></Field>
              {row?.file ? <div><p>已核验：{row.file.original_filename}</p><Button tone="quiet" disabled={blocked || stale}
                onClick={() => update({ file: undefined, blocking: false })}>更换第 {item.ordinal} 项证据</Button></div> :
                <FormalFileUploadField purpose="daily_reconciliation_evidence" label={`第 ${item.ordinal} 项证据`} disabled={blocked || stale}
                  bindingKey={`${access.person_id}:${access.authorization_version}:${detail.cutoff_id}:${item.ordinal}`}
                  onAvailableChange={files => { if (files[0]) update({ file: files[0], blocking: false }); }} onBlockingChange={blocking => update({ blocking })} /> }
            </>}
          </article>;
        })}
        {can(access, detail, 'explain') && <Button disabled={blocked || stale || !items.some(i => draft.selected.includes(i.ordinal))
          || items.some(i => draft.selected.includes(i.ordinal) && (!draft.rows[i.ordinal]?.file || draft.rows[i.ordinal]?.blocking))}
          onClick={() => submit('explain')}>提交本页已选差异解释</Button>}
        {(can(access, detail, 'approve') || can(access, detail, 'request_changes')) && <>
          <Field label="总部审核说明"><textarea aria-label="总部审核说明" maxLength={2000} value={draft.comment} disabled={blocked || stale} onChange={e => edit(d => ({ ...d, comment: e.target.value }))} /></Field>
          <p>总部需独立核验证据；同一人员不能审核自己解释过的对账。审核通过也保留原始数量差异。</p>
          {can(access, detail, 'approve') && <Button disabled={blocked || stale} onClick={() => submit('approve')}>确认总部审核</Button>}
          {can(access, detail, 'request_changes') && selectedItems.some(i => !canReturn(i)) && <>
            <p role="alert">已选项含尚未解释、已退回或不再有差异的明细。请调整选择后再退回，本次不会部分提交。</p>
            <Button tone="quiet" disabled={blocked || stale} onClick={() => edit(d => ({ ...d, selected: d.selected.filter(ordinal => !items.some(i => i.ordinal === ordinal)) }))}>清空本页选择</Button>
          </>}
          {can(access, detail, 'request_changes') && <Button tone="secondary" disabled={blocked || stale || invalidReturnSelection} onClick={() => submit('request_changes')}>退回本页已选差异补证</Button>}
        </>}
      </>}
    </>}
  </section>;
}
