export class UnauthorizedError extends Error {}

// Sessions now really expire (server-side, revocable) instead of the old
// 365-day static cookie, so a 401 can legitimately happen mid-use on ANY
// screen, not just the initial probe. App.tsx registers a single handler here
// so every fetch — Today, Scorecard, Insights, Settings — returns to the
// login screen the moment a session is no longer valid, not just the one
// that happened to notice.
let unauthorizedHandler: (() => void) | null = null;

export function onUnauthorized(handler: () => void): void {
  unauthorizedHandler = handler;
}

async function handle<T>(resp: Response): Promise<T> {
  if (resp.status === 401) {
    unauthorizedHandler?.();
    throw new UnauthorizedError();
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${resp.status})`);
  }
  if (resp.status === 204) return null as T;
  return resp.json();
}

export async function login(password: string): Promise<boolean> {
  const resp = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  return resp.ok;
}

export async function logout(): Promise<void> {
  await fetch("/api/logout", { method: "POST" }).catch(() => {});
}

export function apiGet<T>(path: string): Promise<T> {
  return fetch(`/api${path}`).then((r) => handle<T>(r));
}

export function apiSend<T>(method: string, path: string, body?: unknown): Promise<T> {
  return fetch(`/api${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }).then((r) => handle<T>(r));
}
