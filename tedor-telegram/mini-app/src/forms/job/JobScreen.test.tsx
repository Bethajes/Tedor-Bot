/**
 * The job screen's three outcomes.
 *
 * Which of them renders is decided entirely by the API's answer, so each is
 * driven from a response rather than from a prop — the branch that decides a
 * tutor sees "already applied" instead of a form is the one worth pinning.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import JobScreen, { firstMissingStepIndex, isTerminal } from "./JobScreen";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, text: async () => JSON.stringify(body) };
}

/** Answer the first call with `body`, and anything after it with `{}`. */
function respond(body: unknown, ok = true, status = 200) {
  fetchMock.mockReset();
  fetchMock.mockResolvedValueOnce(jsonResponse(body, ok, status));
  fetchMock.mockResolvedValue(jsonResponse({}));
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  (window as unknown as { Telegram: unknown }).Telegram = {
    WebApp: { initData: "signed-init-data" },
  };
});

describe("JobScreen", () => {
  it("shows the status and a support link when the tutor already applied", async () => {
    respond({
      job_reference: "JOB-000123",
      status: "UNDER_REVIEW",
      status_label: "Under review",
    });

    render(<JobScreen jobReference="JOB-000123" />);

    expect(await screen.findByTestId("job-status")).toBeInTheDocument();
    expect(screen.getByText("Under review")).toBeInTheDocument();
    expect(screen.getByTestId("contact-support")).toBeInTheDocument();
    // The application form must not be offered again.
    expect(screen.queryByTestId("confirm-application")).not.toBeInTheDocument();
    expect(screen.queryByTestId("complete-profile")).not.toBeInTheDocument();
  });

  it("lists the missing fields and offers to complete the profile", async () => {
    respond({
      job_reference: "JOB-000123",
      profile_state: "INCOMPLETE",
      profile_percentage: 70,
      missing_fields: ["University", "CGPA (must be between 0 and 4)"],
      missing_field_keys: ["university", "cgpa"],
      missing_documents: ["UNIVERSITY_TRANSCRIPT"],
    });

    render(<JobScreen jobReference="JOB-000123" />);

    expect(await screen.findByTestId("job-incomplete")).toBeInTheDocument();
    const list = screen.getByTestId("missing-fields");
    expect(list).toHaveTextContent("University");
    expect(list).toHaveTextContent("CGPA");
    expect(screen.getByText(/70% complete/)).toBeInTheDocument();
    expect(screen.queryByTestId("confirm-application")).not.toBeInTheDocument();
  });

  it("opens the completion form on the first missing step when asked to", async () => {
    respond({
      job_reference: "JOB-000123",
      profile_state: "INCOMPLETE",
      profile_percentage: 70,
      missing_fields: ["University"],
      missing_field_keys: ["university"],
    });

    render(
      <JobScreen
        jobReference="JOB-000123"
        fieldsByIndex={["full_name", "university", "cgpa"]}
        renderCompletion={(startAt) => <output data-testid="form">{`step ${startAt}`}</output>}
      />,
    );

    await userEvent.click(await screen.findByTestId("complete-profile"));

    // "university" is step 1, so the tutor lands there rather than at the top.
    expect(screen.getByTestId("form")).toHaveTextContent("step 1");
  });

  it("shows the job and the score for a complete profile, and confirms", async () => {
    const user = userEvent.setup();
    respond(
      {
        job_reference: "JOB-000123",
        subjects: "Mathematics",
        student_level: "Grade 9",
        location: "Bole",
        score: 92,
        recommendation: "Strong subject overlap.",
        is_strong_match: true,
        profile_state: "AWAITING_VERIFICATION",
      },
      true,
    );
    // The confirmation POST follows the GET.
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ job_reference: "JOB-000123", status: "SUBMITTED", confirmed: true }),
    );

    render(<JobScreen jobReference="JOB-000123" />);

    await screen.findByTestId("job-details");
    expect(screen.getByTestId("match-score")).toHaveTextContent(/92\/100/);
    expect(screen.getByTestId("match-score")).toHaveTextContent(/strong match/);
    expect(screen.getByTestId("job-details")).toHaveTextContent("Mathematics");
    expect(screen.getByTestId("job-details")).toHaveTextContent("Bole");

    await user.click(screen.getByTestId("confirm-application"));

    expect(await screen.findByTestId("job-confirmed")).toBeInTheDocument();
  });

  it("reports a failed confirmation without losing the job", async () => {
    const user = userEvent.setup();
    fetchMock.mockReset();
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ job_reference: "JOB-000123", score: 80 }, true, 200),
    );
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Server error" }, false, 500));

    render(<JobScreen jobReference="JOB-000123" />);
    await user.click(await screen.findByTestId("confirm-application"));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("could not submit"),
    );
    expect(screen.getByTestId("job-details")).toBeInTheDocument();
  });

  it("explains a link that arrived without a job reference", async () => {
    render(<JobScreen jobReference="" />);
    expect(await screen.findByTestId("job-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("offers a retry when the job cannot be loaded", async () => {
    const user = userEvent.setup();
    fetchMock.mockReset();
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Not found" }, false, 404));
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ job_reference: "JOB-000123", score: 70 }, true, 200),
    );

    render(<JobScreen jobReference="JOB-000999" />);
    await user.click(await screen.findByText("Try again"));

    expect(await screen.findByTestId("job-details")).toBeInTheDocument();
  });
});

describe("which step the completion form opens on", () => {
  it("jumps to the first field the API listed as missing", () => {
    const fields = ["full_name", "university", "cgpa", "documents"];
    expect(firstMissingStepIndex(["cgpa", "university"], fields)).toBe(1);
    expect(firstMissingStepIndex(["documents"], fields)).toBe(3);
  });

  it("falls back to the first step when nothing is recognisable", () => {
    // The API sends labels for display; an unrecognised one must not send the
    // tutor somewhere meaningless.
    expect(firstMissingStepIndex(["Current address (optional)"], ["full_name"])).toBe(0);
    expect(firstMissingStepIndex([], ["full_name"])).toBe(0);
  });

  it("recognises the statuses that end an application", () => {
    for (const status of ["SUBMITTED", "UNDER_REVIEW", "SHORTLISTED", "REJECTED"]) {
      expect(isTerminal(status)).toBe(true);
    }
    expect(isTerminal("STARTED")).toBe(false);
    expect(isTerminal(undefined)).toBe(false);
  });
});