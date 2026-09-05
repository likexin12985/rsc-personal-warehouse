import { useLayoutEffect, useRef, useState } from "react";
import {
  loadOpeningStartOptions, mergeOpeningStartOptions, openingPreparationContext, openingPreparationContextKey,
  type OpeningPreparationActor, type OpeningPreparationContext, type OpeningPreparationOption, type OpeningPreparationStage,
} from "../formalOpeningStartOptions";
import { Button, Field } from "../ui";

const STAGES: OpeningPreparationStage[] = ["regions", "asset-owners", "locations", "assignees"];
const LABELS = { regions: "任务区域", "asset-owners": "资产所有组织", locations: "实物库存位置", assignees: "初盘执行人员" };

function Directory({ context, selection, onInvalidate, onSelect, custodianPersonId }: {
  context: OpeningPreparationContext; selection: OpeningPreparationOption | undefined;
  onInvalidate: () => void; onSelect: (option: OpeningPreparationOption | undefined) => void;
  custodianPersonId?: string;
}) {
  const key = openingPreparationContextKey(context);
  const currentKey = useRef(key);
  currentKey.current = key;
  const mounted = useRef(false);
  const generation = useRef(0);
  const running = useRef(false);
  const invalidateRef = useRef(onInvalidate);
  invalidateRef.current = onInvalidate;
  const [state, setState] = useState<{
    key: string; items: readonly OpeningPreparationOption[]; next: string | null; loading: boolean; error: boolean;
  }>({ key, items: [], next: null, loading: true, error: false });
  const current = state.key === key;
  const items = current ? state.items : [];
  const loading = !current || state.loading;

  async function load(after: string | null, previous: readonly OpeningPreparationOption[]) {
    if (running.current) return;
    running.current = true;
    const invocation = ++generation.current;
    invalidateRef.current();
    setState({ key, items: previous, next: null, loading: true, error: false });
    const isCurrent = () => mounted.current && generation.current === invocation && currentKey.current === key;
    try {
      const page = await loadOpeningStartOptions(context, after);
      if (!isCurrent()) return;
      if (custodianPersonId && page.items.some((row) => row.id !== custodianPersonId)) throw new Error("custodian mismatch");
      const merged = mergeOpeningStartOptions(previous, page, context, after);
      setState({ key, items: merged, next: page.nextAfterId, loading: false, error: false });
    } catch {
      if (!isCurrent()) return;
      invalidateRef.current();
      setState({ key, items: [], next: null, loading: false, error: true });
    } finally {
      if (isCurrent()) running.current = false;
    }
  }

  useLayoutEffect(() => {
    mounted.current = true;
    running.current = false;
    void load(null, []);
    return () => { mounted.current = false; generation.current += 1; };
    // Canonical context includes actor/version and every selected coordinate;
    // the parent additionally remounts descendants on upstream revalidation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const label = LABELS[context.stage];
  const selected = selection && items.includes(selection) ? selection.id : "";
  return <div className="opening-detail-section" aria-label={`${label}目录`}>
    <Field label={label}><select aria-label={label} value={selected} disabled={loading || state.error}
      onChange={(event) => {
        if (running.current || loading || state.error) return;
        onSelect(items.find((item) => item.id === event.target.value));
      }}>
      <option value="">{loading ? "正在核验可选范围与人员" : `请选择${label}`}</option>
      {items.map((item) => <option key={item.id} value={item.id}>
        {item.name}{"code" in item ? ` · ${item.code}` : ""}
        {item.stage === "locations" ? ` · ${item.locationType === "personal" ? "个人仓" : "区域仓"}` : ""}
      </option>)}
    </select></Field>
    {current && state.error && <div className="alert alert-error" role="alert">{label}目录核验失败，旧选项和后续选择已清除，请刷新后重选。</div>}
    {!loading && !state.error && items.length === 0 && <p>暂无可选{label}，请管理员核对区域负责人配置、正式授权和保管责任；系统不会自动指派人员。</p>}
    <div className="form-actions">
      <Button type="button" tone="quiet" disabled={loading} onClick={() => void load(null, [])}>刷新{label}</Button>
      {current && state.next && <Button type="button" tone="quiet" disabled={loading}
        onClick={() => void load(state.next, items)}>加载更多{label}</Button>}
    </div>
  </div>;
}

function Directories({ actor }: { actor: OpeningPreparationActor }) {
  const [selected, setSelected] = useState<Partial<Record<OpeningPreparationStage, OpeningPreparationOption>>>({});
  const [epochs, setEpochs] = useState([0, 0, 0, 0]);
  function change(stage: OpeningPreparationStage, value?: OpeningPreparationOption) {
    const index = STAGES.indexOf(stage);
    setSelected((old) => {
      const next = { ...old };
      for (const descendant of STAGES.slice(index)) delete next[descendant];
      if (value) next[stage] = value;
      return next;
    });
    setEpochs((old) => old.map((epoch, position) => position > index ? epoch + 1 : epoch));
  }
  const region = selected.regions;
  const owner = selected["asset-owners"];
  const location = selected.locations;
  const person = selected.assignees;
  const contexts: OpeningPreparationContext[] = [openingPreparationContext(actor, "regions")];
  if (region) contexts.push(openingPreparationContext(actor, "asset-owners", { region_org_id: region.id }));
  if (region && owner) contexts.push(openingPreparationContext(actor, "locations", { region_org_id: region.id, owner_org_id: owner.id }));
  if (region && owner && location) contexts.push(openingPreparationContext(actor, "assignees", {
    region_org_id: region.id, owner_org_id: owner.id, location_id: location.id,
  }));
  return <>
    <p className="alert alert-warning">仅核验范围与人员，尚未评估省级控制库存，不能启动。这里不查询数量、不选择控制同步批次，也不创建任务。</p>
    {contexts.map((context, index) => <Directory key={`${openingPreparationContextKey(context)}:${epochs[index]}`}
      context={context} selection={selected[context.stage]} onInvalidate={() => change(context.stage)}
      onSelect={(value) => change(context.stage, value)} custodianPersonId={context.stage === "assignees"
        && location?.stage === "locations" && location.locationType === "personal" ? location.custodianPersonId || undefined : undefined} />)}
    {owner && location?.stage === "locations" && <dl className="detail-grid">
      <div><dt>资产所有组织</dt><dd>{owner.name}</dd></div>
      <div><dt>库位物理归属</dt><dd>{location.physicalOwnerName}</dd></div>
      <div><dt>保管责任人</dt><dd>{location.custodianName ?? "未指定保管人（区域仓）"}</dd></div>
      {person && <div><dt>本次核验人员</dt><dd>{person.name}</dd></div>}
    </dl>}
  </>;
}

function Panel({ actor }: { actor: OpeningPreparationActor }) {
  const [expanded, setExpanded] = useState(false);
  return <section className="content-section" aria-label="期初盘点准备（只读）">
    <div className="content-title"><div><h2>期初盘点准备（只读）</h2><p>先核验可选范围与人员；不是启动授权或库存证据。</p></div>
      <Button type="button" tone="secondary" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
        {expanded ? "收起准备目录" : "展开准备目录"}
      </Button>
    </div>
    {expanded && <Directories actor={actor} />}
  </section>;
}

export default function OpeningStartPreparationPanel({ actor }: { actor: OpeningPreparationActor }) {
  let context: OpeningPreparationContext;
  try { context = openingPreparationContext(actor, "regions"); }
  catch { return null; }
  return <Panel key={openingPreparationContextKey(context)} actor={{
    person_id: context.actor_person_id, authorization_version: context.authorization_version,
  }} />;
}
