/**
 * Location search and selection.
 *
 * Search is debounced at 300 ms so typing a place name costs one request
 * rather than one per keystroke, and every selection is stored with the
 * canonical row the server owns (`id`, `normalizedName`) — the matching engine
 * compares on the normalized name, so a location typed as free text could
 * never be matched to a job (Requirements 4.1–4.5).
 *
 * An online-only tutor has no location constraint, so the step is not required
 * for them and an explicit banner says why (Requirement 4.5).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import api from "../api/client";
import type { SelectedLocation } from "../forms/tutor/store";

/** How long to wait after the last keystroke before searching. */
export const SEARCH_DEBOUNCE_MS = 300;

/** One suggestion as `GET /api/locations/search` returns it. */
export interface LocationSuggestion {
  id: number | null;
  name: string;
  normalizedName: string;
  city: string;
  latitude: number | null;
  longitude: number | null;
}

/** One suggestion as the search endpoint's `suggestions[]` entries look. */
interface RawSuggestion {
  placeId?: string;
  name?: string;
  formattedAddress?: string;
  city?: string;
  subCity?: string;
  latitude?: number;
  longitude?: number;
}

interface SearchResponse {
  query?: string;
  known?: {
    id: number;
    name: string;
    normalizedName: string;
    city: string;
    latitude: number | null;
    longitude: number | null;
  } | null;
  suggestions?: RawSuggestion[];
  error?: string | null;
}

/** The fields the form state keeps for every selection (Requirement 4.2). */
export const REQUIRED_LOCATION_FIELDS = [
  "id",
  "name",
  "normalizedName",
  "city",
] as const;

/**
 * Turn a suggestion into what the form stores.
 *
 * Every selection carries all four required fields plus coordinates: the API
 * links the profile to the canonical location row by id, and falls back to the
 * normalized name when the provider's suggestion has no row of its own.
 */
export function toSelectedLocation(
  suggestion: LocationSuggestion,
): SelectedLocation {
  return {
    id: suggestion.id ?? null,
    name: suggestion.name,
    normalizedName:
      suggestion.normalizedName || suggestion.name.trim().toLowerCase(),
    city: suggestion.city ?? "",
    latitude: suggestion.latitude ?? null,
    longitude: suggestion.longitude ?? null,
  };
}

/** Map a search response onto the selection shape. */
export function suggestionsFrom(response: SearchResponse): LocationSuggestion[] {
  const fromRaw: LocationSuggestion[] = (response.suggestions ?? []).map((item) => ({
    id: null,
    name: item.name || item.formattedAddress || "",
    normalizedName: (item.name || "").trim().toLowerCase(),
    city: item.city || item.subCity || "",
    latitude: item.latitude ?? null,
    longitude: item.longitude ?? null,
  }));
  if (response.known) {
    return [
      {
        id: response.known.id,
        name: response.known.name,
        normalizedName: response.known.normalizedName,
        city: response.known.city ?? "",
        latitude: response.known.latitude ?? null,
        longitude: response.known.longitude ?? null,
      },
      ...fromRaw,
    ];
  }
  return fromRaw;
}

export interface LocationSearchProps {
  /** The selected locations. */
  value: SelectedLocation[];
  onChange: (next: SelectedLocation[]) => void;
  /** True for an online-only tutor: the step is skipped, not required. */
  optional?: boolean;
  /** Error message for the whole field, shown above the control. */
  error?: string | null;
  /** Called with the selections for the draft/submit payload. */
  label?: string;
  /** Disable the control (e.g. while submitting). */
  disabled?: boolean;
}

export default function LocationSearch({
  value,
  onChange,
  optional = false,
  error = null,
  label = "Tutoring areas",
  disabled = false,
}: LocationSearchProps) {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<LocationSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [manualOpen, setManualOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runSearch = useCallback(async (term: string) => {
    setSearching(true);
    setSearchError(null);
    try {
      const response = await api.get<SearchResponse>(
        `/locations/search?q=${encodeURIComponent(term)}`,
      );
      setSuggestions(suggestionsFrom(response));
      setSearchError(response.error ?? null);
    } catch {
      setSuggestions([]);
      setSearchError("We could not search just now. Try adding it manually.");
    } finally {
      setSearching(false);
    }
  }, []);

  // Debounce: one request per pause in typing, not one per keystroke.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const term = query.trim();
    if (term.length < 2) {
      setSuggestions([]);
      return;
    }
    timer.current = setTimeout(() => {
      void runSearch(term);
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [query, runSearch]);

  const add = (location: SelectedLocation) => {
    const already = value.some((item) => item.name === location.name);
    if (already) return;
    onChange([...value, location]);
    setQuery("");
    setSuggestions([]);
    setOpen(false);
  };

  const remove = (name: string) => {
    onChange(value.filter((item) => item.name !== name));
  };

  return (
    <div className="space-y-3" data-testid="location-search">
      {optional ? (
        <p
          className="rounded-lg bg-sky-50 p-3 text-sm text-sky-800"
          data-testid="online-banner"
        >
          You teach online, so there is no area to choose. You can still add one if
          you also travel to families.
        </p>
      ) : null}

      <div>
        <label className="mb-1 block text-sm font-medium" htmlFor="location-query">
          {label}
        </label>
        <input
          id="location-query"
          type="search"
          value={query}
          disabled={disabled}
          placeholder="Start typing an area, e.g. Bole"
          className="w-full rounded-lg border border-slate-300 px-3 py-2"
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
        />
      </div>

      {searchError ? (
        <p className="text-sm text-amber-700" data-testid="location-search-error">
          {searchError}
        </p>
      ) : null}

      {open && suggestions.length > 0 ? (
        <ul
          className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white"
          data-testid="location-suggestions"
        >
          {suggestions.map((suggestion, index) => (
            <li key={`${suggestion.name}-${index}`}>
              <button
                type="button"
                className="w-full px-3 py-2 text-left text-sm hover:bg-slate-50"
                onClick={() => add(toSelectedLocation(suggestion))}
              >
                <span className="font-medium">{suggestion.name}</span>
                {suggestion.city ? (
                  <span className="ml-2 text-xs text-slate-500">{suggestion.city}</span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {open && searching ? (
        <p className="text-sm text-slate-500" data-testid="location-searching">
          Searching…
        </p>
      ) : null}

      {value.length > 0 ? (
        <ul className="flex flex-wrap gap-2" data-testid="selected-locations">
          {value.map((item) => (
            <li
              key={item.name}
              className="flex items-center gap-2 rounded-full bg-tedor-50 px-3 py-1 text-sm"
            >
              <span>{item.name}</span>
              <button
                type="button"
                aria-label={`Remove ${item.name}`}
                className="text-slate-500"
                onClick={() => remove(item.name)}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {error ? (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      ) : null}

      {manualOpen ? (
        <ManualLocationForm
          disabled={disabled}
          onCancel={() => setManualOpen(false)}
          onCreated={(location) => {
            add(location);
            setManualOpen(false);
          }}
        />
      ) : (
        <button
          type="button"
          className="text-sm text-tedor-700 underline"
          data-testid="add-manually"
          onClick={() => setManualOpen(true)}
        >
          Add manually
        </button>
      )}
    </div>
  );
}

/** The manual-entry sub-form (Requirement 4.4). */
function ManualLocationForm({
  onCreated,
  onCancel,
  disabled,
}: {
  onCreated: (location: SelectedLocation) => void;
  onCancel: () => void;
  disabled?: boolean;
}) {
  const [name, setName] = useState("");
  const [city, setCity] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!name.trim()) {
      setError("Please enter the area name.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const created = await api.post<{
        id: number;
        name: string;
        normalizedName: string;
        city: string;
        latitude: number | null;
        longitude: number | null;
      }>("/locations", { name: name.trim(), city: city.trim() });
      onCreated({
        id: created.id,
        name: created.name,
        normalizedName: created.normalizedName,
        city: created.city ?? "",
        latitude: created.latitude ?? null,
        longitude: created.longitude ?? null,
      });
    } catch {
      setError("We could not add that area. Please try again.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-2 rounded-lg border border-slate-200 bg-white p-3">
      <label className="block text-sm">
        <span className="mb-1 block font-medium">Area name</span>
        <input
          className="w-full rounded-lg border border-slate-300 px-3 py-2"
          value={name}
          disabled={disabled}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label className="block text-sm">
        <span className="mb-1 block font-medium">City (optional)</span>
        <input
          className="w-full rounded-lg border border-slate-300 px-3 py-2"
          value={city}
          disabled={disabled}
          onChange={(event) => setCity(event.target.value)}
        />
      </label>
      {error ? (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      ) : null}
      <div className="flex gap-2">
        <button
          type="button"
          className="rounded-lg bg-tedor-500 px-3 py-2 text-sm text-white disabled:opacity-50"
          disabled={saving || disabled}
          onClick={() => void submit()}
        >
          {saving ? "Adding…" : "Add area"}
        </button>
        <button
          type="button"
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}