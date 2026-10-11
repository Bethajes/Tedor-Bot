/**
 * The parent "Request a Tutor" form.
 *
 * The same fields as the bot conversation, driven by ``steps.ts``. Two rules
 * differ from a naive copy of the bot flow and both come from the data:
 *
 * * The budget is labelled in the request's own currency — ETB for an Ethiopian
 *   family, USD for everybody else — because the number they type is stored in
 *   exactly that currency.
 * * The location step disappears entirely for an online-only request: there is
 *   no place to go, and asking for one would be a step they cannot complete
 *   (Requirement 6.3).
 *
 * On submit the parent gets the public request ID, plus any tutors already
 * matched to it, each with a Select Tutor button (Requirement 6.5).
 */

import { useEffect, useMemo, useState } from "react";

import ProgressBar from "../../components/ProgressBar";
import LocationSearch from "../../components/LocationSearch";
import api from "../../api/client";
import type { SelectedLocation } from "../tutor/store";
import { budgetLabel, currencyForCountry } from "./currency";
import { PARENT_STEPS, stepsForMode, type ParentStep } from "./steps";

/** One matched tutor, as `POST /api/miniapp/parent/submit` returns them. */
export interface MatchedTutor {
  public_tutor_id: string;
  display_name: string;
  subjects: string[];
  levels: string[];
  teaching_mode: string;
  city: string;
  score: number;
  max_score: number;
  reasons: string[];
}

export interface SubmitResult {
  public_request_id: string;
  status: string;
  currency: string;
  matches: MatchedTutor[];
}

type Values = Record<string, unknown>;

export default function ParentForm() {
  const [values, setValues] = useState<Values>({});
  const [stepIndex, setStepIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [result, setResult] = useState<SubmitResult | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // An online-only request drops the location step, so the step list is
  // recomputed whenever the mode changes and the index follows it.
  const steps = useMemo(() => stepsForMode(values.mode), [values.mode]);

  useEffect(() => {
    if (stepIndex > steps.length - 1) setStepIndex(Math.max(0, steps.length - 1));
  }, [steps, stepIndex]);

  // The draft is saved on every step change, like the tutor form.
  useEffect(() => {
    void api
      .post("/miniapp/parent/draft", { ...values, _step: stepIndex })
      .catch(() => undefined);
  }, [values, stepIndex]);

  const setValue = (field: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [field]: value }));
    setError(null);
  };

  const validate = (step: ParentStep): string | null => {
    const value = values[step.field];
    const blank =
      value === undefined ||
      value === null ||
      value === "" ||
      (Array.isArray(value) && value.length === 0);
    if (step.kind === "location") {
      if (!blank) return null;
      return "Please choose the area where the tutoring will happen.";
    }
    if (blank) {
      if (step.optional) return null;
      return "This field is required.";
    }
    if (step.field === "budget") {
      const amount = Number(value);
      if (!Number.isFinite(amount) || amount < 0) {
        return "Please enter the budget as a number.";
      }
    }
    if (step.field === "contact") {
      const text = String(value).trim();
      if (!/^[+0-9][0-9\s\-()]{5,}$/.test(text) && !/^@[A-Za-z0-9_]{4,}$/.test(text)) {
        return "Please enter a phone number or a Telegram username.";
      }
    }
    return null;
  };

  const next = () => {
    const step = steps[stepIndex];
    if (!step) return;
    const message = validate(step);
    setError(message);
    if (message) return;
    setStepIndex((index) => Math.min(index + 1, steps.length));
  };

  const back = () => {
    setError(null);
    setStepIndex((index) => Math.max(0, index - 1));
  };

  const submit = async () => {
    for (const step of steps) {
      const message = validate(step);
      if (message) {
        setError(message);
        setStepIndex(steps.indexOf(step));
        return;
      }
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      const payload = {
        ...values,
        location:
          values.location === undefined
            ? ""
            : ((values.location as SelectedLocation[])?.[0]?.name ?? ""),
        location_selections: values.location ?? [],
      };
      const response = await api.post<SubmitResult>("/miniapp/parent/submit", payload);
      setResult(response);
    } catch (caught) {
      setSubmitError(
        caught instanceof Error ? caught.message : "We could not send your request.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (result) {
    return (
      <div className="p-4" data-testid="parent-confirmation">
        <h1 className="mb-2 text-lg font-semibold">Request sent</h1>
        <p className="mb-4 text-sm text-slate-600">
          Your request ID is <strong>{result.public_request_id}</strong>. Keep it for
          any follow-up with our team.
        </p>

        {result.matches.length > 0 ? (
          <div data-testid="parent-matches">
            <h2 className="mb-2 font-medium">Tutors who fit this request</h2>
            <ul className="space-y-2">
              {result.matches.map((match) => (
                <li
                  key={match.public_tutor_id}
                  className="rounded-lg border border-slate-200 bg-white p-3"
                >
                  <p className="font-medium">{match.display_name}</p>
                  <p className="text-xs text-slate-500">
                    {match.public_tutor_id} · Match {match.score}/{match.max_score}
                  </p>
                  <button
                    type="button"
                    className="mt-2 rounded-lg bg-tedor-500 px-3 py-1 text-sm text-white"
                    data-testid={`select-tutor-${match.public_tutor_id}`}
                    onClick={() =>
                      api
                        .post("/miniapp/parent/select-tutor", {
                          request_id: result.public_request_id,
                          tutor_id: match.public_tutor_id,
                        })
                        .catch(() => undefined)
                    }
                  >
                    Select Tutor
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    );
  }

  const step = steps[stepIndex];
  const isLast = stepIndex === steps.length - 1;
  const locations = (values.location as SelectedLocation[] | undefined) ?? [];

  return (
    <div className="p-4" data-testid="parent-form">
      <ProgressBar current={stepIndex + 1} total={steps.length} />

      {step ? (
        <>
          <h1 className="mb-1 text-lg font-semibold">
            {step.field === "budget"
              ? budgetLabel(values.country as string)
              : step.label}
          </h1>
          {step.help ? <p className="mb-3 text-sm text-slate-600">{step.help}</p> : null}
          {step.field === "budget" ? (
            <p className="mb-2 text-xs text-slate-500" data-testid="budget-currency">
              Amounts are in {currencyForCountry(values.country as string)}.
            </p>
          ) : null}

          {step.kind === "location" ? (
            <LocationSearch
              value={locations}
              onChange={(next) => setValue("location", next)}
              error={error}
              label="Where should the tutoring happen?"
            />
          ) : (
            <StepControl
              step={step}
              values={values}
              error={error}
              onChange={setValue}
            />
          )}
        </>
      ) : null}

      {submitError ? (
        <p className="mt-2 text-sm text-red-600" role="alert" data-testid="parent-submit-error">
          {submitError}
        </p>
      ) : null}

      <div className="mt-6 flex gap-2">
        {stepIndex > 0 ? (
          <button
            type="button"
            className="rounded-lg border border-slate-300 px-4 py-2"
            onClick={back}
          >
            Back
          </button>
        ) : null}
        {isLast ? (
          <button
            type="button"
            className="flex-1 rounded-lg bg-tedor-500 px-4 py-2 text-white disabled:opacity-50"
            data-testid="parent-submit"
            disabled={submitting}
            onClick={() => void submit()}
          >
            {submitting ? "Sending…" : "Send request"}
          </button>
        ) : (
          <button
            type="button"
            className="flex-1 rounded-lg bg-tedor-500 px-4 py-2 text-white"
            data-testid="parent-next"
            onClick={next}
          >
            Next
          </button>
        )}
      </div>
    </div>
  );
}

/** The control for a step, chosen by its kind. */
function StepControl({
  step,
  values,
  error,
  onChange,
}: {
  step: ParentStep;
  values: Values;
  error: string | null;
  onChange: (field: string, value: unknown) => void;
}) {
  const inputClass = "w-full rounded-lg border border-slate-300 px-3 py-2";

  if (step.kind === "choice" || step.kind === "multiselect") {
    const selected = step.kind === "multiselect" ? ((values[step.field] as string[]) ?? []) : [];
    return (
      <fieldset>
        <legend className="sr-only">{step.label}</legend>
        <div className={step.options && step.options.length > 8 ? "grid gap-2" : "grid grid-cols-2 gap-2"}>
          {(step.options ?? []).map((option) => {
            const isSelected =
              step.kind === "multiselect"
                ? selected.includes(option.value)
                : values[step.field] === option.value;
            return (
              <label
                key={option.value}
                className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm ${
                  isSelected ? "border-tedor-500 bg-tedor-50" : "border-slate-200"
                }`}
              >
                <input
                  type={step.kind === "multiselect" ? "checkbox" : "radio"}
                  name={step.field}
                  value={option.value}
                  checked={isSelected}
                  onChange={(event) => {
                    if (step.kind === "multiselect") {
                      onChange(
                        step.field,
                        event.target.checked
                          ? [...selected, option.value]
                          : selected.filter((item) => item !== option.value),
                      );
                    } else {
                      onChange(step.field, option.value);
                    }
                  }}
                />
                <span>{option.label}</span>
              </label>
            );
          })}
        </div>
        {error ? (
          <p className="mt-2 text-sm text-red-600" role="alert">
            {error}
          </p>
        ) : null}
      </fieldset>
    );
  }

  return (
    <div>
      <label className="mb-1 block text-sm font-medium" htmlFor={`field-${step.field}`}>
        {step.label}
      </label>
      <input
        id={`field-${step.field}`}
        data-testid={`input-${step.field}`}
        className={inputClass}
        type={step.kind === "float" ? "number" : "text"}
        step={step.kind === "float" ? "0.01" : undefined}
        value={(values[step.field] as string | number | undefined) ?? ""}
        onChange={(event) => onChange(step.field, event.target.value)}
      />
      {error ? (
        <p className="mt-1 text-sm text-red-600" role="alert" data-testid={`error-${step.field}`}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

export { PARENT_STEPS };