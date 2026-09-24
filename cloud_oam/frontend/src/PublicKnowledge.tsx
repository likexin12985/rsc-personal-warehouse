import { useMemo, useState } from "react";
import catalog from "./knowledge-catalog.json";
import { searchKnowledge, type KnowledgeCatalog } from "./knowledge";
import "./knowledge.css";

const PAGE_SIZE = 20;

export default function PublicKnowledge({ data = catalog }: { data?: KnowledgeCatalog }) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);
  const ready = data.status === "ready" && data.items.length > 0;
  const items = ready ? data.items : [];
  const categories = useMemo(() => [...new Set(items.map((item) => item.category))].sort(), [items]);
  const results = useMemo(() => searchKnowledge(items, query, category), [items, query, category]);
  const pageCount = Math.max(1, Math.ceil(results.length / PAGE_SIZE));

  return (
    <div className="knowledge-site">
      <header className="knowledge-header">
        <a href="/" className="knowledge-brand"><span aria-hidden="true">交</span>交流备件知识大全</a>
        <div className="knowledge-header-actions">
          <span className="knowledge-edition">备件资料 · 公开查询</span>
          <a className="knowledge-admin-link" href="/xx">星星后台管理</a>
        </div>
      </header>
      <main id="main-content">
        <section className="knowledge-hero" aria-labelledby="knowledge-title">
          <p className="knowledge-eyebrow">AC CHARGING · PARTS REFERENCE</p>
          <h1 id="knowledge-title">找到备件，<br />也找到它的适用信息。</h1>
          <p className="knowledge-intro">查询交流充电设备的物料编码、备件名称与适用型号。</p>
          <form className="knowledge-search" role="search" onSubmit={(event) => event.preventDefault()}>
            <label htmlFor="knowledge-query">搜索备件资料</label>
            <div><span aria-hidden="true">⌕</span><input id="knowledge-query" type="search" maxLength={160} placeholder="输入物料编码、备件名称或型号" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} /><button type="submit">查询</button></div>
          </form>
        </section>

        <section className="knowledge-results" aria-label="备件查询结果">
          <div className="knowledge-results-heading"><div><p className="knowledge-eyebrow">PARTS LIBRARY</p><h2>备件资料库</h2></div><label className="knowledge-filter">备件分类<select value={category} onChange={(event) => { setCategory(event.target.value); setPage(1); }}><option value="">全部分类</option>{categories.map((name) => <option key={name}>{name}</option>)}</select></label></div>
          {!ready ? <div className="knowledge-empty" role="status"><span className="knowledge-empty-icon" aria-hidden="true">册</span><h3>资料待更新</h3><p>备件资料正在整理，完成核对后将在这里提供查询。</p><p>当前尚未导入可查询条目。</p></div> : <>
            <p className="knowledge-count" role="status">共找到 {results.length} 条资料</p>
            {results.length === 0 ? <div className="knowledge-empty"><h3>未找到相关备件</h3><p>试试更短的关键词，或选择全部分类。</p><button onClick={() => { setQuery(""); setCategory(""); setPage(1); }}>清除筛选</button></div> : <div className="knowledge-grid">{results.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE).map((item) => <article className="knowledge-card" key={`${item.sourceSheet}:${item.sourceRow}:${item.code}`}><div className="knowledge-card-top"><span>{item.category}</span><code>{item.code}</code></div><h3>{item.name}</h3><dl><dt>适用型号</dt><dd>{item.model || "未注明"}</dd>{item.note && <><dt>资料说明</dt><dd>{item.note}</dd></>}</dl><p className="knowledge-reference">来源：{item.sourceSheet} · 第 {item.sourceRow} 行</p></article>)}</div>}
            {pageCount > 1 && <nav className="knowledge-pagination" aria-label="查询结果分页"><button disabled={page === 1} onClick={() => setPage(page - 1)}>上一页</button><span>{page} / {pageCount}</span><button disabled={page === pageCount} onClick={() => setPage(page + 1)}>下一页</button></nav>}
          </>}
        </section>
        <aside className="knowledge-source"><strong>资料来源</strong><div><a href={data.sourceUrl} target="_blank" rel="noreferrer">交流备件知识大全 · 飞书原表 ↗</a><p>{ready && data.verifiedAt ? `资料核对时间：${data.verifiedAt.slice(0, 10)}` : "导入并核对后显示资料版本。"} 适配信息以原表记录为准。</p></div></aside>
      </main>
      <footer className="knowledge-footer">
        <span>交流备件知识大全</span>
        <div className="knowledge-filing">
          <span>豫ICP备2026043964号-1</span>
          <a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">工业和信息化部备案管理系统</a>
        </div>
        <span>让备件资料更容易查找</span>
      </footer>
    </div>
  );
}
