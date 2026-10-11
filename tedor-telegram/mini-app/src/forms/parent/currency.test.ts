/**
 * Property 18: currency selection matches country.
 *
 * An Ethiopian family quotes ETB and everybody else quotes USD. The number a
 * parent types is stored in exactly the currency the screen showed them, so
 * the two ends have to agree for every country string — including the ones
 * nobody types deliberately, like " Ethiopia " or "ethiopia 🇪🇹".
 */

import fc from "fast-check";
import { describe, expect, it } from "vitest";

import {
  ETHIOPIA_ALIASES,
  budgetLabel,
  currencyForCountry,
  formatBudget,
  isEthiopia,
  normaliseCountry,
} from "./currency";

/** The server's rule, restated: `app/services/currency.py::_ETHIOPIA_ALIASES`. */
const SERVER_ALIASES = [
  "ethiopia",
  "ethiopian",
  "et",
  "eth",
  "addis ababa, ethiopia",
  "ethiopia 🇪🇹",
];

// Property 18: Currency selection matches country
describe("Property 18: Currency selection matches country", () => {
  it("returns ETB for Ethiopia and USD for every other country", () => {
    fc.assert(
      fc.property(
        fc.oneof(
          fc.constantFrom(...SERVER_ALIASES),
          fc.constantFrom(
            "Kenya",
            "Eritrea",
            "Canada",
            "Germany",
            "Saudi Arabia",
            "United Arab Emirates",
            "Other",
          ),
        ),
        (country) => {
          expect(currencyForCountry(country)).toBe(isEthiopia(country) ? "ETB" : "USD");
        },
      ),
      { numRuns: 500 },
    );
  });

  it("is not fooled by case or surrounding whitespace", () => {
    fc.assert(
      fc.property(fc.constantFrom(...SERVER_ALIASES), fc.integer({ min: 0, max: 3 }), (alias, pad) => {
        const padded = `${" ".repeat(pad)}${alias.toUpperCase()}${" ".repeat(pad)}`;
        // The server lower-cases and trims before comparing; so must this.
        expect(currencyForCountry(padded)).toBe(
          SERVER_ALIASES.includes(padded.trim().toLowerCase()) ? "ETB" : "USD",
        );
      }),
      { numRuns: 300 },
    );
  });

  it("never invents ETB for a country that merely mentions it", () => {
    fc.assert(
      fc.property(
        fc.stringMatching(/^[A-Za-z ]{1,20}$/),
        (country) => {
          if (!SERVER_ALIASES.includes(country.trim().toLowerCase())) {
            expect(currencyForCountry(country)).toBe("USD");
          }
        },
      ),
      { numRuns: 1000 },
    );
  });

  it("treats a missing country as USD rather than guessing ETB", () => {
    for (const empty of ["", "   ", null, undefined]) {
      expect(currencyForCountry(empty)).toBe("USD");
      expect(isEthiopia(empty)).toBe(false);
    }
  });

  it("labels the budget in the request's own currency", () => {
    expect(budgetLabel("Ethiopia")).toBe("Hourly budget (ETB)");
    expect(budgetLabel("Kenya")).toBe("Hourly budget (USD)");
    expect(budgetLabel("ethiopian")).toBe("Hourly budget (ETB)");
    expect(budgetLabel("Other")).toBe("Hourly budget (USD)");
  });

  it("renders a budget in that same currency", () => {
    expect(formatBudget(500, "Ethiopia")).toBe("500 ETB");
    expect(formatBudget(500, "Kenya")).toBe("500 USD");
    expect(formatBudget(null, "Ethiopia")).toBe("—");
    expect(formatBudget("", "Kenya")).toBe("—");
  });

  it("uses the server's own alias list", () => {
    // A divergence here means the screen and the stored request disagree.
    expect([...ETHIOPIA_ALIASES]).toEqual(SERVER_ALIASES);
    expect(normaliseCountry("  Kenya  ")).toBe("Kenya");
  });
});