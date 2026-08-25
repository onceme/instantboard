/**
 * Service Worker registration: registered only in production builds and only
 * when the browser supports it; the script URL is resolved against BASE_URL.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { registerServiceWorker } from "@/utils/serviceWorker";

let registerMock: ReturnType<typeof vi.fn>;

function stubNavigator(withServiceWorker: boolean) {
  const nav: Record<string, unknown> = {};
  if (withServiceWorker) {
    nav.serviceWorker = { register: registerMock };
  }
  vi.stubGlobal("navigator", nav);
}

describe("registerServiceWorker", () => {
  beforeEach(() => {
    registerMock = vi.fn().mockResolvedValue({ scope: "/" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("registers sw.js in production builds", async () => {
    vi.stubEnv("PROD", true);
    vi.stubEnv("BASE_URL", "/");
    stubNavigator(true);

    const registration = await registerServiceWorker();

    expect(registerMock).toHaveBeenCalledTimes(1);
    expect(registerMock).toHaveBeenCalledWith("/sw.js");
    expect(registration).toEqual({ scope: "/" });
  });

  it("resolves the script path relative to BASE_URL", async () => {
    vi.stubEnv("PROD", true);
    vi.stubEnv("BASE_URL", "/instantboard/");
    stubNavigator(true);

    await registerServiceWorker();

    expect(registerMock).toHaveBeenCalledWith("/instantboard/sw.js");
  });

  it("does not register outside production", async () => {
    vi.stubEnv("PROD", false);
    stubNavigator(true);

    const registration = await registerServiceWorker();

    expect(registerMock).not.toHaveBeenCalled();
    expect(registration).toBeNull();
  });

  it("does not register when the browser lacks service worker support", async () => {
    vi.stubEnv("PROD", true);
    vi.stubEnv("BASE_URL", "/");
    stubNavigator(false);

    const registration = await registerServiceWorker();

    expect(registerMock).not.toHaveBeenCalled();
    expect(registration).toBeNull();
  });

  it("returns null instead of throwing when registration fails", async () => {
    vi.stubEnv("PROD", true);
    vi.stubEnv("BASE_URL", "/");
    registerMock.mockRejectedValueOnce(new Error("registration denied"));
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    stubNavigator(true);

    const registration = await registerServiceWorker();

    expect(registration).toBeNull();
    expect(consoleSpy).toHaveBeenCalled();
  });
});
