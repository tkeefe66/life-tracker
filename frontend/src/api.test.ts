import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet, logout, onUnauthorized, UnauthorizedError } from "./api";

describe("logout", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs to /api/logout so the server invalidates the session", async () => {
    await logout();
    expect(fetchMock).toHaveBeenCalledWith("/api/logout", { method: "POST" });
  });

  it("never throws even if the request fails — the client always treats logout as done", async () => {
    fetchMock.mockRejectedValue(new Error("network down"));
    await expect(logout()).resolves.toBeUndefined();
  });
});

describe("onUnauthorized", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    onUnauthorized(() => {});
    vi.unstubAllGlobals();
  });

  it("fires the registered handler on any 401, not just the initial probe", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    await expect(apiGet("/targets")).rejects.toBeInstanceOf(UnauthorizedError);
    expect(handler).toHaveBeenCalledTimes(1);
  });
});
