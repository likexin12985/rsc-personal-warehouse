import { useLayoutEffect, useRef, useState } from "react";
import { controlDirectoryKey, loadControlBatches, type ControlBatch } from "../formalOpeningControlDirectory";
import type { OpeningPreparationActor } from "../formalOpeningStartOptions";
import { Button, Field } from "../ui";

function Directory({ actor, regionId, onSelect, startEnabled = false }: { actor: OpeningPreparationActor; regionId: string; onSelect?: (batch?: ControlBatch) => void; startEnabled?: boolean }) {
  const selectRef = useRef(onSelect); selectRef.current = onSelect;
  const [state, setState] = useState<{ items: readonly ControlBatch[]; next: string | null; loading: boolean; error: boolean }>({
    items: [], next: null, loading: true, error: false,
  });
  const [selected, setSelected] = useState("");
  const generation = useRef(0), running = useRef(false);
  async function load(after: string | null = null) {
    if (running.current) return;
    running.current = true;
    const invocation = ++generation.current;
    setSelected("");
    selectRef.current?.();
    const previous = after === null ? [] : state.items;
    setState({ items: previous, next: null, loading: true, error: false });
    try {
      const page = await loadControlBatches(actor, regionId, after);
      if (invocation !== generation.current) return;
      if (after !== null && previous.at(-1)?.publicationId !== after) throw new Error("cursor changed");
      setState({ items: [...previous, ...page.items], next: page.nextAfterId, loading: false, error: false });
    } catch {
      if (invocation !== generation.current) return;
      setState({ items: [], next: null, loading: false, error: true });
    } finally {
      if (invocation === generation.current) running.current = false;
    }
  }
  useLayoutEffect(() => {
    running.current = false;
    void load();
    return () => { generation.current += 1; };
    // The wrapper and preparation parent remount on actor/region/revalidation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const item = state.items.find((row) => row.publicationId === selected);
  const date = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });
  return <section className="opening-detail-section" aria-label="已发布控制批次目录">
    <Field label="查看已发布控制批次"><select aria-label="查看已发布控制批次" value={selected} disabled={state.loading || state.error}
      onChange={(event) => { if (!running.current && !state.error) { setSelected(event.target.value); selectRef.current?.(state.items.find((row) => row.publicationId === event.target.value)); } }}>
      <option value="">{state.loading ? "正在读取批次" : "请选择批次查看"}</option>
      {state.items.map((row) => <option key={row.publicationId} value={row.publicationId}>
        {row.sourceName} · 采集 {date(row.capturedAt)} · {row.isLatest ? "最新发布" : "历史批次"}
      </option>)}
    </select></Field>
    <p>仅显示已发布记录。批次来源授权、有效期与物料版本仍需在启动时重新核验{startEnabled ? '。' : '，当前不能启动盘点。'}</p>
    {state.error && <p role="alert" className="alert alert-error">控制批次读取失败，旧批次已清除，请刷新后重选。</p>}
    {!state.loading && !state.error && state.items.length === 0 && <p>本区域暂无已发布控制批次。这不代表库存为零。</p>}
    {item && <dl className="detail-grid">
      <div><dt>采集时间</dt><dd>{date(item.capturedAt)}</dd></div>
      <div><dt>发布时间</dt><dd>{date(item.publishedAt)}</dd></div>
      <div><dt>发布证据截止时间</dt><dd>{date(item.validUntil)}</dd></div>
      <div><dt>控制明细条数</dt><dd>{item.recordCount} 条（不是库存数量）</dd></div>
      <div><dt>发布状态</dt><dd>{item.isLatest ? "最新发布，待启动校验" : "已被后续批次替代，仅供查阅"}</dd></div>
    </dl>}
    <div className="form-actions">
      <Button type="button" tone="quiet" disabled={state.loading} onClick={() => void load()}>刷新控制批次</Button>
      {state.next && <Button type="button" tone="quiet" disabled={state.loading} onClick={() => void load(state.next)}>加载更多控制批次</Button>}
    </div>
  </section>;
}

export default function OpeningControlDirectory(props: { actor: OpeningPreparationActor; regionId: string; onSelect?: (batch?: ControlBatch) => void; startEnabled?: boolean }) {
  return <Directory key={controlDirectoryKey(props.actor, props.regionId)} {...props} />;
}
