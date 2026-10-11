/**
 * Property (frontend): client validators agree with the server.
 *
 * The client checks exist to save a tutor a round trip, not to make a second
 * decision. So the rules are stated here independently of `validators.ts` —
 * as the range the *server* enforces — and every generated value must land on
 * the same side of it.
 *
 * The backend suite pins the server's ranges
 * (`tests/test_profile_service.py`), so the two ends are checked from both
 * directions: this file against the documented rule, that file against the
 * implementation.
 */

import fc from "fast-check";
import { describe, expect, it } from "vitest";

import {
  AGE_MAX,
  AGE_MIN,
  CGPA_MAX,
  CGPA_MIN,
  ENGLISH_MAX,
  ENGLISH_MIN,
  PHONE_RE,
  validateAge,
  validateCgpa,
  validateEnglishProficiency,
  validatePhone,
  validateYear,
} from "./validators";

// ---------------------------------------------------------------------------
// The server's rules, restated (app/config.py + tutor_profile_service.py)
// ---------------------------------------------------------------------------

/** `validate_age`: a whole number between TUTOR_MIN_AGE and TUTOR_MAX_AGE. */
const serverAcceptsAge = (value: number): boolean =>
  Number.isInteger(value) && value >= AGE_MIN && value <= AGE_MAX;

/** `validate_english_proficiency`: a whole number from 1 to 10. */
const serverAcceptsEnglish = (value: number): boolean =>
  Number.isInteger(value) && value >= ENGLISH_MIN && value <= ENGLISH_MAX;

/** `validate_cgpa`: 0 to CGPA_MAX inclusive. */
const serverAcceptsCgpa = (value: number): boolean =>
  value >= CGPA_MIN && value <= CGPA_MAX;

/** `validate_year`: a whole number from 1950 to this year + 6. */
const serverAcceptsYear = (value: number): boolean => {
  const max = new Date().getFullYear() + 6;
  return Number.isInteger(value) && value >= 1950 && value <= max;
};

/** The client's answer for a numeric field, as a string, is never a surprise. */
function accepts(validate: (value: unknown) => string | null, value: number): boolean {
  return validate(String(value)) === null;
}

// Feature: tedor-mini-app, Property: client-side age/english/cgpa validators match server rules
describe("client-side age/english/cgpa validators match server rules", () => {
  it("agrees with the server on age, for every value in a wide range", () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 130 }), (value) => {
        expect(accepts(validateAge, value)).toBe(serverAcceptsAge(value));
      }),
      { numRuns: 1000 },
    );
  });

  it("agrees with the server on age, for values typed as text", () => {
    fc.assert(
      fc.property(fc.integer({ min: -50, max: 150 }), (value) => {
        // A phone keyboard sends strings, padded with the spaces people type:
        // " 27 " must be accepted exactly as 27 is.
        const message = validateAge(` ${value} `);
        if (serverAcceptsAge(value)) {
          expect(message).toBeNull();
        } else {
          expect(typeof message).toBe("string");
          expect(message).not.toBe("");
        }
      }),
      { numRuns: 500 },
    );
  });

  it("agrees with the server on English proficiency, for every value around the scale", () => {
    fc.assert(
      fc.property(fc.integer({ min: -5, max: 15 }), (value) => {
        expect(accepts(validateEnglishProficiency, value)).toBe(
          serverAcceptsEnglish(value),
        );
      }),
      { numRuns: 1000 },
    );
  });

  it("agrees with the server on CGPA, for any decimal", () => {
    fc.assert(
      fc.property(fc.double({ min: -1, max: 5, noNaN: true }), (value) => {
        expect(accepts(validateCgpa, value)).toBe(serverAcceptsCgpa(value));
      }),
      { numRuns: 1000 },
    );
  });

  it("agrees with the server on the university and exam years", () => {
    fc.assert(
      fc.property(fc.integer({ min: 1940, max: 2100 }), (value) => {
        expect(accepts(validateYear, value)).toBe(serverAcceptsYear(value));
      }),
      { numRuns: 1000 },
    );
  });

  it("agrees with the server on the phone format", () => {
    // The server's PHONE_RE is /^\+?[0-9][0-9\s\-()]{6,19}$/; the client must
    // accept and refuse exactly the same strings.
    const serverPhoneRe = /^\+?[0-9][0-9\s\-()]{6,19}$/;
    fc.assert(
      fc.property(
        fc.stringMatching(/^[+0-9 ()-]{0,25}$/),
        (value) => {
          const client = validatePhone(value);
          const server = value.trim() !== "" && serverPhoneRe.test(value.trim());
          expect(client === null).toBe(server);
        },
      ),
      { numRuns: 1000 },
    );
  });

  it("uses the same ranges the server is configured with", () => {
    // A deployment that changes TUTOR_MIN_AGE changes what the server accepts;
    // these are the defaults it ships with.
    expect([AGE_MIN, AGE_MAX]).toEqual([18, 100]);
    expect([ENGLISH_MIN, ENGLISH_MAX]).toEqual([1, 10]);
    expect([CGPA_MIN, CGPA_MAX]).toEqual([0, 4]);
    expect(PHONE_RE.source).toBe(/^\+?[0-9][0-9\s\-()]{6,19}$/.source);
  });
});