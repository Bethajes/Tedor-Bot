# Design Document — Tedor Tutors Telegram Bot

## Overview

The Tedor Tutors Telegram Bot is a standalone Python 3.12+ application with two independently runnable processes:

1. **The Bot Process** — a `python-telegram-bot` application handling all user-facing conversation flows (tutor onboarding, student requests, support, admin management).
2. **The API Process** — a FastAPI/Uvicorn REST server exposing structured tutor data to the main Tedor Tutors website.

Both processes share the same SQLite database and service layer. Telegram acts as the primary file/document archive; SQLite stores only structured metadata and Telegram references. The REST API is the clean integration boundary between this system and the website.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     TELEGRAM USERS                              │
│         (Tutors / Parents / Students / Admins)                  │
└───────────────────────────┬─────────────────────────────────────┘
                            │ Telegram MTProto
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                  PYTHON TELEGRAM BOT                            │
│   bot/handlers/  (start, tutor, student, support, admin)        │
│   bot/keyboards/ (inline keyboards)                             │
│   bot/states.py  (ConversationHandler states)                   │
└───────────────────────────┬─────────────────────────────────────┘
                            │ calls
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     SERVICE LAYER                               │
│  tutor_service  student_service  storage_service                │
│  notification_service  search_service                           │
└────────────┬──────────────────────────┬────────────────────────┘
             │ SQLAlchemy ORM            │ python-telegram-bot API
             ▼                          ▼
┌────────────────────────┐  ┌───────────────────────────────────┐
│   SQLite (structured   │  │  Telegram Private Channels        │
│   index + references)  │  │  - Tutor Storage Channel          │
│   data/tutor_index.db  │  │  - Student Request Channel        │
└────────────────────────┘  │  - Support Channel / Admin Chat   │
             ▲              └───────────────────────────────────┘
             │ SQLAlchemy ORM
             │
┌────────────────────────────────────────────────────────────────┐
│                  FASTAPI REST API                               │
│   api/server.py  api/routes/tutors.py  api/routes/health.py    │
│   api/schemas.py                                               │
└────────────────────────────────────────────────────────────────┘
             ▲
             │ HTTP/JSON
             ▼
┌────────────────────────────────────────────────────────────────┐
│           MAIN TEDOR TUTORS WEBSITE                            │
│           (independent application)                            │
└────────────────────────────────────────────────────────────────┘
```

**Key architecture rules:**
- SQLite is the query layer. All search operations hit SQLite, never Telegram.
- Telegram is the archive layer. All document/file persistence goes to Telegram.
- The REST API never exposes Telegram credentials, bot tokens, or internal IDs.
- Business logic lives in services, not in handlers or route files.

---

## Components and Interfaces

### Bot Layer (`app/bot/`)

**`handlers/start.py`**
- `/start` command → main menu inline keyboard
- `/help` command → command listing
- `/cancel` command → clears conversation state, returns to main menu
- Handles unrecognised messages outside conversation state

**`handlers/tutor.py`**
- `ConversationHandler` with ~20 states for full tutor application flow
- Back/Edit/Cancel/Continue navigation at every step
- Calls `tutor_service` and `storage_service` on submission

**`handlers/student.py`**
- `ConversationHandler` for student/parent request flow
- ETB/USD currency logic based on country selection
- Calls `student_service` on submission

**`handlers/support.py`**
- Category selection inline keyboard
- Message collection
- Calls `notification_service` to forward to support channel

**`handlers/admin.py`**
- `/admin` command — admin-only gate by Telegram user ID
- `/stats` command
- `/search_tutor` command
- Inline button callbacks for verify/reject/request-info on tutor notifications

### Keyboards (`app/bot/keyboards/`)
Each keyboard module returns `InlineKeyboardMarkup` or `ReplyKeyboardMarkup` objects. No raw button arrays in handler files.

**`keyboards/main.py`** — main menu, help menu  
**`keyboards/tutor.py`** — tutor application navigation, summary confirm  
**`keyboards/student.py`** — student request navigation, tutor match list  
**`keyboards/admin.py`** — admin menu, per-tutor action buttons, status buttons

### States (`app/bot/states.py`)
All `ConversationHandler` state constants defined here as an `IntEnum` or plain integer constants.

```python
class TutorStates(IntEnum):
    FULL_NAME = 1
    DISPLAY_NAME = 2
    PHONE = 3
    EMAIL = 4
    COUNTRY = 5
    CITY = 6
    PHOTO = 7
    SUBJECTS = 8
    LEVELS = 9
    TEACHING_MODE = 10
    LANGUAGES = 11
    EXPERIENCE = 12
    BIO = 13
    EDU_INSTITUTION = 14
    EDU_DEGREE = 15
    EDU_FIELD = 16
    EDU_YEAR = 17
    ETB_RATE = 18
    USD_RATE = 19
    AVAIL_DAYS = 20
    AVAIL_TIMES = 21
    TIMEZONE = 22
    DOC_CV = 23
    DOC_DEGREE = 24
    DOC_EXTRA = 25
    SUMMARY = 26
```

### Service Layer (`app/services/`)

**`tutor_service.py`**
- `create_tutor(data: dict) -> Tutor`
- `get_tutor_by_telegram_id(telegram_id: int) -> Tutor | None`
- `get_tutor_by_tdr_id(tdr_id: str) -> Tutor | None`
- `update_tutor_status(tdr_id: str, status: TutorStatus) -> Tutor`
- `search_tutors(filters: SearchFilters) -> list[Tutor]`
- `generate_tdr_id() -> str` — atomic sequential counter

**`student_service.py`**
- `create_student_request(data: dict) -> StudentRequest`
- `get_request_by_id(request_id: str) -> StudentRequest | None`

**`storage_service.py`**
- `store_tutor_record(bot, tutor: Tutor, documents: list[Document]) -> StorageResult`
- `update_tutor_record_status(bot, tutor: Tutor) -> None`
- `store_student_request(bot, request: StudentRequest) -> None`
- Interface-based design: `AbstractStorageService` with `TelegramStorageService` implementation, enabling future swap to S3/R2.

**`notification_service.py`**
- `notify_admin_new_tutor(bot, tutor: Tutor) -> None`
- `notify_tutor_verified(bot, tutor: Tutor) -> None`
- `notify_tutor_rejected(bot, tutor: Tutor, reason: str) -> None`
- `request_more_info(bot, tutor: Tutor, message: str) -> None`
- `notify_support_ticket(bot, ticket: SupportTicket) -> None`

**`search_service.py`**
- `match_tutors(request: StudentRequest) -> list[Tutor]`
- Deterministic scoring: exact subject match (+3), level match (+2), country match (+2), language match (+1), mode match (+1), within budget (+2)
- Returns top results sorted by score descending

### API Layer (`app/api/`)

**`server.py`** — FastAPI app factory, middleware (CORS, rate limiting), startup events  
**`routes/tutors.py`** — all `/api/tutors/*` routes  
**`routes/health.py`** — `/api/health`  
**`schemas.py`** — Pydantic request/response models

### Core (`app/`)

**`config.py`** — `Settings` class using `pydantic-settings` / `python-dotenv`  
**`database.py`** — SQLAlchemy engine, session factory, `Base`, `get_db()` dependency  
**`models.py`** — all SQLAlchemy ORM models  
**`main.py`** — bot application factory and startup

### Importer (`app/import_tutors.py`)
- CLI entry point: `python -m app.import_tutors --file tutors.csv [--dry-run] [--with-telegram]`
- Validates records, generates TDR IDs, prevents duplicates, logs per-row results

---

## Data Models

### SQLAlchemy ORM Models (`app/models.py`)

```python
class Tutor(Base):
    __tablename__ = "tutors"
    id: int (PK, autoincrement)
    public_tutor_id: str (unique, indexed)  # TDR-000001
    telegram_user_id: int (unique, indexed)
    telegram_storage_chat_id: int | None
    telegram_storage_message_id: int | None
    name: str
    display_name: str
    phone: str
    email: str
    country: str (indexed)
    city: str
    bio: str
    profile_photo_file_id: str | None
    etb_rate: float | None
    usd_rate: float | None
    experience_years: int
    teaching_mode: str (indexed)  # ONLINE | IN_PERSON | BOTH
    status: str (indexed)  # PENDING | UNDER_REVIEW | VERIFIED | REJECTED | SUSPENDED
    created_at: datetime
    updated_at: datetime

class TutorSubject(Base):
    __tablename__ = "tutor_subjects"
    id, tutor_id (FK, indexed), subject (indexed)

class TutorLevel(Base):
    __tablename__ = "tutor_levels"
    id, tutor_id (FK, indexed), level (indexed)

class TutorLanguage(Base):
    __tablename__ = "tutor_languages"
    id, tutor_id (FK), language

class TutorEducation(Base):
    __tablename__ = "tutor_education"
    id, tutor_id (FK), institution, degree, field, graduation_year

class TutorAvailability(Base):
    __tablename__ = "tutor_availability"
    id, tutor_id (FK), day, start_time, end_time, timezone

class TutorDocument(Base):
    __tablename__ = "tutor_documents"
    id, tutor_id (FK), document_type, telegram_file_id,
    telegram_file_unique_id, telegram_message_id, storage_chat_id, uploaded_at

class StudentRequest(Base):
    __tablename__ = "student_requests"
    id, public_request_id (unique), telegram_user_id, parent_name,
    phone, country, student_name, education_level, subject,
    preferred_language, mode, budget, schedule,
    additional_requirements, status, created_at, updated_at

class SupportTicket(Base):
    __tablename__ = "support_tickets"
    id, telegram_user_id, category, message, status, created_at
```

### Pydantic API Schemas (`app/api/schemas.py`)

```python
class TutorPublicResponse(BaseModel):
    id: str           # TDR-000001
    displayName: str
    country: str
    city: str
    bio: str
    subjects: list[str]
    levels: list[str]
    languages: list[str]
    teachingMode: str
    experienceYears: int
    etbRate: float | None
    usdRate: float | None
    verified: bool

class TutorSearchParams(BaseModel):
    subject: str | None
    level: str | None
    country: str | None
    language: str | None
    mode: str | None
    min_etb: float | None
    max_etb: float | None
    min_usd: float | None
    max_usd: float | None
    page: int = 1
    page_size: int = 20
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

**Property 1: TDR ID uniqueness**
*For any* sequence of N tutor creation calls, all resulting TDR IDs must be distinct.
**Validates: Requirements 3.1, 3.2**

**Property 2: TDR ID format**
*For any* generated TDR ID, the value must match the regular expression `^TDR-\d{6}$`.
**Validates: Requirements 3.1**

**Property 3: TDR ID monotonicity**
*For any* two tutors created in sequence, the later tutor's TDR ID sequence number must be strictly greater than the earlier tutor's.
**Validates: Requirements 3.2, 3.3**

**Property 4: Currency routing by country**
*For any* tutor application where country is "Ethiopia", the ETB rate field must be non-null; *for any* tutor application where country is not "Ethiopia", the USD rate field must be non-null.
**Validates: Requirements 2.4, 2.5, 6.2, 6.3**

**Property 5: Verified-only public API**
*For any* set of tutors in the database with mixed statuses, the `/api/tutors` and `/api/tutors/search` endpoints must return only tutors whose status is VERIFIED.
**Validates: Requirements 10.1, 10.3**

**Property 6: Search filter soundness**
*For any* search query with one or more filter parameters, every tutor returned must satisfy all specified filter criteria (subject contained in tutor's subjects, level contained in tutor's levels, country matches, etc.).
**Validates: Requirements 10.3, 7.1**

**Property 7: No private data in public API response**
*For any* VERIFIED tutor, the public API response must not contain the fields: `telegram_user_id`, `phone`, `email`, `telegram_storage_chat_id`, `telegram_storage_message_id`.
**Validates: Requirements 10.7**

**Property 8: Matching service score ordering**
*For any* student request and set of matching tutors, the list returned by the matching service must be sorted in descending order of score (no tutor with a lower score appears before one with a higher score).
**Validates: Requirements 7.2, 7.6**

**Property 9: Status transition safety**
*For any* tutor record, updating the status must only change the `status` field and `updated_at` timestamp; all other fields must remain unchanged.
**Validates: Requirements 5.2, 5.3**

**Property 10: Import idempotency**
*For any* import run with the same input file executed twice, the total number of tutor records in the database must equal the count after the first run (no duplicates created on re-run).
**Validates: Requirements 11.4**

**Property 11: API authentication**
*For any* request to a protected admin API endpoint without a valid `X-API-Secret` header, the response status must be 401.
**Validates: Requirements 10.6**

---

## Error Handling

| Failure Scenario | Handling Strategy |
|---|---|
| Telegram API timeout during storage | Retry up to 3 times with exponential backoff; mark record `storage_failed`; alert admin |
| SQLite write failure | Rollback transaction; return error to user via safe message; log at ERROR |
| User sends invalid input | Validate inline; prompt user with specific, friendly error; stay on same conversation step |
| User cancels mid-conversation | Clear state; return to main menu; no partial record written |
| Duplicate tutor submission | Detect by `telegram_user_id`; inform user of existing application; do not create second record |
| Admin action on unknown TDR ID | Return "Tutor not found" inline notification; log warning |
| API request with invalid secret | Return 401; log at WARNING without echoing the provided secret value |
| Import of duplicate record | Skip and log; continue processing remaining records |
| Bot process crash | `python-telegram-bot` application-level error handler logs and continues; single-user failure does not restart the process |

---

## Testing Strategy

### Property-Based Testing

**Library:** `hypothesis` (Python)

Each correctness property defined above is implemented as a single Hypothesis-based test. Tests are configured to run a minimum of 100 examples. Each test is annotated with a comment:

```
# Feature: tedor-telegram-bot, Property N: <property text>
# Validates: Requirements X.Y
```

### Unit Testing

**Framework:** `pytest` with `pytest-asyncio`

Unit tests cover:
- TDR ID generation (sequential, format, no duplicates)
- Tutor creation and retrieval via `tutor_service`
- Status update logic
- Search filter application
- Student request creation
- ETB/USD currency routing logic
- API authentication middleware
- Duplicate detection in importer

### Integration Points Tested

- `tutor_service` + SQLite round-trip (create → read → verify)
- `search_service` filter correctness with a seeded database
- REST API endpoint responses with a test SQLite database

### Test Database

All tests use an in-memory SQLite database (`sqlite:///:memory:`) to avoid filesystem side effects. Telegram API calls are mocked using `unittest.mock.AsyncMock`.
