import { useEffect, useRef, useState } from "react";

import {
  checkOpeningCountFormat,
  openingCountFormatErrors,
  openingCountTemplate,
  saveOpeningCountWorkbook,
  type OpeningCountFormatResult,
} from "../openingCountImportFormat";
import { Button, showError } from "../ui";

export default function OpeningCountFormatPanel() {
  const epoch = useRef(0);
  const mounted = useRef(true);
  const running = useRef(false);
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<OpeningCountFormatResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; epoch.current += 1; };
  }, []);

  async function perform(work: (isCurrent: () => boolean) => Promise<void>): Promise<void> {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError("");
    const attempt = epoch.current;
    const isCurrent = () => mounted.current && attempt === epoch.current;
    try { await work(isCurrent); }
    catch (reason) { if (attempt === epoch.current) setError(showError(reason)); }
    finally {
      running.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  return <section className="content-section" aria-label="期初盘点 Excel 格式预检">
    <div className="content-title"><div><h2>期初盘点 Excel 格式预检</h2>
      <p>仅检查文件格式和字段；未绑定盘点任务或范围，不提交计数，也不生成库存。</p></div></div>
    <div className="section-actions">
      <Button type="button" tone="secondary" disabled={busy} onClick={() => void perform(async (isCurrent) => {
        const blob = await openingCountTemplate();
        if (isCurrent()) saveOpeningCountWorkbook(blob, "rsc-opening-count-v1.xlsx");
      })}>下载空白模板</Button>
    </div>
    <label className="field"><span>选择 XLSX 文件（最多 8 MiB）</span>
      <input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        disabled={busy} onChange={(event) => {
          epoch.current += 1;
          setFile(event.target.files?.[0] || null);
          setResult(null);
          setError("");
        }} />
    </label>
    <div className="section-actions">
      <Button type="button" disabled={!file || busy} onClick={() => void perform(async (isCurrent) => {
        if (!file) return;
        const checked = await checkOpeningCountFormat(file);
        if (isCurrent()) setResult(checked);
      })}>检查格式</Button>
      {file && result && !result.format_valid && <Button type="button" tone="secondary" disabled={busy}
        onClick={() => void perform(async (isCurrent) => {
          const blob = await openingCountFormatErrors(file);
          if (isCurrent()) saveOpeningCountWorkbook(blob, "rsc-opening-count-errors.xlsx");
        })}>下载错误报告</Button>}
    </div>
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {result && <div role="status" className={`alert ${result.format_valid ? "alert-info" : "alert-warning"}`}>
      {result.format_valid
        ? `文件格式通过，共 ${result.row_count} 行。仍需任务/范围、物料与权限的业务预校验和显式确认，当前未导入。`
        : `发现 ${result.errors.length} 项格式错误，共读取 ${result.row_count} 行；未导入任何行。`}
    </div>}
    {result && !result.format_valid && <div className="table-wrap"><table>
      <thead><tr><th>行号</th><th>字段</th><th>错误</th></tr></thead>
      <tbody>{result.errors.map((item, index) => <tr key={`${item.row}:${item.field}:${index}`}>
        <td>{item.row}</td><td>{item.field}</td><td>{item.message}</td>
      </tr>)}</tbody>
    </table></div>}
  </section>;
}
