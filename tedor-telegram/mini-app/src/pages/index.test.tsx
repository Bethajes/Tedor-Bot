/**
 * Hash routing.
 *
 * The bot sends people to `<MINI_APP_URL>#screen=…`, so the screen arrives in
 * the hash — and a wrong parse here opens the wrong form, which is the one
 * failure a user cannot work around.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Router, { navigate, parseRoute, routeHash } from "./index";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, text: async () => JSON.stringify(body) };
}

beforeEach(() => {
  window.location.hash = "";
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(jsonResponse({ saved: true }));
  vi.stubGlobal("fetch", fetchMock);
  (window as unknown as { Telegram: unknown }).Telegram = {
    WebApp: { initData: "signed-init-data" },
  };
});

describe("parseRoute", () => {
  it("reads the tutor screen", () => {
    expect(parseRoute("screen=tutor")).toEqual({ screen: "tutor", ref: undefined, next: undefined });
  });

  it("reads the parent screen", () => {
    expect(parseRoute("screen=parent").screen).toBe("parent");
  });

  it("reads the job screen and its reference", () => {
    expect(parseRoute("screen=job&ref=JOB-000123")).toEqual({
      screen: "job",
      ref: "JOB-000123",
      next: undefined,
    });
  });

  it("falls back to the tutor screen for anything unrecognised", () => {
    expect(parseRoute("").screen).toBe("tutor");
    expect(parseRoute("screen=admin").screen).toBe("tutor");
    expect(parseRoute("garbage").screen).toBe("tutor");
  });

  it("reads the login screen and where to return to", () => {
    expect(parseRoute("screen=login&next=screen%3Djob%26ref%3DJOB-000123")).toEqual({
      screen: "login",
      next: "screen=job&ref=JOB-000123",
    });
  });
});

describe("routeHash", () => {
  it("round-trips a job route through the hash", () => {
    const route = { screen: "job" as const, ref: "JOB-000123" };
    expect(parseRoute(routeHash(route).slice(1))).toEqual({
      screen: "job",
      ref: "JOB-000123",
      next: undefined,
    });
  });

  it("encodes a reference that needs it", () => {
    const hash = routeHash({ screen: "job", ref: "JOB-000123&x=1" });
    expect(hash.startsWith("#")).toBe(true);
    expect(parseRoute(hash.slice(1)).ref).toBe("JOB-000123&x=1");
  });
});

describe("navigate", () => {
  it("writes the hash the parser reads back", () => {
    navigate({ screen: "parent" });
    expect(parseRoute(window.location.hash.slice(1)).screen).toBe("parent");
  });
});

describe("the router", () => {
  it("renders the tutor form for its own screen", async () => {
    window.location.hash = "#screen=tutor";
    render(<Router />);
    expect(await screen.findByTestId("tutor-form")).toBeInTheDocument();
  });

  it("renders the parent form for the parent screen", async () => {
    window.location.hash = "#screen=parent";
    render(<Router />);
    expect(await screen.findByTestId("parent-form")).toBeInTheDocument();
  });

  it("renders the job screen with the reference from the link", async () => {
    window.location.hash = "#screen=job&ref=JOB-000123";
    render(<Router />);

    // The screen asks for exactly this job, carrying the reference in the path.
    await vi.waitFor(() => {
      const jobCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/jobs/"));
      expect(String(jobCall?.[0])).toContain("JOB-000123");
    });
  });

  it("renders the login page for a visitor with no Telegram session", async () => {
    delete window.Telegram;
    window.location.hash = "#screen=login";
    render(<Router />);
    expect(await screen.findByTestId("login-page")).toBeInTheDocument();
  });

  it("sends a Telegram visitor straight past the login page", async () => {
    window.location.hash = "#screen=login&next=screen%3Djob%26ref%3DJOB-000123";
    render(<Router />);

    await vi.waitFor(() =>
      expect(window.location.hash).toContain("JOB-000123"),
    );
  });
});