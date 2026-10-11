/**
 * The tutor form end to end: walk it, validate it, submit it.
 *
 * The behaviours pinned here are the ones a tutor would notice immediately if
 * they broke — the step counter moving, an inline error stopping the advance,
 * the draft surviving a reload, and the TDR ID at the end.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TutorForm from "./TutorForm";
import { TUTOR_STEPS } from "./steps";
import { useTutorStore } from "./store";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, text: async () => JSON.stringify(body) };
}

/**
 * Answer each URL fragment with its own response.
 *
 * A value of ``{ body, ok, status }`` sets the status; a bare object is a 200,
 * which keeps the common case short.
 */
function respond(routes: Record<string, unknown>) {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string) => {
    for (const [fragment, response] of Object.entries(routes)) {
      if (!url.includes(fragment)) continue;
      if (response && typeof response === "object" && "body" in response) {
        const { body, ok, status } = response as {
          body: unknown;
          ok?: boolean;
          status?: number;
        };
        return jsonResponse(body, ok ?? false, status ?? 400);
      }
      return jsonResponse(response);
    }
    return jsonResponse({ saved: true });
  });
}

beforeEach(() => {
  useTutorStore.getState().reset();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  (window as unknown as { Telegram: unknown }).Telegram = {
    WebApp: { initData: "signed-init-data" },
  };
  // No draft and no profile: the first-time applicant.
  respond({
    "/miniapp/tutor/draft": { body: { detail: "No draft found" }, status: 404 },
    "/miniapp/tutor/profile": { body: { detail: "Tutor profile not found" }, status: 404 },
  });
});

describe("TutorForm", () => {
  it("opens on the first step with a progress bar", async () => {
    render(<TutorForm />);

    await screen.findByTestId("tutor-form");
    expect(screen.getByLabelText("Full name")).toBeInTheDocument();
    expect(screen.getByTestId("progress-bar")).toHaveTextContent(
      `Step 1 of ${TUTOR_STEPS.length}`,
    );
  });

  it("blocks the advance and shows an inline error for a blank required field", async () => {
    const user = userEvent.setup();
    render(<TutorForm />);
    await screen.findByTestId("tutor-form");

    await user.click(screen.getByTestId("next-step"));

    expect(await screen.findByRole("alert")).toHaveTextContent("required");
    expect(screen.getByLabelText("Full name")).toBeInTheDocument();
  });

  it("blocks an out-of-range age with the same message the server gives", async () => {
    const user = userEvent.setup();
    render(<TutorForm />);
    await screen.findByTestId("tutor-form");

    await user.type(screen.getByLabelText("Full name"), "Abebe Bekele");
    await user.click(screen.getByTestId("next-step")); // → gender
    await user.click(screen.getByText("Male"));
    await user.click(screen.getByTestId("next-step")); // → age

    await user.clear(screen.getByTestId("input-age"));
    await user.type(screen.getByTestId("input-age"), "12");
    await user.click(screen.getByTestId("next-step"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Age must be between 18 and 100.",
    );
  });

  it("lets an optional step be skipped with Continue", async () => {
    const user = userEvent.setup();
    render(<TutorForm />);
    await screen.findByTestId("tutor-form");

    // Walk to the CGPA step (13) — optional.
    for (let index = 0; index < 13; index += 1) {
      const field = TUTOR_STEPS[index];
      await answerStep(user, field.field, index);
    }

    expect(await screen.findByRole("heading", { name: "CGPA" })).toBeInTheDocument();
    await user.click(screen.getByTestId("next-step"));
    expect(
      await screen.findByRole("heading", { name: "Maximum score for your entrance exam" }),
    ).toBeInTheDocument();
  });

  it("offers to resume a draft left on a later step", async () => {
    respond({
      "/miniapp/tutor/draft": { full_name: "Abebe Bekele", _step: 4 },
      "/miniapp/tutor/profile": { body: { detail: "Tutor profile not found" }, status: 404 },
    });

    render(<TutorForm />);

    expect(await screen.findByTestId("resume-prompt")).toBeInTheDocument();
    expect(screen.getByText(/step 5/i)).toBeInTheDocument();
  });

  it("starts clean when the tutor chooses to start again", async () => {
    const user = userEvent.setup();
    respond({
      "/miniapp/tutor/draft": { full_name: "Abebe Bekele", _step: 4 },
      "/miniapp/tutor/profile": { body: { detail: "Tutor profile not found" }, status: 404 },
    });

    render(<TutorForm />);
    await user.click(await screen.findByTestId("start-over"));

    expect(await screen.findByLabelText("Full name")).toBeInTheDocument();
  });

  it("opens on the step the job screen asked for, skipping the resume prompt", async () => {
    respond({
      "/miniapp/tutor/draft": { full_name: "Abebe Bekele", _step: 1 },
      "/miniapp/tutor/profile": { body: { detail: "Tutor profile not found" }, status: 404 },
    });

    const startAt = TUTOR_STEPS.findIndex((step) => step.field === "university");
    render(<TutorForm startAt={startAt} />);

    expect(await screen.findByLabelText("University / institution")).toBeInTheDocument();
    expect(screen.queryByTestId("resume-prompt")).not.toBeInTheDocument();
  });

  it("shows the TDR ID once the application is submitted", async () => {
    const user = userEvent.setup();
    respond({
      "/miniapp/tutor/draft": { body: { detail: "No draft found" }, status: 404 },
      "/miniapp/tutor/profile": { body: { detail: "Tutor profile not found" }, status: 404 },
      "/miniapp/tutor/submit": { public_tutor_id: "TDR-000042", status: "PENDING" },
    });

    const store = useTutorStore.getState();
    store.setValues({
      full_name: "Abebe Bekele",
      gender: "Male",
      age: 27,
      phone: "+251911234567",
      current_address: "Bole",
      locations: [{ id: 1, name: "Bole", normalizedName: "bole", city: "Addis Ababa", latitude: null, longitude: null }],
      grades: ["Grade 4"],
      subjects: ["Mathematics"],
      english_proficiency: "9",
      education_level: "UNIVERSITY_GRADUATE",
      university: "AASTU",
      department: "Mathematics",
      university_year: "2019",
      cgpa: "3.75",
      entrance_exam_max_score: "840",
      entrance_exam_score: "612",
      entrance_exam_year: "2016",
      academic_stream: "NATURAL",
      teaching_experience_years: "3",
      teaching_experience_description: "Taught Grade 4-6.",
    });
    useTutorStore.getState().goToStep(TUTOR_STEPS.length);

    render(<TutorForm />);
    await user.click(await screen.findByTestId("submit-application"));

    expect(await screen.findByTestId("tutor-submitted")).toHaveTextContent("TDR-000042");
  });

  it("keeps the answers when the submission is refused", async () => {
    const user = userEvent.setup();
    respond({
      "/miniapp/tutor/draft": { body: { detail: "No draft found" }, status: 404 },
      "/miniapp/tutor/profile": { body: { detail: "Tutor profile not found" }, status: 404 },
      "/miniapp/tutor/submit": {
        body: { detail: "An application already exists" },
        status: 409,
      },
    });

    useTutorStore.getState().setValues({ full_name: "Abebe Bekele" });
    useTutorStore.getState().goToStep(TUTOR_STEPS.length);

    render(<TutorForm />);
    await user.click(await screen.findByTestId("submit-application"));

    expect(await screen.findByTestId("submit-error")).toBeInTheDocument();
    expect(screen.queryByTestId("tutor-submitted")).not.toBeInTheDocument();
    expect(useTutorStore.getState().values.full_name).toBe("Abebe Bekele");
  });
});

/** Answer one step with a plausible value, so the walk can continue. */
async function answerStep(
  user: ReturnType<typeof userEvent.setup>,
  field: string,
  index: number,
) {
  const step = TUTOR_STEPS[index];
  const testId = `input-${field}`;

  switch (step.kind) {
    case "choice":
    case "english": {
      const option = screen.getByText(step.options?.[0]?.label ?? "");
      await user.click(option);
      break;
    }
    case "multiselect": {
      await user.click(screen.getByText(step.options?.[0]?.label ?? ""));
      break;
    }
    case "location": {
      // The store is the source of truth; the selector is tested on its own.
      useTutorStore.getState().setValue(field, [
        { id: 1, name: "Bole", normalizedName: "bole", city: "Addis Ababa", latitude: null, longitude: null },
      ]);
      break;
    }
    case "documents": {
      // Uploading is tested on the component; the store carries the result.
      useTutorStore.getState().setValue(field, [
        { documentType: "UNIVERSITY_TRANSCRIPT", label: "University transcript" },
      ]);
      // The form asks for uploads to be complete before leaving the step.
      const inputs = screen.getAllByLabelText(/^Upload /);
      expect(inputs.length).toBeGreaterThan(0);
      return;
    }
    default: {
      const input = screen.getByTestId(testId);
      await user.clear(input);
      const sample: Record<string, string> = {
        full_name: "Abebe Bekele",
        age: "27",
        phone: "+251911234567",
        current_address: "Bole, Addis Ababa",
        university: "AASTU",
        department: "Mathematics",
        university_year: "2019",
        cgpa: "3.75",
        entrance_exam_max_score: "840",
        entrance_exam_score: "612",
        entrance_exam_year: "2016",
        teaching_experience_years: "3",
        teaching_experience_description: "Taught Grade 4-6 mathematics.",
        grade8_exam_average: "82",
        grade9_average: "78",
        grade10_average: "80",
        grade11_average: "84",
        grade12_average: "86",
      };
      await user.type(input, sample[field] ?? "3");
      break;
    }
  }
  await user.click(screen.getByTestId("next-step"));
}
// ---------------------------------------------------------------------------
// The documents step must explain itself
// ---------------------------------------------------------------------------

describe("TutorForm documents step", () => {
  it("says why Next does nothing when a document is missing", async () => {
    const user = userEvent.setup();
    render(<TutorForm />);
    await screen.findByTestId("tutor-form");

    // Walk to the last step with every answer filled.
    for (let index = 0; index < TUTOR_STEPS.length; index += 1) {
      const field = TUTOR_STEPS[index].field;
      if (field === "documents") {
        // Press Next with nothing attached: the tutor must be told why.
        await user.click(screen.getByTestId("next-step"));
        const alert = await screen.findByTestId("error-documents");
        expect(alert).toHaveTextContent("attach every required document");
        expect(alert).toHaveAttribute("role", "alert");
        return;
      }
      await answerStep(user, field, index);
    }
    throw new Error("the documents step was never reached");
  });

  it("treats documents uploaded earlier in the session as satisfied", async () => {
    const user = userEvent.setup();
    useTutorStore.getState().setValue("documents", [
      { documentType: "UNIVERSITY_TRANSCRIPT", label: "University transcript" },
      { documentType: "UNIVERSITY_ENTRANCE_RESULT", label: "Entrance result" },
    ]);
    useTutorStore.getState().goToStep(TUTOR_STEPS.length - 1);

    render(<TutorForm />);
    await screen.findByTestId("tutor-form");

    // Both types are marked done without a fresh upload...
    expect(await screen.findByTestId("done-UNIVERSITY_TRANSCRIPT")).toBeInTheDocument();
    expect(screen.getByTestId("done-UNIVERSITY_ENTRANCE_RESULT")).toBeInTheDocument();

    // ...so Next moves on instead of refusing.
    await user.click(screen.getByTestId("next-step"));
    expect(await screen.findByTestId("review-summary")).toBeInTheDocument();
  });
});

describe("TutorForm reload", () => {
  it("shows documents uploaded before a reload as already done", async () => {
    // A phone closing the app on the documents step is the common case: the
    // draft is all that survives, so it has to carry the uploads.
    respond({
      "/miniapp/tutor/draft": {
        full_name: "Abebe Bekele",
        _step: TUTOR_STEPS.findIndex((s) => s.field === "documents"),
        documents: [
          { documentType: "UNIVERSITY_TRANSCRIPT", label: "University transcript" },
          { documentType: "UNIVERSITY_ENTRANCE_RESULT", label: "Entrance result" },
        ],
      },
      "/miniapp/tutor/profile": { body: { detail: "not found" }, status: 404 },
    });

    useTutorStore.getState().reset();
    render(<TutorForm />);

    // Resume first, so the draft's documents reach the upload step.
    await userEvent.click(await screen.findByTestId("resume-draft"));

    await waitFor(() =>
      expect(screen.getByTestId("done-UNIVERSITY_TRANSCRIPT")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("done-UNIVERSITY_ENTRANCE_RESULT")).toBeInTheDocument();
  });
});

describe("TutorForm submitting twice", () => {
  it("shows the existing application instead of a conflict error", async () => {
    // The account already applied. That is what the tutor was trying to
    // achieve, so it must read as success — not as a dead end after 26 steps.
    const user = userEvent.setup();
    respond({
      "/miniapp/tutor/draft": { body: { detail: "not found" }, status: 404 },
      "/miniapp/tutor/profile": { id: 1, public_tutor_id: "TDR-000123", status: "PENDING" },
      "/miniapp/tutor/submit": {
        body: { detail: "A tutor application already exists for this account" },
        status: 409,
      },
    });

    useTutorStore.getState().reset();
    useTutorStore.getState().setValues({ full_name: "Abebe Bekele" });
    useTutorStore.getState().goToStep(TUTOR_STEPS.length);

    render(<TutorForm />);
    await user.click(await screen.findByTestId("submit-application"));

    expect(await screen.findByTestId("tutor-submitted")).toHaveTextContent("TDR-000123");
    expect(screen.queryByTestId("submit-error")).not.toBeInTheDocument();
  });

  it("still reports a refusal that is not a conflict", async () => {
    const user = userEvent.setup();
    respond({
      "/miniapp/tutor/draft": { body: { detail: "not found" }, status: 404 },
      "/miniapp/tutor/profile": { body: { detail: "not found" }, status: 404 },
      "/miniapp/tutor/submit": { body: { detail: "Phone number is required." }, status: 422 },
    });

    useTutorStore.getState().reset();
    useTutorStore.getState().setValues({ full_name: "Abebe Bekele" });
    useTutorStore.getState().goToStep(TUTOR_STEPS.length);

    render(<TutorForm />);
    await user.click(await screen.findByTestId("submit-application"));

    expect(await screen.findByTestId("submit-error")).toBeInTheDocument();
    expect(screen.queryByTestId("tutor-submitted")).not.toBeInTheDocument();
  });
});
