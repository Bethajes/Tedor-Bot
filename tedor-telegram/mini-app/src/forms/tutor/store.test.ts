/**
 * The draft store: what is saved, when, and what is never sent.
 *
 * A step that advances without its previous answers persisted loses a tutor's
 * work; a payload carrying UI state stores nonsense in the database. Both are
 * tested here because both are cheap to get wrong and invisible until someone
 * loses an afternoon's typing.
 */

import fc from "fast-check";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TUTOR_STEPS } from "./steps";
import { draftPayload, submitPayload, useTutorStore } from "./store";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    text: async () => JSON.stringify(body),
  };
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(jsonResponse({ saved: true }));
  vi.stubGlobal("fetch", fetchMock);
  (window as unknown as { Telegram: unknown }).Telegram = {
    WebApp: { initData: "signed-init-data" },
  };
  useTutorStore.getState().reset();
});

describe("the tutor draft store", () => {
  it("sends the answers and the step on every step change", async () => {
    const { setValue, goToStep } = useTutorStore.getState();

    setValue("full_name", "Abebe Bekele");
    setValue("age", "27");
    goToStep(3);

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, options] = fetchMock.mock.calls.at(-1)!;
    const body = JSON.parse(options.body);

    expect(body).toMatchObject({ full_name: "Abebe Bekele", age: "27", _step: 3 });
  });

  it("never sends the transient UI fields to the API", () => {
    const payload = draftPayload(
      { full_name: "Abebe", stepIndex: 4, savedAt: "now", cgpa: 3.5 },
      4,
    );
    expect(payload).not.toHaveProperty("stepIndex");
    expect(payload).not.toHaveProperty("savedAt");
    expect(payload).toEqual({ full_name: "Abebe", cgpa: 3.5, _step: 4 });
  });

  it("does not send the step marker on submit", () => {
    const payload = submitPayload({ full_name: "Abebe", _step: 9 });
    expect(payload).toEqual({ full_name: "Abebe" });
  });

  it("reports a failed save instead of silently dropping it", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "boom" }, false, 500));
    const { goToStep } = useTutorStore.getState();

    goToStep(1);

    await vi.waitFor(() =>
      expect(useTutorStore.getState().error).toBeTruthy(),
    );
    expect(useTutorStore.getState().saving).toBe(false);
  });

  it("clamps a step to the ones the form has", () => {
    const { goToStep } = useTutorStore.getState();

    goToStep(-5);
    expect(useTutorStore.getState().stepIndex).toBe(0);

    goToStep(9999);
    expect(useTutorStore.getState().stepIndex).toBe(TUTOR_STEPS.length);
  });

  it("loads a draft and offers to resume the step it was left on", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ full_name: "Abebe", _step: 5 }),
    );

    await useTutorStore.getState().loadDraft();

    const state = useTutorStore.getState();
    expect(state.values).toEqual({ full_name: "Abebe" });
    expect(state.draftStepIndex).toBe(5);
    expect(state.stepIndex).toBe(5);
    expect(state.draftLoaded).toBe(true);
  });

  it("treats a missing draft as nothing to resume, not an error", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "No draft found" }, false, 404));

    const found = await useTutorStore.getState().loadDraft();

    expect(found).toBe(false);
    expect(useTutorStore.getState().draftLoaded).toBe(true);
    expect(useTutorStore.getState().error).toBeNull();
  });

  it("submits the answers and records the TDR ID", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ public_tutor_id: "TDR-000123", status: "PENDING" }),
    );
    useTutorStore.getState().setValues({ full_name: "Abebe", age: 27 });
    fetchMock.mockResolvedValue(jsonResponse({ saved: true }));

    await useTutorStore.getState().submit();

    expect(useTutorStore.getState().submitted).toEqual({
      publicTutorId: "TDR-000123",
      status: "PENDING",
    });
  });

  it("keeps a failed submission on the form instead of losing the answers", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Invalid phone" }, false, 422));
    useTutorStore.getState().setValues({ full_name: "Abebe" });
    fetchMock.mockResolvedValue(jsonResponse({ saved: true }));

    await useTutorStore.getState().submit();

    expect(useTutorStore.getState().submitted).toBeNull();
    expect(useTutorStore.getState().submitError).toBeTruthy();
    expect(useTutorStore.getState().values).toEqual({ full_name: "Abebe" });
  });

  // Feature: tedor-mini-app, Property: draft payloads round-trip through JSON
  it("survives a JSON round trip for any answers", () => {
    fc.assert(
      fc.property(
        fc.dictionary(
          fc.string({ minLength: 1, maxLength: 8 }),
          fc.oneof(fc.string(), fc.integer(), fc.boolean(), fc.constant(null)),
          { maxKeys: 12 },
        ),
        fc.integer({ min: 0, max: TUTOR_STEPS.length }),
        (values, step) => {
          const payload = draftPayload(values, step);
          expect(JSON.parse(JSON.stringify(payload))).toEqual(payload);
        },
      ),
      { numRuns: 300 },
    );
  });
});
describe("the submit payload", () => {
  it("never carries the form's document list", () => {
    // Documents are uploaded one at a time and held server-side; the submit
    // payload speaks the API's shape, not the UI's, and sending the UI list
    // was what produced "422 Unknown document type: None".
    const payload = submitPayload({
      full_name: "Abebe Bekele",
      documents: [{ documentType: "UNIVERSITY_TRANSCRIPT", label: "Transcript" }],
      _step: 26,
    });

    expect(payload).not.toHaveProperty("documents");
    expect(payload).not.toHaveProperty("_step");
    expect(payload).toEqual({ full_name: "Abebe Bekele" });
  });

  it("still sends every real answer", () => {
    const payload = submitPayload({
      full_name: "Abebe Bekele",
      age: "27",
      phone: "+251911234567",
      locations: [{ id: 1, name: "Bole", normalizedName: "bole", city: "Addis Ababa", latitude: null, longitude: null }],
      grades: ["Grade 4"],
      subjects: ["Mathematics"],
    });

    expect(payload.full_name).toBe("Abebe Bekele");
    expect(payload.age).toBe("27");
    expect(payload.grades).toEqual(["Grade 4"]);
    expect(payload.locations).toHaveLength(1);
  });
});
