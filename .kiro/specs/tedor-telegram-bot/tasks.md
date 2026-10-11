# Implementation Plan — Tedor Tutors Telegram Bot

- [x] 1. Project scaffold and configuration
  - Create the full `tedor-telegram/` directory structure as specified in the design
  - Create `requirements.txt` with pinned versions: `python-telegram-bot==21.*`, `fastapi`, `uvicorn[standard]`, `sqlalchemy`, `pydantic`, `pydantic-settings`, `python-dotenv`, `hypothesis`, `pytest`, `pytest-asyncio`, `httpx`
  - Create `.env.example` with all required environment variable keys and placeholder values
  - Create `.gitignore` ignoring `.env`, `data/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`
  - Create `run.py` that starts both the bot and the FastAPI server concurrently using `asyncio`
  - _Requirements: 12.4_

- [x] 2. Configuration and database foundation
  - [x] 2.1 Implement `app/config.py` with a `Settings` class using `pydantic-settings` loading `BOT_TOKEN`, `ADMIN_CHAT_ID`, `TUTOR_STORAGE_CHAT_ID`, `STUDENT_REQUEST_CHAT_ID`, `SUPPORT_CHAT_ID`, `API_SECRET`, `API_HOST`, `API_PORT` from environment variables
    - _Requirements: 12.4_
  - [x] 2.2 Implement `app/database.py` with SQLAlchemy engine (SQLite), `SessionLocal` factory, `Base`, and `get_db()` dependency; auto-create tables on startup
    - _Requirements: 12.1_
  - [x] 2.3 Implement all SQLAlchemy ORM models in `app/models.py`: `Tutor`, `TutorSubject`, `TutorLevel`, `TutorLanguage`, `TutorEducation`, `TutorAvailability`, `TutorDocument`, `StudentRequest`, `SupportTicket` with all fields and indexes as specified in the design
    - _Requirements: 11, 12.1_

- [x] 3. Tutor service and TDR ID generation
  - [x] 3.1 Implement `app/services/tutor_service.py` with `generate_tdr_id()` using an atomic sequential counter, `create_tutor()`, `get_tutor_by_telegram_id()`, `get_tutor_by_tdr_id()`, `update_tutor_status()`, and `search_tutors()` with filter support
    - _Requirements: 3.1, 3.2, 3.3, 5.2, 5.3_
  - [x] 3.2 Write property test for TDR ID uniqueness
    - **Property 1: TDR ID uniqueness** — for any N tutor creation calls, all resulting TDR IDs must be distinct
    - **Validates: Requirements 3.1, 3.2**
  - [x] 3.3 Write property test for TDR ID format
    - **Property 2: TDR ID format** — for any generated TDR ID, value must match `^TDR-\d{6}$`
    - **Validates: Requirements 3.1**
  - [x] 3.4 Write property test for TDR ID monotonicity
    - **Property 3: TDR ID monotonicity** — for any two sequential tutor creations, later ID sequence number must be strictly greater
    - **Validates: Requirements 3.2, 3.3**
  - [x] 3.5 Write property test for status transition safety
    - **Property 9: Status transition safety** — for any tutor with any status, after update_tutor_status() only status and updated_at change; all other fields remain identical
    - **Validates: Requirements 5.2, 5.3**

- [x] 4. Currency routing and student service
  - [x] 4.1 Implement `app/services/student_service.py` with `create_student_request()` including ETB/USD currency routing logic based on country field
    - _Requirements: 6.1, 6.2, 6.3, 6.4_
  - [x] 4.2 Write property test for currency routing by country
    - **Property 4: Currency routing by country** — for any tutor/request where country is "Ethiopia", ETB rate is non-null; for any other country, USD rate is non-null
    - **Validates: Requirements 2.4, 2.5, 6.2, 6.3**

- [x] 5. Checkpoint — Ensure all tests pass, ask the user if questions arise.

- [x] 6. Storage service
  - [x] 6.1 Define `AbstractStorageService` interface in `app/services/storage_service.py` with methods `store_tutor_record()`, `update_tutor_record_status()`, `store_student_request()`
    - _Requirements: 4.1, 4.2, 12.2_
  - [x] 6.2 Implement `TelegramStorageService` using the abstract interface — sends structured tutor record message with hashtags to `TUTOR_STORAGE_CHAT_ID`, uploads/forwards each document as a separate message, saves all Telegram message IDs / file IDs / chat IDs back to SQLite via `TutorDocument` records; includes retry logic (3 attempts, exponential backoff) and marks record `storage_failed` on final failure
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_
  - [x] 6.3 Write property test for storage service Telegram references saved to SQLite
    - **Property (from 4.3)** — for any tutor with N documents, after store_tutor_record() with a mocked bot, all N TutorDocument rows in SQLite must have non-null telegram_file_id and telegram_message_id
    - **Validates: Requirements 4.3**

- [x] 7. Notification and search services
  - [x] 7.1 Implement `app/services/notification_service.py` with `notify_admin_new_tutor()`, `notify_tutor_verified()`, `notify_tutor_rejected()`, `request_more_info()`, `notify_support_ticket()`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 8.3_
  - [x] 7.2 Implement `app/services/search_service.py` with `match_tutors()` using deterministic scoring (subject +3, level +2, country +2, language +1, mode +1, within budget +2), returning results sorted by score descending
    - _Requirements: 7.1, 7.2, 7.6_
  - [x] 7.3 Write property test for matching service score ordering
    - **Property 8: Matching service score ordering** — for any student request and set of matching tutors, the returned list must be sorted in descending order of score
    - **Validates: Requirements 7.2**
  - [x] 7.4 Write property test for search filter soundness
    - **Property 6: Search filter soundness** — for any search query with filter parameters, every returned tutor must satisfy all specified criteria
    - **Validates: Requirements 10.3, 7.1**

- [x] 8. Checkpoint — Ensure all tests pass, ask the user if questions arise.

- [x] 9. Bot states and keyboards
  - [x] 9.1 Define all `TutorStates`, `StudentStates`, `SupportStates`, `AdminStates` integer constants in `app/bot/states.py`
    - _Requirements: 2.1, 2.2_
  - [x] 9.2 Implement `app/bot/keyboards/main.py` (main menu, help), `keyboards/tutor.py` (application navigation, summary confirm), `keyboards/student.py` (request navigation, tutor match list), `keyboards/admin.py` (admin menu, per-tutor action buttons, status buttons)
    - _Requirements: 1.1, 2.2, 5.1, 9.1_

- [x] 10. Bot start and navigation handlers
  - [x] 10.1 Implement `app/bot/handlers/start.py` with `/start` (main menu), `/help` (command list), `/cancel` (clears state, returns to menu), and fallback handler for unrecognised messages
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 11. Tutor onboarding conversation handler
  - [x] 11.1 Implement `app/bot/handlers/tutor.py` as a `ConversationHandler` covering all 26 states defined in `TutorStates` — collects all required tutor fields, handles Back/Edit/Cancel/Continue at every step, displays application summary before submission, calls `tutor_service.create_tutor()`, `storage_service.store_tutor_record()`, and `notification_service.notify_admin_new_tutor()` on submission, sends tutor their TDR ID
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 4.1, 4.2, 5.1_
  - [x] 11.2 Implement `/my_application` command handler that looks up the tutor by Telegram user ID and displays current TDR ID and status
    - _Requirements: 2.8_

- [x] 12. Student request conversation handler
  - Implement `app/bot/handlers/student.py` as a `ConversationHandler` collecting all required student/parent request fields, applying ETB/USD logic based on country, calling `student_service.create_student_request()` and `storage_service.store_student_request()`, then calling `search_service.match_tutors()` and displaying ranked results with 👤 View Tutor / 📩 Request Tutor / 🔎 More Tutors buttons; handle no-match case
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 7.1, 7.2, 7.3, 7.4, 7.5_

- [x] 13. Support ticket handler
  - Implement `app/bot/handlers/support.py` with category selection keyboard, message collection, `SupportTicket` creation via `student_service`, and admin notification via `notification_service.notify_support_ticket()`; implement admin reply relay
  - _Requirements: 8.1, 8.2, 8.3, 8.4_

- [x] 14. Admin handler
  - [x] 14.1 Implement `app/bot/handlers/admin.py` with `/admin` command (admin-ID gate), `/stats` command, admin menu navigation, Pending Verification list with inline action buttons, Search Tutors flow, and Statistics display; implement inline callback handlers for Verify/Reject/Request-More-Info/Under-Review actions that call `tutor_service.update_tutor_status()` and `notification_service`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 9.1, 9.2, 9.3, 9.4, 9.5_
  - [x] 14.2 Write property test for admin authorization
    - **Property 11: API authentication / admin auth** — for any Telegram user ID not in the admin list, all admin command handlers must deny access
    - **Validates: Requirements 5.6, 9.1**

- [x] 15. Bot application wiring
  - Implement `app/bot/__init__.py` and `app/main.py` — create the `Application` instance, register all `CommandHandler`s, `ConversationHandler`s, and `CallbackQueryHandler`s, configure application-level error handler that logs without crashing, start polling
  - _Requirements: 13.4_

- [x] 16. Checkpoint — Ensure all tests pass, ask the user if questions arise.

- [x] 17. REST API — schemas and server
  - [x] 17.1 Implement `app/api/schemas.py` with `TutorPublicResponse`, `TutorListResponse`, `TutorSearchParams`, `HealthResponse` Pydantic models ensuring no private fields are present
    - _Requirements: 10.1, 10.7, 12.3_
  - [x] 17.2 Implement `app/api/server.py` — FastAPI app factory with CORS middleware, `X-API-Secret` authentication dependency for admin routes, startup event that initialises DB tables
    - _Requirements: 10.6, 13_

- [x] 18. REST API routes
  - [x] 18.1 Implement `app/api/routes/health.py` with `GET /api/health` returning system status
    - _Requirements: 10.5_
  - [x] 18.2 Implement `app/api/routes/tutors.py` with all tutor endpoints: `GET /api/tutors` (paginated, VERIFIED only), `GET /api/tutors/{tutor_id}` (VERIFIED or 404), `GET /api/tutors/search` (filtered, VERIFIED only), `GET /api/tutors/{tutor_id}/photo` (proxies Telegram file), `GET /api/subjects`, `GET /api/levels`, `GET /api/countries`, `GET /api/languages`, and protected `GET /api/tutors/{tutor_id}/documents`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.7, 10.8_
  - [x] 18.3 Write property test for verified-only public API
    - **Property 5: Verified-only public API** — for any database state with mixed-status tutors, GET /api/tutors and GET /api/tutors/search must return only VERIFIED tutors
    - **Validates: Requirements 10.1, 10.3**
  - [x] 18.4 Write property test for no private data in API response
    - **Property 7: No private data in public API response** — for any VERIFIED tutor, the serialized TutorPublicResponse must not contain telegram_user_id, phone, email, telegram_storage_chat_id, telegram_storage_message_id
    - **Validates: Requirements 10.7**
  - [x] 18.5 Write property test for API authentication
    - **Property 11: API authentication** — for any request to a protected admin endpoint without a valid X-API-Secret header, response status must be 401
    - **Validates: Requirements 10.6**

- [x] 19. Checkpoint — Ensure all tests pass, ask the user if questions arise.

- [x] 20. Tutor importer
  - Implement `app/import_tutors.py` as a CLI module supporting `--file`, `--dry-run`, and `--with-telegram` flags; validates each record (required fields), generates TDR IDs, detects duplicates by Telegram user ID or name+email, creates SQLite records, optionally sends Telegram storage records; prints per-row summary and totals
  - _Requirements: 11.1, 11.2, 11.3, 11.4_
  - [x] Write property test for import idempotency
    - **Property 10: Import idempotency** — for any valid input file run twice, total tutor count in DB equals count after first run
    - **Validates: Requirements 11.4**

- [x] 21. Logging configuration
  - Add structured Python logging to all services and handlers: INFO for application submission, tutor verification/rejection, student request creation, support ticket creation; ERROR for Telegram storage failures, DB write failures, API auth failures; ensure no tokens, secrets, or PII appear in log output
  - _Requirements: 13.1, 13.2, 13.3_

- [x] 22. README
  - Write `README.md` covering: project architecture, installation (Python 3.12+, venv, dependencies), BotFather setup, Telegram storage channel setup, admin configuration, environment variables reference, database initialization, running the bot, running FastAPI, API examples (curl), tutor import, backup/export, deployment notes, known limitations of Telegram-as-storage
  - _Requirements: 35_

- [x] 23. Final Checkpoint — Ensure all tests pass, ask the user if questions arise.
