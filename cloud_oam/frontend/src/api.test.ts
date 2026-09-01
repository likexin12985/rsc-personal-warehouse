import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  api,
  createAuthenticationRefreshCoordinator,
  createIdempotencyKey,
  mutationHeaders,
  subscribeAuthenticationEstablished,
  subscribeAuthenticationTerminalLogout,
} from "./api";

class TestLockManager {
  private readonly tails = new Map<string, Promise<void>>();

  request<T>(name: string, callback: () => Promise<T> | T): Promise<T> {
    const previous = this.tails.get(name) ?? Promise.resolve();
    const result = previous.catch(() => undefined).then(callback);
    this.tails.set(name, result.then(() => undefined, () => undefined));
    return result;
  }
}

class TestRefreshStorage {
  readonly values = new Map<string, string>();
  readable = true;
  writable = true;

  getItem(key: string): string | null {
    if (!this.readable) throw new Error("storage unavailable");
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    if (!this.writable) throw new Error("storage unavailable");
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    if (!this.writable) throw new Error("storage unavailable");
    this.values.delete(key);
  }
}

class TestBroadcastBus {
  private readonly channels = new Map<string, Set<TestBroadcastChannel>>();

  constructor(
    private readonly deliveryDelayMs = 0,
    private readonly dropMessages = false,
  ) {}

  create = (name: string): TestBroadcastChannel => {
    const channel = new TestBroadcastChannel(name, this);
    const group = this.channels.get(name) ?? new Set<TestBroadcastChannel>();
    group.add(channel);
    this.channels.set(name, group);
    return channel;
  };

  post(sender: TestBroadcastChannel, message: unknown): void {
    if (this.dropMessages) return;
    for (const channel of this.channels.get(sender.name) ?? []) {
      if (channel === sender) continue;
      if (this.deliveryDelayMs > 0) {
        setTimeout(() => channel.deliver(message), this.deliveryDelayMs);
      } else {
        queueMicrotask(() => channel.deliver(message));
      }
    }
  }

  remove(channel: TestBroadcastChannel): void {
    this.channels.get(channel.name)?.delete(channel);
  }
}

class TestBroadcastChannel {
  private readonly listeners = new Set<(event: MessageEvent<unknown>) => void>();

  constructor(readonly name: string, private readonly bus: TestBroadcastBus) {}

  postMessage(message: unknown): void {
    this.bus.post(this, message);
  }

  addEventListener(_type: "message", listener: (event: MessageEvent<unknown>) => void): void {
    this.listeners.add(listener);
  }

  removeEventListener(_type: "message", listener: (event: MessageEvent<unknown>) => void): void {
    this.listeners.delete(listener);
  }

  close(): void {
    this.bus.remove(this);
    this.listeners.clear();
  }

  deliver(data: unknown): void {
    for (const listener of this.listeners) listener({ data } as MessageEvent<unknown>);
  }
}

function installBrowserRefreshCoordination(): void {
  const locks = new TestLockManager();
  const bus = new TestBroadcastBus();
  const storage = new TestRefreshStorage();
  vi.stubGlobal("navigator", { locks });
  vi.stubGlobal("localStorage", storage);
  vi.stubGlobal("BroadcastChannel", class {
    private readonly channel: TestBroadcastChannel;

    constructor(name: string) {
      this.channel = bus.create(name);
    }

    postMessage(message: unknown): void { this.channel.postMessage(message); }
    addEventListener(type: "message", listener: (event: MessageEvent<unknown>) => void): void {
      this.channel.addEventListener(type, listener);
    }
    removeEventListener(type: "message", listener: (event: MessageEvent<unknown>) => void): void {
      this.channel.removeEventListener(type, listener);
    }
    close(): void { this.channel.close(); }
  });
}

describe("API transport quarantine", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("rejects a legacy write before fetch is called", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(api("/transfers/legacy/dispatch", { method: "POST" })).rejects.toMatchObject({
      status: 403,
    } satisfies Partial<ApiError>);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects legacy history reads before fetch is called", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(api("/transfers?limit=20")).rejects.toMatchObject({ status: 403 });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not add request coordinates to GET or HEAD requests", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await api("/v1/inventory/summary");
    await api("/v1/inventory/summary", { method: "head" });

    const getHeaders = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers);
    const headHeaders = new Headers((fetchMock.mock.calls[1][1] as RequestInit).headers);
    expect(getHeaders.has("X-Request-ID")).toBe(false);
    expect(headHeaders.has("X-Request-ID")).toBe(false);
    expect(getHeaders.has("Idempotency-Key")).toBe(false);
    expect(headHeaders.has("Idempotency-Key")).toBe(false);
  });

  it("adds a unique request id, but no idempotency key, to each mutation", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await api("/access/provincial-managers/assignments", { method: "post" });
    await api("/access/provincial-managers/assignments", { method: "POST" });

    const first = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers);
    const second = new Headers((fetchMock.mock.calls[1][1] as RequestInit).headers);
    expect(first.get("X-Request-ID")).toMatch(/^[A-Za-z0-9._:-]{8,160}$/);
    expect(first.get("X-Request-ID")).not.toBe(second.get("X-Request-ID"));
    expect(first.has("Idempotency-Key")).toBe(false);
    expect(second.has("Idempotency-Key")).toBe(false);
  });

  it("preserves a caller-provided request id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await api("/access/provincial-managers/assignments", {
      method: "POST",
      headers: { "x-request-id": "caller-request-123" },
    });

    const headers = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers);
    expect(headers.get("X-Request-ID")).toBe("caller-request-123");
    expect(headers.has("Idempotency-Key")).toBe(false);
  });

  it("reuses the original request coordinates after refresh and coordinates the refresh POST", async () => {
    installBrowserRefreshCoordination();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api<{ ok: boolean }>("/access/provincial-managers/assignments", {
      method: "POST",
    })).resolves.toEqual({ ok: true });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/access/provincial-managers/assignments");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/auth/refresh");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/access/provincial-managers/assignments");

    const firstHeaders = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers);
    const refreshHeaders = new Headers((fetchMock.mock.calls[1][1] as RequestInit).headers);
    const retryHeaders = new Headers((fetchMock.mock.calls[2][1] as RequestInit).headers);
    expect(retryHeaders.get("X-Request-ID")).toBe(firstHeaders.get("X-Request-ID"));
    expect(refreshHeaders.get("X-Request-ID")).toMatch(/^[A-Za-z0-9._:-]{8,160}$/);
    expect(refreshHeaders.get("X-Request-ID")).not.toBe(firstHeaders.get("X-Request-ID"));
    expect(refreshHeaders.get("Idempotency-Key")).toMatch(/^[A-Za-z0-9._:-]{16,128}$/);
    expect(firstHeaders.has("Idempotency-Key")).toBe(false);
    expect(retryHeaders.has("Idempotency-Key")).toBe(false);
  });

  it("adds safe independent idempotency keys to every formal authentication write", async () => {
    installBrowserRefreshCoordination();
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const sensitiveValues = [
      "13800000000",
      "246810",
      "refresh-secret-value",
      "device-secret-value",
      "session-secret-value",
    ];

    await api("/auth/sms/request", {
      method: "POST",
      body: JSON.stringify({ mobile: sensitiveValues[0] }),
    });
    await api("/auth/sms/login", {
      method: "POST",
      body: JSON.stringify({ mobile: sensitiveValues[0], code: sensitiveValues[1] }),
    });
    await api("/auth/refresh", { method: "POST" });
    await api("/auth/logout", { method: "POST" });
    await api(`/auth/sessions/${sensitiveValues[4]}/revoke`, { method: "POST" });

    const headers = fetchMock.mock.calls.map((call) => new Headers((call[1] as RequestInit).headers));
    const idempotencyKeys = headers.map((value) => value.get("Idempotency-Key") || "");
    const requestIds = headers.map((value) => value.get("X-Request-ID") || "");
    expect(new Set(idempotencyKeys).size).toBe(idempotencyKeys.length);
    expect(new Set(requestIds).size).toBe(requestIds.length);
    for (const key of idempotencyKeys) {
      expect(key).toMatch(/^[A-Za-z0-9._:-]{16,128}$/);
      for (const sensitive of sensitiveValues) expect(key).not.toContain(sensitive);
    }
  });

  it("preserves a safe caller key for an authentication request and rejects an unsafe one", async () => {
    installBrowserRefreshCoordination();
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const safeKey = createIdempotencyKey("auth-logout");

    await api("/auth/logout", {
      method: "POST",
      headers: { "Idempotency-Key": safeKey },
    });
    await expect(api("/auth/logout", {
      method: "POST",
      headers: { "Idempotency-Key": "13800000000" },
    })).rejects.toMatchObject({ status: 400 } satisfies Partial<ApiError>);

    expect(fetchMock).toHaveBeenCalledOnce();
    const headers = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers);
    expect(headers.get("Idempotency-Key")).toBe(safeKey);
  });

  it("exposes credential-clear proof only from the exact server response header", async () => {
    installBrowserRefreshCoordination();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "session absent" }), {
        status: 401,
        headers: {
          "content-type": "application/json",
          "X-Auth-Credentials-Cleared": "true",
        },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "proxy unavailable" }), {
        status: 503,
        headers: { "content-type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api("/auth/logout", { method: "POST" })).rejects.toMatchObject({
      status: 401,
      responseReceived: true,
      credentialsCleared: true,
    } satisfies Partial<ApiError>);
    await expect(api("/auth/logout", { method: "POST" })).rejects.toMatchObject({
      status: 503,
      responseReceived: true,
      credentialsCleared: false,
    } satisfies Partial<ApiError>);
  });

  it("coalesces concurrent automatic refresh into one request with one fixed key", async () => {
    installBrowserRefreshCoordination();
    let releaseRefresh: ((response: Response) => void) | undefined;
    const attempts = new Map<string, number>();
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url === "/api/auth/refresh") {
        return new Promise<Response>((resolve) => { releaseRefresh = resolve; });
      }
      const attempt = (attempts.get(url) || 0) + 1;
      attempts.set(url, attempt);
      if (attempt === 1) return new Response(null, { status: 401 });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const first = api<{ ok: boolean }>("/v1/inventory/summary");
    const second = api<{ ok: boolean }>("/v1/inventory/personal/me");
    await vi.waitFor(() => {
      expect(fetchMock.mock.calls.filter((call) => call[0] === "/api/auth/refresh")).toHaveLength(1);
    });
    releaseRefresh?.(new Response(null, { status: 204 }));

    await expect(Promise.all([first, second])).resolves.toEqual([{ ok: true }, { ok: true }]);
    const refreshCalls = fetchMock.mock.calls.filter((call) => call[0] === "/api/auth/refresh");
    const refreshHeaders = new Headers((refreshCalls[0][1] as RequestInit).headers);
    expect(refreshHeaders.get("Idempotency-Key")).toMatch(/^auth-refresh-[a-f0-9]{36}$/);
    expect(refreshHeaders.get("X-Request-ID")).toMatch(/^[A-Za-z0-9._:-]{8,160}$/);
  });

  it("reuses an authentication write key only for that request's credential retry", async () => {
    installBrowserRefreshCoordination();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await api("/auth/sessions/session-id/revoke", { method: "POST" });

    const first = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers);
    const refresh = new Headers((fetchMock.mock.calls[1][1] as RequestInit).headers);
    const retry = new Headers((fetchMock.mock.calls[2][1] as RequestInit).headers);
    expect(retry.get("Idempotency-Key")).toBe(first.get("Idempotency-Key"));
    expect(retry.get("X-Request-ID")).toBe(first.get("X-Request-ID"));
    expect(refresh.get("Idempotency-Key")).not.toBe(first.get("Idempotency-Key"));
  });

  it("coordinates two tabs so only the lock holder refreshes and both original requests recover", async () => {
    const locks = new TestLockManager();
    const bus = new TestBroadcastBus();
    const storage = new TestRefreshStorage();
    let releaseRefresh: ((response: Response) => void) | undefined;
    const refreshTransport = vi.fn().mockImplementation(() => new Promise<Response>((resolve) => {
      releaseRefresh = resolve;
    }));
    const makeCoordinator = () => createAuthenticationRefreshCoordinator({
      locks,
      channelFactory: bus.create,
      storage,
      fetcher: refreshTransport,
    });
    const firstTab = makeCoordinator();
    const secondTab = makeCoordinator();
    const protectedAttempts = [0, 0];
    const firstVersion = firstTab.captureVersion();
    const secondVersion = secondTab.captureVersion();
    const requestFromTab = async (
      tab: number,
      refresh: (versionBeforeRequest: string | null) => Promise<boolean>,
      versionBeforeRequest: string | null,
    ) => {
      protectedAttempts[tab] += 1;
      if (!await refresh(versionBeforeRequest)) return false;
      protectedAttempts[tab] += 1;
      return true;
    };

    const first = requestFromTab(0, firstTab.refresh, firstVersion);
    const second = requestFromTab(1, secondTab.refresh, secondVersion);
    await vi.waitFor(() => expect(refreshTransport).toHaveBeenCalledOnce());
    releaseRefresh?.(new Response(null, { status: 204 }));

    await expect(Promise.all([first, second])).resolves.toEqual([true, true]);
    expect(protectedAttempts).toEqual([2, 2]);
    expect(refreshTransport).toHaveBeenCalledOnce();
    const headers = new Headers((refreshTransport.mock.calls[0][1] as RequestInit).headers);
    expect(headers.get("Idempotency-Key")).toMatch(/^auth-refresh-[a-f0-9]{36}$/);
    firstTab.close();
    secondTab.close();
  });

  it.each([
    ["60ms-delayed", new TestBroadcastBus(60, false)],
    ["dropped", new TestBroadcastBus(0, true)],
  ])("uses the storage sentinel, not %s BroadcastChannel delivery, to prevent a second key", async (_label, bus) => {
    const locks = new TestLockManager();
    const storage = new TestRefreshStorage();
    const refreshTransport = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const makeCoordinator = () => createAuthenticationRefreshCoordinator({
      locks,
      channelFactory: bus.create,
      storage,
      fetcher: refreshTransport,
    });
    const firstTab = makeCoordinator();
    const secondTab = makeCoordinator();
    const firstVersion = firstTab.captureVersion();
    const secondVersion = secondTab.captureVersion();

    await expect(Promise.all([
      firstTab.refresh(firstVersion),
      secondTab.refresh(secondVersion),
    ])).resolves.toEqual([true, true]);
    expect(refreshTransport).toHaveBeenCalledOnce();
    const refreshHeaders = new Headers((refreshTransport.mock.calls[0][1] as RequestInit).headers);
    expect(refreshHeaders.get("Idempotency-Key")).toMatch(/^auth-refresh-[a-f0-9]{36}$/);
    firstTab.close();
    secondTab.close();
  });

  it("broadcasts a failed refresh so a waiting tab does not blindly retry it", async () => {
    const locks = new TestLockManager();
    const bus = new TestBroadcastBus();
    const storage = new TestRefreshStorage();
    const refreshTransport = vi.fn().mockResolvedValue(new Response(null, { status: 503 }));
    const makeCoordinator = () => createAuthenticationRefreshCoordinator({
      locks,
      channelFactory: bus.create,
      storage,
      fetcher: refreshTransport,
    });
    const firstTab = makeCoordinator();
    const secondTab = makeCoordinator();
    const firstVersion = firstTab.captureVersion();
    const secondVersion = secondTab.captureVersion();

    await expect(Promise.all([
      firstTab.refresh(firstVersion),
      secondTab.refresh(secondVersion),
    ])).resolves.toEqual([false, false]);
    expect(refreshTransport).toHaveBeenCalledOnce();

    const laterFirstVersion = firstTab.captureVersion();
    const laterSecondVersion = secondTab.captureVersion();
    await expect(Promise.all([
      firstTab.refresh(laterFirstVersion),
      secondTab.refresh(laterSecondVersion),
    ])).resolves.toEqual([false, false]);
    expect(refreshTransport).toHaveBeenCalledOnce();
    firstTab.close();
    secondTab.close();
  });

  it("keeps a failed refresh sticky for later requests until explicit reauthentication clears it", async () => {
    const locks = new TestLockManager();
    const bus = new TestBroadcastBus();
    const storage = new TestRefreshStorage();
    const refreshTransport = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const makeCoordinator = () => createAuthenticationRefreshCoordinator({
      locks,
      channelFactory: bus.create,
      storage,
      fetcher: refreshTransport,
    });
    const currentPage = makeCoordinator();

    await expect(currentPage.refresh(currentPage.captureVersion())).resolves.toBe(false);
    await expect(currentPage.refresh(currentPage.captureVersion())).resolves.toBe(false);
    expect(refreshTransport).toHaveBeenCalledOnce();

    await expect(currentPage.authenticate(async () => new Response(null, { status: 204 })))
      .resolves.toMatchObject({ status: 204 });
    await expect(currentPage.refresh(currentPage.captureVersion())).resolves.toBe(true);
    expect(refreshTransport).toHaveBeenCalledTimes(2);
    currentPage.close();
  });

  it("allows a new refresh after a prior successful outcome instead of locking the success chain", async () => {
    const locks = new TestLockManager();
    const bus = new TestBroadcastBus();
    const storage = new TestRefreshStorage();
    const refreshTransport = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const coordinator = createAuthenticationRefreshCoordinator({
      locks,
      channelFactory: bus.create,
      storage,
      fetcher: refreshTransport,
    });

    await expect(coordinator.refresh(coordinator.captureVersion())).resolves.toBe(true);
    const firstKey = new Headers((refreshTransport.mock.calls[0][1] as RequestInit).headers)
      .get("Idempotency-Key");
    await expect(coordinator.refresh(coordinator.captureVersion())).resolves.toBe(true);
    const secondKey = new Headers((refreshTransport.mock.calls[1][1] as RequestInit).headers)
      .get("Idempotency-Key");

    expect(refreshTransport).toHaveBeenCalledTimes(2);
    expect(firstKey).toMatch(/^auth-refresh-[a-f0-9]{36}$/);
    expect(secondKey).toMatch(/^auth-refresh-[a-f0-9]{36}$/);
    expect(secondKey).not.toBe(firstKey);
    coordinator.close();
  });

  it("keeps an uncertain pending sentinel across coordinator recreation without TTL release", async () => {
    const locks = new TestLockManager();
    const bus = new TestBroadcastBus(0, true);
    const storage = new TestRefreshStorage();
    const refreshTransport = vi.fn().mockRejectedValue(new TypeError("response lost"));
    const makeCoordinator = () => createAuthenticationRefreshCoordinator({
      locks,
      channelFactory: bus.create,
      storage,
      fetcher: refreshTransport,
    });
    const beforeReload = makeCoordinator();

    await expect(beforeReload.refresh(beforeReload.captureVersion())).resolves.toBe(false);
    beforeReload.close();
    const afterReload = makeCoordinator();
    await expect(afterReload.refresh(afterReload.captureVersion())).resolves.toBe(false);

    expect(refreshTransport).toHaveBeenCalledOnce();
    expect([...storage.values.values()][0]).toContain('"state":"pending"');
    afterReload.close();
  });

  it("fails closed before fetch when localStorage cannot be read", async () => {
    const storage = new TestRefreshStorage();
    storage.readable = false;
    const refreshTransport = vi.fn();
    const coordinator = createAuthenticationRefreshCoordinator({
      locks: new TestLockManager(),
      channelFactory: new TestBroadcastBus().create,
      storage,
      fetcher: refreshTransport,
    });

    await expect(coordinator.refresh(coordinator.captureVersion())).rejects.toMatchObject({
      status: 503,
      message: "当前浏览器无法安全读取会话刷新状态，已失败关闭",
    } satisfies Partial<ApiError>);
    expect(refreshTransport).not.toHaveBeenCalled();
    coordinator.close();
  });

  it("serializes logout behind an in-flight refresh and leaves a terminal cross-tab barrier", async () => {
    const locks = new TestLockManager();
    const bus = new TestBroadcastBus(60, false);
    const storage = new TestRefreshStorage();
    const events: string[] = [];
    let releaseRefresh: ((response: Response) => void) | undefined;
    const refreshTransport = vi.fn().mockImplementation(() => {
      events.push("refresh-start");
      return new Promise<Response>((resolve) => { releaseRefresh = resolve; });
    });
    const logoutTransport = vi.fn().mockImplementation(async () => {
      events.push("logout-start");
      return new Response(null, { status: 204 });
    });
    const makeCoordinator = () => createAuthenticationRefreshCoordinator({
      locks,
      channelFactory: bus.create,
      storage,
      fetcher: refreshTransport,
    });
    const refreshTab = makeCoordinator();
    const logoutTab = makeCoordinator();
    const refresh = refreshTab.refresh(refreshTab.captureVersion());
    await vi.waitFor(() => expect(refreshTransport).toHaveBeenCalledOnce());
    const logout = logoutTab.logout(logoutTransport);
    expect(logoutTransport).not.toHaveBeenCalled();

    events.push("refresh-response");
    releaseRefresh?.(new Response(null, { status: 204 }));
    await expect(refresh).resolves.toBe(true);
    await expect(logout).resolves.toMatchObject({ status: 204 });

    expect(events).toEqual(["refresh-start", "refresh-response", "logout-start"]);
    expect(logoutTransport).toHaveBeenCalledOnce();
    expect(logoutTab.captureVersion()).toBe("blocked-refresh-state");
    await expect(refreshTab.refresh(refreshTab.captureVersion())).resolves.toBe(false);
    expect(refreshTransport).toHaveBeenCalledOnce();
    refreshTab.close();
    logoutTab.close();
  });

  it("serializes explicit SMS login behind an in-flight refresh and makes login the final baseline", async () => {
    const locks = new TestLockManager();
    const bus = new TestBroadcastBus(60, false);
    const storage = new TestRefreshStorage();
    const events: string[] = [];
    let releaseRefresh: ((response: Response) => void) | undefined;
    const refreshTransport = vi.fn().mockImplementation(() => {
      events.push("refresh-start");
      return new Promise<Response>((resolve) => { releaseRefresh = resolve; });
    });
    const loginTransport = vi.fn().mockImplementation(async () => {
      events.push("login-start");
      return new Response(JSON.stringify({ name: "user" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    const makeCoordinator = () => createAuthenticationRefreshCoordinator({
      locks,
      channelFactory: bus.create,
      storage,
      fetcher: refreshTransport,
    });
    const refreshTab = makeCoordinator();
    const loginTab = makeCoordinator();
    const refresh = refreshTab.refresh(refreshTab.captureVersion());
    await vi.waitFor(() => expect(refreshTransport).toHaveBeenCalledOnce());
    const login = loginTab.authenticate(loginTransport);
    expect(loginTransport).not.toHaveBeenCalled();

    events.push("refresh-response");
    releaseRefresh?.(new Response(null, { status: 204 }));
    await expect(refresh).resolves.toBe(true);
    await expect(login).resolves.toMatchObject({ status: 200 });

    expect(events).toEqual(["refresh-start", "refresh-response", "login-start"]);
    expect(loginTab.captureVersion()).toMatch(/^authcoord-[a-f0-9]{36}$/);
    refreshTab.close();
    loginTab.close();
  });

  it("serializes explicit SMS login behind logout so late logout cookies cannot clear the new login", async () => {
    const locks = new TestLockManager();
    const bus = new TestBroadcastBus();
    const storage = new TestRefreshStorage();
    const events: string[] = [];
    let releaseLogout: ((response: Response) => void) | undefined;
    const logoutTransport = vi.fn().mockImplementation(() => {
      events.push("logout-start");
      return new Promise<Response>((resolve) => { releaseLogout = resolve; });
    });
    const loginTransport = vi.fn().mockImplementation(async () => {
      events.push("login-start");
      return new Response(JSON.stringify({ name: "user" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    const makeCoordinator = () => createAuthenticationRefreshCoordinator({
      locks,
      channelFactory: bus.create,
      storage,
      fetcher: vi.fn(),
    });
    const logoutTab = makeCoordinator();
    const loginTab = makeCoordinator();
    const logout = logoutTab.logout(logoutTransport);
    await vi.waitFor(() => expect(logoutTransport).toHaveBeenCalledOnce());
    const login = loginTab.authenticate(loginTransport);
    expect(loginTransport).not.toHaveBeenCalled();

    events.push("logout-response");
    releaseLogout?.(new Response(null, { status: 204 }));
    await expect(logout).resolves.toMatchObject({ status: 204 });
    await expect(login).resolves.toMatchObject({ status: 200 });

    expect(events).toEqual(["logout-start", "logout-response", "login-start"]);
    expect(loginTab.captureVersion()).toMatch(/^authcoord-[a-f0-9]{36}$/);
    logoutTab.close();
    loginTab.close();
  });

  it("restores the prior barrier after a definite rejected SMS login", async () => {
    const locks = new TestLockManager();
    const bus = new TestBroadcastBus();
    const storage = new TestRefreshStorage();
    const coordinator = createAuthenticationRefreshCoordinator({
      locks,
      channelFactory: bus.create,
      storage,
      fetcher: vi.fn().mockResolvedValue(new Response(null, { status: 503 })),
    });
    await coordinator.refresh(coordinator.captureVersion());
    const blockedBeforeLogin = coordinator.captureVersion();

    await expect(coordinator.authenticate(async () => new Response(null, { status: 401 })))
      .resolves.toMatchObject({ status: 401 });
    expect(coordinator.captureVersion()).toBe(blockedBeforeLogin);
    coordinator.close();
  });

  it("uses the shared logout sentinel when terminal BroadcastChannel delivery is lost", async () => {
    const locks = new TestLockManager();
    const bus = new TestBroadcastBus(0, true);
    const storage = new TestRefreshStorage();
    const refreshTransport = vi.fn();
    const makeCoordinator = () => createAuthenticationRefreshCoordinator({
      locks,
      channelFactory: bus.create,
      storage,
      fetcher: refreshTransport,
    });
    const logoutTab = makeCoordinator();
    const otherTab = makeCoordinator();

    await expect(logoutTab.logout(async () => new Response(null, { status: 204 })))
      .resolves.toMatchObject({ status: 204 });
    await expect(otherTab.refresh(otherTab.captureVersion())).resolves.toBe(false);
    expect(refreshTransport).not.toHaveBeenCalled();
    logoutTab.close();
    otherTab.close();
  });

  it("notifies cross-tab subscribers when the terminal logout broadcast arrives", async () => {
    const locks = new TestLockManager();
    const bus = new TestBroadcastBus();
    const storage = new TestRefreshStorage();
    const makeCoordinator = () => createAuthenticationRefreshCoordinator({
      locks,
      channelFactory: bus.create,
      storage,
      fetcher: vi.fn(),
    });
    const logoutTab = makeCoordinator();
    const listeningTab = makeCoordinator();
    const listener = vi.fn();
    const unsubscribe = subscribeAuthenticationTerminalLogout(listener);

    await logoutTab.logout(async () => new Response(null, { status: 204 }));
    await vi.waitFor(() => expect(listener).toHaveBeenCalledOnce());

    unsubscribe();
    logoutTab.close();
    listeningTab.close();
  });

  it("notifies only another tab when an authenticated terminal broadcast arrives", async () => {
    const locks = new TestLockManager();
    const bus = new TestBroadcastBus();
    const storage = new TestRefreshStorage();
    const makeCoordinator = () => createAuthenticationRefreshCoordinator({
      locks,
      channelFactory: bus.create,
      storage,
      fetcher: vi.fn(),
    });
    const loginTab = makeCoordinator();
    const listener = vi.fn();
    const unsubscribe = subscribeAuthenticationEstablished(listener);

    await loginTab.authenticate(async () => new Response(JSON.stringify({ name: "B" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    await Promise.resolve();
    expect(listener).not.toHaveBeenCalled();

    const otherTab = makeCoordinator();
    await loginTab.authenticate(async () => new Response(JSON.stringify({ name: "C" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    await vi.waitFor(() => expect(listener).toHaveBeenCalledOnce());

    unsubscribe();
    loginTab.close();
    otherTab.close();
  });

  it("fails closed when either Web Locks or BroadcastChannel is unavailable", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("navigator", {});
    const withoutLocks = createAuthenticationRefreshCoordinator({ fetcher: fetchMock });

    await expect(withoutLocks.refresh(withoutLocks.captureVersion())).rejects.toMatchObject({
      status: 503,
      message: "当前浏览器无法安全协调多标签会话刷新，已失败关闭",
    } satisfies Partial<ApiError>);

    vi.stubGlobal("navigator", { locks: new TestLockManager() });
    vi.stubGlobal("BroadcastChannel", undefined);
    const withoutChannel = createAuthenticationRefreshCoordinator({ fetcher: fetchMock });
    await expect(withoutChannel.refresh(withoutChannel.captureVersion())).rejects.toMatchObject({
      status: 503,
      message: "当前浏览器无法安全协调多标签会话刷新，已失败关闭",
    } satisfies Partial<ApiError>);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("extracts the safe domain message from structured API errors", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: "authorization_version_mismatch",
        category: "precondition_failed",
        message: "目标账号授权版本已变化，请重新读取后再操作",
      },
    }), {
      status: 412,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api("/access/provincial-managers/assignments", {
      method: "POST",
    })).rejects.toMatchObject({
      status: 412,
      message: "目标账号授权版本已变化，请重新读取后再操作",
    } satisfies Partial<ApiError>);
  });

  it("creates safe request and idempotency coordinates for formal writes", () => {
    const first = new Headers(mutationHeaders("provincial-manager-grant").headers);
    const second = new Headers(mutationHeaders("provincial-manager-grant").headers);

    expect(first.get("Idempotency-Key")).toMatch(/^[A-Za-z0-9._:-]{16,128}$/);
    expect(first.get("X-Request-ID")).toMatch(/^[A-Za-z0-9._:-]{8,160}$/);
    expect(first.get("Idempotency-Key")).not.toBe(second.get("Idempotency-Key"));
  });

  it("can create a request coordinate without an idempotency key", () => {
    const headers = new Headers(mutationHeaders().headers);

    expect(headers.get("X-Request-ID")).toMatch(/^[A-Za-z0-9._:-]{8,160}$/);
    expect(headers.has("Idempotency-Key")).toBe(false);
  });
});
