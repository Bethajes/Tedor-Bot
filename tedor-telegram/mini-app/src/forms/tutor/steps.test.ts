/**
 * Property 14: the tutor form asks exactly what the bot asks.
 *
 * The two channels must not drift: a tutor who answered a question in Telegram
 * and is asked it again in the Mini App has been asked twice, and the field
 * count is the cheapest way to notice that happened.
 */

import fc from "fast-check";
import { describe, expect, it } from "vitest";

import {
  ALL_TUTOR_STEPS,
  TUTOR_SUMMARY_STEP,
  TUTOR_STEPS,
  TUTOR_STEP_COUNT,
  stepForField,
  stepIndexFor,
} from "./steps";

// Feature: tedor-mini-app, Property 14: Tutor form field count matches the onboarding STEPS
describe("Property 14: Tutor form field count matches the onboarding STEPS", () => {
  it("has exactly the 26 collectable steps the bot conversation walks", () => {
    const nonSummary = ALL_TUTOR_STEPS.filter((step) => step.kind !== "summary");
    expect(nonSummary).toHaveLength(26);
    expect(TUTOR_STEP_COUNT).toBe(26);
  });

  it("mirrors the bot's 27 numbered steps, minus the voice step a WebApp cannot collect", () => {
    // 27 steps in app/bot/handlers/tutor_onboarding.py::STEPS, one of which is
    // `english_voice`, plus the summary screen.
    expect(TUTOR_STEPS).toHaveLength(26);
    expect(ALL_TUTOR_STEPS).toHaveLength(27);
    expect(TUTOR_STEPS.some((step) => step.field === "english_voice")).toBe(false);
  });

  // Feature: tedor-mini-app, Property 14: Tutor form field count matches the onboarding STEPS
  it("never repeats a field, whatever the step list", () => {
    fc.assert(
      fc.property(fc.constantFrom(...TUTOR_STEPS), (step) => {
        const occurrences = TUTOR_STEPS.filter((item) => item.field === step.field);
        expect(occurrences).toHaveLength(1);
      }),
    );
  });

  it("gives every step a label and a payload key", () => {
    for (const step of TUTOR_STEPS) {
      expect(step.field).toMatch(/^[a-z0-9_]+$/);
      expect(step.label.trim().length).toBeGreaterThan(0);
    }
  });

  it("asks the bot's fields in the bot's order", () => {
    // The order that matters: the profile is built top-down, and the entrance
    // score can only be normalised against the maximum asked immediately
    // before it.
    expect(TUTOR_STEPS.map((step) => step.field)).toEqual([
      "full_name",
      "gender",
      "age",
      "phone",
      "current_address",
      "locations",
      "grades",
      "subjects",
      "english_proficiency",
      "education_level",
      "university",
      "department",
      "university_year",
      "cgpa",
      "entrance_exam_max_score",
      "entrance_exam_score",
      "entrance_exam_year",
      "academic_stream",
      "grade8_exam_average",
      "grade9_average",
      "grade10_average",
      "grade11_average",
      "grade12_average",
      "teaching_experience_years",
      "teaching_experience_description",
      "documents",
    ]);
  });

  it("gives every choice and multiselect step its options", () => {
    for (const step of TUTOR_STEPS) {
      if (step.kind === "choice" || step.kind === "multiselect" || step.kind === "english") {
        expect(step.options ?? []).not.toHaveLength(0);
      }
    }
  });

  it("resolves any index back to a field, and any field back to an index", () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: TUTOR_STEPS.length - 1 }), (index) => {
        const step = TUTOR_STEPS[index];
        // stepIndexFor and stepForField must be inverses, or the job screen
        // could not jump a tutor to the first step their profile is missing.
        expect(stepIndexFor(step.field)).toBe(index);
        expect(stepForField(step.field)).toBe(step);
      }),
    );
  });

  it("reports -1 for a field the form does not collect", () => {
    fc.assert(
      fc.property(fc.string({ minLength: 1, maxLength: 12 }), (field) => {
        expect(stepIndexFor(field)).toBe(TUTOR_STEPS.findIndex((s) => s.field === field));
        expect(stepForField(field) ?? undefined).toBe(
          TUTOR_STEPS.find((s) => s.field === field),
        );
      }),
    );
  });

  it("keeps the summary screen out of the input steps", () => {
    expect(TUTOR_SUMMARY_STEP.kind).toBe("summary");
    expect(TUTOR_STEPS).not.toContain(TUTOR_SUMMARY_STEP);
    expect(stepForField("summary")).toBeUndefined();
  });
});