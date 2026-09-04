// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { loadOpeningRecountAssignees } from "../formalOpeningRecountAssignees";
import type {
  OpeningRecountAssignee,
  OpeningRecountAssigneeContext,
  OpeningRecountAssigneePage,
  OpeningRecountAssigneeSelection,
} from "../formalOpeningRecountAssignees";
import OpeningRecountAssigneePicker from "./OpeningRecountAssigneePicker";

vi.mock("../formalOpeningRecountAssignees", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../formalOpeningRecountAssignees")>();
  return { ...original, loadOpeningRecountAssignees: vi.fn() };
});

const id = (prefix: string) => `${prefix}0000000-0000-4000-8000-000000000001`;
const context: OpeningRecountAssigneeContext = {
  task_id: id("1"),
  source_round_id: id("2"),
  scope_id: id("3"),
  location_id: id("4"),
  region_org_id: id("5"),
  task_version: 9,
  actor_person_id: id("6"),
  actor_authorization_version: 3,
};
const engineer: OpeningRecountAssignee = {
  user_id: "formal-user-01",
  person_id: id("7"),
  display_name: "测试工程师",
  employee_no: "TEST-001",
  role_code: "technician",
};
const manager: OpeningRecountAssignee = {
  user_id: "formal-manager-02",
  person_id: id("8"),
  display_name: "区域负责人",
  employee_no: "TEST-002",
  role_code: "provincial_manager",
};

function page(
  items: readonly OpeningRecountAssignee[] = [engineer],
  next: string | null = null,
  pageContext: OpeningRecountAssigneeContext = context,
): OpeningRecountAssigneePage {
  return { ...pageContext, schema_version: "1.0", items, next_after_person_id: next };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function renderPicker(
  overrides: Partial<{
    context: OpeningRecountAssigneeContext;
    selection: OpeningRecountAssigneeSelection | null;
    onChange: (selection: OpeningRecountAssigneeSelection | null) => void;
  }> = {},
) {
  const onChange = overrides.onChange || vi.fn();
  const result = render(<OpeningRecountAssigneePicker
    context={overrides.context || context}
    selection={overrides.selection || null}
    onChange={onChange}
    label="范围 1 复盘人员"
  />);
  return { ...result, onChange };
}

afterEach(cleanup);

describe("opening recount assignee picker", () => {
  beforeEach(() => vi.clearAllMocks());

  it("submits the selected real user id, never a person UUID, and never auto-selects", async () => {
    vi.mocked(loadOpeningRecountAssignees).mockResolvedValue(page());
    const onChange = vi.fn();
    renderPicker({ onChange });
    const select = await screen.findByRole("combobox", {
      name: "范围 1 复盘人员",
    }) as HTMLSelectElement;

    await waitFor(() => expect(select.disabled).toBe(false));
    expect(select.value).toBe("");
    expect(screen.queryByRole("textbox", { name: /复盘人员/ })).toBeNull();
    expect((screen.getByRole("option", {
      name: /测试工程师/,
    }) as HTMLOptionElement).value).toBe(engineer.user_id);
    expect(engineer.user_id).not.toBe(engineer.person_id);
    expect(vi.mocked(onChange).mock.calls.every(([value]) => value === null)).toBe(true);

    fireEvent.change(select, { target: { value: engineer.user_id } });

    expect(onChange).toHaveBeenLastCalledWith({ context, assignee: engineer });
  });

  it("loads the next authorized candidate page only after an explicit click", async () => {
    vi.mocked(loadOpeningRecountAssignees)
      .mockResolvedValueOnce(page([engineer], engineer.person_id))
      .mockResolvedValueOnce(page([manager], null));
    renderPicker();

    expect(await screen.findByRole("option", { name: /测试工程师/ })).toBeTruthy();
    expect(loadOpeningRecountAssignees).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "加载更多人员" }));

    expect((await screen.findByRole("option", {
      name: /区域负责人/,
    }) as HTMLOptionElement).value).toBe(manager.user_id);
    expect(loadOpeningRecountAssignees).toHaveBeenLastCalledWith(context, engineer.person_id);
  });

  it("fails closed and clears all options on a duplicate user or person across pages", async () => {
    const sameUser = { ...manager, user_id: engineer.user_id };
    vi.mocked(loadOpeningRecountAssignees)
      .mockResolvedValueOnce(page([engineer], id("7")))
      .mockResolvedValueOnce(page([sameUser], null));
    const onChange = vi.fn();
    renderPicker({ onChange });
    const select = await screen.findByRole("combobox", {
      name: "范围 1 复盘人员",
    }) as HTMLSelectElement;
    await waitFor(() => expect(select.disabled).toBe(false));
    fireEvent.change(select, { target: { value: engineer.user_id } });
    fireEvent.click(screen.getByRole("button", { name: "加载更多人员" }));

    expect((await screen.findByRole("alert")).textContent).toContain("已清除旧选项");
    expect(screen.queryByRole("option", { name: /测试工程师/ })).toBeNull();
    expect(onChange).toHaveBeenLastCalledWith(null);

    vi.mocked(loadOpeningRecountAssignees).mockReset();
    vi.mocked(loadOpeningRecountAssignees)
      .mockResolvedValueOnce(page([engineer], id("7")))
      .mockResolvedValueOnce(page([{ ...manager, person_id: engineer.person_id }], null));
    cleanup();
    renderPicker({ onChange });
    await screen.findByRole("option", { name: /测试工程师/ });
    fireEvent.click(screen.getByRole("button", { name: "加载更多人员" }));
    expect((await screen.findByRole("alert")).textContent).toContain("已清除旧选项");
  });

  it("clears an existing selection and PII whenever refresh fails", async () => {
    vi.mocked(loadOpeningRecountAssignees)
      .mockResolvedValueOnce(page())
      .mockRejectedValueOnce(new Error("directory unavailable"));
    const onChange = vi.fn();
    renderPicker({ onChange });
    const select = await screen.findByRole("combobox", {
      name: "范围 1 复盘人员",
    }) as HTMLSelectElement;
    await waitFor(() => expect(select.disabled).toBe(false));
    fireEvent.change(select, { target: { value: engineer.user_id } });
    fireEvent.click(screen.getByRole("button", { name: "刷新人员" }));

    expect(onChange).toHaveBeenLastCalledWith(null);
    expect((await screen.findByRole("alert")).textContent).toContain("目录核验失败");
    expect(screen.queryByText("测试工程师")).toBeNull();
  });

  it.each([
    ["actor", { actor_person_id: id("a") }],
    ["actor authorization", { actor_authorization_version: 4 }],
    ["task version", { task_version: 10 }],
    ["round and scope context", {
      source_round_id: id("9"),
      scope_id: id("a"),
      location_id: id("b"),
    }],
  ])("discards old async results after %s changes", async (_label, changed) => {
    const oldRead = deferred<OpeningRecountAssigneePage>();
    const changedContext = {
      ...context,
      ...changed,
    };
    vi.mocked(loadOpeningRecountAssignees)
      .mockReturnValueOnce(oldRead.promise)
      .mockResolvedValueOnce(page([manager], null, changedContext));
    const onChange = vi.fn();
    const rendered = renderPicker({ onChange });

    rendered.rerender(<OpeningRecountAssigneePicker
      context={changedContext}
      selection={null}
      onChange={onChange}
      label="范围 1 复盘人员"
    />);
    expect(await screen.findByRole("option", { name: /区域负责人/ })).toBeTruthy();
    oldRead.resolve(page([engineer]));
    await Promise.resolve();

    expect(screen.queryByText(/测试工程师/)).toBeNull();
    expect(screen.getByRole("option", { name: /区域负责人/ })).toBeTruthy();
    expect(onChange.mock.calls.every(([value]) => value === null)).toBe(true);
  });

  it("hides previously loaded personnel immediately while a new context is pending", async () => {
    const nextRead = deferred<OpeningRecountAssigneePage>();
    const changedContext = { ...context, task_version: 10 };
    vi.mocked(loadOpeningRecountAssignees)
      .mockResolvedValueOnce(page([engineer]))
      .mockReturnValueOnce(nextRead.promise);
    const onChange = vi.fn();
    const rendered = renderPicker({ onChange });
    const select = await screen.findByRole("combobox", {
      name: "范围 1 复盘人员",
    }) as HTMLSelectElement;
    await waitFor(() => expect(select.disabled).toBe(false));
    fireEvent.change(select, { target: { value: engineer.user_id } });

    rendered.rerender(<OpeningRecountAssigneePicker
      context={changedContext}
      selection={{ context, assignee: engineer }}
      onChange={onChange}
      label="范围 1 复盘人员"
    />);

    expect(screen.queryByText(/测试工程师/)).toBeNull();
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(null));
    rendered.unmount();
    nextRead.resolve(page([manager], null, changedContext));
    await Promise.resolve();
    expect(screen.queryByText(/区域负责人/)).toBeNull();
  });

  it("does not restore options or selection after the picker unmounts", async () => {
    const pending = deferred<OpeningRecountAssigneePage>();
    vi.mocked(loadOpeningRecountAssignees).mockReturnValue(pending.promise);
    const onChange = vi.fn();
    const rendered = renderPicker({ onChange });
    await waitFor(() => expect(loadOpeningRecountAssignees).toHaveBeenCalledTimes(1));
    rendered.unmount();
    const callsAtUnmount = onChange.mock.calls.length;
    pending.resolve(page());
    await Promise.resolve();

    expect(onChange).toHaveBeenCalledTimes(callsAtUnmount);
    expect(screen.queryByText(/测试工程师/)).toBeNull();
  });
});
