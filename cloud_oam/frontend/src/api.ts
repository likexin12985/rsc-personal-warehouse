import { blockedClientWriteReason } from "./clientPolicy";

export class ApiError extends Error {
  status: number;
  responseReceived: boolean;
  credentialsCleared: boolean;
  constructor(
    status: number,
    message: string,
    response?: Readonly<{ credentialsCleared?: boolean }>,
  ) {
    super(message);
    this.status = status;
    this.responseReceived = response !== undefined;
    this.credentialsCleared = response?.credentialsCleared === true;
  }
}

let fallbackRequestSequence = 0;

const AUTH_REFRESH_LOCK_NAME = "cloud-oam-auth-refresh-v1";
const AUTH_REFRESH_CHANNEL_NAME = "cloud-oam-auth-refresh-outcome-v1";
const AUTH_REFRESH_SENTINEL_KEY = "cloud-oam-auth-refresh-sentinel-v1";
const BLOCKED_REFRESH_VERSION = "blocked-refresh-state";
const UNAVAILABLE_REFRESH_VERSION = "unavailable-refresh-state";
const SAFE_AUTH_COORDINATION_ID = /^authcoord-[a-f0-9]{36}$/;

const SAFE_IDEMPOTENCY_KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/;
const SAFE_IDEMPOTENCY_PREFIX = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,47}$/;
const SAFE_AUTHENTICATION_IDEMPOTENCY_KEY = /^(?:webidem|sms-request|sms-login|auth-refresh|auth-logout|auth-session-revoke)-[a-f0-9]{36}$/;

function requestSuffix(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  if (globalThis.crypto?.getRandomValues) {
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  }
  fallbackRequestSequence += 1;
  return `${Date.now().toString(36)}-${fallbackRequestSequence.toString(36)}`;
}

function requestId(suffix = requestSuffix()): string {
  return `web-${suffix}`;
}

function secureRandomHex(): string {
  if (!globalThis.crypto?.getRandomValues) {
    throw new ApiError(503, "当前浏览器缺少安全随机数能力，已停止认证写请求");
  }
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(18));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function createIdempotencyKey(prefix = "webidem"): string {
  if (!SAFE_IDEMPOTENCY_PREFIX.test(prefix)) {
    throw new ApiError(400, "幂等键前缀不符合安全格式");
  }
  const key = `${prefix}-${secureRandomHex()}`;
  if (!SAFE_IDEMPOTENCY_KEY.test(key)) {
    throw new ApiError(500, "无法生成安全幂等键");
  }
  return key;
}

function requiresRequestId(method: string): boolean {
  const normalizedMethod = method.toUpperCase();
  return !["GET", "HEAD", "OPTIONS"].includes(normalizedMethod);
}

function isAuthenticationWrite(path: string, method: string): boolean {
  const normalizedPath = path.split("?", 1)[0].replace(/\/+$/, "") || "/";
  return (
    (normalizedPath === "/auth" || normalizedPath.startsWith("/auth/"))
    && requiresRequestId(method)
  );
}

function requireSafeAuthenticationIdempotencyKey(headers: Headers, path: string, method: string): void {
  if (!isAuthenticationWrite(path, method)) return;
  const supplied = headers.get("Idempotency-Key");
  if (supplied && !SAFE_AUTHENTICATION_IDEMPOTENCY_KEY.test(supplied)) {
    throw new ApiError(400, "认证写请求的幂等键不符合安全格式");
  }
  if (!supplied) headers.set("Idempotency-Key", createIdempotencyKey());
}

function shouldRefresh(path: string): boolean {
  return ![
    "/auth/login-options",
    "/auth/sms/login",
    "/auth/sms/request",
    "/auth/refresh",
    "/auth/logout",
  ].includes(path);
}

interface RefreshLockManager {
  request<T>(name: string, callback: () => Promise<T> | T): Promise<T>;
}

interface RefreshChannel {
  postMessage(message: unknown): void;
  addEventListener(type: "message", listener: (event: MessageEvent<unknown>) => void): void;
  removeEventListener(type: "message", listener: (event: MessageEvent<unknown>) => void): void;
  close(): void;
}

interface RefreshStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

type RefreshSentinelState = "pending" | "succeeded" | "authenticated" | "logged_out";

type RefreshSentinel = Readonly<{
  v: 1;
  state: RefreshSentinelState;
  created_at: number;
  opaque_attempt_id: string;
}>;

type RefreshChannelMessage = Readonly<{
  type: "refresh-outcome" | "logout-terminal" | "authenticated-terminal";
  opaque_attempt_id: string;
  succeeded: boolean;
}>;

export interface AuthenticationRefreshCoordinator {
  captureVersion(): string | null;
  refresh(versionBeforeRequest: string | null): Promise<boolean>;
  logout(transport: () => Promise<Response>): Promise<Response>;
  authenticate(transport: () => Promise<Response>): Promise<Response>;
  close(): void;
}

interface AuthenticationRefreshCoordinatorOptions {
  locks?: RefreshLockManager;
  channelFactory?: (name: string) => RefreshChannel;
  storage?: RefreshStorage;
  fetcher?: typeof fetch;
}

type SentinelRead =
  | Readonly<{ kind: "missing" }>
  | Readonly<{ kind: "valid"; value: RefreshSentinel }>
  | Readonly<{ kind: "unknown" }>
  | Readonly<{ kind: "unavailable" }>;

function isRefreshSentinel(value: unknown): value is RefreshSentinel {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<RefreshSentinel>;
  const keys = Object.keys(value).sort();
  return (
    keys.join(",") === "created_at,opaque_attempt_id,state,v"
    && candidate.v === 1
    && ["pending", "succeeded", "authenticated", "logged_out"].includes(candidate.state || "")
    && typeof candidate.created_at === "number"
    && Number.isFinite(candidate.created_at)
    && candidate.created_at > 0
    && typeof candidate.opaque_attempt_id === "string"
    && SAFE_AUTH_COORDINATION_ID.test(candidate.opaque_attempt_id)
  );
}

const terminalLogoutListeners = new Set<() => void>();
const terminalAuthenticatedListeners = new Set<() => void>();
let storageAuthenticationListenerInstalled = false;

function notifyTerminalLogout(): void {
  for (const listener of terminalLogoutListeners) listener();
}

function notifyTerminalAuthenticated(): void {
  for (const listener of terminalAuthenticatedListeners) listener();
}

function ensureStorageAuthenticationListener(): void {
  if (storageAuthenticationListenerInstalled || typeof window === "undefined") return;
  window.addEventListener("storage", (event) => {
    if (event.key !== AUTH_REFRESH_SENTINEL_KEY || event.newValue === null) return;
    try {
      const value: unknown = JSON.parse(event.newValue);
      if (isRefreshSentinel(value) && value.state === "logged_out") notifyTerminalLogout();
      if (isRefreshSentinel(value) && value.state === "authenticated") notifyTerminalAuthenticated();
    } catch {
      // Unknown storage is handled as a fail-closed refresh state, not a logout assertion.
    }
  });
  storageAuthenticationListenerInstalled = true;
}

export function subscribeAuthenticationTerminalLogout(listener: () => void): () => void {
  ensureStorageAuthenticationListener();
  terminalLogoutListeners.add(listener);
  return () => terminalLogoutListeners.delete(listener);
}

export function subscribeAuthenticationEstablished(listener: () => void): () => void {
  ensureStorageAuthenticationListener();
  terminalAuthenticatedListeners.add(listener);
  return () => terminalAuthenticatedListeners.delete(listener);
}

function defaultRefreshChannelFactory(name: string): RefreshChannel {
  return new BroadcastChannel(name);
}

function browserRefreshStorage(): RefreshStorage | undefined {
  try {
    return typeof localStorage === "undefined" ? undefined : localStorage;
  } catch {
    return undefined;
  }
}

/**
 * Coordinates refresh-token rotation across every tab on this origin.
 *
 * The fixed lock/channel/storage names carry no user, token, device, session or request data.
 * localStorage contains only a version, state, timestamp and independent opaque attempt id; tokens
 * and idempotency keys are never persisted or broadcast. Missing coordination capabilities fail
 * closed. BroadcastChannel is only a fast notification path: storage under Web Locks is the source
 * of correctness, so delayed or lost messages cannot authorize another refresh.
 */
export function createAuthenticationRefreshCoordinator(
  options: AuthenticationRefreshCoordinatorOptions = {},
): AuthenticationRefreshCoordinator {
  const locks = options.locks
    ?? (typeof navigator !== "undefined" ? navigator.locks as RefreshLockManager | undefined : undefined);
  const channelFactory = options.channelFactory
    ?? (typeof BroadcastChannel === "function" ? defaultRefreshChannelFactory : undefined);
  const storage = options.storage ?? browserRefreshStorage();
  const fetcher = options.fetcher ?? ((input, init) => fetch(input, init));
  let channel: RefreshChannel | null = null;
  let refreshInFlight: Promise<boolean> | null = null;

  const onMessage = (event: MessageEvent<unknown>): void => {
    const message = event.data as Partial<RefreshChannelMessage> | null;
    if (
      message?.type === "logout-terminal"
      && message.succeeded === true
      && typeof message.opaque_attempt_id === "string"
      && SAFE_AUTH_COORDINATION_ID.test(message.opaque_attempt_id)
    ) {
      notifyTerminalLogout();
    }
    if (
      message?.type === "authenticated-terminal"
      && message.succeeded === true
      && typeof message.opaque_attempt_id === "string"
      && SAFE_AUTH_COORDINATION_ID.test(message.opaque_attempt_id)
    ) {
      notifyTerminalAuthenticated();
    }
  };

  if (locks && channelFactory && storage) {
    try {
      channel = channelFactory(AUTH_REFRESH_CHANNEL_NAME);
      channel.addEventListener("message", onMessage);
    } catch {
      channel = null;
    }
  }

  function readSentinel(): SentinelRead {
    if (!storage) return { kind: "unavailable" };
    try {
      const raw = storage.getItem(AUTH_REFRESH_SENTINEL_KEY);
      if (raw === null) return { kind: "missing" };
      const parsed: unknown = JSON.parse(raw);
      return isRefreshSentinel(parsed) ? { kind: "valid", value: parsed } : { kind: "unknown" };
    } catch {
      return { kind: "unavailable" };
    }
  }

  function writeSentinel(state: RefreshSentinelState, opaqueAttemptId: string): void {
    if (!storage) throw new ApiError(503, "当前浏览器无法安全保存会话刷新状态，已失败关闭");
    const sentinel: RefreshSentinel = {
      v: 1,
      state,
      created_at: Date.now(),
      opaque_attempt_id: opaqueAttemptId,
    };
    try {
      storage.setItem(AUTH_REFRESH_SENTINEL_KEY, JSON.stringify(sentinel));
    } catch {
      throw new ApiError(503, "当前浏览器无法安全保存会话刷新状态，已失败关闭");
    }
  }

  function captureVersion(): string | null {
    const sentinel = readSentinel();
    if (sentinel.kind === "unavailable") return UNAVAILABLE_REFRESH_VERSION;
    if (sentinel.kind === "missing") return null;
    if (
      sentinel.kind === "valid"
      && ["succeeded", "authenticated"].includes(sentinel.value.state)
    ) {
      return sentinel.value.opaque_attempt_id;
    }
    return BLOCKED_REFRESH_VERSION;
  }

  async function refresh(versionBeforeRequest: string | null): Promise<boolean> {
    if (!locks || !channel || !storage) {
      throw new ApiError(503, "当前浏览器无法安全协调多标签会话刷新，已失败关闭");
    }
    if (versionBeforeRequest === UNAVAILABLE_REFRESH_VERSION) {
      throw new ApiError(503, "当前浏览器无法安全读取会话刷新状态，已失败关闭");
    }
    if (versionBeforeRequest === BLOCKED_REFRESH_VERSION) return false;
    if (refreshInFlight) return refreshInFlight;

    refreshInFlight = locks.request(AUTH_REFRESH_LOCK_NAME, async () => {
      const sentinel = readSentinel();
      if (sentinel.kind === "unavailable") {
        throw new ApiError(503, "当前浏览器无法安全读取会话刷新状态，已失败关闭");
      }
      if (sentinel.kind === "unknown") return false;
      if (
        sentinel.kind === "valid"
        && !["succeeded", "authenticated"].includes(sentinel.value.state)
      ) return false;
      const currentVersion = sentinel.kind === "valid" ? sentinel.value.opaque_attempt_id : null;
      if (currentVersion !== versionBeforeRequest) return true;

      // Persist the non-sensitive pending sentinel before creating the key or touching the network.
      // It has no TTL: an uncertain submission requires reauthentication or confirmed logout.
      const opaqueAttemptId = `authcoord-${secureRandomHex()}`;
      writeSentinel("pending", opaqueAttemptId);
      const coordinates = mutationHeaders("auth-refresh");
      let succeeded = false;
      try {
        const response = await fetcher("/api/auth/refresh", {
          method: "POST",
          headers: coordinates.headers,
          credentials: "include",
        });
        succeeded = response.ok;
      } catch {
        succeeded = false;
      }
      if (succeeded) writeSentinel("succeeded", opaqueAttemptId);
      channel?.postMessage({
        type: "refresh-outcome",
        opaque_attempt_id: opaqueAttemptId,
        succeeded,
      } satisfies RefreshChannelMessage);
      return succeeded;
    }).catch((error: unknown) => {
      if (error instanceof ApiError) throw error;
      throw new ApiError(503, "多标签会话刷新协调失败，已停止刷新请求");
    }).finally(() => {
      refreshInFlight = null;
    });
    return refreshInFlight;
  }

  async function logout(transport: () => Promise<Response>): Promise<Response> {
    if (!locks || !channel || !storage) {
      throw new ApiError(503, "当前浏览器无法安全协调多标签退出，已失败关闭");
    }
    return locks.request(AUTH_REFRESH_LOCK_NAME, async () => {
      const sentinel = readSentinel();
      if (sentinel.kind === "unavailable") {
        throw new ApiError(503, "当前浏览器无法安全读取会话刷新状态，已失败关闭");
      }
      const opaqueAttemptId = `authcoord-${secureRandomHex()}`;
      writeSentinel("pending", opaqueAttemptId);
      const response = await transport();
      const credentialsCleared = response.ok
        || response.headers.get("X-Auth-Credentials-Cleared")?.toLowerCase() === "true";
      if (credentialsCleared) {
        writeSentinel("logged_out", opaqueAttemptId);
        channel?.postMessage({
          type: "logout-terminal",
          opaque_attempt_id: opaqueAttemptId,
          succeeded: true,
        } satisfies RefreshChannelMessage);
      }
      return response;
    }).catch((error: unknown) => {
      if (error instanceof ApiError) throw error;
      throw error;
    });
  }

  async function authenticate(transport: () => Promise<Response>): Promise<Response> {
    if (!locks || !channel || !storage) {
      throw new ApiError(503, "当前浏览器无法安全协调登录凭据写入，已失败关闭");
    }
    return locks.request(AUTH_REFRESH_LOCK_NAME, async () => {
      let previousRaw: string | null;
      try {
        previousRaw = storage.getItem(AUTH_REFRESH_SENTINEL_KEY);
      } catch {
        throw new ApiError(503, "当前浏览器无法安全读取会话刷新状态，已失败关闭");
      }
      const opaqueAttemptId = `authcoord-${secureRandomHex()}`;
      writeSentinel("pending", opaqueAttemptId);
      let response: Response;
      try {
        response = await transport();
      } catch (error) {
        // The login may have reached the application. Keep pending so no old-cookie refresh can
        // race a result whose Set-Cookie outcome is unknown.
        throw error;
      }
      if (response.ok) {
        writeSentinel("authenticated", opaqueAttemptId);
        channel?.postMessage({
          type: "authenticated-terminal",
          opaque_attempt_id: opaqueAttemptId,
          succeeded: true,
        } satisfies RefreshChannelMessage);
      } else {
        // A definite rejected login must not erase the prior refresh/logout barrier.
        try {
          if (previousRaw === null) storage.removeItem(AUTH_REFRESH_SENTINEL_KEY);
          else storage.setItem(AUTH_REFRESH_SENTINEL_KEY, previousRaw);
        } catch {
          // The pending sentinel was already written; leaving it in place is the safe fallback.
        }
      }
      return response;
    });
  }

  return {
    captureVersion,
    refresh,
    logout,
    authenticate,
    close(): void {
      channel?.removeEventListener("message", onMessage);
      channel?.close();
      channel = null;
    },
  };
}

let defaultRefreshCoordinator: {
  locks: LockManager | undefined;
  channelConstructor: typeof BroadcastChannel | undefined;
  storage: Storage | undefined;
  value: AuthenticationRefreshCoordinator;
} | null = null;

function authenticationRefreshCoordinator(): AuthenticationRefreshCoordinator {
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  const channelConstructor = typeof BroadcastChannel === "function" ? BroadcastChannel : undefined;
  const storage = browserRefreshStorage() as Storage | undefined;
  if (
    !defaultRefreshCoordinator
    || defaultRefreshCoordinator.locks !== locks
    || defaultRefreshCoordinator.channelConstructor !== channelConstructor
    || defaultRefreshCoordinator.storage !== storage
  ) {
    defaultRefreshCoordinator?.value.close();
    defaultRefreshCoordinator = {
      locks,
      channelConstructor,
      storage,
      value: createAuthenticationRefreshCoordinator(),
    };
  }
  return defaultRefreshCoordinator.value;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method || "GET";
  const normalizedPath = path.split("?", 1)[0].replace(/\/+$/, "") || "/";
  const isPrivateIdentityRead = method.toUpperCase() === "GET" && (
    normalizedPath === "/auth/me" || normalizedPath === "/access/context"
  );
  const isLogoutWrite = normalizedPath === "/auth/logout" && method.toUpperCase() === "POST";
  const isSmsLoginWrite = normalizedPath === "/auth/sms/login" && method.toUpperCase() === "POST";
  const blockedReason = blockedClientWriteReason(path, method);
  if (blockedReason) throw new ApiError(403, blockedReason);
  const headers = new Headers(init.headers);
  if (requiresRequestId(method) && !headers.has("X-Request-ID")) {
    headers.set("X-Request-ID", requestId());
  }
  requireSafeAuthenticationIdempotencyKey(headers, path, method);
  if (init.body && !(init.body instanceof FormData)) headers.set("content-type", "application/json");
  if (isPrivateIdentityRead) {
    headers.set("Cache-Control", "no-store");
    headers.set("Pragma", "no-cache");
  }
  const refreshCoordinator = (shouldRefresh(path) || isLogoutWrite || isSmsLoginWrite)
    ? authenticationRefreshCoordinator()
    : null;
  const refreshVersionBeforeRequest = refreshCoordinator?.captureVersion() ?? null;
  const preparedInit: RequestInit = {
    ...init,
    ...(isPrivateIdentityRead ? { cache: "no-store" as RequestCache } : {}),
    headers,
    credentials: "include",
  };
  const transport = () => fetch(`/api${path}`, preparedInit);
  let response = isLogoutWrite && refreshCoordinator
    ? await refreshCoordinator.logout(transport)
    : isSmsLoginWrite && refreshCoordinator
      ? await refreshCoordinator.authenticate(transport)
      : await transport();
  if (
    response.status === 401
    && refreshCoordinator
    && await refreshCoordinator.refresh(refreshVersionBeforeRequest)
  ) {
    response = await fetch(`/api${path}`, preparedInit);
  }
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") message = payload.detail;
      else if (payload.detail && typeof payload.detail.message === "string") {
        message = payload.detail.message;
      }
    } catch {
      // Keep the HTTP fallback when the response is not JSON.
    }
    throw new ApiError(response.status, message, {
      credentialsCleared: response.headers.get("X-Auth-Credentials-Cleared")?.toLowerCase() === "true",
    });
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function jsonBody(value: unknown): Pick<RequestInit, "body"> {
  return { body: JSON.stringify(value) };
}

export function mutationHeaders(prefix?: string): Pick<RequestInit, "headers"> {
  const headers: Record<string, string> = {
    "X-Request-ID": requestId(),
  };
  if (prefix) headers["Idempotency-Key"] = createIdempotencyKey(prefix);
  return {
    headers,
  };
}
