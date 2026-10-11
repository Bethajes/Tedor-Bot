# Requirements Document

## Introduction

Tedor Tutors Telegram Bot is a standalone Python application that serves as the operational backbone for Tedor Tutors — a professional tutoring platform with 1,000+ tutors. The system handles tutor onboarding, document collection, verification, parent/student tutor requests, customer support, and admin management entirely through Telegram. A separate FastAPI REST layer exposes structured tutor data to the main Tedor Tutors website, keeping the two systems fully independent with no shared database.

Telegram acts as the primary document and file archive. SQLite stores only structured metadata and Telegram references. The REST API is the clean boundary between the Telegram bot system and the website.

## Glossary

- **Bot**: The Python Telegram Bot application that handles all user interactions.
- **Tutor**: A teaching professional who registers through the Telegram bot.
- **Parent/Student**: A user seeking a tutor through the Telegram bot.
- **Admin**: A privileged Telegram user who manages verifications, reviews, and operations.
- **TDR ID**: A permanent, human-readable tutor identifier in the format TDR-000001.
- **Tutor Storage Channel**: A private Telegram channel/group used as the primary archive for tutor application records and documents.
- **Student Request Channel**: A private Telegram channel/group where student tutor requests are forwarded.
- **Support Channel**: A private Telegram channel/group for customer support tickets.
- **SQLite Index**: The local SQLite database storing structured tutor metadata and Telegram file/message references.
- **Telegram File ID**: A Telegram-assigned identifier for an uploaded file that can be used to retrieve the file without downloading it locally.
- **ETB**: Ethiopian Birr — the default currency for Ethiopian clients.
- **USD**: United States Dollar — the default currency for international clients.
- **Teaching Mode**: One of ONLINE, IN_PERSON, or BOTH.
- **Tutor Status**: One of PENDING, UNDER_REVIEW, VERIFIED, REJECTED, or SUSPENDED.
- **Conversation State**: The current step in a multi-step Telegram conversation flow.
- **REST API**: The FastAPI server exposing structured tutor data to the main website.
- **API Secret**: A shared secret used to authenticate requests to protected REST API endpoints.
- **Dry Run**: An import mode that validates records without writing to the database.

---

## Requirements

### Requirement 1

**User Story:** As a user opening the bot for the first time, I want to see a clear welcome menu, so that I understand what the bot can do and where to navigate.

#### Acceptance Criteria

1. WHEN a user sends `/start`, THE Bot SHALL display a welcome message and an inline keyboard with buttons: 👨‍🏫 Become a Tutor, 🔎 Find a Tutor, 📩 Contact Us, ℹ️ How It Works, 🌐 Visit Website.
2. WHEN a user sends `/help`, THE Bot SHALL display a list of available commands and a brief description of each.
3. WHEN a user sends `/cancel` during any active conversation, THE Bot SHALL terminate the current conversation state and return the user to the main menu.
4. WHEN a user sends an unrecognised message outside of an active conversation state, THE Bot SHALL respond with a prompt to use the main menu or type `/start`.

---

### Requirement 2

**User Story:** As a prospective tutor, I want to complete a guided application through Telegram, so that I can register with Tedor Tutors without visiting a website.

#### Acceptance Criteria

1. WHEN a user selects 👨‍🏫 Become a Tutor, THE Bot SHALL begin a multi-step conversational application and collect: full name, display name, phone number, email, country, city, profile photo, subjects, education levels, teaching mode, languages, years of experience, short biography, education records (institution, degree, field, graduation year), ETB hourly rate, USD hourly rate, available days, available time ranges, timezone, CV/resume document, degree/certificate document, and optional supporting documents.
2. WHEN a tutor is on any step of the application, THE Bot SHALL display ⬅️ Back, ✏️ Edit, ❌ Cancel, and ✅ Continue navigation buttons to allow the tutor to move backward, edit previous entries, or cancel without restarting.
3. WHEN a tutor selects ⬅️ Back, THE Bot SHALL return to the previous step while preserving all previously entered data.
4. WHEN a tutor's country is Ethiopia, THE Bot SHALL present the ETB hourly rate field as the primary pricing field.
5. WHEN a tutor's country is not Ethiopia, THE Bot SHALL present the USD hourly rate field as the primary pricing field.
6. WHEN a tutor completes all application steps, THE Bot SHALL display a full application summary showing all collected fields and documents before submission.
7. WHEN a tutor selects ✅ Submit Application from the summary screen, THE Bot SHALL submit the application for processing and notify the tutor with their assigned TDR ID.
8. WHEN a tutor sends `/my_application`, THE Bot SHALL display the current status of their application including their TDR ID and status label.

---

### Requirement 3

**User Story:** As the system, I want every tutor to have a unique and permanent identifier, so that records remain consistent across the bot, the database, and the website.

#### Acceptance Criteria

1. WHEN a tutor application is submitted, THE System SHALL generate a unique TDR ID in the format TDR-XXXXXX (zero-padded six digits, e.g. TDR-000001) that is assigned to the tutor permanently.
2. WHEN a TDR ID is generated, THE System SHALL ensure no two tutors share the same TDR ID by using a sequential counter stored in the database.
3. WHEN a TDR ID is assigned, THE System SHALL never reassign that ID to a different tutor even if the original tutor is rejected or deleted.

---

### Requirement 4

**User Story:** As the system, I want to store all tutor records and documents in Telegram, so that files are reliably archived without requiring external storage.

#### Acceptance Criteria

1. WHEN a tutor application is submitted, THE Storage Service SHALL send a structured tutor record message to the private Tutor Storage Channel containing all profile fields, education, pricing, availability, and status tags.
2. WHEN documents are submitted by a tutor, THE Storage Service SHALL upload or forward each document (profile photo, CV, degree/certificate, supporting documents) to the Tutor Storage Channel as separate messages attached to the tutor record.
3. WHEN a tutor record and documents are stored in Telegram, THE Storage Service SHALL save the resulting Telegram message IDs, file IDs, file unique IDs, and chat ID to the SQLite Index.
4. IF a Telegram storage operation fails, THEN THE Storage Service SHALL log the error, mark the record as storage-failed, and notify the admin without deleting the SQLite record or crashing the bot.
5. WHEN storing tutor records, THE Storage Service SHALL apply hashtags in the format #TDRxxxxxx #Subject #Country #Mode #Status to make records searchable within Telegram.

---

### Requirement 5

**User Story:** As a system administrator, I want to be notified of new tutor applications and be able to review, verify, or reject them through Telegram, so that I can manage the tutor onboarding pipeline.

#### Acceptance Criteria

1. WHEN a tutor application is submitted, THE Bot SHALL send a notification message to the Admin Chat containing the tutor's TDR ID, name, subjects, country, teaching mode, and rate, with inline buttons: 🔍 Review, ✅ Verify, ❌ Reject, 📩 Request More Information.
2. WHEN an admin selects ✅ Verify, THE Bot SHALL update the tutor's status to VERIFIED in SQLite, update the Telegram storage record, and send a confirmation message to the tutor.
3. WHEN an admin selects ❌ Reject, THE Bot SHALL update the tutor's status to REJECTED in SQLite, update the Telegram storage record, and send a rejection notification to the tutor.
4. WHEN an admin selects 📩 Request More Information, THE Bot SHALL prompt the admin to type a message, then send that message to the tutor on behalf of Tedor Tutors.
5. WHEN a tutor replies to a "Request More Information" message, THE Bot SHALL forward the tutor's reply to the Admin Chat and associate it with the original application.
6. THE Bot SHALL restrict status change actions (VERIFIED, REJECTED, UNDER_REVIEW, SUSPENDED) to Telegram user IDs listed in the admin configuration.

---

### Requirement 6

**User Story:** As a parent or student, I want to submit a tutor request through Telegram, so that I can find a suitable tutor for my child.

#### Acceptance Criteria

1. WHEN a user selects 🔎 Find a Tutor, THE Bot SHALL collect: parent name, student name, country, education level, subject, preferred language, teaching mode preference, budget, preferred schedule, additional requirements, and contact information.
2. WHEN the user's country is Ethiopia, THE Bot SHALL present the budget field in ETB.
3. WHEN the user's country is not Ethiopia, THE Bot SHALL present the budget field in USD.
4. WHEN a student request is submitted, THE System SHALL generate a unique public request ID, create a StudentRequest record in SQLite, and forward the request to the Student Request Channel.
5. WHEN a student request is stored, THE Bot SHALL confirm to the parent/student that their request has been received and provide the request ID.

---

### Requirement 7

**User Story:** As a parent or student, I want to browse matched tutors from within Telegram, so that I can evaluate options before contacting Tedor Tutors.

#### Acceptance Criteria

1. WHEN a student request is submitted, THE Matching Service SHALL query the SQLite Index for VERIFIED tutors matching subject, education level, country, language, teaching mode, and budget range.
2. WHEN matching tutors are found, THE Bot SHALL display a ranked list showing each tutor's display name, verification badge, subjects, levels, teaching mode, and rate.
3. WHEN a user selects 👤 View Tutor, THE Bot SHALL display the full tutor profile for that tutor.
4. WHEN a user selects 📩 Request Tutor, THE Bot SHALL record the tutor selection against the student request and notify the admin.
5. WHEN no matching tutors are found, THE Bot SHALL inform the user and offer to forward the request to the support team.
6. THE Matching Service SHALL use deterministic filtering and scoring without an AI or machine-learning component in V1.

---

### Requirement 8

**User Story:** As a user with a question or problem, I want to contact Tedor Tutors through Telegram, so that I get help without leaving the app.

#### Acceptance Criteria

1. WHEN a user selects 📩 Contact Us, THE Bot SHALL display category options: 💬 General Question, 👨‍🏫 Tutor Application, 👨‍🎓 Finding a Tutor, 💳 Payment, 🛠 Technical Problem, 👤 Talk to Admin.
2. WHEN a user selects a support category, THE Bot SHALL collect the user's message and create a SupportTicket record in SQLite.
3. WHEN a SupportTicket is created, THE Bot SHALL forward the ticket to the Support Channel and notify the admin chat.
4. WHEN an admin replies to a support ticket notification in the Admin Chat, THE Bot SHALL relay the reply to the original user.

---

### Requirement 9

**User Story:** As an admin, I want a dedicated admin interface in Telegram, so that I can manage tutors, review requests, and view statistics without leaving Telegram.

#### Acceptance Criteria

1. WHEN an admin sends `/admin`, THE Bot SHALL display the admin menu only if the sender's Telegram user ID is in the configured admin list; otherwise, THE Bot SHALL respond with an access-denied message.
2. WHEN an admin selects 📊 Statistics, THE Bot SHALL display: total tutors, pending tutors, verified tutors, rejected tutors, suspended tutors, total student requests, and total support tickets.
3. WHEN an admin selects 🔎 Search Tutors, THE Bot SHALL accept search criteria (subject, country, status) and return matching tutors from SQLite.
4. WHEN an admin sends `/stats`, THE Bot SHALL display the same statistics summary as the Statistics menu option.
5. WHEN an admin selects ⏳ Pending Verification, THE Bot SHALL list all tutors with PENDING or UNDER_REVIEW status with action buttons per tutor.

---

### Requirement 10

**User Story:** As the main Tedor Tutors website, I want to query verified tutor data through a REST API, so that I can display tutor profiles without connecting to Telegram or the bot database directly.

#### Acceptance Criteria

1. WHEN the website sends `GET /api/tutors`, THE REST API SHALL return a paginated list of VERIFIED tutors with public profile fields only (TDR ID, display name, country, city, bio, subjects, levels, languages, teaching mode, experience years, ETB rate, USD rate, verified flag).
2. WHEN the website sends `GET /api/tutors/{tutor_id}`, THE REST API SHALL return the full public profile for that VERIFIED tutor or a 404 response if not found or not verified.
3. WHEN the website sends `GET /api/tutors/search` with query parameters, THE REST API SHALL filter VERIFIED tutors by subject, level, country, language, mode, and price range and return matching results.
4. WHEN the website sends `GET /api/tutors/{tutor_id}/photo`, THE REST API SHALL return or proxy the tutor's profile photo using the stored Telegram file ID without exposing bot credentials to the caller.
5. WHEN the website sends `GET /api/health`, THE REST API SHALL return a 200 response with system status information.
6. WHEN any request arrives at a protected admin API endpoint, THE REST API SHALL validate the `X-API-Secret` header against the configured API_SECRET and return 401 if invalid.
7. THE REST API SHALL never expose internal Telegram user IDs, private phone numbers, private email addresses, internal chat IDs, internal message IDs, or bot tokens in any public endpoint response.
8. WHEN the website sends `GET /api/subjects`, `GET /api/levels`, `GET /api/countries`, or `GET /api/languages`, THE REST API SHALL return distinct values derived from VERIFIED tutor records in SQLite.

---

### Requirement 11

**User Story:** As an operations engineer, I want to import existing tutor records in bulk, so that the 1,000+ existing tutors can be migrated into the new system without manual re-entry.

#### Acceptance Criteria

1. WHEN the import script is run with a CSV or JSON file, THE Importer SHALL validate each record against required fields and report validation errors per row without stopping the entire import.
2. WHEN a valid record is imported, THE Importer SHALL generate a TDR ID, create the SQLite record, and optionally send a Telegram storage record if the `--with-telegram` flag is provided.
3. WHEN an import is run with the `--dry-run` flag, THE Importer SHALL validate and report on records without writing any data to SQLite or Telegram.
4. WHEN the importer encounters a duplicate (same Telegram user ID or same name+email combination), THE Importer SHALL skip the record and log a warning rather than creating a duplicate entry.

---

### Requirement 12

**User Story:** As a developer, I want the system to use structured data models and layered services, so that the SQLite database can be migrated to PostgreSQL and the Telegram storage layer can be replaced with S3 or another provider in the future.

#### Acceptance Criteria

1. THE System SHALL use SQLAlchemy ORM models for all database operations so that the database engine can be swapped by changing only the connection string.
2. THE System SHALL isolate all Telegram file and message operations inside the Storage Service so that replacing Telegram with another storage provider requires changes only to that service.
3. THE System SHALL use Pydantic models for all API request and response validation so that data contracts are enforced at the boundary.
4. THE System SHALL use environment variables loaded via python-dotenv for all secrets and configuration values so that no credentials are hardcoded in source files.

---

### Requirement 13

**User Story:** As an operations engineer, I want comprehensive logging and safe error handling, so that the system can be monitored and debugged without exposing sensitive data.

#### Acceptance Criteria

1. THE System SHALL log the following events at INFO level: application submission, tutor verification, tutor rejection, student request creation, support ticket creation.
2. THE System SHALL log the following events at ERROR level: Telegram storage failures, database write failures, API authentication failures.
3. THE System SHALL never include bot tokens, API secrets, Telegram user IDs, private phone numbers, or private email addresses in log output.
4. IF an error occurs in one user's conversation, THEN THE Bot SHALL handle it gracefully, notify the user with a generic error message, and continue serving other users without restarting.
5. THE System SHALL validate all user inputs before writing to SQLite and return clear, non-technical error messages to the user when validation fails.
