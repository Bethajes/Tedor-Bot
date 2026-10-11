# The Tedor Mini App

A Telegram Mini App that runs the two conversations the bot runs, as web forms:
the tutor application and the parent "Request a Tutor" request — plus a job
application screen opened from a job post's button.

Everything it does is already available in the bot. The Mini App exists because
a 26-step application on a phone keyboard, one question per message, is a long
conversation — and because a family asking for a tutor should not have to work
out which button to press.

---

## Contents

1. [What it does](#what-it-does)
2. [Architecture](#architecture)
3. [Environment variables](#environment-variables)
4. [BotFather setup](#botfather-setup)
5. [Local development](#local-development)
6. [Deploying](#deploying)
7. [API reference](#api-reference)
8. [Authentication](#authentication)
9. [Privacy](#privacy)
10. [Tests](#tests)

---

## What it does

| Screen | Deep link | What it is for |
| --- | --- | --- |
| Tutor application | `#screen=tutor` | The 26-step tutor application, with a resume draft |
| Parent request | `#screen=parent` | Requesting a tutor, then choosing from the matches |
| Job application | `#screen=job&ref=JOB-000123` | Applying for a published job post |

All three send the same payloads to the same services the bot uses
(`tutor_profile_service`, `student_service`, `job_application_service`), so a
tutor who starts in Telegram and finishes in the Mini App — or the reverse — has
one application, not two.

---

## Architecture

```
Telegram (WebApp button)
   │  window.Telegram.WebApp.initData   ← signed by Telegram with the bot token
   ▼
mini-app/  (React + Vite + TypeScript, hash routing)
   │  X-Telegram-Init-Data: <signed init data>
   ▼
FastAPI  app/api/routes/miniapp.py
   │  HMAC-SHA256 validation, then identity from the validated user id
   ▼
services (tutor_profile_service, student_service, job_application_service)
```

**Identity is never a parameter.** No request carries a `telegram_user_id`. The
server derives the user from the validated Init Data (or the web-login token),
and every route resolves rows by that id alone. That is why there is no URL a
client can edit to reach somebody else's application.

---

## Environment variables

Copy `.env.example` to `.env`. The Mini App adds these:

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `MINI_APP_URL` | for the buttons | `""` | Public HTTPS URL of the built Mini App. The bot appends `#screen=…` to it. Empty ⇒ no WebApp buttons, and the bot works exactly as before. |
| `MINI_APP_JWT_SECRET` | for the web login | `""` | Signing secret for the web-login JWT. At least 32 random characters. Empty ⇒ the web login is disabled and only Telegram Init Data works. |
| `MINI_APP_CODE_TTL_SECONDS` | no | `600` | How long a one-time login code stays valid. |
| `MINI_APP_CODE_RATE_LIMIT` | no | `3` | Code requests allowed per client address per window. |
| `MINI_APP_CODE_RATE_WINDOW` | no | `3600` | That window, in seconds. |
| `MINI_APP_DEV_CODES` | local only | `false` | Returns the one-time code in the API response, because no bot is running locally to deliver it. **Never set in production** — it hands out logins. |

The frontend reads:

| Variable | Default | Meaning |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `/api` | API base. The default is right when a reverse proxy serves both from one origin. |
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8000` | Where the Vite dev server proxies `/api` during `npm run dev`. |

---

## BotFather setup

1. **Set the menu button URL.** This is what makes "Open in the app" appear
   when someone opens the bot:

   ```
   /setmenubutton
   ```

   Give it the Mini App URL. It may include `#screen=tutor` to open the
   application directly.

2. **Set the Mini App domain**, if you serve the app from your own domain:

   ```
   /setdomain
   ```

   Answer with the domain that hosts the app (`example.com`, not the full URL).
   Telegram only accepts a domain that serves the app over **HTTPS** with a
   valid certificate. `localhost` works for local development but not for a
   published bot.

3. **Open it from a chat.** Start the bot, tap the menu button, and the app
   opens with `window.Telegram.WebApp.initData` populated. That string is what
   the API validates — if it is empty, you are not in Telegram, and the app
   sends the visitor to the login page instead of loading any data.

---

## Local development

Telegram will not load a Mini App from `http://localhost`, so local work needs
a public HTTPS tunnel. Use `./tunnel.sh`, which starts a Cloudflare quick
tunnel (see "Why not ngrok?" below).

```bash
# 1. Start the backend
cd tedor-telegram
uvicorn app.api.server:app --reload --port 8000

# 2. Start the frontend, pointed at the backend
cd tedor-telegram/mini-app
npm install
npm run dev            # Vite on http://localhost:5180, proxying /api

# 3. In another terminal, expose the frontend over HTTPS
./tunnel.sh
```

Copy the URL `./tunnel.sh` prints into `MINI_APP_URL`, then point
`/setdomain` at the same domain. A quick tunnel picks a new hostname each time
it starts, so restart the bot whenever you restart the tunnel. For the login code to work
locally, either start the bot so it can deliver the code, or set
`MINI_APP_DEV_CODES=true` and read the code out of the API response.

### Why not ngrok?

The ngrok free tier serves a **browser interstitial** in front of every tunnel:

> You are about to visit `…`, served by `…`. You should only visit this website
> if you trust whoever sent the link to you. (ERR_NGROK_6024)

`curl` is not a browser and passes straight through it, so the tunnel looks
perfectly healthy from the command line while Telegram — whose WebView *is* a
browser — receives the warning page instead of the app, and reports "WebView
crashed". A Cloudflare quick tunnel has no interstitial, so what you test with
curl is what Telegram gets.

`./tunnel.sh` uses Cloudflare for this reason and verifies the served page with
a browser user agent before reporting success.

### "WebView crashed"

Telegram shows this when the page never arrived, or arrived and the bundle
threw before the first paint. It is not an exception you can read — check in
this order:

1. **Is the tunnel serving an interstitial rather than the app?** The check is
   a browser user agent, not a bare `curl`:

   ```bash
   curl -s -A "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 Chrome/107.0 Mobile Safari/537.36 TelegramAndroid" \
        https://<your-tunnel-host>/ | grep -o "<title>.*</title>"
   ```

   `<title>Tedor Tutors</title>` is correct. Anything mentioning ngrok,
   `ERR_NGROK`, or "not authorized" means the tunnel is showing a warning page —
   that is what crashes the WebView.

2. **Is the tunnel pointing at your dev server?** Vite is configured with
   `strictPort: true`, so if 5180 is taken it refuses to start rather than
   silently drifting to 5174 — where a tunnel still aimed at 5180 would be
   serving whatever *else* holds that port. Confirm the port:

   ```bash
   ss -ltnp | grep 5180
   ```

   **This project uses port 5180, not Vite's default 5173**, because another
   Vite app on this machine already holds 5173. `strictPort` is on, so if 5180
   is ever taken the server exits instead of quietly moving — which is what
   leaves a tunnel pointed at the wrong app.

3. **Does the tunnel host get a 403?** Vite refuses unknown `Host` headers by
   default, and behind a tunnel the host is your public hostname, not
   `localhost`. The 403 body contains no app, which is what Telegram renders
   as a crash. `allowedHosts: true` in `vite.config.ts` allows it. Check:

   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" -H "Host: <your-tunnel-host>" http://localhost:5180/
   ```

   `200` means the app is reachable; `403` means the host header is blocked.

4. **Does the tunnel serve HTTPS?** Telegram requires it. Check with
   `curl -I https://<your-tunnel-host>`.

5. **Is `MINI_APP_URL` HTTPS and matching?** A `http://` URL, or one with a
   trailing slash, produces a link Telegram will not open.

Run the tests:

```bash
cd tedor-telegram/mini-app
npm test          # vitest: unit and property tests
npm run typecheck # tsc --noEmit
```

---

## Deploying

```bash
cd mini-app
npm ci
npm run build      # emits dist/
```

`dist/` is a static bundle. Serve it from any static host, and serve `/api` from
the FastAPI backend — either from the same origin (simplest: no CORS to
configure) or from a different one (add both origins to `API_CORS_ORIGINS`).

The bot only needs `MINI_APP_URL` to point at wherever it ended up.

---

## API reference

Every route is under `/api/miniapp` and every one but `/auth/*` requires the
`X-Telegram-Init-Data` (or `X-Telegram-Token`) header.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/auth/code` | Issue a one-time login code (web fallback). |
| `POST` | `/auth/verify` | Exchange the code for a short-lived JWT. |
| `GET` | `/tutor/profile` | The tutor's own profile, public fields only. |
| `GET` | `/tutor/draft` | The saved draft, or 404. |
| `POST` | `/tutor/draft` | Save the draft. |
| `POST` | `/tutor/submit` | Submit the application; returns the TDR ID. |
| `GET` | `/parent/draft` | The saved parent draft, or 404. |
| `POST` | `/parent/draft` | Save the parent draft. |
| `POST` | `/parent/submit` | Submit the request; returns the request ID and matches. |
| `POST` | `/parent/select-tutor` | Choose one of the matched tutors. |
| `GET` | `/jobs/{ref}` | Job details, match score, or the outstanding profile fields. |
| `POST` | `/jobs/{ref}/confirm` | Submit the job application. |
| `POST` | `/documents/upload` | Upload one academic document (≤ 20 MB). Returns `pending: true` when staged (see below). |

Status codes worth knowing:

| Code | When |
| --- | --- |
| `401` | Missing or invalid credentials. |
| `403` | The resource belongs to another user. |
| `404` | Nothing to resume yet, or the resource does not exist. |
| `409` | A tutor application already exists for this account. |
| `413` | A file over 20 MB. |
| `422` | A value failed validation. |

---

## Authentication

**Inside Telegram (the normal case).** The WebApp button populates
`window.Telegram.WebApp.initData`. The app sends it as
`X-Telegram-Init-Data`; the server checks its HMAC-SHA256 signature against the
bot token, exactly as the Telegram Bot API specifies. A tampered string is a
`401`.

**Outside Telegram.** There is no Init Data, so the visitor signs in with the
phone number already on their application. The API issues a one-time code, and
the code buys a short-lived JWT carrying the same `telegram_user_id` the Init
Data would — which is why every route handler above the auth dependency is
unchanged by it.

Three things the web login deliberately does not do:

* it cannot create access — a code is only issued for a number already on file,
  and every failure answers identically, so it cannot be used to find out who
  has applied;
* it does not store the code — only its SHA-256;
* it does not last — the code is consumed on first use and the token expires
  (one hour by default). The token lives in `sessionStorage`, so it dies with
  the tab.

---

## Privacy

The Mini App shows a tutor their own profile and a parent their own request.
Neither ever sees anybody else's data: every route resolves rows by the
authenticated user id and answers `403` if a resolved row turns out to belong to
someone else.

Responses exclude every private field — phone, email, Telegram ids, storage
chat and message ids, voice file ids. The document screen shows which document
*types* are on file, never a Telegram file id.

### Documents uploaded before the application exists

Documents are the second-to-last step of the application, so they are uploaded
*before* the `Tutor` row that will own them exists. Rather than refuse those
uploads — which leaves the form impossible to finish — they are staged in
`pending_documents`, keyed by the authenticated Telegram user, and promoted to
real `tutor_documents` rows the moment the application is submitted.

Staged documents are scoped to the uploader exactly like everything else: an
upload can only ever be attached to the account that made it.

---

## Tests

Backend property tests live with the rest of the suite:

```bash
cd tedor-telegram
pytest tests/test_miniapp_isolation.py -v    # cross-user isolation
pytest tests/test_miniapp_missing_fields.py -v # the job screen's missing list
pytest tests/test_miniapp_web_login.py -v     # the non-Telegram login
```

Frontend tests use `fast-check`:

```bash
cd tedor-telegram/mini-app
npm test
```

| Property | Where |
| --- | --- |
| Cross-user request isolation | `tests/test_miniapp_isolation.py` |
| Missing fields match the Profile Completion Service | `tests/test_miniapp_missing_fields.py` |
| Form field count matches the onboarding steps | `src/forms/tutor/steps.test.ts` |
| Client validators match the server's ranges | `src/forms/tutor/validators.test.ts` |
| Location selection stores every required field | `src/components/LocationSearch.test.tsx` |
| Document validation rejects disallowed types | `src/components/FileUpload.test.ts` |
| Currency selection matches the country | `src/forms/parent/currency.test.ts` |
| Job URL construction embeds the reference | `tests/test_mini_app_urls.py` |