import { validateFormalStocktakePage, type FormalStocktakeSummary } from "./formalStocktakes";

// The API cursor names the next unseen UUID (inclusive), not the last row.
// Never silently deduplicate a changed/overlapping page or claim it is complete.
export function appendFormalStocktakePage(
  previous: readonly FormalStocktakeSummary[],
  value: unknown,
  requestedCursor: string | null,
) {
  const page = validateFormalStocktakePage(value);
  const fail = (): never => { throw new Error("盘点任务分页重复或顺序异常，请刷新后重新读取"); };
  if (page.items.length > 50 || (previous.length > 0 && requestedCursor === null)) fail();
  let last = previous.at(-1)?.task_id ?? null;
  if (requestedCursor && last && requestedCursor <= last) fail();
  for (const item of page.items) {
    if ((last && item.task_id <= last) || (requestedCursor && item.task_id < requestedCursor)) fail();
    last = item.task_id;
  }
  if (page.next_after_id && (!page.items.length || !last || page.next_after_id <= last)) fail();
  return Object.freeze({
    items: Object.freeze([...previous, ...page.items]),
    next_after_id: page.next_after_id,
  });
}
