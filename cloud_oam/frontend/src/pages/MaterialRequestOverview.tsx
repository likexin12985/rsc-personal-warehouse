import { useEffect, useState } from "react";
import { Button, Loading, showError } from "../ui";
import { OVERVIEW_DIMENSIONS, readMaterialRequestOverview, type MaterialRequestOverview as Overview, type OverviewQuery } from "../materialRequestOverviewClient";
import "./materialRequestOverview.css";

export default function MaterialRequestOverview() {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [request, setRequest] = useState<{ query: OverviewQuery; label: string }>({ query: {}, label: "全部创建时间" });
  const [result, setResult] = useState<Overview | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setResult(null); setBusy(true); setError("");
    readMaterialRequestOverview(request.query, controller.signal).then(value => {
      if (current) setResult(value);
    }).catch(reason => { if (current) setError(showError(reason)); })
      .finally(() => { if (current) setBusy(false); });
    return () => { current = false; controller.abort(); };
  }, [request]);

  function search(event: React.FormEvent) {
    event.preventDefault();
    if (start && end && start > end) { setError("开始日期不能晚于结束日期"); setResult(null); return; }
    const query: OverviewQuery = {};
    if (start) query.created_from = new Date(`${start}T00:00:00+08:00`).toISOString();
    if (end) query.created_before = new Date(Date.parse(`${end}T00:00:00+08:00`) + 86400000).toISOString();
    setResult(null);
    setRequest({ query, label: !start && !end ? "全部创建时间" : `${start || "不限开始日期"} 至 ${end || "不限结束日期"}` });
  }

  return <section className="material-request-overview">
    <div className="section-header"><div><h1>需求与履约概览</h1><p>查看授权区域内申请的当前进度，统计单位为单。</p></div></div>
    <form className="panel" onSubmit={search} style={{ display: "flex", flexWrap: "wrap", alignItems: "end", gap: 16 }}>
      <label>创建开始日期（北京时间）<input type="date" aria-label="创建开始日期" value={start} onChange={event => setStart(event.target.value)} /></label>
      <label>创建结束日期（含当天）<input type="date" aria-label="创建结束日期" value={end} onChange={event => setEnd(event.target.value)} /></label>
      <Button type="submit" disabled={busy}>查询</Button>
      <Button type="button" disabled={busy} onClick={() => { setStart(""); setEnd(""); setResult(null); setRequest({ query: {}, label: "全部创建时间" }); }}>清除筛选</Button>
    </form>
    {busy && <Loading label="正在读取需求与履约概览" />}
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {result && <>
      <section className="panel" aria-label="查询范围">
        <h2>{result.matched_requests} 单申请</h2>
        <p>{request.label} · 当前授权范围</p>
        <p>统计时间：{new Date(result.observed_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}（北京时间）</p>
        <p>各环节分别统计，不能相加。审批通过、物流签收和个人仓入账分别显示；三级审批仅统计当前申请版本的最新审批尝试。</p>
      </section>
      {result.matched_requests === 0 ? <div className="panel">当前筛选范围内暂无申请。</div> :
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))", gap: 16 }}>
          {Object.entries(OVERVIEW_DIMENSIONS).map(([dimension, definition]) => <section className="panel" key={dimension} aria-label={definition.label}>
            <h2>{definition.label}</h2>
            <dl style={{ margin: 0 }}>{Object.entries(definition.states).map(([state, label]) =>
              <div key={state} style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "6px 0" }}>
                <dt>{label}</dt><dd style={{ margin: 0, fontVariantNumeric: "tabular-nums" }}>{result.counts[dimension][state]} 单</dd>
              </div>)}</dl>
          </section>)}
        </div>}
    </>}
  </section>;
}
