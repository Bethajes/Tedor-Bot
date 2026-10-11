/**
 * Property (frontend): location selection stores all required fields.
 *
 * A tutoring location is stored as the canonical row the matching engine
 * compares on. Anything the profile loses here — the id, the normalized name —
 * is a location that will never match a job, however carefully it was typed,
 * so the selection carries all of it or the form is wrong.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import fc from "fast-check";
import { useState } from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import LocationSearch, {
  REQUIRED_LOCATION_FIELDS,
  toSelectedLocation,
  type LocationSuggestion,
} from "./LocationSearch";
import type { SelectedLocation } from "../forms/tutor/store";

/** Every field the form state must carry for a selected location. */
function assertCarriesRequiredFields(location: SelectedLocation) {
  for (const field of REQUIRED_LOCATION_FIELDS) {
    expect(location).toHaveProperty(field);
  }
  expect(typeof location.id === "number" || location.id === null).toBe(true);
  expect(typeof location.name).toBe("string");
  expect(location.name.length).toBeGreaterThan(0);
  expect(typeof location.normalizedName).toBe("string");
  expect(location.normalizedName.length).toBeGreaterThan(0);
  expect(typeof location.city).toBe("string");
}

// A name that is always a plausible place: non-empty, no control characters.
const placeName = fc
  .stringMatching(/^[A-Za-z][A-Za-z ]{1,24}$/)
  .filter((value) => value.trim().length > 1);

const coordinate = fc.double({ min: 3, max: 15, noNaN: true });
const longitude = fc.double({ min: 30, max: 50, noNaN: true });
const nullableCoordinate = fc.oneof(fc.constant(null), coordinate);
const idOrNull = fc.oneof(fc.constant(null), fc.integer({ min: 1, max: 100000 }));

const suggestion: fc.Arbitrary<LocationSuggestion> = fc.record({
  id: idOrNull,
  name: placeName,
  normalizedName: placeName,
  city: fc.oneof(fc.constant(""), placeName),
  latitude: nullableCoordinate,
  longitude: fc.oneof(fc.constant(null), longitude),
});

// Feature: tedor-mini-app, Property: location selection stores required fields
describe("Property: location selection stores required fields", () => {
  it("keeps every required field for any suggestion", () => {
    fc.assert(
      fc.property(suggestion, (item) => {
        const selected = toSelectedLocation(item);
        assertCarriesRequiredFields(selected);
        // The fields are carried through unchanged: the server links the
        // profile by id and compares on the normalized name.
        expect(selected.id).toBe(item.id);
        expect(selected.name).toBe(item.name);
        expect(selected.city).toBe(item.city);
        expect(selected.latitude).toBe(item.latitude);
        expect(selected.longitude).toBe(item.longitude);
      }),
      { numRuns: 500 },
    );
  });

  it("falls back to a normalized name when the provider gives none", () => {
    fc.assert(
      fc.property(placeName, (name) => {
        const selected = toSelectedLocation({
          id: null,
          name,
          normalizedName: "",
          city: "",
          latitude: null,
          longitude: null,
        });
        expect(selected.normalizedName).toBe(name.trim().toLowerCase());
        assertCarriesRequiredFields(selected);
      }),
      { numRuns: 300 },
    );
  });

  // --- component behaviour -------------------------------------------------

  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    // The API attaches credentials itself; a browser with Init Data is the
    // normal case inside Telegram.
    (window as unknown as { Telegram: unknown }).Telegram = {
      WebApp: { initData: "signed-init-data" },
    };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("stores every required field when a suggestion is picked", async () => {
    const user = userEvent.setup();
    let latest: SelectedLocation[] = [];

    function Harness() {
      const [value, setValue] = useState<SelectedLocation[]>([]);
      latest = value;
      return (
        <>
          <LocationSearch value={value} onChange={setValue} />
          <output data-testid="state">{JSON.stringify(value)}</output>
        </>
      );
    }

    const example = {
      id: 42,
      name: "Bole",
      normalizedName: "bole",
      city: "Addis Ababa",
      latitude: 9.0192,
      longitude: 38.7525,
    };

    render(<Harness />);

    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({ suggestions: [{ name: "Bole", city: "Addis Ababa" }], known: example }),
    });
    await user.type(screen.getByLabelText("Tutoring areas"), "Bol");
    // The response names "Bole" twice: once as our own canonical row and once
    // as the provider's suggestion. The first entry is the one to pick.
    const [canonical] = await screen.findAllByText("Bole", {}, { timeout: 3000 });
    await user.click(canonical);

    await waitFor(() => expect(latest).toHaveLength(1));
    assertCarriesRequiredFields(latest[0]);
    expect(latest[0].name).toBe("Bole");
    expect(latest[0].id).toBe(42);
    expect(JSON.parse(screen.getByTestId("state").textContent ?? "[]")).toHaveLength(1);
  });

  it("removes a selected location without touching the others", async () => {
    const user = userEvent.setup();
    let latest: SelectedLocation[] = [];

    function Harness() {
      const [value, setValue] = useState<SelectedLocation[]>([
        { id: 1, name: "Bole", normalizedName: "bole", city: "Addis Ababa", latitude: null, longitude: null },
        { id: 2, name: "CMC", normalizedName: "cmc", city: "Addis Ababa", latitude: null, longitude: null },
      ]);
      latest = value;
      return <LocationSearch value={value} onChange={setValue} />;
    }

    render(<Harness />);
    await user.click(screen.getByLabelText("Remove Bole"));

    await waitFor(() => expect(latest).toHaveLength(1));
    expect(latest[0].name).toBe("CMC");
  });

  it("skips the required-field check for an online-only tutor", () => {
    render(<LocationSearch value={[]} onChange={() => {}} optional />);
    expect(screen.getByTestId("online-banner")).toBeInTheDocument();
    expect(screen.getByTestId("add-manually")).toBeInTheDocument();
  });
});