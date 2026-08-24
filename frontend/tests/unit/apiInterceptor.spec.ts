/**
 * Axios interceptor regression: 401s on the login flows (/auth/sso/{provider},
 * /auth/admin/login) are rethrown to the caller (so login errors are visible)
 * without touching token refresh / session state / redirects; every other 401
 * attempts a refresh and bounces the user back to the login page of the entry
 * that created the session (admin → /ibadmin, sso → /login).
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import axios, { AxiosError } from "axios";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";
import { apiClient, apiGet, apiPost } from "@/utils/api";

function errorResponse(
  status: number,
  config: InternalAxiosRequestConfig,
  data: unknown = { detail: { error: { code: "ERR", message: "boom" } } },
): AxiosError {
  const response = {
    status,
    statusText: String(status),
    data,
    headers: {},
    config,
  } as AxiosResponse;
  return new AxiosError(
    `Request failed with status code ${status}`,
    String(status),
    config,
    null,
    response,
  );
}

function okResponse(
  config: InternalAxiosRequestConfig,
  data: unknown,
): AxiosResponse {
  return {
    status: 200,
    statusText: "OK",
    data,
    headers: {},
    config,
  } as AxiosResponse;
}

// The auth header may live on an AxiosHeaders instance or a plain object.
function authHeader(config: InternalAxiosRequestConfig): string | undefined {
  const headers = config.headers as unknown as {
    Authorization?: string;
    get?: (name: string) => string;
  };
  if (typeof headers?.get === "function")
    return headers.get("Authorization") ?? undefined;
  return headers?.Authorization;
}

// Replace window.location so redirect assertions don't navigate happy-dom.
const fakeLocation = { href: "" };

beforeEach(() => {
  localStorage.clear();
  fakeLocation.href = "";
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: fakeLocation,
  });
});

afterEach(() => {
  delete (apiClient.defaults as { adapter?: unknown }).adapter;
  delete (axios.defaults as { adapter?: unknown }).adapter;
});

describe("401 exemptions for login flows", () => {
  it("rethrows 401 on /auth/sso/{provider} without refresh or redirect", async () => {
    localStorage.setItem("access_token", "tok");
    localStorage.setItem("refresh_token", "rtok");
    const requestedUrls: string[] = [];

    apiClient.defaults.adapter = async (config) => {
      requestedUrls.push(config.url ?? "");
      throw errorResponse(401, config, {
        detail: {
          error: { code: "INVALID_CREDENTIALS", message: "SSO login failed" },
        },
      });
    };
    axios.defaults.adapter = async (config) => {
      requestedUrls.push(config.url ?? "");
      throw errorResponse(401, config);
    };

    const err = await apiPost("/auth/sso/github", {}).catch((e) => e);
    expect(err.response.status).toBe(401);
    // Only the login request was made: no /auth/refresh attempt
    expect(requestedUrls).toEqual(["/auth/sso/github"]);
    // Session state untouched so the user can simply retry
    expect(localStorage.getItem("access_token")).toBe("tok");
    expect(fakeLocation.href).toBe("");
  });

  it("rethrows 401 on /auth/admin/login without refresh or redirect", async () => {
    localStorage.setItem("refresh_token", "rtok");
    const requestedUrls: string[] = [];

    apiClient.defaults.adapter = async (config) => {
      requestedUrls.push(config.url ?? "");
      throw errorResponse(401, config, {
        detail: {
          error: { code: "INVALID_CREDENTIALS", message: "账号或密码错误" },
        },
      });
    };
    axios.defaults.adapter = async (config) => {
      requestedUrls.push(config.url ?? "");
      throw errorResponse(401, config);
    };

    const err = await apiPost("/auth/admin/login", {}).catch((e) => e);
    expect(err.response.status).toBe(401);
    expect(requestedUrls).toEqual(["/auth/admin/login"]);
    expect(fakeLocation.href).toBe("");
  });
});

describe("non-exempt 401 handling", () => {
  it("attaches the stored access token as a Bearer header", async () => {
    localStorage.setItem("access_token", "tok123");
    let seen: InternalAxiosRequestConfig | undefined;
    apiClient.defaults.adapter = async (config) => {
      seen = config;
      return okResponse(config, { success: true, data: [] });
    };

    await apiGet("/finance/watchlist");
    expect(authHeader(seen!)).toBe("Bearer tok123");
  });

  it("without refresh token: clears session and redirects admin entry to /ibadmin", async () => {
    localStorage.setItem("session_entry", "admin");
    localStorage.setItem("access_token", "expired");

    apiClient.defaults.adapter = async (config) => {
      throw errorResponse(401, config);
    };

    await expect(apiGet("/tech/news")).rejects.toBeDefined();
    expect(localStorage.getItem("access_token")).toBeNull();
    expect(localStorage.getItem("session_entry")).toBeNull();
    expect(fakeLocation.href).toBe("/ibadmin");
  });

  it("without refresh token: sso entry redirects to /login", async () => {
    localStorage.setItem("session_entry", "sso");

    apiClient.defaults.adapter = async (config) => {
      throw errorResponse(401, config);
    };

    await expect(apiGet("/tech/news")).rejects.toBeDefined();
    expect(fakeLocation.href).toBe("/login");
  });

  it("failing refresh still clears the session and redirects by entry", async () => {
    localStorage.setItem("access_token", "old");
    localStorage.setItem("refresh_token", "rt");
    localStorage.setItem("session_entry", "sso");

    apiClient.defaults.adapter = async (config) => {
      throw errorResponse(401, config);
    };
    const refreshCalls: string[] = [];
    axios.defaults.adapter = async (config) => {
      refreshCalls.push(config.url ?? "");
      throw errorResponse(401, config);
    };

    await expect(apiGet("/tech/news")).rejects.toBeDefined();
    expect(refreshCalls).toEqual(["/api/v1/auth/refresh"]);
    expect(fakeLocation.href).toBe("/login");
  });

  it("successful refresh retries the original request with the new token", async () => {
    localStorage.setItem("access_token", "old");
    localStorage.setItem("refresh_token", "rt");

    let apiCalls = 0;
    let retryAuth: string | undefined;
    apiClient.defaults.adapter = async (config) => {
      apiCalls += 1;
      if (apiCalls === 1) throw errorResponse(401, config);
      retryAuth = authHeader(config);
      return okResponse(config, { success: true, data: ["retried"] });
    };
    axios.defaults.adapter = async (config) => {
      expect(config.url).toBe("/api/v1/auth/refresh");
      return okResponse(config, {
        success: true,
        data: { access_token: "fresh" },
      });
    };

    const result = await apiGet("/tech/news");
    expect(result.data).toEqual(["retried"]);
    expect(apiCalls).toBe(2);
    expect(retryAuth).toBe("Bearer fresh");
    expect(localStorage.getItem("access_token")).toBe("fresh");
    expect(fakeLocation.href).toBe("");
  });
});
