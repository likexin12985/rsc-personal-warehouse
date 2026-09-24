import { useEffect, useState } from "react";

import { showError, Button } from "../ui";
import {
  clearInventoryReportKey, createInventoryReportDownload, inventoryReportAvailable,
  newInventoryReportKey, readInventoryReport, readInventoryReportKey,
  recoverInventoryReport, requestInventoryReport, saveInventoryReportKey,
  type InventoryReportStatus,
} from "../inventoryReportClient";

const STATUS_LABELS: Record<InventoryReportStatus["status"], string> = {
  queued: "排队中", running: "生成中", succeeded: "已生成", failed: "生成失败", cancelled: "已取消",
};

export default function InventoryReportPanel({ personId, authorizationVersion }: {
  personId: string;
  authorizationVersion: number;
}) {
  const [available, setAvailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [coordinate, setCoordinate] = useState<string | null>(null);
  const [report, setReport] = useState<InventoryReportStatus | null>(null);
  const [download, setDownload] = useState<{ url: string; expires_at: string } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    inventoryReportAvailable().then(async enabled => {
      if (!active || !enabled) return;
      setAvailable(true);
      const key = readInventoryReportKey(personId, authorizationVersion);
      if (!key) return;
      setCoordinate(key);
      const status = await recoverInventoryReport(key);
      if (active) setReport(status);
    }).catch(reason => { if (active) setError(showError(reason)); });
    return () => { active = false; };
  }, [personId, authorizationVersion]);

  useEffect(() => {
    if (!download) return;
    const remaining = Date.parse(download.expires_at) - Date.now();
    if (remaining <= 0) { setDownload(null); return; }
    const timer = window.setTimeout(() => setDownload(null), remaining);
    return () => window.clearTimeout(timer);
  }, [download]);

  if (!available) return error
    ? <div className="alert alert-error" role="alert">库存报表能力读取失败：{error}</div>
    : null;

  async function recover(key: string) {
    setDownload(null);
    const status = await recoverInventoryReport(key);
    setReport(status);
  }

  async function apply() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      if (coordinate) {
        await recover(coordinate);
      } else {
        const key = newInventoryReportKey();
        saveInventoryReportKey(personId, authorizationVersion, key);
        setCoordinate(key);
        try {
          await requestInventoryReport(key);
        } catch {
          // A lost POST response has unknown outcome. Only the original-key GET
          // may resolve it; a fresh POST is never issued automatically.
        }
        await recover(key);
      }
    } catch (reason) {
      setReport(null);
      setError(`申请结果未确认，请按原申请找回：${showError(reason)}`);
    } finally {
      setBusy(false);
    }
  }

  async function refresh() {
    if (!report || busy) return;
    setBusy(true);
    setError("");
    setDownload(null);
    try { setReport(await readInventoryReport(report.job_id)); }
    catch (reason) { setReport(null); setError(showError(reason)); }
    finally { setBusy(false); }
  }

  async function signDownload() {
    if (!report?.file_available || busy) return;
    setBusy(true);
    setError("");
    setDownload(null);
    try { setDownload(await createInventoryReportDownload(report.job_id)); }
    catch (reason) { setError(`下载意图结果未确认；如需再次签发请明确点击下载：${showError(reason)}`); }
    finally { setBusy(false); }
  }

  function startAnother() {
    try {
      clearInventoryReportKey(personId, authorizationVersion);
      setCoordinate(null);
      setReport(null);
      setDownload(null);
      setError("");
    } catch (reason) { setError(showError(reason)); }
  }

  return <section className="content-section" aria-label="库存报表导出">
    <div className="content-title"><div><h2>库存余额导出</h2>
      <p>异步生成当前授权范围的 Excel；任务状态与受控下载分别记录。</p></div></div>
    {report && <div role="status">
      <p>任务 {report.job_id} · {STATUS_LABELS[report.status]}</p>
      <p>申请时间：{new Date(report.created_at).toLocaleString("zh-CN")} · 已签发下载意图 {report.download_count} 次</p>
    </div>}
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    <div className="section-actions">
      <Button disabled={busy} onClick={apply}>{coordinate ? "按原申请找回" : "申请导出"}</Button>
      {report && <Button tone="secondary" disabled={busy} onClick={refresh}>刷新任务状态</Button>}
      {report?.file_available && <Button tone="secondary" disabled={busy} onClick={signDownload}>签发下载链接</Button>}
      {(report && ["succeeded", "failed", "cancelled"].includes(report.status) || coordinate && !report && !!error)
        && <Button tone="quiet" disabled={busy} onClick={startAnother}>结束跟踪并准备新申请</Button>}
    </div>
    {download && Date.parse(download.expires_at) > Date.now() && <p>
      <a href={download.url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">下载库存报表</a>
      <span className="muted small"> 链接有效至 {new Date(download.expires_at).toLocaleString("zh-CN")}</span>
    </p>}
  </section>;
}
