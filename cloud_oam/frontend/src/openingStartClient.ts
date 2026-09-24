import { apiNoReplay, mutationHeaders } from "./api";
import { createFormalStocktakeAdapter } from "./formalStocktakeAdapter";
import { validateOpeningStocktakeTaskDetail } from "./formalOpeningStocktake";
import { loadOpeningStartOptions, openingPreparationContext } from "./formalOpeningStartOptions";
import { loadControlBatches } from "./formalOpeningControlDirectory";
import { createStartStore, startActor, startActiveIdentity, type StartPorts, type StartStore } from "./openingStartCore";

// Candidate remains closed until current release gates and startup acceptance.
export const OPENING_START_ENABLED = false;
let store: StartStore | undefined;
export function getOpeningStartStore(): StartStore {
  if (!store) {
    try { store = createStartStore(localStorage, navigator.locks); }
    catch { store = createStartStore(null, null); }
  }
  return store;
}
const noStore = { method: "GET", cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } } as const;
export function createOpeningStartPorts(): StartPorts {
  return Object.freeze({
    async identity(expected) {
      const actor = startActor(expected), user = await apiNoReplay<Record<string, unknown>>("/auth/me", noStore);
      startActiveIdentity(user, actor);
      const access = await createFormalStocktakeAdapter(actor, apiNoReplay, apiNoReplay).loadAccess();
      if (!access.can_read || !access.can_manage) throw new Error('当前权限不允许启动或恢复');
    },
    async verifySelection(input, actor) {
      async function contains(stage: 'regions'|'asset-owners'|'locations'|'assignees', coordinates: Record<string,string>, id: string) {
        let cursor: string | null = null;
        do {
          const page = await loadOpeningStartOptions(openingPreparationContext(actor, stage, coordinates), cursor);
          if (page.items.some((item) => (item.stage === 'assignees' ? item.userId : item.id) === id)) return;
          cursor = page.nextAfterId;
        } while (cursor);
        throw new Error('选定范围或人员已变化，请重新选择');
      }
      await contains('regions', {}, input.region_org_id);
      for (const scope of input.scopes) {
        await contains('asset-owners', { region_org_id: input.region_org_id }, scope.owner_org_id);
        await contains('locations', { region_org_id: input.region_org_id, owner_org_id: scope.owner_org_id }, scope.location_id);
        await contains('assignees', { region_org_id: input.region_org_id, owner_org_id: scope.owner_org_id, location_id: scope.location_id }, scope.assignee_user_id);
      }
      let cursor: string | null = null;
      do {
        const page = await loadControlBatches(actor, input.region_org_id, cursor);
        const batch = page.items.find((item) => item.publicationId === input.publication_id);
        if (batch) { if (!batch.isLatest || Date.parse(batch.validUntil) <= Date.now()) throw new Error('请重新选择最新有效批次'); return; }
        cursor = page.nextAfterId;
      } while (cursor);
      throw new Error('控制发布批次已变化');
    },
    coordinates() {
      const headers = new Headers(mutationHeaders('opening-start').headers);
      return { requestId: headers.get('X-Request-ID')!, idempotencyKey: headers.get('Idempotency-Key')! };
    },
    post(input, coordinates) { return apiNoReplay('/v1/stocktakes/opening/from-publication', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': coordinates.idempotencyKey, 'X-Request-ID': coordinates.requestId }, body: JSON.stringify(input) }); },
    seal(marker) { return apiNoReplay('/v1/stocktakes/opening/seal-start-command', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': 'opening-start-seal:' + marker.trace_request_id, 'X-Request-ID': marker.trace_request_id },
      body: JSON.stringify({ region_org_id: marker.region_org_id, publication_id: marker.publication_id, trace_request_id: marker.trace_request_id }) }); },
    lookup(marker) {
      const query = new URLSearchParams({ region_org_id: marker.region_org_id, publication_id: marker.publication_id, trace_request_id: marker.trace_request_id });
      return apiNoReplay(`/v1/stocktakes/opening/start-command-result?${query}`, noStore);
    },
    async detail(task) { return validateOpeningStocktakeTaskDetail(await apiNoReplay(`/v1/stocktakes/opening/${task}`, noStore)); },
  });
}
