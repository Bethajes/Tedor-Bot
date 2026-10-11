# Tedor Tutors Telegram Bot

Operational backbone for **Tedor Tutors** — a Python 3.12+ Telegram bot plus a FastAPI REST
API. The bot runs the whole business: tutor onboarding, document collection, verification,
parent/student tutor requests, customer support and admin operations. The REST API exposes
verified tutor data to the main Tedor Tutors website.

* **Telegram** is the document and file archive.
* **SQLite** stores only structured metadata plus Telegram references (message IDs, file IDs).
* The **REST API** is the only integration point with the website — the two systems never
  share a database.

---

## Contents

1. [Architecture](#architecture)
2. [Installation](#installation)
3. [BotFather setup](#botfather-setup)
4. [Telegram storage channel setup](#telegram-storage-channel-setup)
5. [Administrator configuration](#administrator-configuration)
6. [Job post generator (`/post`)](#job-post-generator-post)
7. [Tutoring locations](#tutoring-locations)
8. [The Mini App](#the-mini-app)
9. [Environment variables](#environment-variables)
10. [Database initialisation](#database-initialisation)
11. [Running the bot](#running-the-bot)
12. [Running the REST API](#running-the-rest-api)
13. [REST API reference](#rest-api-reference)
14. [Tutor import](#tutor-import)
15. [Backup and export](#backup-and-export)
16. [Tests](#tests)
17. [Deployment notes](#deployment-notes)
18. [Known limitations of Telegram-as-storage](#known-limitations-of-telegram-as-storage)

---

## Architecture

```
Telegram users
      │
      ▼
python-telegram-bot application          app/bot/
  handlers/  start · tutor · student · support · admin
  keyboards/ inline keyboards (no raw button arrays in handlers)
  states.py  ConversationHandler state constants
      │
      ▼
Service layer                            app/services/
  tutor_service      TDR IDs, CRUD, status, search
  student_service    student/parent requests + ETB/USD routing
  storage_service    AbstractStorageService → TelegramStorageService
  notification_service admin/tutor/support notifications
  search_service     deterministic scoring (no AI/ML in V1)
  location_service   name normalisation, haversine distance, proximity
  location_registry_service  the only writer of the locations table
  location_channel_service    curated Telegram channel → database sync
  matching_service    weighted ranking + per-criterion explanation
      │                       │
      ▼                       ▼
SQLite (metadata index)   Telegram private channels
data/tutor_index.db        tutor storage · student requests · support
      ▲
      ▼
FastAPI REST API                           app/api/
  server.py · routes/tutors.py · routes/health.py · schemas.py · auth.py
      ▲
      ▼
Main Tedor Tutors website (independent application)
```

Directory layout:

```
tedor-telegram/
├── app/
│   ├── api/                 REST API (server, schemas, auth, routes/)
│   ├── bot/                 bot wiring, states, handlers/, keyboards/
│   ├── services/            business logic (Telegram/DB boundary)
│   ├── config.py            pydantic-settings configuration
│   ├── database.py          engine, SessionLocal, Base, get_db
│   ├── enums.py             shared enums and value objects
│   ├── logging_config.py    structured logging + secret redaction
│   ├── main.py              bot-only entry point
│   ├── import_tutors.py     bulk importer CLI
│   └── import_locations.py  location channel backfill CLI
├── tests/                   unit + property based tests
├── data/                    SQLite database (created automatically)
├── .env.example
├── requirements.txt
├── run.py                   bot + API in one process
└── pytest.ini
```

Design rules enforced by the code:

* Business logic lives in services — never in handlers or route functions.
* Every Telegram file/message operation lives in `storage_service`, so the archive can be
  swapped for S3/R2 by implementing `AbstractStorageService`.
* Every database access goes through SQLAlchemy ORM models, so the engine can be swapped for
  PostgreSQL by changing `DATABASE_URL` only.
* Search and matching are deterministic — no AI/ML component in V1.

---

## Installation

Requires **Python 3.12+**.

```bash
cd tedor-telegram
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in the values
```

`python-telegram-bot==21.*`, FastAPI, Uvicorn, SQLAlchemy, Pydantic, pydantic-settings,
python-dotenv, Hypothesis, pytest, pytest-asyncio and httpx are installed by
`requirements.txt`.

---

## BotFather setup

1. Open Telegram, talk to **@BotFather**.
2. Send `/newbot` and follow the prompts (name + username ending in `bot`).
3. Copy the token into `.env`:

   ```dotenv
   BOT_TOKEN=123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

4. Optional but recommended BotFather settings:
   * `/setdescription` — what the bot offers.
   * `/setuserpic` — bot avatar.
   * `/setcommands` — `/start`, `/help`, `/cancel`, `/my_application`, `/support`.
5. While developing locally, disable privacy mode for group posting
   (`/setprivacy → Disable`) if you want the bot to read replies inside the ops chat.

---

## Telegram storage channel setup

Telegram is the primary archive, so the bot needs private chats it can post into.

| Purpose | Setting | Notes |
| --- | --- | --- |
| Tutor records + documents + admin notifications | `TUTOR_STORAGE_CHAT_ID` | Private channel/group, bot added as **administrator** |
| Student/parent tutor requests | `STUDENT_REQUEST_CHAT_ID` | Private channel/group, bot as administrator |
| Support tickets | `SUPPORT_CHAT_ID` | Private channel/group, bot as administrator |
| Published job posts (`/post`) | `JOB_POST_CHAT_ID` | Optional — falls back to the admin's own chat |

Steps:

1. Create a private channel (for example *Tedor Tutor Archive*) and a second private channel
   for student requests and support traffic.
2. Add the bot as an administrator with permission to post messages and upload documents.
3. Forward any message from the channel to `@RawDataBot` (or open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` while the bot polls) and read `chat.id`.
   Channel IDs are negative, for example `-1001234567890`.
4. Put the three IDs into `.env`.

Tutor records are written with searchable hashtags:

```
#TDR000042 #Mathematics #Ethiopia #Online #Pending
```

Every document is uploaded as a **separate message** attached to the record, and the returned
message ID, file ID, file unique ID and chat ID are stored in SQLite so the website and admins
can reference them later. If Telegram fails, the service retries three times with exponential
backoff, then marks the record `STORAGE_FAILED`, logs an error and alerts the admin — the
SQLite record is never deleted.

---

## Administrator configuration

Admin rights are granted by **Telegram user ID only** (never by username, which can change).

```dotenv
ADMIN_USER_IDS=111111111,222222222
```

How to find your Telegram user ID: message [@userinfobot](https://t.me/userinfobot), or read
`message.from.id` from the bot's update payload.

Admins get `/admin`, `/stats`, `/search_tutor`, `/post`, plus the inline buttons attached to every new
application notification (🔍 Review, ✅ Verify, ❌ Reject, 📩 Request More Information,
⏳ Under Review). Any non-admin trying those commands receives an access-denied message.

---

## Job post generator (`/post`)

Admins build a branded tutor job post with `/post`. The bot asks for exactly seven
variables — **location, student's level, tutor category, duration, frequency,
target subjects, compensation** — then shows the finished post with
✏️ Edit / ❌ Cancel / 📢 Publish. Nothing is ever published automatically.

The **Requirements** block is static template content. The admin is never asked
for requirements, and there is no `requirements` placeholder, field, prompt, or
database column, so the wording cannot drift.

The location is entered once and reused everywhere it appears: the bot converts it
to a Telegram hashtag automatically (`Ayat Tsebel` → `#Ayat_Tsebel`) and inserts
it into both the description and the requirements. Editing the location
regenerates the hashtag in every position; editing anything else leaves the
requirements untouched.

The template lives in `app/templates/tutor_job_post.py`, separate from the
conversation logic in `app/bot/handlers/job_post.py`.

Published posts are stored in the `job_posts` table together with their variables
and the Telegram `message_id`.

### Choosing the job's location

Step 1 of `/post` opens the same location selector a tutor uses, so a job's
place comes from the same directory rather than from free text. Type an area
and the bot searches by **standardised name and by alias**, so `Bole`,
`bole` and `Bole Kerfe` all reach the same place.

* The **quick-pick buttons** resolve against the directory too. A button whose
  label is not in the directory resolves to nothing rather than creating a row —
  a typo in a button list must not become a place in the directory.
* The **preview** names the standardised location, with its channel code and
  verification badge, before anything is published.
* If the place cannot be resolved, the preview says so plainly and points at the
  location channel. The post is still publishable, but matching will report the
  proximity as unknown — **coordinates are never guessed**.
* Exactly one canonical location id is stored on `job_posts.location_id` when a
  single place was picked. With several, the text keeps the full list and
  matching resolves by name rather than being handed an arbitrary one of them.

Everything else is untouched: the same template, the same channel publishing,
and the same Apply button.

### Reading the recommendations

After publishing (or on `/match <job id>`) the top five candidates are sent to
the administrators. For each one:

* name and TDR id, and the overall match percentage;
* the **closest** selected tutoring area — not the tutor's whole list, which
  used to be shown and answered nothing;
* the distance to it in kilometres, **labelled straight-line**;
* subject, grade and English suitability;
* the per-criterion score breakdown with the weight each criterion actually
  carried, and the reasons, weaknesses and missing information behind them.

Where the distance could not be measured, the card says **distance unknown**
and why, rather than implying closeness. No message ever describes the figure
as a driving distance or a travel time.

---

## The Mini App

The tutor application and the parent request both run as Telegram Mini App web
forms as well as bot conversations. Same fields, same order, same services
behind them — so a tutor who starts in Telegram and finishes in the app has one
application, not two.

Set `MINI_APP_URL` and the bot adds two WebApp buttons to its main menu
(*📝 Complete Tutor Application*, *👨‍👩‍👧 Request a Tutor*) and a third
(*📝 Apply in the App*) beside the job application buttons. Nothing existing is
removed: with no `MINI_APP_URL` the keyboards are byte-for-byte what they were.

The app itself lives in [`mini-app/`](mini-app) (React + Vite + TypeScript).
Authentication is the signed Init Data Telegram attaches to every WebApp
request; opened in a plain browser instead, it falls back to a one-time code
minted by the bot and exchanged for a short-lived token.

**See [MINIAPP.md](MINIAPP.md) for the full guide** — BotFather setup,
environment variables, local development over `ngrok`, the API reference, and
what the app deliberately refuses to do.

---

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `BOT_TOKEN` | ✅ | – | Token from @BotFather |
| `ADMIN_USER_IDS` | ✅ | – | Comma separated Telegram user IDs allowed to use admin features |
| `TUTOR_STORAGE_CHAT_ID` | ✅ | – | Ops/admin chat + tutor document archive |
| `STUDENT_REQUEST_CHAT_ID` | ✅ | – | Channel receiving student tutor requests |
| `SUPPORT_CHAT_ID` | ✅ | – | Channel receiving support tickets |
| `JOB_POST_CHAT_ID` | – | – | Channel receiving job posts published with `/post` (defaults to the admin's own chat) |
| `API_SECRET` | ✅ | – | Shared secret for protected API routes (`X-API-Secret`) |
| `API_HOST` | ➖ | `0.0.0.0` | REST API bind address |
| `API_PORT` | ➖ | `8000` | REST API port |
| `API_CORS_ORIGINS` | ➖ | – | Comma separated allowed origins, e.g. `https://tedortutors.com` |
| `DATABASE_URL` | ➖ | `sqlite:///data/tutor_index.db` | SQLAlchemy URL (swap for PostgreSQL) |
| `WEBSITE_URL` | ➖ | `https://tedortutors.com` | Target of the “Visit Website” button |
| `ETB_PER_USD` | ➖ | `130.0` | Reference rate used to preview the secondary currency |
| `STORAGE_MAX_ATTEMPTS` | ➖ | `3` | Telegram attempts (first try included) |
| `STORAGE_BACKOFF_SECONDS` | ➖ | `1.0` | Base delay for exponential backoff |
| `LOG_LEVEL` | ➖ | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `GEOAPIFY_API_KEY` | ➖ | – | [Geoapify](https://www.geoapify.com) key for place search. When unset, search is disabled and location entry falls back to manual input |
| `GEOAPIFY_BASE_URL` | ➖ | `https://api.geoapify.com/v1/geocode/autocomplete` | Overridable for tests or a replacement provider |
| `GEOAPIFY_TIMEOUT_SECONDS` | ➖ | `3.0` | Per-search timeout. A slow search is worse than none |
| `GEOAPIFY_CACHE_TTL_SECONDS` | ➖ | `86400` | How long a search result may be reused for the same query |
| `GEOAPIFY_ENABLED` | ➖ | `true` | Set `false` to disable search without removing the key |
| `GEOAPIFY_LIMIT` | ➖ | `8` | Results requested per search (the selector shows 5 at a time) |
| `LOCATION_SEARCH_RATE_LIMIT` | ➖ | `20` | Searches per user per window |
| `LOCATION_SEARCH_RATE_WINDOW` | ➖ | `60` | Window in seconds |
| `LOCATION_MANUAL_RATE_LIMIT` | ➖ | `3` | Manual registrations per user per window |
| `LOCATION_MANUAL_RATE_WINDOW` | ➖ | `3600` | Window in seconds |
| `LOCATION_API_RATE_LIMIT` | ➖ | `60` | REST searches per client per window |
| `LOCATION_API_RATE_WINDOW` | ➖ | `60` | Window in seconds |
| `LOCATION_CHANNEL_ID` | ➖ | – | The curated location channel (negative id). Empty disables the channel importer |
| `LOCATION_CHANNEL_TRUST_VERIFIED` | ➖ | `true` | Honour `Status: verified` in a channel post |
| `LOCATION_CHANNEL_ENFORCE_BOUNDS` | ➖ | `true` | Reject coordinates outside Ethiopia |
| `LOCATION_CHANNEL_IMPORT_SKIP_STALE` | ➖ | `true` | An old export must not roll back a live edit |
| `LOCATION_CHANNEL_NOTIFY_REJECTIONS` | ➖ | `false` | Message admins when a channel post is rejected |

### Mini App

The Mini App variables are documented in full in [MINIAPP.md](MINIAPP.md).

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `MINI_APP_URL` | ➖ | `` | Public HTTPS URL of the Mini App. The bot appends `#screen=tutor` / `#screen=parent` / `#screen=job&ref=JOB-…` to build its WebApp buttons. Empty ⇒ no Mini App buttons, and the bot behaves exactly as before |
| `MINI_APP_JWT_SECRET` | ➖ | `` | Signing secret for the web-login JWT (the non-Telegram browser). At least 32 random characters. Empty ⇒ the web login is disabled; Telegram Init Data still works |
| `MINI_APP_CODE_TTL_SECONDS` | ➖ | `600` | How long a one-time login code stays valid |
| `MINI_APP_CODE_RATE_LIMIT` | ➖ | `3` | Code requests per client per window |
| `MINI_APP_CODE_RATE_WINDOW` | ➖ | `3600` | Window in seconds |
| `MINI_APP_DEV_CODES` | ➖ | `false` | Local development only: returns the login code in the response, because no bot is running to deliver it. **Never set in production** |

Copy [`.env.example`](.env.example) to `.env` to see every variable with a
placeholder value.

No credential is ever hardcoded in source: everything is read from the environment (or `.env`
via `python-dotenv`). `.env` is git-ignored. The Geoapify key is additionally
redacted by the log filter, so it never reaches a log line or an error response.

---

## Tutoring locations

Locations used to be free text: a tutor typed "Ayat, Bole" and a job post
stored a hashtag. They are now records, which is what makes proximity a real
signal rather than a string comparison.

### How a location is found

1. **Search.** The tutor, parent or admin types an area and the bot offers
   suggestions from the Geoapify autocomplete endpoint, restricted to Ethiopia
   and biased toward Addis Ababa. Requests are debounced (0.45 s) so typing
   costs one request, not one per character.
2. **Manual entry.** If the provider does not know the area, it can be typed by
   hand. It is stored with `provider=MANUAL`, `verification_status=PENDING` and
   **no coordinates**, and is shown everywhere with an "unverified" marker.
3. **Duplicate detection.** A submission is checked against canonical names and
   admin-confirmed aliases before anything is created. An existing equivalent is
   offered instead of a second row.

The provider is replaceable: `app/services/geocoding/base.py` defines the
contract and `PROVIDERS` in `app/services/geocoding/__init__.py` is the registry.

### How a tutor picks their areas

The tutor onboarding flow asks for the areas they can teach in and opens the
shared selector (`app/bot/handlers/location_selector.py`):

1. **Search** by standardised name **or alias**, case-insensitively, so `Bole`,
   `bole` and `Bole Kerfe` all reach the same place. Suggestions are debounced,
   so typing costs one lookup rather than one per keystroke.
2. **Pick several.** Each choice appears as a chip, and the same place cannot be
   added twice. Every selected area carries a ❌ button, so a tutor who picked
   one by mistake can take it back — previously the removal code existed but no
   keyboard ever offered the button, which made a choice irreversible for the
   rest of the conversation.
3. **Not in the directory?** The *"Can't find your location? Add it manually."*
   button creates a `PENDING` row with **no coordinates**. That is a request for
   administrator review, never a set of invented coordinates, and the chip is
   marked ✍️ unverified wherever it appears.

A tutor's **home address is never treated as a tutoring area.** The bot asks
once, separately, and adds it only on an explicit yes — recorded with
`source=HOME_ADDRESS` so an admin can tell it apart from a deliberately
selected area.

### Data model

| Table | Purpose |
| --- | --- |
| `locations` | One canonical place, with its provider, coordinates and review state |
| `location_aliases` | Spellings an admin has confirmed point at a location |
| `tutor_locations` | A tutor's areas — unchanged text plus an optional `location_id` |
| `job_posts` | Existing `location` text plus an optional `location_id` |
| `student_requests` | The area a parent picked, plus an optional `location_id` |

Three rules the schema enforces:

* A tutor's **home address is not their tutoring areas**. It is never added to
  them automatically — the bot asks once, and adds it only on an explicit yes.
* `UNIQUE (normalized_name, city)` means the same place cannot be created twice
  from two spellings.
* A manual location's coordinates are null until an admin establishes one, and
  the matching engine reports proximity as *uncertain* rather than guessing.

### Admin review (`/locations`)

Queued behind the same admin check as the rest of the console:

* **Approve** / **Reject** — a rejected location keeps its links so existing
  jobs and tutors still resolve.
* **Merge** — moves tutor links, job links, parent-request links and aliases into
  one transaction. The merged-away row is kept as a `REJECTED` tombstone rather
  than deleted, so a report written before the merge still resolves.
* **Add a spelling** — an alias, so the next person searching that variant finds
  the same row.
* **Set coordinates** — accepted only inside Ethiopia, since a wrong position
  misleads every match that uses it.
* **Unmatched tutor areas** — the work list of free-text areas that matched
  nothing. Nothing here is changed unless an admin does it.

### Matching

`app/services/location_service.proximity()` resolves the location score. In
order of evidence: the same location is an exact match; both sides with
coordinates give a measured, approximate distance; anything else falls back to
the curated zone ladder and is flagged **uncertain**. Location is still a
preference, never a filter — a distant tutor is ranked and explained, not
excluded, and an online job gives location no weight at all.

### The curated location channel

The directory above can be maintained by hand, or by an administrator posting
to a dedicated Telegram channel and editing those posts as the city changes.
The channel is the **editable source of truth**; the `locations` table is the
queryable index that matching reads. Nothing on the job-posting path calls
Telegram to answer "where is this place?".

#### Why a listener *and* a file import

Telegram's Bot API has **no method that returns a channel's message history**.
`getChatHistory` does not exist in any published Bot API version, so a normal
bot token cannot read posts that are already in the channel. What a bot *can*
receive is `channel_post` and `edited_channel_post` updates — and only for
posts made after it became an administrator. The two halves are therefore
separate and named honestly:

* **Future posts and edits** are handled live by
  `app/bot/handlers/location_channel.py`.
* **Posts that already exist** are backfilled once from a channel export by
  `python -m app.import_locations --file result.json`.

Both call the same `sync_message`, so a record behaves identically however it
arrived. No personal-account session is used and no Telethon/Pyrogram
dependency is introduced: the platform keeps exactly one Telegram client, the
bot it already runs.

#### Setting the channel up

1. Create a channel, e.g. *Tedor Location Directory*.
2. Add the bot as an **administrator**. It needs to receive channel updates;
   it does not need permission to post, and giving it none is the safer choice.
3. Get the channel id (a negative number like `-1001234567890`) from
   [@userinfobot](https://t.me/userinfobot) or by forwarding a channel post and
   reading the id in the URL.
4. Put it in `.env`:

   ```
   LOCATION_CHANNEL_ID=-1001234567890
   ```

Leave the variable empty and the feature is simply off: the bot falls back to
the curated registry seeded at startup and behaves exactly as before.

#### The record format

One record per channel post. Keys are case-insensitive, `:` or `=` both work,
and the order does not matter.

```
ID: AA-0001
Name: Bole
Aliases: Bole Kerfe, Bole Medhanialem
Latitude: 8.9987
Longitude: 38.7890
Sub City: Bole
Woreda: 03
Status: verified
```

| Field | Required | Notes |
| --- | --- | --- |
| `ID` | **yes** | `AA-0001` form. The stable key: editing a post keeps the same ID and updates one row. A post without a usable ID is rejected rather than guessed at. |
| `Name` | **yes** | The standardised neighbourhood name. |
| `Aliases` | no | Comma- or semicolon-separated alternative spellings. |
| `Latitude` / `Longitude` | no | **Both or neither.** Out of range, `0, 0`, or outside Ethiopia is rejected. |
| `Sub City`, `Woreda` | no | Administrative divisions. |
| `Status` | no | `verified`, `pending` (default), or `disabled`. |

**Coordinates are never invented.** A record with none is stored without them,
and matching reports its proximity as uncertain rather than guessing. Aliases
the curated registry already folds onto the place are reported back to you
rather than stored as duplicates.

#### Backfilling existing posts

Telegram Desktop → open the channel → *Export channel history* → choose
**JSON** (not HTML) → unpack the `result` folder →

```
# validate everything first; nothing is written
python -m app.import_locations --file result/result.json --dry-run

# import
python -m app.import_locations --file result/result.json

# import, and deactivate anything the export no longer contains
python -m app.import_locations --file result/result.json --reconcile
```

The command prints a summary: how many records were created, updated, linked to
an existing place, left unchanged, deactivated, and rejected with a reason for
each rejection. Re-running it is safe and creates no duplicates.

#### Deleting and deactivating

Telegram sends **no update** when a channel post is deleted, so a running bot
cannot notice. `--reconcile` against a complete export is how deletions are
picked up.

Deactivation, never deletion. A location may already be referenced by a tutor's
areas and by a published job; dropping the row would silently rewrite history
for both. A deactivated place keeps its name, coordinates and references so
those stay explainable, but it stops appearing in search results and stops being
verifiable for a new selection. It still resolves by name for a tutor or job
that already points at it.

#### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `LOCATION_CHANNEL_ID` | *(empty)* | The channel to watch. Empty disables the feature. |
| `LOCATION_CHANNEL_TRUST_VERIFIED` | `true` | Honour `Status: verified`. Set `false` to route every import through the in-bot review queue. |
| `LOCATION_CHANNEL_ENFORCE_BOUNDS` | `true` | Reject coordinates outside Ethiopia. |
| `LOCATION_CHANNEL_IMPORT_SKIP_STALE` | `true` | Do not let an old export overwrite a record a live edit already refreshed. |
| `LOCATION_CHANNEL_NOTIFY_REJECTIONS` | `false` | Message admins about a rejected post. Off by default: the channel is public, and a stranger must not be able to make the bot message an admin. |

#### Distance calculation

`app/services/location_service.py` provides the one seam every distance passes
through:

* `calculate_distance_km(lat1, lon1, lat2, lon2)` — the public entry point.
  Validates every argument and returns `None` (never `0`, never a guess) when a
  coordinate is missing, unparseable or out of range.
* `nearest_area(job_location, tutor_locations)` — the closest of a tutor's
  selected areas, chosen by measured distance, returning the distance and the
  area together so the two can never describe different places.
* `DistanceCalculator` — a per-run memo, so scoring hundreds of tutors against
  one job computes each distinct pair once. Deliberately not a process-wide
  singleton: a long-running server must not accumulate an unbounded cache.

All of these are **straight-line (great-circle) distance**. They are an
approximate proximity signal, not a driving distance and not a travel time,
and every message that shows one says so. `DistanceCalculator` is the single
seam to replace if road distance or estimated travel time is ever added.

---

## Database initialisation

Tables are created automatically on bot start-up, API start-up and importer start-up
(`Base.metadata.create_all`). To initialise manually:

```bash
python -c "from app.database import init_db; init_db(); print('schema ready')"
```

The database lives at `data/tutor_index.db`. Tables: `tutors`, `tutor_subjects`,
`tutor_levels`, `tutor_languages`, `tutor_education`, `tutor_availability`,
`tutor_documents`, `student_requests`, `support_tickets`, `id_counters`.

To use PostgreSQL instead, change one line:

```dotenv
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/tedor
```

---

## Running the bot

Bot polling only:

```bash
python -m app.main
```

Bot **and** REST API in one process (shared service layer and database):

```bash
python run.py
```

Both processes are independent — for production it is usually better to run them separately
(see [Deployment notes](#deployment-notes)).

---

## Running the REST API

```bash
uvicorn app.api.server:app --host 0.0.0.0 --port 8000
```

Interactive documentation: <http://localhost:8000/docs>.

---

## REST API reference

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/api/health` | public | Status of the API and the database |
| `GET` | `/api/tutors` | public | Paginated **VERIFIED** tutors (public fields only) |
| `GET` | `/api/tutors/search` | public | Filter verified tutors by subject/level/country/language/mode/price |
| `GET` | `/api/tutors/{tdr_id}` | public | Full public profile, `404` when missing or not verified |
| `GET` | `/api/tutors/{tdr_id}/photo` | public | Proxies the stored Telegram photo (bot credentials never leave the server) |
| `GET` | `/api/tutors/{tdr_id}/documents` | `X-API-Secret` | Document references — protected |
| `GET` | `/api/subjects` | public | Distinct subjects from verified tutors |
| `GET` | `/api/levels` | public | Distinct education levels |
| `GET` | `/api/countries` | public | Distinct countries |
| `GET` | `/api/languages` | public | Distinct languages |

Public responses never contain Telegram user IDs, phone numbers, email addresses, chat IDs,
message IDs or bot tokens.

### Examples

```bash
# Health
curl http://localhost:8000/api/health

# First page of verified tutors
curl "http://localhost:8000/api/tutors?page=1&pageSize=20"

# Search
curl "http://localhost:8000/api/tutors/search?subject=Mathematics&country=Ethiopia&mode=ONLINE&max_etb=600"

# Single tutor
curl http://localhost:8000/api/tutors/TDR-000001

# Profile photo (proxied from Telegram)
curl -o tutor.jpg http://localhost:8000/api/tutors/TDR-000001/photo

# Protected endpoint
curl -H "X-API-Secret: $API_SECRET" \
     http://localhost:8000/api/tutors/TDR-000001/documents

# Distinct values
curl http://localhost:8000/api/subjects
curl http://localhost:8000/api/levels
curl http://localhost:8000/api/countries
curl http://localhost:8000/api/languages
```

### Locations

Search and registration are guarded by `X-API-Secret`: both spend something —
search spends the geocoding quota, registration writes a row a person has to
review. Reading a location by id is public and returns only that location's own
fields, never anything about the tutors who work there.

```bash
# Search — restricted to Ethiopia, Addis Ababa first (requires the secret)
curl -H "X-API-Secret: $API_SECRET" \
     "http://localhost:8000/api/locations/search?q=ayat"

# Register a location the geocoder could not find (requires the secret)
curl -X POST -H "X-API-Secret: $API_SECRET" \
     -H "Content-Type: application/json" \
     -d '{"name":"Woreda 12","city":"Addis Ababa","subCity":"Woreda 12","landmark":"near the local market"}' \
     http://localhost:8000/api/locations

# Read one location (public — location fields only, never tutor data)
curl http://localhost:8000/api/locations/42
```

```json
{
  "query": "ayat",
  "known": {
    "id": 12,
    "name": "Ayat Tsebel",
    "normalizedName": "ayat tsebel",
    "city": "Addis Ababa",
    "subCity": null,
    "landmark": null,
    "latitude": 9.0302,
    "longitude": 38.7540,
    "provider": "REGISTRY",
    "verificationStatus": "VERIFIED"
  },
  "suggestions": [
    {
      "placeId": "5ea7c3a1...",
      "name": "Ayat Tsebel",
      "formattedAddress": "Ayat Tsebel, Addis Ababa, Ethiopia",
      "city": "Addis Ababa",
      "subCity": "",
      "countryCode": "et",
      "latitude": 9.0302,
      "longitude": 38.7540
    }
  ],
  "error": null
}
```

Both guarded routes are rate limited (`429` with `Retry-After`), and a provider
outage returns `200` with an empty `suggestions` list and an `error` message
rather than failing the request.

Response shape (list):

```json
{
  "items": [
    {
      "id": "TDR-000001",
      "displayName": "Abebe B.",
      "country": "Ethiopia",
      "city": "Addis Ababa",
      "bio": "I teach maths and physics.",
      "subjects": ["Mathematics", "Physics"],
      "levels": ["Grade 9-10"],
      "languages": ["Amharic", "English"],
      "teachingMode": "ONLINE",
      "experienceYears": 5,
      "etbRate": 350.0,
      "usdRate": 3.5,
      "verified": true
    }
  ],
  "page": 1,
  "pageSize": 20,
  "total": 1,
  "totalPages": 1
}
```

---

## Tutor import

Bulk import of the existing tutors from a CSV, TSV or JSON file.

```bash
# Validate only — nothing is written
python -m app.import_tutors --file tutors.csv --dry-run

# Import into SQLite
python -m app.import_tutors --file tutors.csv

# Import and also archive each record in the Telegram channel
python -m app.import_tutors --file tutors.json --with-telegram
```

Behaviour:

* Every row is validated independently; a bad row is reported and the import continues.
* Each valid row gets a sequential TDR ID (`TDR-000001`, `TDR-000002`, …).
* Duplicates (same Telegram user ID, or same name + email) are skipped with a warning.
  Legacy rows without a Telegram ID get a stable synthetic ID derived from name + email, so
  re-running the import never creates duplicates.
* `--dry-run` writes nothing to SQLite or Telegram.
* `--with-telegram` sends the structured record to the storage channel; without it the import
  is database-only.
* Expected columns (aliases in brackets): `full_name` [`name`], `display_name`,
  `telegram_user_id` [`telegram_id`], `phone`, `email`, `country`, `city`, `bio`, `subjects`,
  `levels`, `languages`, `teaching_mode`, `experience_years`, `etb_rate`, `usd_rate`,
  `timezone`, `institution`, `degree`, `field`, `graduation_year`, `status`.
  Multi-value fields accept `;`, `|` or `,` as separators.
* Per-row results and totals are printed:

  ```
    created  Abebe Bekele -> TDR-000001
    skipped  Meron T. — duplicate telegram user id (existing TDR-000007)
    invalid  row 4 — missing field(s): city, teaching_mode
  import: 120 row(s) — 118 created, 1 skipped, 1 invalid, 0 archived to Telegram
  ```

---

## Backup and export

```bash
# Full backup (metadata index)
sqlite3 data/tutor_index.db ".backup 'backups/tutor_index-$(date +%F).db'"

# Export tutors for the website
sqlite3 -header -csv data/tutor_index.db \
  "SELECT public_tutor_id, display_name, country, city, etb_rate, usd_rate
   FROM tutors WHERE status = 'VERIFIED'" > verified-tutors.csv
```

Document files live in Telegram, so a backup consists of `data/tutor_index.db` **plus** the
Telegram channel export (Telegram Desktop → channel → Export chat history).

---

## Tests

```bash
pip install -r requirements.txt
python -m pytest -q
```

The suite mixes pytest unit tests with Hypothesis property tests (100 examples per property),
all against an in-memory SQLite database with mocked Telegram calls:

| Property | File |
| --- | --- |
| 1 – TDR ID uniqueness | `tests/test_tdr_id.py` |
| 2 – TDR ID format | `tests/test_tdr_id.py` |
| 3 – TDR ID monotonicity | `tests/test_tdr_id.py` |
| 4 – Currency routing by country | `tests/test_currency.py` |
| 5 – Verified-only public API | `tests/test_api.py` |
| 6 – Search filter soundness | `tests/test_search.py` |
| 7 – No private data in public API | `tests/test_api.py` |
| 8 – Matching score ordering | `tests/test_search.py` |
| 9 – Status transition safety | `tests/test_tdr_id.py` |
| 10 – Import idempotency | `tests/test_import.py` |
| 11 – API / admin authentication | `tests/test_api.py`, `tests/test_admin_auth.py` |
| 4.3 – Telegram references saved to SQLite | `tests/test_storage.py` |
| 13 – Logging and redaction | `tests/test_logging.py` |
| Job post template, flow, preview and publishing | `tests/test_job_post.py` |

> If a ROS environment exports `PYTHONPATH`, `pytest.ini` already disables the unrelated ROS
> plugins.

---

## Deployment notes

* **Process layout** — run the bot and the API as two supervised services
  (`systemd`, `supervisor`, Docker Compose). Both read the same `.env` and database.
* **Reverse proxy** — terminate TLS in front of the API; only allow `GET` publicly and keep
  `/api/tutors/*/documents` restricted by the API secret.
* **Webhook vs polling** — V1 uses long polling (`drop_pending_updates=True`). For a large
  user base, switch the updater to a webhook and put a TLS endpoint in front of it.
* **Migrations** — the schema is created by SQLAlchemy; introduce Alembic when the first
  production migration is needed.
* **Scaling** — SQLite handles a 1,000+ tutor workload comfortably. Move to PostgreSQL by
  changing `DATABASE_URL`; no query code changes.
* **Monitoring** — logs are structured (`time | level | logger | message`). The redaction
  filter guarantees bot tokens, API secrets, Telegram user IDs, phone numbers and emails never
  reach the log output.
* **Backups** — schedule `sqlite3 .backup` (see above) and export the Telegram channel
  periodically.
* **Health checks** — poll `/api/health`; it returns `200` with `"status": "ok"` only when the
  database answers.

---

## Known limitations of Telegram-as-storage

Telegram makes an excellent zero-cost archive, but it has hard limits worth knowing about:

1. **File size** — bots can send documents up to **50 MB**; larger files must be split or
   linked elsewhere.
2. **Not a real database** — files can be deleted by channel admins, and channel membership is
   the only access control. A lost archive cannot be reconstructed from SQLite alone (only the
   references survive).
3. **No versioning** — editing or replacing an archived document means sending a new message;
   the old message stays and both IDs are kept in SQLite.
4. **Searchability is limited** — records are located with hashtags and Telegram's own search.
   Complex queries (for example "all grade 9 maths tutors under 400 ETB") must go through the
   REST API, which is exactly why SQLite stores the structured metadata.
5. **Rate limits** — aggressive bulk imports or uploads will hit Telegram's flood limits. The
   storage service retries three times with exponential backoff and flags failures instead of
   crashing; bulk jobs should be run in batches.
6. **Retention and jurisdiction** — personal data (degrees, IDs, contact details) lives on a
   third-party platform. Obtain tutor consent, keep the channels private, and export to the
   internal archive for long-term storage.
7. **Photo proxying costs bandwidth** — `GET /api/tutors/{id}/photo` downloads the file from
   Telegram on each request; add a cache in front of it for a production website.