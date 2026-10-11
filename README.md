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
7. [Environment variables](#environment-variables)
8. [Database initialisation](#database-initialisation)
9. [Running the bot](#running-the-bot)
10. [Running the REST API](#running-the-rest-api)
11. [REST API reference](#rest-api-reference)
12. [Tutor import](#tutor-import)
13. [Backup and export](#backup-and-export)
14. [Tests](#tests)
15. [Deployment notes](#deployment-notes)
16. [Known limitations of Telegram-as-storage](#known-limitations-of-telegram-as-storage)

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
│   └── import_tutors.py     bulk importer CLI
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

No credential is ever hardcoded in source: everything is read from the environment (or `.env`
via `python-dotenv`). `.env` is git-ignored.

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