/**
 * The web login, for the Mini App opened outside Telegram.
 *
 * Inside Telegram there is nothing to log into: the signed Init Data identifies
 * the user. In a plain browser the app asks for the phone number already on the
 * tutor's application, the bot sends a one-time code, and the code buys a
 * short-lived token for this tab only (Requirements 2.5, 2.6).
 *
 * The phone number is never used to *create* access: it must match an
 * application already on file, so this cannot be used to probe who has applied.
 */

import { useEffect, useState } from "react";

import api from "../api/client";
import { setWebToken, hasInitData } from "../api/telegram";

export interface LoginPageProps {
  /** Where to go after a successful login (from `#next=`). */
  next?: string;
}

export default function LoginPage({ next }: LoginPageProps) {
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [stage, setStage] = useState<"phone" | "code">("phone");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Opened from Telegram: there is nothing to sign in to, so send the visitor
  // on to the screen the link was for. Done in an effect, never during render.
  useEffect(() => {
    if (!hasInitData()) return;
    window.location.hash = next ? `#${next.replace(/^#/, "")}` : "#screen=tutor";
  }, [next]);

  const requestCode = async () => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api.post<{ delivered: boolean; dev_code?: string }>(
        "/miniapp/auth/code",
        { phone: phone.trim() },
      );
      setStage("code");
      setMessage(
        result.delivered
          ? "We sent a one-time code to your Telegram chat."
          : "We created a one-time code. Enter it below.",
      );
    } catch {
      // The API answers the same way for an unknown number, so a wrong phone
      // cannot be distinguished from an empty database here.
      setError(
        "We could not send a code to that number. Check it and try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ access_token: string }>(
        "/miniapp/auth/verify",
        { phone: phone.trim(), code: code.trim() },
      );
      setWebToken(result.access_token);
      // The token lives in sessionStorage; the app re-reads it on every request.
      window.location.hash = next ? `#${next.replace(/^#/, "")}` : "#screen=tutor";
    } catch {
      setError("That code did not work. Request a new one and try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="p-4" data-testid="login-page">
      <h1 className="mb-1 text-lg font-semibold">Sign in to Tedor Tutors</h1>
      <p className="mb-4 text-sm text-slate-600">
        Use the phone number on your application. We will send a one-time code to
        your Telegram chat.
      </p>

      <label className="mb-1 block text-sm font-medium" htmlFor="login-phone">
        Phone number
      </label>
      <input
        id="login-phone"
        data-testid="login-phone"
        className="mb-3 w-full rounded-lg border border-slate-300 px-3 py-2"
        type="tel"
        value={phone}
        placeholder="+251911234567"
        onChange={(event) => setPhone(event.target.value)}
      />

      {stage === "phone" ? (
        <button
          type="button"
          className="w-full rounded-lg bg-tedor-500 px-4 py-2 text-white disabled:opacity-50"
          data-testid="request-code"
          disabled={busy || phone.trim().length < 5}
          onClick={() => void requestCode()}
        >
          {busy ? "Sending…" : "Send me a code"}
        </button>
      ) : (
        <>
          <label className="mb-1 block text-sm font-medium" htmlFor="login-code">
            One-time code
          </label>
          <input
            id="login-code"
            data-testid="login-code"
            className="mb-3 w-full rounded-lg border border-slate-300 px-3 py-2"
            inputMode="numeric"
            maxLength={6}
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
          <div className="flex gap-2">
            <button
              type="button"
              className="flex-1 rounded-lg bg-tedor-500 px-4 py-2 text-white disabled:opacity-50"
              data-testid="verify-code"
              disabled={busy || code.trim().length < 4}
              onClick={() => void verify()}
            >
              {busy ? "Checking…" : "Sign in"}
            </button>
            <button
              type="button"
              className="rounded-lg border border-slate-300 px-4 py-2"
              onClick={() => setStage("phone")}
            >
              Change
            </button>
          </div>
        </>
      )}

      {message ? (
        <p className="mt-3 text-sm text-slate-600" data-testid="login-message">
          {message}
        </p>
      ) : null}
      {error ? (
        <p className="mt-3 text-sm text-red-600" role="alert" data-testid="login-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}