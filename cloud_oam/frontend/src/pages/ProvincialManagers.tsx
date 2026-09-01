import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, ShieldCheck, UserMinus, UserPlus } from "lucide-react";

import { api, jsonBody, mutationHeaders } from "../api";
import type {
  ProvincialManagerAssignment,
  ProvincialManagerCandidate,
  ProvincialManagerMutation,
  ProvincialRegionOption,
} from "../types";
import {
  Button,
  Empty,
  Field,
  Loading,
  SectionHeader,
  formatDate,
  showError,
} from "../ui";

export default function ProvincialManagersPage() {
  const [regions, setRegions] = useState<ProvincialRegionOption[]>([]);
  const [regionId, setRegionId] = useState("");
  const [candidates, setCandidates] = useState<ProvincialManagerCandidate[]>([]);
  const [assignments, setAssignments] = useState<ProvincialManagerAssignment[]>([]);
  const [personId, setPersonId] = useState("");
  const [validTo, setValidTo] = useState("");
  const [reason, setReason] = useState("");
  const [loadingRegions, setLoadingRegions] = useState(true);
  const [loadingScope, setLoadingScope] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const selectedRegion = useMemo(
    () => regions.find((row) => row.organization_id === regionId) || null,
    [regionId, regions],
  );
  const selectedCandidate = useMemo(
    () => candidates.find((row) => row.person_id === personId) || null,
    [candidates, personId],
  );

  const loadRegions = useCallback(async () => {
    setLoadingRegions(true);
    setError("");
    try {
      const rows = await api<ProvincialRegionOption[]>(
        "/access/provincial-managers/regions",
      );
      setRegions(rows);
      setRegionId((current) => (
        rows.some((row) => row.organization_id === current)
          ? current
          : rows[0]?.organization_id || ""
      ));
    } catch (err) {
      setError(showError(err));
    } finally {
      setLoadingRegions(false);
    }
  }, []);

  const loadScope = useCallback(async () => {
    if (!regionId) {
      setCandidates([]);
      setAssignments([]);
      return;
    }
    setLoadingScope(true);
    setError("");
    try {
      const query = new URLSearchParams({ organization_id: regionId }).toString();
      const [nextCandidates, nextAssignments] = await Promise.all([
        api<ProvincialManagerCandidate[]>(
          `/access/provincial-managers/candidates?${query}`,
        ),
        api<ProvincialManagerAssignment[]>(
          `/access/provincial-managers/assignments?${query}`,
        ),
      ]);
      setCandidates(nextCandidates);
      setAssignments(nextAssignments);
      setPersonId((current) => (
        nextCandidates.some((row) => row.person_id === current)
          ? current
          : nextCandidates[0]?.person_id || ""
      ));
    } catch (err) {
      setError(showError(err));
    } finally {
      setLoadingScope(false);
    }
  }, [regionId]);

  useEffect(() => { loadRegions(); }, [loadRegions]);
  useEffect(() => { loadScope(); }, [loadScope]);

  async function grant(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedCandidate || !selectedRegion) {
      setError("请选择一个可授权人员和区域公司");
      return;
    }
    const checkedReason = reason.trim();
    if (!checkedReason) {
      setError("请填写本次任命原因");
      return;
    }
    let validToIso: string | null = null;
    if (validTo) {
      const parsed = new Date(validTo);
      if (Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
        setError("授权有效期必须晚于当前时间，或留空表示长期有效");
        return;
      }
      validToIso = parsed.toISOString();
    }
    setSubmitting(true);
    setError("");
    setNotice("");
    try {
      const result = await api<ProvincialManagerMutation>(
        "/access/provincial-managers/assignments",
        {
          method: "POST",
          ...mutationHeaders("provincial-manager-grant"),
          ...jsonBody({
            person_id: selectedCandidate.person_id,
            organization_id: selectedRegion.organization_id,
            expected_authorization_version: selectedCandidate.authorization_version,
            valid_to: validToIso,
            reason: checkedReason,
          }),
        },
      );
      setNotice(
        `已任命 ${selectedCandidate.person_name}；授权版本 ${result.authorization_version}，审计证据已写入。`,
      );
      setReason("");
      setValidTo("");
      await loadScope();
    } catch (err) {
      setError(showError(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function revoke(row: ProvincialManagerAssignment) {
    const entered = window.prompt(
      `请输入撤销 ${row.person_name} 省背包负责人授权的原因`,
      "人员或职责调整",
    );
    const checkedReason = entered?.trim() || "";
    if (!checkedReason) return;
    setSubmitting(true);
    setError("");
    setNotice("");
    try {
      const result = await api<ProvincialManagerMutation>(
        `/access/provincial-managers/assignments/${row.assignment_id}/revoke`,
        {
          method: "POST",
          ...mutationHeaders("provincial-manager-revoke"),
          ...jsonBody({
            expected_authorization_version: row.authorization_version,
            reason: checkedReason,
          }),
        },
      );
      setNotice(
        `已撤销 ${row.person_name}；授权版本 ${result.authorization_version}，历史授权未删除。`,
      );
      await loadScope();
    } catch (err) {
      setError(showError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return <>
    <SectionHeader
      title="省背包负责人"
      subtitle="负责人默认留空，仅由蔚来总部全国管理员按区域手工配置"
      actions={<Button tone="secondary" icon={<RefreshCw size={17} />} onClick={loadRegions} disabled={loadingRegions || submitting}>刷新</Button>}
    />
    <div className="alert alert-info">
      <ShieldCheck size={18} />
      <span>授权固定为“区域公司负责人 + 所选区域组织范围”；不能在客户端改成全国管理员或其他范围。每次任命与撤销均校验授权版本并写入状态和审计证据。</span>
    </div>
    {error && <div className="alert alert-error">{error}</div>}
    {notice && <div className="form-notice role-notice">{notice}</div>}
    {loadingRegions ? <Loading label="正在读取区域公司" /> : regions.length === 0 ? (
      <Empty title="暂无可配置区域公司" detail="请先完成组织只读投影并确认 region_company 状态。" />
    ) : <>
      <section className="content-section role-scope-panel">
        <div className="content-title"><div><h2>配置范围</h2><p>请选择要维护的区域公司，人员候选仅来自该组织子树。</p></div></div>
        <div className="role-scope-form">
          <Field label="区域公司">
            <select value={regionId} onChange={(event) => setRegionId(event.target.value)}>
              {regions.map((row) => <option key={row.organization_id} value={row.organization_id}>{row.organization_name} · {row.organization_code}</option>)}
            </select>
          </Field>
          <div className="role-scope-summary"><span>当前负责人</span><strong>{assignments.length}</strong><small>{selectedRegion?.province_code || "未登记省级代码"}</small></div>
          <div className="role-scope-summary"><span>可选人员</span><strong>{candidates.length}</strong><small>已排除无身份、停用及重复授权</small></div>
        </div>
      </section>

      {loadingScope ? <Loading label="正在读取负责人授权" /> : <>
        <section className="content-section table-section">
          <div className="content-title"><div><h2>当前授权</h2><p>保留当前及未来生效的未终结授权；撤销只关闭授权，不删除历史。</p></div></div>
          {assignments.length === 0 ? <Empty title="当前未配置负责人" detail="这是按本次确认保留的安全初始状态。" /> : <div className="table-wrap"><table><thead><tr><th>人员</th><th>所属组织</th><th>生效时间</th><th>有效期至</th><th>状态</th><th>授权版本</th><th>操作</th></tr></thead><tbody>{assignments.map((row) => <tr key={row.assignment_id}><td><strong>{row.person_name}</strong><span className="cell-subtitle mono">{row.employee_no}</span></td><td>{row.organization_name}<span className="cell-subtitle mono">{row.organization_code}</span></td><td>{formatDate(row.valid_from)}</td><td>{row.valid_to ? formatDate(row.valid_to) : "长期有效"}</td><td><span className="status status-success">{row.status === "scheduled" ? "待生效" : "生效中"}</span></td><td className="mono">v{row.authorization_version}</td><td><Button tone="danger" icon={<UserMinus size={16} />} disabled={submitting} onClick={() => revoke(row)}>撤销</Button></td></tr>)}</tbody></table></div>}
        </section>

        <section className="content-section role-grant-panel">
          <div className="content-title"><div><h2>任命负责人</h2><p>只显示已启用账号、已验证登录身份且具备本人范围工程师角色的在职人员。</p></div></div>
          {candidates.length === 0 ? <Empty title="暂无可授权人员" detail="请检查人员唯一绑定、正式身份、工程师授权和所属组织。" /> : <form className="role-grant-form" onSubmit={grant}>
            <Field label="人员">
              <select value={personId} onChange={(event) => setPersonId(event.target.value)} required>
                {candidates.map((row) => <option key={row.person_id} value={row.person_id}>{row.person_name} · {row.employee_no} · {row.organization_name}</option>)}
              </select>
            </Field>
            <Field label="有效期至（可选）" hint="留空表示长期有效；到期后仍须显式终结状态才能重新授权。">
              <input type="datetime-local" value={validTo} onChange={(event) => setValidTo(event.target.value)} />
            </Field>
            <Field label="任命原因">
              <textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={3} maxLength={2000} placeholder="请填写任命依据或职责范围" required />
            </Field>
            <div className="form-actions"><Button type="submit" icon={<UserPlus size={17} />} disabled={submitting}>{submitting ? "正在写入审计" : "确认任命"}</Button></div>
          </form>}
        </section>
      </>}
    </>}
  </>;
}
