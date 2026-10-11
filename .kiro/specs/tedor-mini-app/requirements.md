# Requirements Document

## Introduction

Tedor Tutors currently operates as a Python Telegram bot that walks users through multi-step chat conversations to collect tutor applications and parent/student tutor requests. This feature migrates those existing form workflows to a Telegram Mini App (also usable as a standalone web app), replacing the message-by-message chat experience with a graphical, multi-step form interface. The existing Python bot, its database, all business logic, validation rules, matching engine, job application system, and location services are preserved without modification. Only the user-facing form interface changes.

The frontend is a React + TypeScript + Tailwind CSS application. The backend extends the existing FastAPI server with new authenticated endpoints that the Mini App calls. The Telegram bot continues to work unchanged alongside the Mini App; existing tutor records, parent requests, job applications, and conversation handlers are not deleted or replaced.

## Glossary

- **Mini App**: A Telegram-hosted web application embedded in a Telegram chat, opened via a WebApp button.
- **Bot**: The existing Python Telegram Bot application (`tedor-telegram/`), unchanged.
- **API Server**: The existing FastAPI application (`app/api/server.py`), extended with new Mini App endpoints.
- **Init Data**: The signed string `window.Telegram.WebApp.initData` that Telegram passes into every Mini App; used to identify the authenticated user without a separate login.
- **Init Data Validation**: Server-side HMAC-SHA256 verification of Init Data against the bot token, as defined by the Telegram Bot API documentation.
- **Tutor Application**: The 27-step onboarding form currently implemented in `app/bot/handlers/tutor_onboarding.py`.
- **Parent Request Form**: The multi-step tutor-request form currently implemented in `app/bot/handlers/student.py`.
- **TDR ID**: A permanent sequential public tutor identifier in the format `TDR-000001`.
- **Job Reference**: A published job identifier in the format `JOB-000001`.
- **Profile Completion Service**: `app/services/profile_completion_service.py` — the server-side logic that decides whether a tutor's profile is complete, incomplete, awaiting verification, or blocked.
- **Tutor Profile Service**: `app/services/tutor_profile_service.py` — the validation functions and field-level rules for the tutor application.
- **Draft**: A partially completed tutor application or parent request saved to the database so the user can resume it later.
- **HMAC**: Hash-based Message Authentication Code.
- **ETB**: Ethiopian Birr — the default currency for Ethiopian users.
- **USD**: US Dollar — the default currency for international users.
- **Teaching Mode**: One of `ONLINE`, `IN_PERSON`, or `BOTH`.
- **Education Level**: One of `UNIVERSITY_STUDENT`, `UNIVERSITY_GRADUATE`, `MASTERS_STUDENT`, `MASTERS_GRADUATE`, or `OTHER`.
- **Academic Stream**: `NATURAL` or `SOCIAL` — the Ethiopian preparatory stream.
- **Location Registry**: The curated set of tutoring locations in `app/services/location_registry_service.py` and the `locations` database table.
- **Geoapify**: The geocoding provider used for location autocomplete, configured via `GEOAPIFY_API_KEY`.
- **WebApp Button**: A Telegram `InlineKeyboardButton` with `web_app` set, which opens a URL inside the Telegram Mini App container.
- **Deep Link**: A `t.me/BotName?startapp=…` URL that opens the bot and passes a parameter.

---

## Requirements

### Requirement 1 — Mini App Entry Points in the Bot

**User Story:** As a Telegram bot user, I want to tap a button that opens the Mini App directly, so that I can access the graphical form without leaving Telegram.

#### Acceptance Criteria

1. WHEN a user selects 👨‍🏫 Become a Tutor from the bot's main menu, THE Bot SHALL send an inline keyboard button labelled "📝 Complete Tutor Application" that opens the Mini App at the tutor application screen.
2. WHEN a user selects 🔎 Find a Tutor from the bot's main menu, THE Bot SHALL send an inline keyboard button labelled "👨‍👩‍👧 Request a Tutor" that opens the Mini App at the parent request screen.
3. WHEN a published job post is sent to the job channel, THE Job Post SHALL include an "Apply" button that, when pressed inside a private chat, opens the Mini App at the job application screen pre-loaded with the correct Job Reference.
4. WHEN the Mini App URL is constructed, THE Bot SHALL append the correct screen identifier as a query parameter or hash fragment so the Mini App opens at the intended step.
5. WHEN a user's Telegram client does not support Mini Apps, THE Bot SHALL fall back to the existing conversational flow without error.

---

### Requirement 2 — Telegram Authentication and Identity

**User Story:** As a system operator, I want every Mini App request authenticated against Telegram's Init Data, so that no user can access or modify another user's application.

#### Acceptance Criteria

1. WHEN the Mini App loads in Telegram, THE Mini App SHALL read `window.Telegram.WebApp.initData` and include it as an `X-Telegram-Init-Data` header on every authenticated API request.
2. WHEN the API Server receives a request with an `X-Telegram-Init-Data` header, THE API Server SHALL validate the HMAC-SHA256 signature of that Init Data using the bot token as the secret key, following the Telegram Bot API verification procedure.
3. IF the Init Data signature is invalid or the Init Data is absent on a protected endpoint, THEN THE API Server SHALL return HTTP 401 and the Mini App SHALL display an authentication error without exposing internal details.
4. WHEN Init Data is valid, THE API Server SHALL extract the Telegram user ID from it and use only that server-derived ID to look up or create records; the Telegram user ID SHALL NOT be accepted as a client-supplied parameter.
5. WHEN the Mini App is opened as a normal web app outside Telegram, THE Mini App SHALL redirect the user to a login page and SHALL NOT allow access to any tutor or parent data without an authenticated session.
6. WHEN a non-Telegram session is authenticated, THE API Server SHALL enforce that the session owner may only read and write their own application records; requests for another user's data SHALL return HTTP 403.

---

### Requirement 3 — Tutor Application Form

**User Story:** As a prospective tutor, I want to complete my application in a graphical form inside Telegram, so that the experience is easier than a chat conversation.

#### Acceptance Criteria

1. WHEN the Mini App opens at the tutor application screen, THE Mini App SHALL render all 27 fields defined in `app/bot/handlers/tutor_onboarding.py` STEPS, in the same order, with the same labels and validation rules.
2. WHEN a tutor has an existing incomplete application, THE Mini App SHALL load the saved field values from the API and pre-populate the form so the tutor resumes rather than restarts.
3. WHEN a tutor submits a field value, THE Mini App SHALL apply the same validation rules used by the existing `app/services/tutor_profile_service.py` validators; invalid values SHALL display an inline error message without clearing the field.
4. WHEN a tutor navigates between steps, THE Mini App SHALL display a progress indicator showing the current step number out of the total, Back and Next buttons, and preserve all entered values.
5. WHEN a tutor reaches the final step, THE Mini App SHALL display a review screen listing every field value and document upload status before showing a Submit button.
6. WHEN the tutor submits the completed application, THE Mini App SHALL call the API submission endpoint and, on success, display a confirmation screen matching the information currently shown by the bot's summary message.
7. WHEN the tutor does not submit and closes the Mini App, THE Mini App SHALL have saved the answers to the draft endpoint so the data is not lost.

---

### Requirement 4 — Location Search in the Mini App

**User Story:** As a tutor or parent using the Mini App, I want to search for tutoring locations by name and pick from autocomplete suggestions, so that I can select verified locations quickly.

#### Acceptance Criteria

1. WHEN a user types into the location search field, THE Mini App SHALL call the existing `GET /api/locations/search` endpoint after a debounce delay and display the returned suggestions as a selectable list.
2. WHEN a user selects a suggestion, THE Mini App SHALL store the location's `id`, `name`, `normalizedName`, `city`, and `latitude`/`longitude` in the form state.
3. WHEN a user's location is not found in the suggestions, THE Mini App SHALL provide a "Add manually" option that opens a sub-form collecting name, city, sub-city, and landmark, then calls `POST /api/locations` and stores the returned location record.
4. WHEN a tutor selects multiple tutoring locations, THE Mini App SHALL display each selected location as a removable tag and allow adding more.
5. WHEN an online-only tutor reaches the tutoring locations step, THE Mini App SHALL allow proceeding with zero locations selected.

---

### Requirement 5 — Document Upload in the Mini App

**User Story:** As a tutor, I want to upload my academic documents through the Mini App, so that I do not have to send them as Telegram messages.

#### Acceptance Criteria

1. WHEN a tutor reaches the documents step, THE Mini App SHALL render one upload control for each required document type specified by `app/services/tutor_profile_service.py` for the tutor's education level.
2. WHEN a tutor selects a file for upload, THE Mini App SHALL validate that the file is a PDF or image (JPEG, PNG, or WEBP) and that the file size does not exceed 20 MB before sending it to the upload endpoint.
3. IF a file fails validation, THEN THE Mini App SHALL display an inline error message identifying the failing constraint without clearing the other already-uploaded files.
4. WHEN a valid file is selected, THE Mini App SHALL upload it to the API upload endpoint and display an upload progress indicator until the server confirms receipt.
5. WHEN a document is successfully uploaded, THE Mini App SHALL display a success indicator for that document type and retain the uploaded file reference in the form state.
6. WHEN a tutor re-opens an existing application, THE Mini App SHALL display which document types are already on file so the tutor is not asked to re-upload them.

---

### Requirement 6 — Parent / Student Request Form

**User Story:** As a parent, I want to submit a tutor request through a graphical form in Telegram, so that I can describe what I need more clearly than through a chat conversation.

#### Acceptance Criteria

1. WHEN the Mini App opens at the parent request screen, THE Mini App SHALL render all fields defined in `app/bot/handlers/student.py` STEPS, in the same order, with the same labels and validation rules: parent name, student name, country, education level (multi-select), subject, preferred language, teaching mode, location, budget, schedule, additional requirements, and contact information.
2. WHEN a parent selects Ethiopia as the country, THE Mini App SHALL display the budget field labelled in ETB; for all other countries THE Mini App SHALL display it in USD.
3. WHEN a parent selects Online as the teaching mode, THE Mini App SHALL hide the location search step.
4. WHEN a parent submits the completed form, THE Mini App SHALL call the parent request submission endpoint and display a confirmation screen showing the assigned public request ID.
5. WHEN matched tutors are returned by the API, THE Mini App SHALL display each match with display name, subjects, education levels, teaching mode, and score, along with a Select Tutor button.

---

### Requirement 7 — Job Application via Mini App

**User Story:** As a tutor, I want to apply for a job through the Mini App, so that I can see my match score and confirm my application graphically.

#### Acceptance Criteria

1. WHEN a tutor opens the Mini App from a job post's Apply button, THE Mini App SHALL load the Job Reference from the launch parameter and display the job details alongside the tutor's match score and recommendation.
2. WHEN a tutor's profile is incomplete, THE Mini App SHALL display exactly the fields that are missing (as identified by the Profile Completion Service) and resume the tutor application form at the first missing field, pre-populated with all data already on file.
3. WHEN a tutor's profile is complete, THE Mini App SHALL show the job recommendation and a Confirm Application button.
4. WHEN the tutor taps Confirm Application, THE Mini App SHALL call the job application confirmation endpoint and display a success message matching the one the bot currently sends.
5. WHEN the tutor has already submitted an application for the job, THE Mini App SHALL display the current application status rather than re-opening the application form.

---

### Requirement 8 — Draft Persistence and Resume

**User Story:** As a tutor or parent, I want my form answers saved automatically so I can close the Mini App and continue later without losing my progress.

#### Acceptance Criteria

1. WHEN a user fills in a field and moves to the next step, THE Mini App SHALL call the save-draft endpoint within 2 seconds to persist the current field values.
2. WHEN a user opens the Mini App and an incomplete draft exists for that Telegram user ID, THE Mini App SHALL detect the draft and offer the user a choice between resuming it and starting fresh.
3. WHEN a user chooses to resume a draft, THE Mini App SHALL pre-populate every field with the saved values and navigate to the first unanswered step.
4. WHEN a user chooses to start fresh, THE Mini App SHALL discard the draft and begin at step 1 with empty fields.
5. WHEN a tutor application is successfully submitted, THE Mini App SHALL mark the draft as submitted so the API never returns it again as a resumable draft.

---

### Requirement 9 — New API Endpoints

**User Story:** As a developer, I want the existing FastAPI server extended with Mini App–specific endpoints, so that the Mini App can read and write data without duplicating any business logic already in the bot.

#### Acceptance Criteria

1. THE API Server SHALL expose `GET /api/miniapp/tutor/profile` that returns the authenticated tutor's stored field values (or a 404 if no tutor record exists yet), excluding all private fields (phone, email, Telegram IDs, storage chat IDs).
2. THE API Server SHALL expose `GET /api/miniapp/tutor/draft` that returns the authenticated user's incomplete tutor application draft, or a 404 if none exists.
3. THE API Server SHALL expose `POST /api/miniapp/tutor/draft` that saves a partial tutor application payload as a draft, calling the same validation functions already used by the bot without creating or overwriting a submitted application.
4. THE API Server SHALL expose `POST /api/miniapp/tutor/submit` that submits the tutor application by calling the same service functions (`tutor_service`, `tutor_profile_service`) already used by the bot's submission handler.
5. THE API Server SHALL expose `GET /api/miniapp/parent/draft` and `POST /api/miniapp/parent/draft` for parent request drafts, following the same pattern as the tutor draft endpoints.
6. THE API Server SHALL expose `POST /api/miniapp/parent/submit` that submits the parent request by calling `student_service.create_student_request`.
7. THE API Server SHALL expose `POST /api/miniapp/documents/upload` that accepts a multipart file upload, validates the file type and size, and stores the file reference using the existing storage service; the response SHALL include the `document_type` and `telegram_file_id`.
8. THE API Server SHALL expose `GET /api/miniapp/jobs/{job_reference}` that returns the job details and the authenticated tutor's match score for that job, calling `job_application_service.evaluate_and_store`.
9. THE API Server SHALL expose `POST /api/miniapp/jobs/{job_reference}/confirm` that confirms the tutor's job application by calling `job_application_service.submit`.
10. WHEN any Mini App endpoint is called without valid Init Data, THE API Server SHALL return HTTP 401.

---

### Requirement 10 — Form UI Quality

**User Story:** As a mobile Telegram user, I want the Mini App forms to be easy to use on a small screen, so that I can complete my application without frustration.

#### Acceptance Criteria

1. WHEN the Mini App renders on a mobile screen narrower than 480 px, THE Mini App SHALL display each field on a full-width row with a touch target of at least 44 × 44 CSS px for interactive elements.
2. WHEN a required field is left empty and the user taps Next, THE Mini App SHALL display an inline error message directly below the field and scroll to the first error.
3. WHEN a multi-select field is rendered (grades, subjects, education level), THE Mini App SHALL render checkboxes or chip toggles rather than a free-text input.
4. WHEN a single-choice field is rendered (gender, country, teaching mode, education level, academic stream), THE Mini App SHALL render radio buttons or a dropdown rather than a free-text input.
5. WHEN the English proficiency field is rendered, THE Mini App SHALL render a 1–10 numeric slider or button row rather than a free-text input.
6. WHEN a user is on a step with optional fields, THE Mini App SHALL visually distinguish optional fields from required ones using a label suffix or muted styling.

---

### Requirement 11 — Existing Bot Compatibility

**User Story:** As a system operator, I want the existing Telegram bot to keep working without modification, so that users on older Telegram clients and any automated processes continue to function.

#### Acceptance Criteria

1. WHEN the Mini App is deployed, THE existing bot conversation handlers SHALL remain registered and functional.
2. WHEN a tutor submits an application through the Mini App, THE Bot SHALL continue to send the admin notification and store records using the same path as a bot submission.
3. WHEN a parent submits a request through the Mini App, THE Bot SHALL continue to store the request and send the admin notification via the same notification service already used.
4. WHEN an existing tutor record was created through the bot, THE Mini App SHALL load and display it correctly without requiring the tutor to re-enter their data.
5. WHEN the existing automated test suite is run, ALL existing tests SHALL pass without modification.

---

### Requirement 12 — Security and Privacy

**User Story:** As a system operator, I want uploaded documents and personal data accessible only to authorised users, so that no private information is disclosed through the Mini App.

#### Acceptance Criteria

1. WHEN a tutor document is uploaded, THE API Server SHALL store only the Telegram file ID and document metadata in the database; raw file bytes SHALL NOT be persisted in the database.
2. WHEN a Mini App API endpoint returns tutor data, THE response SHALL exclude Telegram user IDs, bot tokens, storage chat IDs, storage message IDs, phone numbers, and email addresses unless the authenticated user is requesting their own record.
3. WHEN the Mini App API is called with a valid session for user A, THE API Server SHALL return HTTP 403 for any request that attempts to read or write records belonging to user B.
4. WHEN the Mini App constructs an API request, THE Mini App SHALL never include the Telegram user ID as a plain parameter; identity SHALL be derived only from the validated Init Data on the server.
5. WHEN a document upload endpoint is called, THE API Server SHALL validate that the authenticated user owns the tutor application before attaching the document to it.
