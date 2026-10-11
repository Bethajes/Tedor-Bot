/**
 * The web login.
 *
 * The property that matters is not the form's shape but what it refuses to do:
 * it must not become a way to find out who has applied, and it must not load
 * any user data before a credential exists.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "./LoginPage";
import { webToken } from "../api/telegram";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, text: async () => JSON.stringify(body) };
}

beforeEach(() => {
  window.sessionStorage.clear();
  delete window.Telegram;
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("LoginPage", () => {
  it("asks for a phone number first", () => {
    render(<LoginPage />);

    expect(screen.getByTestId("login-page")).toBeInTheDocument();
    expect(screen.getByLabelText("Phone number")).toBeInTheDocument();
    // The submit button stays disabled until there is something to send.
    expect(screen.getByTestId("request-code")).toBeDisabled();
  });

  it("requests a code and then asks for it", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValueOnce(jsonResponse({ sent: true, delivered: true }, true, 202));

    render(<LoginPage />);
    await user.type(screen.getByTestId("login-phone"), "+251911234567");
    await user.click(screen.getByTestId("request-code"));

    expect(await screen.findByTestId("login-code")).toBeInTheDocument();
    expect(screen.getByTestId("login-message")).toHaveTextContent("Telegram chat");
  });

  it("stores the token for this session and returns to where the visitor was", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValueOnce(jsonResponse({ sent: true, delivered: true }, true, 202));
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ access_token: "a.jwt.token", token_type: "bearer" }),
    );

    render(<LoginPage next="screen=job&ref=JOB-000123" />);
    await user.type(screen.getByTestId("login-phone"), "+251911234567");
    await user.click(screen.getByTestId("request-code"));
    await user.type(await screen.findByTestId("login-code"), "123456");
    await user.click(screen.getByTestId("verify-code"));

    await waitFor(() => expect(webToken()).toBe("a.jwt.token"));
    expect(window.location.hash).toContain("JOB-000123");
  });

  it("reports a number the API refuses without saying why", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "We could not send a code to that number." }, false, 400),
    );

    render(<LoginPage />);
    await user.type(screen.getByTestId("login-phone"), "+251999999999");
    await user.click(screen.getByTestId("request-code"));

    // The same message either way: the endpoint must not confirm whether a
    // number is on file.
    expect(await screen.findByTestId("login-error")).toHaveTextContent(
      "Check it and try again",
    );
    expect(webToken()).toBeNull();
  });

  it("reports a refused code without storing anything", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValueOnce(jsonResponse({ sent: true, delivered: true }, true, 202));
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "That code did not work." }, false, 401),
    );

    render(<LoginPage />);
    await user.type(screen.getByTestId("login-phone"), "+251911234567");
    await user.click(screen.getByTestId("request-code"));
    await user.type(await screen.findByTestId("login-code"), "000000");
    await user.click(screen.getByTestId("verify-code"));

    expect(await screen.findByTestId("login-error")).toHaveTextContent("did not work");
    expect(webToken()).toBeNull();
  });
});