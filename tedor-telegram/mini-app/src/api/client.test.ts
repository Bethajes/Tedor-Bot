/**
 * The API client's two rules.
 *
 * 1. Identity rides on a header, never on a parameter.
 * 2. No credential, no request: a browser without Init Data and without a
 *    token is sent to the login page *before* anything that reads user data is
 *    attempted.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, apiFetch } from "./client";
import {
  authHeaders,
  clearWebToken,
  hasCredentials,
  initData,
  initDataUser,
  redirectToLogin,
  setWebToken,
  webToken,
} from "./telegram";

const fetchMock = vi.fn();
const assign = vi.fn();

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, text: async () => JSON.stringify(body) };
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, assign, hash: "", pathname: "/", search: "" },
  });
  clearWebToken();
  delete window.Telegram;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("reading the Telegram environment", () => {
  it("reports no Init Data outside Telegram rather than failing", () => {
    expect(initData()).toBe("");
    expect(initDataUser()).toBeUndefined();
    expect(hasCredentials()).toBe(false);
  });

  it("reads the signed Init Data when the SDK has populated it", () => {
    window.Telegram = {
      WebApp: {
        initData: "query_id=abc&hash=def",
        initDataUnsafe: { user: { id: 42, first_name: "Abebe" } },
      },
    };

    expect(initData()).toBe("query_id=abc&hash=def");
    expect(initDataUser()?.id).toBe(42);
    expect(hasCredentials()).toBe(true);
  });

  it("treats empty Init Data as no credential", () => {
    window.Telegram = { WebApp: { initData: "" } };
    expect(hasCredentials()).toBe(false);
  });
});

describe("attaching credentials", () => {
  it("sends Init Data, and never the web token, inside Telegram", () => {
    window.Telegram = { WebApp: { initData: "signed" } };
    setWebToken("a-web-token");

    expect(authHeaders()).toEqual({ "X-Telegram-Init-Data": "signed" });
  });

  it("falls back to the web token in a plain browser", () => {
    setWebToken("a-web-token");

    expect(authHeaders()).toEqual({ "X-Telegram-Token": "a-web-token" });
    expect(hasCredentials()).toBe(true);
  });

  it("keeps the token for the session only", () => {
    setWebToken("a-web-token");
    expect(webToken()).toBe("a-web-token");

    clearWebToken();
    expect(webToken()).toBeNull();
  });

  it("puts the header on the request itself, not the URL", async () => {
    window.Telegram = { WebApp: { initData: "signed" } };
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));

    await api.get("/miniapp/tutor/draft");

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).not.toContain("signed");
    expect(url).not.toContain("telegram_user_id");
    expect(options.headers["X-Telegram-Init-Data"]).toBe("signed");
  });
});

describe("refusing to load data without a credential", () => {
  it("redirects to the login page instead of calling the API", async () => {
    await expect(apiFetch("/miniapp/tutor/draft")).rejects.toBeInstanceOf(ApiError);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(assign).toHaveBeenCalled();
    expect(String(assign.mock.calls[0][0])).toContain("/login");
  });

  it("preserves the screen in the redirect, so login returns there", () => {
    window.location.hash = "#screen=job&ref=JOB-000123";
    redirectToLogin();

    const target = String(assign.mock.calls[0][0]);
    expect(target).toContain("/login");
    expect(decodeURIComponent(target)).toContain("screen=job");
    expect(decodeURIComponent(target)).toContain("JOB-000123");
  });

  it("still allows the login endpoints themselves", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ sent: true }, true, 202));

    await api.post("/miniapp/auth/code", { phone: "+251911234567" });

    expect(fetchMock).toHaveBeenCalled();
    expect(assign).not.toHaveBeenCalled();
  });
});

describe("responses", () => {
  beforeEach(() => {
    window.Telegram = { WebApp: { initData: "signed" } };
  });

  it("returns the parsed body on success", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ public_tutor_id: "TDR-000001" }));
    await expect(api.get("/miniapp/tutor/profile")).resolves.toEqual({
      public_tutor_id: "TDR-000001",
    });
  });

  it("raises the status so a caller can branch on it", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Forbidden" }, false, 403));

    await expect(api.get("/miniapp/tutor/profile")).rejects.toMatchObject({
      status: 403,
      detail: { detail: "Forbidden" },
    });
  });

  it("sends JSON for a plain body and FormData untouched", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}));
    const form = new FormData();
    form.append("file", new Blob(["x"]), "transcript.pdf");

    await api.postForm("/miniapp/documents/upload", form);

    const [, options] = fetchMock.mock.calls[0];
    // A Content-Type set here would lose the multipart boundary.
    expect(options.headers["Content-Type"]).toBeUndefined();
    expect(options.body).toBe(form);
  });
});