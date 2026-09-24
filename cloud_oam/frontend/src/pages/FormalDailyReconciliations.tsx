import type { AccessContext } from '../types';
import DailyReviewActions from './DailyReviewActions';
import { downloadDailyEvidence } from '../dailyReviewProtocol';
import { useEffect, useRef, useState } from 'react';
import { subscribeAuthenticationEstablished, subscribeAuthenticationTerminalLogout } from '../api';
import { dailyReadError, loadDailyDetail, loadDailyExcluded, loadDailyHistory, loadDailyItems, loadDailyList,
  operationLabels, reviewLabels, type DailyDetail } from '../formalDailyReconciliation';
import { Button, Empty, Field, Loading, SectionHeader, formatDate } from '../ui';

type ListPage = Awaited<ReturnType<typeof loadDailyList>>;
type Bundle = { detail: DailyDetail; items: Awaited<ReturnType<typeof loadDailyItems>>;
  excluded: Awaited<ReturnType<typeof loadDailyExcluded>>; history: Awaited<ReturnType<typeof loadDailyHistory>> };
type Filters = { businessDate: string; regionId: string };
const quantityLabel = (status: string) => status === 'matched' ? '数量一致' : '数量有差异';

export default function FormalDailyReconciliationsPage({ access }: { access?: AccessContext } = {}) {
  const [filters, setFilters] = useState<Filters>({ businessDate: '', regionId: '' });
  const applied = useRef(filters);
  const [page, setPage] = useState<ListPage | null>(null);
  const [bundle, setBundle] = useState<Bundle | null>(null);
  const [selected, setSelected] = useState('');
  const [listBusy, setListBusy] = useState(false);
  const [detailBusy, setDetailBusy] = useState(false);
  const [error, setError] = useState('');
  const listGeneration = useRef(0), detailGeneration = useRef(0);

  function invalidateDetail() {
    detailGeneration.current++; setBundle(null); setSelected(''); setDetailBusy(false);
  }
  function invalidateAll() {
    listGeneration.current++; invalidateDetail(); setPage(null); setListBusy(false);
    setError('登录身份已变化，请刷新后查看');
  }
  async function readList(afterId?: string, freshFilters = applied.current) {
    const generation = ++listGeneration.current;
    invalidateDetail(); setPage(null); setError(''); setListBusy(true);
    applied.current = freshFilters;
    try {
      const result = await loadDailyList({ ...freshFilters, afterId });
      if (generation === listGeneration.current) setPage(result);
    } catch (reason) {
      if (generation === listGeneration.current) setError(dailyReadError(reason));
    } finally { if (generation === listGeneration.current) setListBusy(false); }
  }
  async function open(id: string) {
    const generation = ++detailGeneration.current;
    setSelected(id); setBundle(null); setDetailBusy(true); setError('');
    try {
      const detail = await loadDailyDetail(id);
      if (generation !== detailGeneration.current) return;
      const [items, excluded, history] = await Promise.all([loadDailyItems(detail), loadDailyExcluded(detail), loadDailyHistory(detail)]);
      if (generation === detailGeneration.current) setBundle({ detail, items, excluded, history });
    } catch (reason) {
      if (generation === detailGeneration.current) setError(dailyReadError(reason));
    } finally { if (generation === detailGeneration.current) setDetailBusy(false); }
  }
  async function readPage(kind: 'items' | 'excluded' | 'history', after: number) {
    if (!bundle || detailBusy) return;
    const current = bundle, generation = ++detailGeneration.current;
    setDetailBusy(true); setError('');
    try {
      const next = kind === 'items' ? { items: await loadDailyItems(current.detail, after) }
        : kind === 'excluded' ? { excluded: await loadDailyExcluded(current.detail, after) }
          : { history: await loadDailyHistory(current.detail, after) };
      if (generation === detailGeneration.current) setBundle({ ...current, ...next });
    } catch (reason) {
      if (generation === detailGeneration.current) { setBundle(null); setError(dailyReadError(reason)); }
    } finally { if (generation === detailGeneration.current) setDetailBusy(false); }
  }
  useEffect(() => {
    void readList();
    const logout = subscribeAuthenticationTerminalLogout(invalidateAll);
    const login = subscribeAuthenticationEstablished(invalidateAll);
    return () => { listGeneration.current++; detailGeneration.current++; logout(); login(); };
  }, []);

  async function download(fileId: string) {
    const generation = detailGeneration.current;
    try { await downloadDailyEvidence(fileId, () => generation === detailGeneration.current); }
    catch { if (generation === detailGeneration.current) setError('证据下载未确认，请重新核验当前权限与文件状态'); }
  }
  const detail = bundle?.detail;
  return <>
    <SectionHeader title="日终对账" subtitle="按每日截止档案核对 OAM 控制数、本地明细账及差异审核记录"
      actions={<Button tone="secondary" disabled={listBusy} onClick={() => void readList()}>刷新列表</Button>} />
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    <section className="content-section">
      <form className="reconciliation-start-form" onSubmit={event => { event.preventDefault(); void readList(undefined, { ...filters }); }}>
        <Field label="对账日期"><input aria-label="对账日期" type="date" value={filters.businessDate} onChange={e => setFilters({ ...filters, businessDate: e.target.value })} /></Field>
        <Field label="区域标识" hint="留空查询当前有权查看的全部区域"><input aria-label="区域标识" value={filters.regionId} onChange={e => setFilters({ ...filters, regionId: e.target.value })} /></Field>
        <Button type="submit" disabled={listBusy}>查询</Button>
      </form>
    </section>
    <section className="content-section table-section" aria-label="日终档案列表">
      {listBusy ? <Loading label="正在查询日终档案" /> : page?.items.length === 0 ? <Empty title="暂无可见日终档案" detail="可调整日期或区域后查询。" /> : page && <>
        <div className="table-wrap"><table><thead><tr><th>日期 / 区域</th><th>数量比较</th><th>差异审核</th><th>来源采集</th><th>本地截止</th><th>详情</th></tr></thead>
          <tbody>{page.items.map(row => <tr key={row.cutoff_id}>
            <td>{row.business_date}<span className="cell-subtitle mono">{row.region_org_id}</span></td>
            <td>{quantityLabel(row.comparison_status)}</td><td>{reviewLabels[row.review_status]}<span className="cell-subtitle">第 {row.review_version} 版</span></td>
            <td>{formatDate(row.source_captured_at)}</td><td>{formatDate(row.local_captured_at)}</td>
            <td><button className="table-action" aria-label={`查看 ${row.business_date} ${row.cutoff_id}`} onClick={() => void open(row.cutoff_id)}>查看</button></td>
          </tr>)}</tbody></table></div>
        <div className="pagination"><Button tone="secondary" onClick={() => void readList()}>返回第一页</Button>
          {page.next_after_id && <Button tone="secondary" onClick={() => void readList(page.next_after_id!)}>下一页档案</Button>}</div>
      </>}
    </section>
    {selected && <div className="form-actions"><Button tone="secondary" disabled={detailBusy} onClick={() => void open(selected)}>刷新当前对账</Button></div>}
    {detailBusy && <Loading label="正在核验对账明细与审核版本" />}
    {bundle && detail && <section className="content-section opening-detail" aria-label="日终对账详情" aria-busy={detailBusy}>
      <h2>{detail.business_date} 日终对账</h2>
      <div className="opening-lifecycle"><div><span>数量比较</span><strong>{quantityLabel(detail.comparison_status)}</strong></div>
        <div><span>差异审核</span><strong>{reviewLabels[detail.review_status]}</strong></div>
        <div><span>审核版本</span><strong>{detail.review_version}</strong></div></div>
      <p>审核记录保留差异的解释与证据，左右数量保持原始截止口径。</p>
      <dl className="detail-grid">
        <div><dt>区域</dt><dd className="mono">{detail.region_org_id}</dd></div>
        <div><dt>来源采集时间</dt><dd>{formatDate(detail.source_captured_at)}</dd></div>
        <div><dt>本地截止时间</dt><dd>{formatDate(detail.local_captured_at)}</dd></div>
        <div><dt>覆盖仓库</dt><dd>{detail.covered_warehouses.join('、')}</dd></div>
        <div><dt>纳入库存口径</dt><dd>{detail.included_buckets.join('、')}</dd></div>
        <div><dt>最近审核时间</dt><dd>{detail.review_updated_at ? formatDate(detail.review_updated_at) : '尚无审核记录'}</dd></div>
      </dl>
      <h3>数量与差异说明</h3>
      <p>共 {detail.item_count} 项；差异为 OAM 控制数减本地数量。</p>
      <div className="table-wrap"><table aria-label="日终数量明细"><thead><tr><th>序号 / 仓库 / 物料</th><th>成色</th><th>OAM 控制数</th><th>本地数量</th><th>差异</th><th>说明与证据</th></tr></thead>
        <tbody>{bundle.items.items.map(row => <tr key={row.ordinal}>
          <td>{row.ordinal} · {row.warehouse_code}<span className="cell-subtitle mono">{row.material_id}</span></td><td>{row.condition}</td>
          <td className="num mono">{row.external_qty}</td><td className="num mono">{row.local_qty}</td><td className="num mono">{row.difference}</td>
          <td>{row.review.explanation || (row.status === 'matched' ? '数量一致' : '尚未解释')}
            {row.review.evidence && <><span className="cell-subtitle mono">证据 {row.review.evidence.file_id}</span>
              <button className="table-action" disabled={detailBusy} onClick={() => void download(row.review.evidence!.file_id)}>查看第 {row.ordinal} 项证据</button></>}
            {row.review.revision_requested === true && <span className="cell-subtitle">退回补证：{row.review.review_comment}</span>}</td>
        </tr>)}</tbody></table></div>
      <div className="pagination"><Button tone="secondary" disabled={detailBusy} onClick={() => void readPage('items', 0)}>明细第一页</Button>
        {bundle.items.next_after_ordinal !== null && <Button tone="secondary" disabled={detailBusy} onClick={() => void readPage('items', bundle.items.next_after_ordinal!)}>下一页明细</Button>}</div>
      <h3>未纳入口径的数量</h3>
      {detail.excluded_quantity_count === 0 ? <p>无未纳入数量</p> : <>
        <p>共 {detail.excluded_quantity_count} 项，单独列示供核对。</p>
        <div className="table-wrap"><table aria-label="未纳入数量"><thead><tr><th>仓库 / 物料</th><th>成色 / 库存状态</th><th>数量</th></tr></thead>
          <tbody>{bundle.excluded.items.map(row => <tr key={row.ordinal}><td>{row.warehouse_code}<span className="cell-subtitle mono">{row.material_id}</span></td><td>{row.condition} / {row.bucket}</td><td>{row.quantity}</td></tr>)}</tbody></table></div>
        <div className="pagination"><Button tone="secondary" disabled={detailBusy} onClick={() => void readPage('excluded', 0)}>未纳入数量第一页</Button>
          {bundle.excluded.next_after_ordinal !== null && <Button tone="secondary" disabled={detailBusy} onClick={() => void readPage('excluded', bundle.excluded.next_after_ordinal!)}>下一页未纳入数量</Button>}</div>
      </>}
      <h3>审核历史</h3>
      {bundle.history.items.length === 0 ? <p>尚无审核记录</p> : <ol>{bundle.history.items.map(event => <li key={event.event_id}>
        <strong>第 {event.version} 版 · {operationLabels[event.operation]}</strong> · {formatDate(event.occurred_at)}
        <p className="mono">操作人员 {event.actor_person_id}</p>
        {event.comment && <p>{event.comment}</p>}
        {event.returned_ordinals.length > 0 && <p>退回项目：{event.returned_ordinals.join('、')}</p>}
        {event.explanations.map(row => <p key={row.ordinal}>第 {row.ordinal} 项：{row.explanation}<span className="cell-subtitle mono">证据 {row.evidence_file_id}</span></p>)}
      </li>)}</ol>}
      {detail.review_version > 0 && <div className="pagination"><Button tone="secondary" disabled={detailBusy} onClick={() => void readPage('history', 0)}>历史第一页</Button>
        {bundle.history.next_after_version !== null && <Button tone="secondary" disabled={detailBusy} onClick={() => void readPage('history', bundle.history.next_after_version!)}>下一页历史</Button>}</div>}
      {detail.approval_comment && <p>总部审核说明：{detail.approval_comment}</p>}
    </section>}
    {access && <DailyReviewActions access={access} detail={detail} items={bundle?.items.items || []} selected={selected}
      readBusy={listBusy || detailBusy} onChanged={id => { if (id === selected) void open(id); }} />}
  </>;
}
