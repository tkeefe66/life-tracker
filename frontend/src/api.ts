export class UnauthorizedError extends Error {}

async function handle<T>(resp: Response): Promise<T> {
  if (resp.status === 401) throw new UnauthorizedError();
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
