import { useEffect, useState } from "react";
import type { FormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
import type { MaterialRequestDetail } from "./formalMaterialRequests";
import { type OamReceiptEvidenceResult, validateOamReceiptEvidence } from "./materialRequestOamReceipt";
import { showError } from "./ui";

type Props = { adapter: FormalMaterialRequestAdapter; detail: MaterialRequestDetail | null };
const statusLabel = { synced: "已同步", exception: "异常" } as const;

export default function FormalMaterialRequestOamReceiptPanel({ adapter, detail }: Props) {
  const [rows, setRows] = useState<OamReceiptEvidenceResult[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setRows([]); setError("");
    if (!detail || !adapter.listOamReceiptEvidence) return () => { live = false; };
    adapter.listOamReceiptEvidence(detail.request_id).then(value => {
      if (live) setRows(value.map(validateOamReceiptEvidence));
    }).catch(errorValue => {
      if (live) setError(showError(errorValue));
    });
    return () => { live = false; };
  }, [adapter, detail?.request_id, detail?.request_version]);
  if (!detail || !adapter.listOamReceiptEvidence) return null;
  return <section className="opening-detail-section" aria-label="OAM收货证据">
    <header><div><h3>OAM 收货证据</h3><p>只读显示星星 OAM 的收货镜像，不改变本地收货或个人仓入账状态。</p></div></header>
    {error && <div className="alert alert-error">{error}</div>}
    {rows.length === 0 ? <p>暂无 OAM 收货证据。</p> : <div className="table-wrap"><table><thead><tr><th>状态</th><th>发运单</th><th>来源时间</th><th>来源版本</th><th>载荷指纹</th></tr></thead><tbody>
      {rows.map(row => <tr key={row.evidence_id}><td>{statusLabel[row.status]}</td><td className="mono">{row.shipment_id}</td><td>{row.source_time}</td><td>{row.source_version}</td><td className="mono">{row.payload_sha256}</td></tr>)}
    </tbody></table></div>}
  </section>;
}
