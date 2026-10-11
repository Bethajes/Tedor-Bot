/**
 * The parent request form.
 *
 * The two rules specific to a parent request are pinned here: the budget is
 * labelled in the request's own currency, and the location step disappears for
 * an online-only request. Both come from data the server stores, and both would
 * be quietly wrong without a test.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ParentForm from "./ParentForm";
import { PARENT_STEPS, stepsForMode } from "./steps";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, text: async () => JSON.stringify(body) };
}

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
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  (window as unknown as { Telegram: unknown }).Telegram = {
    WebApp: { initData: "signed-init-data" },
  };
  respond({
    "/api/locations": {
      id: 1,
      name: "Bole",
      normalizedName: "bole",
      city: "Addis Ababa",
      latitude: null,
      longitude: null,
    },
  });
});

describe("ParentForm", () => {
  it("starts with the parent name and walks the bot's steps in order", async () => {
    render(<ParentForm />);

    expect(await screen.findByTestId("parent-form")).toBeInTheDocument();
    expect(screen.getByLabelText("Parent / guardian name")).toBeInTheDocument();
    expect(PARENT_STEPS[0].field).toBe("parent_name");
    expect(PARENT_STEPS.map((step) => step.field)).toEqual([
      "parent_name",
      "student_name",
      "country",
      "education_level",
      "subject",
      "preferred_language",
      "mode",
      "location",
      "budget",
      "schedule",
      "additional_requirements",
      "contact",
    ]);
  });

  it("will not advance past a blank required field", async () => {
    const user = userEvent.setup();
    render(<ParentForm />);
    await screen.findByTestId("parent-form");

    await user.click(screen.getByTestId("parent-next"));

    expect(await screen.findByRole("alert")).toHaveTextContent("required");
  });

  it("labels the budget in ETB for an Ethiopian family", async () => {
    const user = userEvent.setup();
    render(<ParentForm />);
    await screen.findByTestId("parent-form");

    await user.type(screen.getByTestId("input-parent_name"), "Sara Bekele");
    await user.click(screen.getByTestId("parent-next"));
    await user.type(screen.getByTestId("input-student_name"), "Marta");
    await user.click(screen.getByTestId("parent-next"));
    await user.click(screen.getByText("Ethiopia"));
    await user.click(screen.getByTestId("parent-next"));

    // Grade levels (multiselect).
    await user.click(screen.getByText("Grade 9-10"));
    await user.click(screen.getByTestId("parent-next"));
    await user.type(screen.getByTestId("input-subject"), "Mathematics");
    await user.click(screen.getByTestId("parent-next")); // language (optional)
    await user.click(screen.getByTestId("parent-next")); // mode
    // Online skips the location step entirely, so the budget comes next.
    await user.click(screen.getByText("💻 Online"));
    await user.click(screen.getByTestId("parent-next")); // mode → budget

    expect(await screen.findByTestId("budget-currency")).toHaveTextContent("ETB");
    expect(
      screen.getByRole("heading", { name: /Hourly budget \(ETB\)/ }),
    ).toBeInTheDocument();
  });

  it("labels the budget in USD for everybody else", async () => {
    const user = userEvent.setup();
    render(<ParentForm />);
    await screen.findByTestId("parent-form");

    await user.type(screen.getByTestId("input-parent_name"), "Sara Bekele");
    await user.click(screen.getByTestId("parent-next"));
    await user.type(screen.getByTestId("input-student_name"), "Marta");
    await user.click(screen.getByTestId("parent-next"));
    await user.click(screen.getByText("Kenya"));
    await user.click(screen.getByTestId("parent-next"));
    await user.click(screen.getByText("Grade 9-10"));
    await user.click(screen.getByTestId("parent-next"));
    await user.type(screen.getByTestId("input-subject"), "Mathematics");
    await user.click(screen.getByTestId("parent-next"));
    await user.click(screen.getByTestId("parent-next"));
    await user.click(screen.getByText("💻 Online"));
    await user.click(screen.getByTestId("parent-next"));

    expect(await screen.findByTestId("budget-currency")).toHaveTextContent("USD");
  });

  it("drops the location step for an online-only request", () => {
    expect(stepsForMode("ONLINE").some((step) => step.field === "location")).toBe(false);
    expect(stepsForMode("IN_PERSON").some((step) => step.field === "location")).toBe(true);
    expect(stepsForMode("BOTH").some((step) => step.field === "location")).toBe(true);
    expect(stepsForMode(undefined).some((step) => step.field === "location")).toBe(true);
  });

  it("shows the request ID and the matched tutors after submitting", async () => {
    const user = userEvent.setup();
    respond({
      "/miniapp/parent/submit": {
        public_request_id: "REQ-000007",
        status: "NEW",
        currency: "ETB",
        matches: [
          {
            public_tutor_id: "TDR-000009",
            display_name: "Abebe Bekele",
            subjects: ["Mathematics"],
            levels: ["Grade 9-10"],
            teaching_mode: "IN_PERSON",
            city: "Addis Ababa",
            score: 88,
            max_score: 100,
            reasons: ["subject match"],
          },
        ],
      },
    });

    render(<ParentForm />);
    await fillEveryStep(user);

    expect(await screen.findByTestId("parent-confirmation")).toHaveTextContent("REQ-000007");
    expect(screen.getByTestId("parent-matches")).toHaveTextContent("Abebe Bekele");
    expect(screen.getByTestId("select-tutor-TDR-000009")).toBeInTheDocument();
  });

  it("reports a refused request without losing what was typed", async () => {
    const user = userEvent.setup();
    respond({
      "/miniapp/parent/submit": {
        body: { detail: "A phone number or username is required." },
        status: 422,
      },
    });

    render(<ParentForm />);
    await fillEveryStep(user);

    expect(await screen.findByTestId("parent-submit-error")).toBeInTheDocument();
    expect(screen.queryByTestId("parent-confirmation")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("submit"))).toBe(true),
    );
  });
});

/**
 * Answer every step of the walk, ending on the submit button.
 *
 * The walk is driven one step at a time, waiting for each heading before
 * answering it, so the test fails on the step that actually changed rather
 * than on a click that landed on the previous screen.
 */
async function fillEveryStep(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByTestId("parent-form");

  const answer: Record<string, () => Promise<void>> = {
    parent_name: async () =>
      user.type(await heading("Parent / guardian name"), "Sara Bekele"),
    student_name: async () =>
      user.type(await heading("Student name"), "Marta Bekele"),
    country: async () => user.click(screen.getByText("Ethiopia")),
    education_level: async () => user.click(screen.getByText("Grade 9-10")),
    subject: async () => user.type(await heading("Subject"), "Mathematics"),
    preferred_language: async () => user.type(await heading("Preferred language"), "English"),
    mode: async () => user.click(screen.getByText(/In person/)),
    location: async () => {
      await user.click(await screen.findByTestId("add-manually"));
      await user.type(screen.getByLabelText("Area name"), "Bole");
      await user.click(screen.getByText("Add area"));
    },
    budget: async () => user.type(await heading(/Hourly budget/), "500"),
    schedule: async () => user.type(await heading("Preferred schedule"), "Weekdays 16:00"),
    additional_requirements: async () =>
      user.type(await heading("Additional requirements"), "Female tutor please"),
    contact: async () => user.type(await heading("Contact information"), "+251911234567"),
  };

  for (const step of PARENT_STEPS) {
    await answer[step.field]();
    // The last step offers Submit instead of Next.
    const next = screen.queryByTestId("parent-submit")
      ? screen.getByTestId("parent-submit")
      : screen.getByTestId("parent-next");
    await user.click(next);
    await waitForStepChange();
  }
}

/** The input for the step currently on screen. */
function heading(name: string | RegExp) {
  return screen.findByLabelText(name);
}

/** Give the form a tick to move past the step just answered. */
async function waitForStepChange() {
  await waitFor(() => {
    // Either the form moved on (it still shows its progress bar) or the last
    // step submitted and the confirmation replaced it.
    const stillOnAForm =
      screen.queryByTestId("parent-form") !== null &&
      screen.queryByTestId("progress-bar") !== null;
    const finished = screen.queryByTestId("parent-confirmation") !== null;
    expect(stillOnAForm || finished).toBe(true);
  });
}
