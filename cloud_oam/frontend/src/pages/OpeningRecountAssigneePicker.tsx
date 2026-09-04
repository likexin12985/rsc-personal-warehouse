import { useEffect, useRef, useState } from "react";
import {
  loadOpeningRecountAssignees, openingRecountAssigneeContextKey,
  type OpeningRecountAssignee, type OpeningRecountAssigneeContext, type OpeningRecountAssigneeSelection,
} from "../formalOpeningRecountAssignees";
import { Button, Field } from "../ui";

const ROLE_LABELS = { admin: "总部管理员", provincial_manager: "省负责人", technician: "工程师" };

export default function OpeningRecountAssigneePicker({ context, selection, onChange, label, disabled = false }: {
  context: OpeningRecountAssigneeContext;
  selection: OpeningRecountAssigneeSelection | null;
  onChange: (selection: OpeningRecountAssigneeSelection | null) => void;
  label: string;
  disabled?: boolean;
}) {
  const contextKey = openingRecountAssigneeContextKey(context);
  const currentContext = useRef(contextKey);
  currentContext.current = contextKey;
  const changeRef = useRef(onChange);
  changeRef.current = onChange;
  const generation = useRef(0);
  const mounted = useRef(false);
  const [state, setState] = useState<{
    key: string; items: readonly OpeningRecountAssignee[]; next: string | null; loading: boolean; error: boolean;
  }>({ key: contextKey, items: [], next: null, loading: true, error: false });
  const usable = state.key === contextKey;
  const items = usable ? state.items : [];
  const loading = !usable || state.loading;

  async function load(after: string | null, previous: readonly OpeningRecountAssignee[]) {
    const invocation = ++generation.current;
    const key = contextKey;
    changeRef.current(null);
    setState({ key, items: previous, next: null, loading: true, error: false });
    const isCurrent = () => mounted.current && generation.current === invocation && currentContext.current === key;
    try {
      const page = await loadOpeningRecountAssignees(context, after);
      if (!isCurrent()) return;
      const merged = [...previous, ...page.items];
      if (new Set(merged.map((item) => item.user_id)).size !== merged.length
        || new Set(merged.map((item) => item.person_id)).size !== merged.length) {
        throw new Error("duplicate directory identity");
      }
      setState({ key, items: merged, next: page.next_after_person_id, loading: false, error: false });
    } catch {
      if (!isCurrent()) return;
      changeRef.current(null);
      setState({ key, items: [], next: null, loading: false, error: true });
    }
  }

  useEffect(() => {
    mounted.current = true;
    void load(null, []);
    return () => { mounted.current = false; generation.current += 1; };
    // The canonical key contains every actor, task, version, round and scope
    // anchor. Object/callback identity changes cannot restart a valid read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextKey]);

  const selected = selection && openingRecountAssigneeContextKey(selection.context) === contextKey
    && items.some((item) => item === selection.assignee) ? selection.assignee.user_id : "";

  return <div>
    <Field label={label}><select aria-label={label} required value={selected} disabled={disabled || loading || state.error}
      onChange={(event) => {
        if (disabled || loading || state.error) return;
        const assignee = items.find((item) => item.user_id === event.target.value);
        changeRef.current(assignee ? Object.freeze({ context, assignee }) : null);
      }}>
      <option value="">{loading ? "正在核验可选人员" : "请选择当前合格人员"}</option>
      {items.map((item) => <option key={item.person_id} value={item.user_id}>
        {item.display_name} · {item.employee_no} · {ROLE_LABELS[item.role_code]}
      </option>)}
    </select></Field>
    {usable && state.error && <div role="alert">人员目录核验失败，已清除旧选项，请刷新后重选。</div>}
    {!loading && !state.error && items.length === 0 && <div>本页没有合格人员{state.next ? "，可继续加载下一页。" : "，请管理员核对正式授权。"}</div>}
    <Button type="button" tone="quiet" disabled={disabled || loading} onClick={() => void load(null, [])}>刷新人员</Button>
    {usable && state.next && <Button type="button" tone="quiet" disabled={disabled || loading}
      onClick={() => void load(state.next, items)}>加载更多人员</Button>}
  </div>;
}
