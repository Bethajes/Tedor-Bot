# Implementation Plan

- [x] 1. Add `Draft` model and `drafts` table to the existing schema
  - Add `Draft` SQLAlchemy model to `app/models.py` with columns: `id`, `telegram_user_id`, `draft_type` (VARCHAR 16), `payload` (Text/JSON), `created_at`, `updated_at`, and a `UNIQUE(telegram_user_id, draft_type)` constraint
  - The `apply_schema_upgrades` function in `app/database.py` will pick this up automatically via `Base.metadata.create_all`
  - _Requirements: 8.1, 9.2, 9.3_

- [x] 1.1 Write property test for draft save-then-load round trip
  - **Property 5: Draft save-then-load round trip**
  - **Validates: Requirements 8.3, 9.3**
  - Use Hypothesis to generate arbitrary partial tutor payloads, save then reload, assert field equality
  - Tag: `# Feature: tedor-mini-app, Property 5: Draft save-then-load round trip`

- [x] 1.2 Write property test for parent draft round trip
  - **Property 6: Parent draft round trip**
  - **Validates: Requirements 9.5**
  - Tag: `# Feature: tedor-mini-app, Property 6: Parent draft round trip`

- [x] 2. Implement Init Data validator and FastAPI auth dependency
  - Create `app/api/miniapp/auth.py` with `validate_init_data(init_data, bot_token) -> dict` using HMAC-SHA256 per Telegram Bot API spec
  - Create `AuthError` exception and `get_current_telegram_user` FastAPI dependency that reads `X-Telegram-Init-Data` header
  - Add `MINI_APP_URL` setting to `app/config.py` (env var `MINI_APP_URL`, default empty string)
  - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [x] 2.1 Write property test for Init Data HMAC validation
  - **Property 1: Init Data HMAC validation**
  - **Validates: Requirements 2.2, 2.3**
  - Use Hypothesis: generate random user dicts, sign them correctly, assert pass; mutate hash field, assert AuthError
  - Tag: `# Feature: tedor-mini-app, Property 1: Init Data HMAC validation`

- [x] 2.2 Write property test for Init Data user ID extraction round-trip
  - **Property 2: Init Data user ID extraction round-trip**
  - **Validates: Requirements 2.4**
  - Tag: `# Feature: tedor-mini-app, Property 2: Init Data user ID extraction round-trip`

- [x] 2.3 Write property test for endpoint 401 without Init Data
  - **Property 13: Mini App endpoint returns 401 without Init Data**
  - **Validates: Requirements 9.10, 2.3**
  - Test each miniapp route with missing and tampered Init Data headers
  - Tag: `# Feature: tedor-mini-app, Property 13: Mini App endpoint returns 401 without Init Data`

- [x] 3. Implement Draft Service
  - Create `app/services/draft_service.py` with `save_draft`, `get_draft`, `delete_draft` functions operating on the `Draft` model
  - `save_draft` upserts on `(telegram_user_id, draft_type)`; payload is stored as JSON string
  - _Requirements: 8.1, 8.2, 8.3, 8.5_

- [x] 4. Implement Mini App API routes (tutor profile, draft, submit)
  - Create `app/api/routes/miniapp.py` and register it in `app/api/server.py`
  - Implement `GET /api/miniapp/tutor/profile` — loads `Tutor` by telegram user ID, returns public fields only (strip phone, email, all Telegram IDs)
  - Implement `GET /api/miniapp/tutor/draft` and `POST /api/miniapp/tutor/draft` using `draft_service`
  - Implement `POST /api/miniapp/tutor/submit` — validates payload using existing `tutor_profile_service.create_ethiopian_tutor` (or updates an existing record), then triggers the same admin notification the bot sends via `notification_service.notify_admin_new_tutor`
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 11.2_

- [x] 4.1 Write property test for tutor profile private field exclusion
  - **Property 4: Tutor profile response excludes private fields**
  - **Validates: Requirements 9.1, 12.2**
  - Use Hypothesis to generate tutor records, call the endpoint, assert none of the private field keys appear in the response
  - Tag: `# Feature: tedor-mini-app, Property 4: Tutor profile response excludes private fields`

- [x] 4.2 Write property test for tutor application submission creates matching DB record
  - **Property 8: Tutor application submission creates a matching database record**
  - **Validates: Requirements 9.4, 11.2**
  - Tag: `# Feature: tedor-mini-app, Property 8: Tutor application submission creates a matching database record`

- [x] 4.3 Write property test for submit clears the draft
  - **Property 7: Submit clears the draft**
  - **Validates: Requirements 8.5**
  - After submit, GET /api/miniapp/tutor/draft should return 404
  - Tag: `# Feature: tedor-mini-app, Property 7: Submit clears the draft`

- [x] 5. Implement Mini App API routes (parent draft, submit, job endpoints)
  - Implement `GET /api/miniapp/parent/draft` and `POST /api/miniapp/parent/draft` using `draft_service`
  - Implement `POST /api/miniapp/parent/submit` — calls `student_service.create_student_request` and triggers `notification_service.notify_admin_student_request`
  - Implement `GET /api/miniapp/jobs/{ref}` — loads job via `applications.get_job`, loads tutor, calls `applications.evaluate_and_store`, returns job fields + score
  - Implement `POST /api/miniapp/jobs/{ref}/confirm` — calls `applications.submit`, triggers notifications
  - _Requirements: 9.5, 9.6, 9.8, 9.9_

- [x] 5.1 Write property test for job match score is bounded
  - **Property 12: Job match score is bounded**
  - **Validates: Requirements 9.8**
  - Use Hypothesis to generate tutor/job combinations, call the endpoint, assert score in [0, 100]
  - Tag: `# Feature: tedor-mini-app, Property 12: Job match score is bounded`

- [x] 5.2 Write property test for already-applied status displayed correctly
  - **Property 17: Already-applied status displayed correctly**
  - **Validates: Requirements 7.5**
  - For each terminal status, assert the response payload has that status and no recommendation field
  - Tag: `# Feature: tedor-mini-app, Property 17: Already-applied status displayed correctly`

- [x] 6. Implement document upload endpoint
  - Implement `POST /api/miniapp/documents/upload` — accepts multipart form, validates file extension and size (≤ 20 MB), forwards to Telegram storage channel via `TelegramStorageService`, stores resulting `TutorDocument` row
  - Validate that the authenticated user owns the tutor record before attaching the document
  - _Requirements: 5.1, 5.2, 5.4, 5.5, 9.7, 12.1, 12.5_

- [x] 6.1 Write property test for document upload rejects invalid file types
  - **Property 10: Document upload rejects invalid file types**
  - **Validates: Requirements 5.2, 9.7**
  - Use Hypothesis to generate file names with random extensions; assert 422 for anything not in the allowed set
  - Tag: `# Feature: tedor-mini-app, Property 10: Document upload rejects invalid file types`

- [x] 6.2 Write property test for document upload stores only metadata
  - **Property 9: Document upload stores only metadata**
  - **Validates: Requirements 12.1**
  - After a mock upload, assert TutorDocument has non-null telegram_file_id and no raw bytes column
  - Tag: `# Feature: tedor-mini-app, Property 9: Document upload stores only metadata`

- [x] 6.3 Write property test for document upload authorization
  - **Property 11: Document upload authorization**
  - **Validates: Requirements 12.5**
  - For any upload authenticated as user A naming user B's application, assert 403
  - Tag: `# Feature: tedor-mini-app, Property 11: Document upload authorization`

- [x] 7. Implement cross-user isolation on all Mini App endpoints
  - Add a guard in the Mini App route handlers: after resolving the resource (tutor profile, draft, or document), check that `resource.telegram_user_id == current_user.id` and raise HTTP 403 otherwise
  - _Requirements: 2.6, 12.3_

- [x] 7.1 Write property test for cross-user request isolation
  - **Property 3: Cross-user request isolation**
  - **Validates: Requirements 2.6, 12.3**
  - Use Hypothesis to generate pairs of distinct user IDs; assert 403 when user A requests user B's resources
  - Tag: `# Feature: tedor-mini-app, Property 3: Cross-user request isolation`

- [x] 8. Checkpoint — ensure all existing and new backend tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Add WebApp buttons to the existing bot
  - Add `MINI_APP_URL` to `app/config.py` (already planned in task 2)
  - Modify `app/bot/handlers/start.py` main menu: add `InlineKeyboardButton("📝 Complete Tutor Application", web_app=WebAppInfo(url=f"{settings.mini_app_url}#screen=tutor"))` and `InlineKeyboardButton("👨‍👩‍👧 Request a Tutor", web_app=WebAppInfo(url=f"{settings.mini_app_url}#screen=parent"))`
  - Modify `app/bot/handlers/job_post.py` (or wherever the Apply button is built): add a parallel `web_app` button using `f"{settings.mini_app_url}#screen=job&ref={job_reference}"`
  - Do not remove any existing buttons or handlers
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 9.1 Write property test for job URL construction embeds the reference
  - **Property 15: Job URL construction embeds the reference**
  - **Validates: Requirements 1.3, 1.4**
  - Use Hypothesis to generate valid job reference strings; assert the built URL contains the reference
  - Tag: `# Feature: tedor-mini-app, Property 15: Job URL construction embeds the reference`

- [x] 10. Scaffold the Mini App frontend project
  - Create `mini-app/` directory with `package.json`, `vite.config.ts`, `tsconfig.json`, `tailwind.config.ts`, `index.html`
  - Dependencies: React 18, TypeScript, Vite, Tailwind CSS, Zustand, fast-check (dev), Vitest, React Testing Library
  - Set up `src/api/client.ts` — a typed fetch wrapper that reads `window.Telegram.WebApp.initData` and injects it as `X-Telegram-Init-Data` on every request; falls back to a stored token for the web path
  - Set up hash-based routing in `src/pages/index.tsx` switching on `#screen=tutor|parent|job`
  - _Requirements: 2.1, 2.5_

- [x] 11. Implement Tutor Application form (frontend)
  - Create `src/forms/tutor/steps.ts` — TypeScript constant array mirroring the 27 `STEPS` from `app/bot/handlers/tutor_onboarding.py`, with field key, label, kind (text/choice/multiselect/int/english/documents), optional flag, and options list for choice/multiselect fields
  - Create `src/forms/tutor/TutorForm.tsx` — multi-step form component driven by `steps.ts`, rendering appropriate widgets per `kind` (see Requirement 10.3–10.5)
  - Implement `ProgressBar` component showing current step / total
  - Implement Back / Next / Submit navigation
  - Connect to `POST /api/miniapp/tutor/draft` on each `nextStep()` via a Zustand middleware
  - Connect to `POST /api/miniapp/tutor/submit` on final Submit
  - On mount: call `GET /api/miniapp/tutor/draft`; if found, populate form state and offer resume prompt
  - _Requirements: 3.1, 3.2, 3.4, 3.5, 3.6, 3.7, 8.1, 8.2, 8.3, 8.4_

- [x] 11.1 Write property test for tutor form field count matches STEPS
  - **Property 14: Tutor form field count matches the onboarding STEPS**
  - **Validates: Requirements 3.1**
  - Use fast-check to assert that the length of non-summary steps in `steps.ts` equals the expected count (26 non-summary steps)
  - Tag: `// Feature: tedor-mini-app, Property 14: Tutor form field count matches the onboarding STEPS`

- [x] 12. Implement client-side field validation for the Tutor form
  - Create `src/forms/tutor/validators.ts` mirroring the rules in `app/services/tutor_profile_service.py` (age range, phone regex, English 1–10, CGPA 0–4, year range, text length limits)
  - Each validator returns `null` on pass or an error string on failure
  - Integrate validators into the form: on Next, run all validators for the current step's fields; if any fail, show inline error below the field and scroll to the first error
  - _Requirements: 3.3, 10.2_

- [x] 12.1 Write property test for client-side validators mirror server validators
  - **Property (frontend): Client validators agree with server for all inputs**
  - Use fast-check to generate random age values; assert the client validator accepts exactly the same set as the server validator rule (18–100). Repeat for English proficiency (1–10) and CGPA (0–4.0).
  - Tag: `// Feature: tedor-mini-app, Property: client-side age/english/cgpa validators match server rules`

- [x] 13. Implement Location Search component
  - Create `src/components/LocationSearch.tsx` with debounced input (300 ms), calling `GET /api/locations/search?q=...` and rendering suggestions as a selectable list
  - On selection: store `id`, `name`, `normalizedName`, `city`, `latitude`, `longitude` in form state
  - Display selected locations as removable tags; allow multiple
  - Provide "Add manually" link that opens a sub-form and calls `POST /api/locations`
  - For online-only tutor: render an info banner and skip required-field validation for location
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [x] 13.1 Write property test for location selection stores required fields
  - **Property (frontend): Location selection stores all required fields**
  - **Validates: Requirements 4.2**
  - Use fast-check to generate mock suggestion objects; simulate selection; assert form state contains id, name, normalizedName, city
  - Tag: `// Feature: tedor-mini-app, Property: location selection stores required fields`

- [x] 14. Implement Document Upload component
  - Create `src/components/FileUpload.tsx` — renders one upload control per required document type (passed as prop, derived from education level)
  - Before uploading: validate extension (pdf/jpg/jpeg/png/webp) and size (≤ 20 MB); show inline error on failure without clearing other uploads
  - On valid selection: POST to `/api/miniapp/documents/upload` with `FormData`; show progress indicator; on success show a green checkmark for that document type
  - On form load with existing application: call profile API to check which document types are already on file; mark those as uploaded
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

- [x] 14.1 Write property test for document validation rejects disallowed types
  - **Property (frontend): Document client validation rejects disallowed file extensions**
  - Use fast-check to generate random filename strings; assert the client validator accepts only files ending in `.pdf`, `.jpg`, `.jpeg`, `.png`, `.webp`
  - Tag: `// Feature: tedor-mini-app, Property: document client validation rejects disallowed types`

- [x] 15. Implement Parent Request form (frontend)
  - Create `src/forms/parent/steps.ts` — TypeScript constant array mirroring `app/bot/handlers/student.py` STEPS (parent name, student name, country, education level multiselect, subject, language, mode, location, budget, schedule, requirements, contact)
  - Create `src/forms/parent/ParentForm.tsx` — multi-step form driven by `steps.ts`
  - Show budget label as ETB when country is Ethiopia, USD otherwise, using a client-side `currencyForCountry` function mirroring `app/services/currency.py`
  - Hide the location step when teaching mode is ONLINE
  - On submit: `POST /api/miniapp/parent/submit`; on success show confirmation with the returned public request ID; if matches are returned display them with Select Tutor buttons
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

- [x] 15.1 Write property test for currency selection matches country
  - **Property 18: Currency selection matches country**
  - **Validates: Requirements 6.2**
  - Use fast-check to generate country strings; assert `currencyForCountry(country)` returns ETB for Ethiopia and USD for all others
  - Tag: `// Feature: tedor-mini-app, Property 18: Currency selection matches country`

- [x] 16. Implement Job Application screen (frontend)
  - Create `src/forms/job/JobScreen.tsx` — reads `ref` from the URL hash, calls `GET /api/miniapp/jobs/{ref}` on mount
  - If the tutor has an existing terminal application: display the status badge and a "Contact Support" link; do not show the application form
  - If the tutor's profile is incomplete: display the list of missing fields (from the API response) and enter `TutorForm` pre-populated with the existing profile data, navigating to the first missing step
  - If the profile is complete: display job title, description, and the match score; render a "Confirm Application" button
  - On confirm: `POST /api/miniapp/jobs/{ref}/confirm`; on success display the confirmation message
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [x] 16.1 Write property test for missing fields match Profile Completion Service
  - **Property 16: Missing fields displayed match Profile Completion Service**
  - **Validates: Requirements 7.2**
  - Use Hypothesis (backend): for any incomplete tutor record, the API response's `missing_fields` list should equal `profile_completion_service.assess(tutor).outstanding`
  - Tag: `# Feature: tedor-mini-app, Property 16: Missing fields displayed match Profile Completion Service`

- [x] 17. Checkpoint — run full test suite
  - Ensure all tests pass, ask the user if questions arise.

- [x] 18. Web fallback authentication
  - When `window.Telegram.WebApp.initData` is empty (non-Telegram browser), `src/api/client.ts` should redirect to `/login` and NOT attempt to load any user data
  - The login page collects a Telegram phone number and issues a one-time code via the bot (or uses Telegram Login Widget); on success, store a short-lived JWT in sessionStorage
  - The JWT is verified server-side in `get_current_telegram_user`; it must contain the same `telegram_user_id` field so the rest of the route handlers are unchanged
  - _Requirements: 2.5, 2.6_

- [x] 19. Environment variables and BotFather configuration documentation
  - Add `MINI_APP_URL` to `.env.example` with a placeholder value
  - Document in `README.md` (or a `MINIAPP.md`): the new env vars, BotFather steps (set Menu Button URL, set the domain), and local development instructions using `ngrok` for HTTPS
  - _Requirements: 1.4_

- [x] 20. Final Checkpoint — ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
