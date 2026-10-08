import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { History, Receipt } from './formalReturnReceiving';
import './returnReceiptPrint.css';

const conditions = { new: '新件', used: '旧件', damaged: '坏件' };
const time = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
export type ReceiptDocument = { history: History; receipt: Receipt };

/** A read-only receipt copy. It never infers an inventory posting or current balance. */
export default function ReturnReceiptPrint({ document: value, busy, error, onPrint, onClose }: {
  document: ReceiptDocument; busy: boolean; error: string; onPrint: () => void; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null), { history, receipt } = value, parcel = history.package;
  useEffect(() => {
    const previous = document.activeElement;
    if (dialog.current?.showModal) dialog.current.showModal();
    else dialog.current?.setAttribute('open', '');
    return () => { if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);
  return createPortal(<dialog ref={dialog} className="return-receipt-print" aria-label="退回收货单打印预览" onCancel={e => { e.preventDefault(); onClose(); }}>
    <style media="print">{'@page { size: A4 portrait; margin: 12mm; @bottom-right { content: "第 " counter(page) " 页 / 共 " counter(pages) " 页"; font: 9pt sans-serif; } }'}</style>
    <div className="receipt-print-controls"><p>打印前会重新核验查看权限和本张收货单。</p><button disabled={busy} onClick={onPrint}>{busy ? '正在核验…' : '打印本收货单'}</button><button onClick={onClose}>关闭打印预览</button>{error && <p role="alert">{error}</p>}</div>
    <header><p>RSC个人仓 · {'origin' in parcel ? '报损退回' : '工单旧坏件退回'}</p><h1>退回收货单</h1><strong>{receipt.receipt_no}</strong></header>
    <dl className="receipt-print-meta">
      <div><dt>退回单号</dt><dd>{parcel.operation_no}</dd></div><div><dt>包裹单号</dt><dd>{parcel.shipment_no}</dd></div>
      <div><dt>收货仓</dt><dd>{parcel.target_location_name}</dd></div><div><dt>验收时间（北京时间）</dt><dd>{time(receipt.received_at)}</dd></div>
      <div><dt>承运商 / 运单号</dt><dd>{parcel.carrier} / {parcel.tracking_no}</dd></div><div><dt>验收结果</dt><dd>{receipt.status === 'exception' ? '有异常，详见本次明细' : '已接受'}</dd></div>
    </dl>
    <p className="receipt-print-boundary">本单仅记录本次实物验收，不作为库存入库证明。破损数量包含在接受数量内；短少为本次观察，不等于累计损失。</p>
    <table><colgroup><col className="receipt-material-column" /><col span={6} /></colgroup><thead><tr><th colSpan={7}>本次验收明细 · {receipt.receipt_no}</th></tr><tr><th>物料 / 批次</th><th>发出成色</th><th>单位</th><th>本次接受</th><th>本次拒收</th><th>本次短少</th><th>接受中破损</th></tr></thead><tbody>{receipt.lines.map(line => <tr key={line.shipment_line_id}>
      <td>{line.sku_code}<br />{line.material_name}{line.lot_no && <><br />批次：{line.lot_no}</>}</td><td>{conditions[line.condition_code]}</td><td>{line.base_unit}</td><td>{line.accepted_qty}</td><td>{line.rejected_qty}</td><td>{line.shortage_qty}</td><td>{line.damaged_qty}</td>
    </tr>)}</tbody></table>
    <section><h2>验收说明</h2><p className="receipt-print-text">{receipt.reason}</p></section>
    {receipt.lines.map(line => {
      const groups = [['接受 SN', line.accepted_serials], ['拒收 SN', line.rejected_serials], ['短少 SN', line.shortage_serials], ['接受中破损 SN', line.accepted_serials.filter(sn => line.damaged_serial_ids.includes(sn.serial_id))]] as const;
      if (!groups.some(([, serials]) => serials.length) && !line.exceptions.length) return null;
      return <section key={line.shipment_line_id}><h2>{line.sku_code} · {line.material_name}</h2>
        {groups.map(([label, serials]) => serials.length > 0 && <div key={label}><h3>{label}</h3><ul className="receipt-print-serials">{serials.map(sn => <li key={sn.serial_id}>{sn.serial_no}</li>)}</ul></div>)}
        {line.exceptions.map(issue => <p className="receipt-print-text" key={issue.exception_type}>异常说明：{issue.description}（原验收记录已关联凭证）</p>)}
      </section>;
    })}
    <footer><p>收货单号：{receipt.receipt_no}</p><p>验收人编号：{receipt.operator_person_id}</p><p>原记录核验时间（北京时间）：{time(history.queried_at)}</p><p>收货人签字：________________　复核签字：________________</p></footer>
  </dialog>, document.body);
}
