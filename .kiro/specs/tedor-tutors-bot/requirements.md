# Requirements Document

## Introduction

Tedor Tutors Telegram Bot is a standalone Python application that serves as an operational platform for the Tedor Tutors tutoring company. It handles tutor onboarding and document collection, tutor verification, parent/student tutor requests, customer support, and admin management — all through Telegram. The system uses Telegram as the primary document/file archive, SQLite as a structured metadata index, and exposes a REST API so the main Tedor Tutors website can fetch tutor records independently. The bot and website are fully decoupled applications.

## Glossary

- **Bot**: The Python Telegram Bot application that handles all user interactions.
- **Tutor**: A registered teaching professional who has submitted an application through the Bot.
- **Student/Parent**: A user who submits a tutor request through the Bot.
- **Admin**: A Telegram user whose ID is listed in the admin configuration and has elevated privileges.
- **TDR ID**: A permanent, sequential public tutor identifier in the format `TDR-000001`.
- **Tutor Storage Channel**: A private Telegram channel/group used to archive complete tutor application records and documents.
- **Student Request Channel**: A private Telegram channel/group used to archive incoming student/parent tutor requests.
- **Support Channel**: A private Telegram channel/group used to route support tickets to admins.
- **SQLite Index**: The local SQLite database storing structured metadata and Telegram references; no large files are stored here.
- **Telegram File ID**: A Telegram-native identifier for a file uploaded to the platform; used to retrieve files without downloading them locally.
- **REST API**: The FastAPI HTTP server that exposes tutor data to the main Tedor Tutors website.
- **API Secret**: A shared secret token used to authenticate requests to protected REST API endpoints.
- **Conversation State**: The current step in a multi-step bot interaction (e.g., onboarding wizard).
- **Teaching Mode**: One of `ONLINE`, `IN_PERSON`, or `BOTH`.
- **Status**: The verification state of a tutor: `PENDING`, `UNDER_REVIEW`, `VERIFIED`, `REJECTED`, or `SUSPENDED`.
- **ETB**: Ethiopian Birr — the default currency for Ethiopian clients.
- **USD**: US Dollar — the default currency for international clients.
- **Dry-run**: An import mode that validates and previews records without writing to the database.
- **Idempotency**: The property that performing an operation multiple times produces the same result as performing it once.

---

## Requirements

### Requirement 1 — Bot Entry Point and Main Menu

**User Story:** As a Telegram user, I want to see a clear welcome menu when I start the bot, so that I can navigate to the service I need.

#### Acceptance Criteria

1. WHEN a user sends `/start`, THE Bot SHALL display a welcome message and an inline keyboard with buttons: 👨‍🏫 Become a Tutor, 🔎 Find a Tutor, 📩 Contact Us, ℹ️ How It Works, 🌐 Visit Website.
2. WHEN a user sends `/help`, THE Bot SHALL display a list of available commands with brief descriptions.
3. WHEN a user sends `/cancel` during any active conversation, THE Bot SHALL terminate the current conversation state and return the user to the main menu.
4. WHEN a user sends `/my_application`, THE Bot SHALL display the tutor's current application status if one exists, or prompt the user to start an application if none exists.
5. WHEN a user sends `/find_tutor`, THE Bot SHALL begin the Find a Tutor flow.
6. WHEN a user sends `/contact`, THE Bot SHALL begin the Contact Us flow.

---

### Requirement 2 — Tutor Onboarding

**User Story:** As a prospective tutor, I want to complete a guided application through Telegram, so that I can register with Tedor Tutors and be discoverable by students.

#### Acceptance Criteria

1. WHEN a user selects 👨‍🏫 Become a Tutor, THE Bot SHALL begin a step-by-step conversational application collecting: full name, preferred/display name, phone number, email, country, city, profile photo, subjects, education levels, teaching mode, languages, years of experience, short professional biography, education records (institution, degree, field, graduation year — multiple allowed), ETB hourly rate, USD hourly rate, available days, available time ranges, timezone, CV/resume document, degree/certificate document, and other supporting documents.
2. WHEN a tutor is at any step of the application, THE Bot SHALL display ⬅️ Back, ✏️ Edit, ❌ Cancel, and ✅ Continue navigation controls so the tutor can move backward, edit previous answers, or cancel without restarting.
3. WHEN a tutor submits invalid input for a field (e.g., non-numeric rate, empty name), THE Bot SHALL display an inline error message and re-prompt for the same field without losing previous answers.
4. WHEN a tutor uploads a document or photo, THE Bot SHALL accept Telegram document and photo message types and store the resulting Telegram file ID and message ID.
5. WHEN a tutor reaches the end of the application, THE Bot SHALL display a complete summary showing all collected fields and the status of each required document (✓ received or ✗ missing), along with ✏️ Edit, ❌ Cancel, and ✅ Submit Application buttons.

---

### Requirement 3 — Tutor ID Generation

**User Story:** As a system operator, I want every tutor to have a unique permanent identifier, so that tutors can be referenced consistently across Telegram and the website.

#### Acceptance Criteria

1. WHEN a tutor application is submitted, THE Bot SHALL assign a unique TDR ID in the format `TDR-NNNNNN` (zero-padded six-digit sequential integer, e.g., `TDR-000001`).
2. THE Bot SHALL guarantee that no two tutors share the same TDR ID.
3. THE Bot SHALL guarantee that a TDR ID assigned to a tutor is never reused or reassigned, even if the tutor record is deleted.
4. THE Bot SHALL derive the TDR ID from an internal auto-increment counter, not from the Telegram user ID.

---

### Requirement 4 — Telegram Storage

**User Story:** As a system operator, I want tutor applications and documents stored in a private Telegram channel, so that all records are archived with zero local file storage.

#### Acceptance Criteria

1. WHEN a tutor application is submitted, THE Bot SHALL send a structured text record to the configured Tutor Storage Channel containing: TDR ID, name, country, city, subjects, education levels, teaching mode, languages, education summary, experience, ETB rate, USD rate, availability, status (`PENDING`), and hashtags for the tutor ID, subjects, country, teaching mode, and status.
2. WHEN a tutor application is submitted, THE Bot SHALL upload/forward the tutor's profile photo, CV, degree/certificate, and any additional supporting documents to the Tutor Storage Channel as separate messages.
3. WHEN the Tutor Storage Channel messages are sent, THE Bot SHALL store the resulting Telegram message IDs, chat ID, and file IDs in the SQLite index for future retrieval.
4. IF any step of the Telegram storage process fails, THEN THE Bot SHALL log the error and halt the submission without creating a duplicate tutor record.
5. THE Bot SHALL implement idempotency so that retrying a failed submission does not create a second tutor record.

---

### Requirement 5 — SQLite Metadata Index

**User Story:** As a system operator, I want all structured tutor metadata stored in SQLite, so that searches and API responses are fast without querying Telegram.

#### Acceptance Criteria

1. THE Bot SHALL maintain SQLite tables for: Tutor, TutorSubject, TutorLevel, TutorLanguage, TutorEducation, TutorAvailability, TutorDocument, StudentRequest, and SupportTicket, each with the fields specified in Section 11 of the project specification.
2. THE Bot SHALL create indexes on: tutor status, country, subjects, education levels, teaching mode, ETB rate, USD rate, and Telegram user ID.
3. WHEN the application starts, THE Bot SHALL automatically initialize the SQLite database and create all tables if they do not exist.
4. THE Bot SHALL store only structured/searchable metadata and Telegram references in SQLite; binary file content SHALL NOT be stored in SQLite.
5. THE Bot SHALL use SQLAlchemy ORM models so the database can be migrated to PostgreSQL without changing business logic.

---

### Requirement 6 — Tutor Verification

**User Story:** As an admin, I want to review and verify tutor applications through Telegram, so that only qualified tutors appear in public search results.

#### Acceptance Criteria

1. WHEN a tutor application is submitted, THE Bot SHALL send an admin notification to the Admin Chat containing: TDR ID, name, subjects, country, teaching mode, ETB rate, and inline buttons: 🔍 Review, ✅ Verify, ❌ Reject, 📩 Request More Information.
2. WHEN an admin presses ✅ Verify, THE Bot SHALL update the tutor's status to `VERIFIED` in SQLite and notify the tutor via Telegram message.
3. WHEN an admin presses ❌ Reject, THE Bot SHALL update the tutor's status to `REJECTED` in SQLite and notify the tutor via Telegram message.
4. WHEN an admin presses 📩 Request More Information, THE Bot SHALL prompt the admin to type the information request, then send that message to the tutor on behalf of Tedor Tutors.
5. WHEN a tutor receives a request for more information, THE Bot SHALL associate the tutor's reply with the corresponding tutor application record.
6. THE Bot SHALL restrict admin actions (Verify, Reject, Request More Information) to Telegram user IDs listed in the admin configuration.
7. WHEN an admin updates a tutor's status to any value in (`PENDING`, `UNDER_REVIEW`, `VERIFIED`, `REJECTED`, `SUSPENDED`), THE Bot SHALL persist the new status and record the update timestamp.

---

### Requirement 7 — Student/Parent Tutor Request

**User Story:** As a parent or student, I want to submit a tutor request through Telegram, so that Tedor Tutors can match me with a suitable tutor.

#### Acceptance Criteria

1. WHEN a user selects 🔎 Find a Tutor, THE Bot SHALL begin a guided flow collecting: parent/student name, student name, country, education level, subject, preferred language, teaching mode preference (online/in-person/both), budget, preferred schedule, additional requirements, and contact information.
2. WHEN a user specifies Ethiopia as their country, THE Bot SHALL prompt for budget in ETB.
3. WHEN a user specifies a country other than Ethiopia, THE Bot SHALL prompt for budget in USD.
4. WHEN the request is complete, THE Bot SHALL create a StudentRequest record in SQLite and forward it to the Student Request Channel.
5. WHEN a StudentRequest is created, THE Bot SHALL assign a unique public request ID.

---

### Requirement 8 — Tutor Matching and Search

**User Story:** As a parent or student, I want to see a ranked list of verified tutors that match my requirements, so that I can choose a suitable tutor.

#### Acceptance Criteria

1. WHEN a student request is submitted, THE Bot SHALL search SQLite for VERIFIED tutors matching: subject, education level, country, language, teaching mode, and price range.
2. THE Bot SHALL return results ranked by relevance score using deterministic filtering and scoring; no AI or ML components are required.
3. WHEN results are returned, THE Bot SHALL display for each tutor: display name, verified badge, subjects, education levels, teaching mode, and rate.
4. WHEN a match is displayed, THE Bot SHALL show inline buttons: 👤 View Tutor, 📩 Request Tutor, 🔎 More Tutors.
5. WHEN no tutors match the criteria, THE Bot SHALL inform the user and suggest broadening the search criteria.

---

### Requirement 9 — Contact Us and Support Tickets

**User Story:** As a user, I want to contact Tedor Tutors support through Telegram, so that I can get help with my inquiry.

#### Acceptance Criteria

1. WHEN a user selects 📩 Contact Us, THE Bot SHALL display category options: 💬 General Question, 👨‍🏫 Tutor Application, 👨‍🎓 Finding a Tutor, 💳 Payment, 🛠 Technical Problem, 👤 Talk to Admin.
2. WHEN a user selects a category and submits a message, THE Bot SHALL create a SupportTicket in SQLite and forward it to the Support Channel.
3. WHEN a support ticket is created, THE Bot SHALL confirm receipt to the user with a ticket reference.
4. WHEN an admin replies to a support ticket message in the Support Channel, THE Bot SHALL relay the admin's reply back to the original user.

---

### Requirement 10 — Admin Interface

**User Story:** As an admin, I want a dedicated Telegram admin interface, so that I can manage tutors, view statistics, and handle requests efficiently.

#### Acceptance Criteria

1. WHEN a user sends `/admin`, THE Bot SHALL verify that the user's Telegram ID is in the admin list; if not, THE Bot SHALL deny access with a generic error message.
2. WHEN an admin accesses `/admin`, THE Bot SHALL display an admin menu with options: 👨‍🏫 Tutors, 📩 Student Requests, ⏳ Pending Verification, ✅ Verified Tutors, ❌ Rejected, 🔎 Search Tutors, 📊 Statistics, 📤 Export, ⚙️ Settings.
3. WHEN an admin selects 📊 Statistics, THE Bot SHALL display: total tutors, pending tutors, verified tutors, rejected tutors, suspended tutors, total student requests, and total support tickets.
4. WHEN an admin sends `/search_tutor`, THE Bot SHALL allow searching tutors by name, subject, country, or TDR ID and return matching records.
5. WHEN an admin sends `/stats`, THE Bot SHALL display the same statistics as the 📊 Statistics menu option.
6. WHEN an admin selects 📤 Export, THE Bot SHALL generate and send a CSV export of the tutor index.

---

### Requirement 11 — REST API

**User Story:** As a website developer, I want a REST API that returns verified tutor data, so that the Tedor Tutors website can display tutors without connecting to Telegram or the bot database directly.

#### Acceptance Criteria

1. THE REST API SHALL expose: `GET /api/health`, `GET /api/tutors`, `GET /api/tutors/{tutor_id}`, `GET /api/tutors/search`, `GET /api/subjects`, `GET /api/levels`, `GET /api/countries`, `GET /api/languages`, `GET /api/tutors/{tutor_id}/photo`, `GET /api/tutors/{tutor_id}/documents`.
2. WHEN `GET /api/tutors` is called without admin authentication, THE API SHALL return only tutors with status `VERIFIED`, excluding any internal Telegram IDs, bot tokens, storage chat IDs, storage message IDs, private phone numbers, and private email addresses.
3. WHEN `GET /api/tutors/{tutor_id}` is called, THE API SHALL return the tutor object as specified in Section 19 of the project specification for VERIFIED tutors.
4. WHEN `GET /api/tutors/search` is called with query parameters (subject, level, country, language, mode, min_rate, max_rate), THE API SHALL return filtered, verified tutors matching all supplied criteria.
5. WHEN a request is made to a protected endpoint without a valid `API_SECRET` token, THE API SHALL return HTTP 401 Unauthorized.
6. THE REST API SHALL run as a separate process from the Telegram Bot and share access to the same SQLite database file.
7. WHEN `GET /api/tutors/{tutor_id}/photo` is called for a VERIFIED tutor, THE API SHALL return or proxy the tutor's profile photo using the stored Telegram file ID without exposing the bot token to the caller.

---

### Requirement 12 — Tutor Import System

**User Story:** As a system operator, I want to bulk-import existing tutor records from CSV or JSON, so that the 1,000+ existing Tedor Tutors tutors can be migrated without manual re-entry.

#### Acceptance Criteria

1. WHEN `python -m app.import_tutors` is run with a valid CSV or JSON file, THE Importer SHALL validate each record, generate TDR IDs for new tutors, prevent duplicates, and create SQLite records.
2. WHEN `python -m app.import_tutors --dry-run` is run, THE Importer SHALL validate and preview all records and report what would be imported without writing to the database.
3. IF a record in the import file is missing required fields or contains invalid data, THEN THE Importer SHALL report the error for that record and continue processing the remaining records.
4. THE Importer SHALL prevent creating duplicate tutor records for the same Telegram user ID or email address.
5. WHERE Telegram storage channel credentials are available, THE Importer SHALL optionally create Telegram storage records for imported tutors.

---

### Requirement 13 — Security and Configuration

**User Story:** As a system operator, I want all secrets and credentials managed through environment variables, so that no sensitive data is ever committed to source control.

#### Acceptance Criteria

1. THE Bot SHALL load all secrets (`BOT_TOKEN`, `ADMIN_CHAT_ID`, `TUTOR_STORAGE_CHAT_ID`, `STUDENT_REQUEST_CHAT_ID`, `SUPPORT_CHAT_ID`, `API_SECRET`) exclusively from environment variables via a `.env` file or system environment.
2. THE Bot SHALL provide a `.env.example` file listing all required environment variables with placeholder values and no real secrets.
3. THE Bot SHALL never log `BOT_TOKEN`, `API_SECRET`, admin IDs, storage chat IDs, passwords, or private documents.
4. THE REST API SHALL authenticate protected endpoints using the `API_SECRET` value; the secret SHALL NOT be embedded in source code.
5. THE Bot SHALL validate and sanitize all user-provided input before processing or storing it.

---

### Requirement 14 — Logging and Error Handling

**User Story:** As a system operator, I want comprehensive logging and graceful error handling, so that the bot remains stable and failures are diagnosable without exposing sensitive data.

#### Acceptance Criteria

1. THE Bot SHALL log: application submission, tutor verification, tutor rejection, student request creation, support ticket creation, Telegram storage errors, and API errors, using Python's standard `logging` module.
2. IF a Telegram API call fails during document upload or message sending, THEN THE Bot SHALL log the error with context and surface a user-friendly error message without crashing the bot process.
3. IF a database operation fails, THEN THE Bot SHALL log the error and return a safe error response without exposing internal details.
4. WHEN a user cancels a conversation or lets it expire, THE Bot SHALL clean up the conversation state and return the user to the main menu without raising an unhandled exception.
5. THE Bot SHALL handle duplicate submission attempts by detecting existing records and returning a safe idempotent response rather than raising an error.

---

### Requirement 15 — Serialization and Data Portability

**User Story:** As a system operator, I want tutor records to be serializable to and from structured formats, so that data can be exported, imported, and validated programmatically.

#### Acceptance Criteria

1. WHEN a tutor record is serialized to JSON (e.g., for the REST API response), THE system SHALL produce a valid JSON object matching the schema defined in Section 19 of the project specification.
2. WHEN a JSON tutor record is deserialized back into a Tutor model, THE system SHALL produce an object equivalent to the original.
3. WHEN a CSV export is generated, THE system SHALL produce a well-formed CSV that can be re-imported by the import system.
4. THE system SHALL use Pydantic models for all API request and response validation, ensuring type safety and automatic error reporting for malformed inputs.
