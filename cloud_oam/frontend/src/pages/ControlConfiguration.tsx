import { useEffect, useRef, useState } from "react";
import { apiNoReplay, jsonBody, mutationHeaders } from "../api";
import { actionLabel, handoffResponse, object, parseCommand, parseResponse, verifiedResponse,
  type ControlActor, type ControlCommand, type Purpose, type ReviewView, type SignedDocument } from "../controlConfiguration";
import { Button, showError } from "../ui";

const path = "/v1/inventory-control/configuration";
const labels = { preview: "核查", execute: "授权", status: "原结果查询" };
const time = (value: unknown) => value == null ? "未指定" : new Date(typeof value === "number" ? value * 1000 : String(value)).toLocaleString("zh-CN", { hour12: false });
function readFile(file: File): Promise<string> {
  if (file.size > 2 * 1024 * 1024) return Promise.reject(new Error("配置文件不能超过 2 MiB"));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("文件读取失败，请保留原文件"));
    reader.onload = () => typeof reader.result === "string" ? resolve(reader.result) : reject(new Error("文件格式无效"));
    reader.readAsText(file);
  });
}
export default function ControlConfiguration({ actor }: { actor: ControlActor }) {
  const [command, setCommand] = useState<ControlCommand | null>(null);
  const [response, setResponse] = useState<SignedDocument | null>(null);
  const [view, setView] = useState<ReviewView | null>(null);
  const [packet, setPacket] = useState<SignedDocument | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const epoch = useRef(0), writing = useRef(false);
  useEffect(() => {
    epoch.current += 1; setCommand(null); setResponse(null); setView(null); setPacket(null);
    setConfirmed(false); setAttempted(false); setNotice(""); setError(""); setBusy(false); writing.current = false;
    const clock = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { epoch.current += 1; window.clearInterval(clock); };
  }, [actor.person_id, actor.authorization_version]);

  async function importFile(file: File, kind: "command" | "response") {
    if (writing.current || (kind === "response" && !command)) return;
    const current = ++epoch.current;
    writing.current = true; setBusy(true); setError(""); setNotice(""); setConfirmed(false);
    // A malformed replacement must not leave an earlier approved view active.
    setView(null); setResponse(null);
    if (kind === "command") { setCommand(null); setPacket(null); setAttempted(false); }
    try {
      const text = await readFile(file);
      if (current !== epoch.current) return;
      if (kind === "command") { setCommand(parseCommand(text)); setNotice("请求已读取，等待核查回执。"); }
      else {
        const uploaded = parseResponse(text);
        const result = verifiedResponse(await apiNoReplay(`${path}/inspect-response`, {
          method: "POST", ...mutationHeaders("control-inspect"),
          ...jsonBody({ command, expected_authorization_version: actor.authorization_version, owner_response: uploaded }),
        }), uploaded, command!, actor);
        if (current !== epoch.current) return;
        setResponse(uploaded); setView(result);
        setNotice(result.purpose === "preview" ? "核查回执已验签，请核对来源、版本与审核证据。"
          : result.result.recorded ? "配置决策已记录。控制账发布与期初启动仍需分别验收。"
            : "尚未查到原决策，请核对原执行状态。不要据此重复授权。");
      }
    } catch (cause) { if (current === epoch.current) setError(showError(cause)); }
    finally { if (current === epoch.current) { writing.current = false; setBusy(false); } }
  }

  async function issue(purpose: Purpose) {
    if (!command || writing.current || (purpose !== "preview" && !response)
      || (purpose === "execute" && (!confirmed || attempted || !view?.can_execute || view.expires_at * 1000 <= Date.now()))) return;
    const current = ++epoch.current;
    writing.current = true; setBusy(true); setError(""); setNotice("");
    if (purpose === "execute") { setAttempted(true); setConfirmed(false); }
    try {
      const result = handoffResponse(await apiNoReplay(`${path}/handoffs`, {
        method: "POST", ...mutationHeaders(`control-${purpose}`),
        ...jsonBody({ command, expected_authorization_version: actor.authorization_version, purpose,
          ...(purpose === "preview" ? {} : { owner_response: response }) }),
      }), command, actor, purpose);
      if (current !== epoch.current) return;
      setPacket(result); setNotice(`${labels[purpose]}交接包已生成，请下载并交给受控执行端。等待执行端签名回执。`);
    } catch (cause) {
      if (current === epoch.current) setError(`签发结果未确认，请保留原请求和回执；需要时生成原结果查询包。${showError(cause)}`);
    } finally { if (current === epoch.current) { writing.current = false; setBusy(false); } }
  }

  function download() {
    if (!packet || Number(packet.payload.expires_at) * 1000 <= Date.now()) return;
    let url: string | undefined;
    try {
      url = URL.createObjectURL(new Blob([JSON.stringify(packet, null, 2)], { type: "application/json" }));
      const link = document.createElement("a"); link.href = url;
      link.download = `control-${packet.payload.purpose}-${packet.payload.handoff_id}.json`;
      document.body.appendChild(link); link.click(); link.remove();
    } catch { setError("下载未确认，可在有效期内重新下载当前交接包。"); }
    finally { if (url) window.setTimeout(() => URL.revokeObjectURL(url!), 1000); }
  }

  const review = view?.purpose === "preview" ? object(view.result.review) : null;
  const region = review ? object(review.region) : null, source = review ? object(review.source) : null;
  const evidence = review ? object(review.evidence_file) : null;
  const canExecute = !!view?.can_execute && view.expires_at * 1000 > now && !attempted;
  return <section className="content-section" aria-label="控制来源配置">
    <div className="content-title"><div><h2>控制来源配置</h2><p>核查总部来源与版本授权。每一步使用单独的交接包，由受控执行端处理并返回签名回执。</p></div></div>
    <p>导入配置请求后先生成核查包；核查回执通过验证后，才能确认授权。交接包有效期最长 5 分钟。</p>
    <label className="field"><span>导入配置请求</span><input type="file" accept=".json,application/json" disabled={busy} onChange={event => {
      const file = event.target.files?.[0]; event.target.value = ""; if (file) void importFile(file, "command");
    }} /></label>
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {notice && <div className="form-notice" role="status">{notice}</div>}
    {command && <>
      <h3>{actionLabel(command)}</h3><p>原因：{command.reason}</p>
      <p>有效期：{command.action === "revoke" ? "撤销原授权" : `${time(command.valid_from)} — ${command.valid_to ? time(command.valid_to) : "未指定截止时间"}`}</p>
      <details><summary>请求明细</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(command, null, 2)}</pre></details>
      <div className="form-actions"><Button type="button" tone="secondary" disabled={busy || attempted} onClick={() => void issue("preview")}>生成核查交接包</Button></div>
      <label className="field"><span>导入执行端签名回执</span><input type="file" accept=".json,application/json" disabled={busy} onChange={event => {
        const file = event.target.files?.[0]; event.target.value = ""; if (file) void importFile(file, "response");
      }} /></label>
      {review && <article className="content-section" aria-label="已验证核查内容">
        <h3>已验证核查内容</h3><p>区域：{String(region!.name)}（{String(region!.code)}）</p>
        <p>来源：{String(source!.code)} · {source!.enabled ? "启用" : "停用"} · {source!.mode === "read_only" ? "只读" : String(source!.mode)}</p>
        <p>版本：{review.catalogue ? String(object(review.catalogue).catalog_revision ?? "缺少版本") : "本次核查来源授权"}</p>
        <p>审核证据：{String(evidence!.mime_type)}，{String(evidence!.size_bytes)} 字节</p>
        <p className="cell-subtitle">请核对审核原文件与明细中的文件编号、摘要；回执有效至 {time(view!.expires_at)}。</p>
        <details><summary>完整核查明细与历史授权</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(review, null, 2)}</pre></details>
        <label><input type="checkbox" checked={confirmed} disabled={busy || !canExecute} onChange={event => setConfirmed(event.target.checked)} />已核对来源、版本与审核证据</label>
        <div className="form-actions"><Button type="button" disabled={busy || !confirmed || !canExecute} onClick={() => void issue("execute")}>确认并生成授权交接包</Button></div>
        {!canExecute && <p>{attempted ? "已尝试签发授权包，请先核查原执行结果。" : "核查回执已过期或当前授权版本已变化，请重新核查。"}</p>}
      </article>}
      {response && <Button type="button" tone="secondary" disabled={busy} onClick={() => void issue("status")}>生成原结果查询包</Button>}
      {view && view.purpose !== "preview" && view.result.recorded === true && <p>决策编号：{String(object(view.result.decision).decision_id)}</p>}
    </>}
    {packet && <article className="content-section" aria-label="待交接文件">
      <h3>{labels[packet.payload.purpose as Purpose]}交接包</h3><p>有效至 {time(packet.payload.expires_at)}；下载文件后仍需等待签名回执确认结果。</p>
      <Button type="button" tone="secondary" disabled={busy || Number(packet.payload.expires_at) * 1000 <= now} onClick={download}>下载当前交接包</Button>
    </article>}
  </section>;
}
