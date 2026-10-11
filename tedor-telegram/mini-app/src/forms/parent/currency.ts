/**
 * Currency selection, mirroring `app/services/currency.py`.
 *
 * An Ethiopian family quotes ETB and everybody else quotes USD. The label the
 * parent sees has to agree with what the request is stored as, so the alias
 * list is copied rather than trimmed: "Ethiopia", "Ethiopian", "ET", "ETH" and
 * "Addis Ababa, Ethiopia" all mean the same country there, and getting this
 * wrong prices a request in a currency nobody will read.
 *
 * Requirements: 6.2
 */

export type Currency = "ETB" | "USD";

/** Mirrors `currency._ETHIOPIA_ALIASES`, lower-cased. */
export const ETHIOPIA_ALIASES: readonly string[] = [
  "ethiopia",
  "ethiopian",
  "et",
  "eth",
  "addis ababa, ethiopia",
  "ethiopia 🇪🇹",
];

/** Mirrors `currency.normalise_country`. */
export function normaliseCountry(country: string | null | undefined): string {
  return (country ?? "").trim();
}

/** Mirrors `currency.is_ethiopia`: true only for an Ethiopian country string. */
export function isEthiopia(country: string | null | undefined): boolean {
  return ETHIOPIA_ALIASES.includes(normaliseCountry(country).toLowerCase());
}

/** The primary currency for a country. */
export function currencyForCountry(country: string | null | undefined): Currency {
  return isEthiopia(country) ? "ETB" : "USD";
}

/** The symbol shown next to a budget input. */
export function currencySymbol(country: string | null | undefined): string {
  return currencyForCountry(country);
}

/** A budget rendered in the right currency. */
export function formatBudget(
  amount: number | string | null | undefined,
  country: string | null | undefined,
): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  const value = Number(amount);
  if (!Number.isFinite(value)) return "—";
  return `${value} ${currencyForCountry(country)}`;
}

/** The label for the budget step, which changes with the country. */
export function budgetLabel(country: string | null | undefined): string {
  return `Hourly budget (${currencyForCountry(country)})`;
}