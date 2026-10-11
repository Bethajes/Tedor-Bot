/**
 * The app must actually mount inside a Telegram WebView.
 *
 * "WebView crashed" in Telegram is not an exception you can read: it is what
 * the client shows when the document never arrived, or arrived and the bundle
 * threw before the first paint. Both are invisible from the outside, so this
 * renders the real entry point against a stand-in for `window.Telegram` and
 * fails loudly here instead.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { readyTelegram } from "../api/telegram";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, ok = false, status = 404) {
  return { ok, status, text: async () => JSON.stringify(body) };
}

/** The parts of `window.Telegram.WebApp` the app actually touches. */
function telegramWebApp(initData: string) {
  return {
    initData,
    initDataUnsafe: initData ? { user: { id: 55, first_name: "Abebe" } } : undefined,
    ready: vi.fn(),
    expand: vi.fn(),
  };
}

beforeEach(() => {
  fetchMock.mockReset();
  // A first-time applicant: no draft, no profile yet.
  fetchMock.mockResolvedValue(jsonResponse({ detail: "not found" }));
  vi.stubGlobal("fetch", fetchMock);
});

describe("the Mini App entry point", () => {
  it("mounts in a Telegram WebView and renders a screen", async () => {
    window.Telegram = { WebApp: telegramWebApp("query_id=abc&hash=def") };
    window.location.hash = "#screen=tutor";

    render(<App />);

    // Any screen at all proves the bundle ran to first paint.
    expect(await screen.findByTestId("tutor-form")).toBeInTheDocument();
  });

  it("mounts with no Telegram object at all", async () => {
    // jsdom and a plain browser both have no window.Telegram. Touching it
    // unguarded would throw on the very first render.
    delete window.Telegram;
    window.location.hash = "#screen=tutor";

    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, assign, hash: window.location.hash },
    });

    expect(() => render(<App />)).not.toThrow();

    // No credential, so nothing that reads user data may be attempted: the
    // app redirects instead of asking the API for a profile it cannot have.
    await vi.waitFor(() => expect(assign).toHaveBeenCalled());
    expect(String(assign.mock.calls[0][0])).toContain("/login");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("tells Telegram it is ready so the window sizes itself", () => {
    const webApp = telegramWebApp("query_id=abc&hash=def");
    window.Telegram = { WebApp: webApp };

    // main() calls this once, before React mounts, so the webview sizes
    // itself to the content rather than bouncing after the first paint.
    readyTelegram();

    expect(webApp.ready).toHaveBeenCalled();
    expect(webApp.expand).toHaveBeenCalled();
  });

  it("does not throw when the SDK exposes no WebApp yet", () => {
    window.Telegram = {};
    expect(() => readyTelegram()).not.toThrow();
  });

  it("survives a Telegram object with an empty WebApp", async () => {
    // Some clients expose `window.Telegram` before the SDK finishes loading.
    window.Telegram = {} as { WebApp?: never };
    window.location.hash = "#screen=tutor";

    expect(() => render(<App />)).not.toThrow();
  });
});