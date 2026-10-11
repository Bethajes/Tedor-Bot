/**
 * The tutor application form.
 *
 * One step at a time, driven entirely by ``steps.ts``: the same fields, in the
 * same order, as the bot conversation — so a tutor who starts in Telegram and
 * finishes in the Mini App never answers the same question twice.
 *
 * Navigation is Back / Next / Submit with a progress bar, validation runs for
 * the current step on Next (inline errors, scrolled to the first one), and the
 * draft is persisted to the server on every step change by the store's
 * middleware (Requirements 3.1–3.7, 8.1–8.4).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import ProgressBar from "../../components/ProgressBar";
import FileUpload from "../../components/FileUpload";
import LocationSearch from "../../components/LocationSearch";
import api from "../../api/client";
import { TUTOR_STEPS, type TutorStep } from "./steps";
import { useTutorStore, type SelectedLocation, type UploadedDocument } from "./store";
import { validateStepValue } from "./validators";

/** Document types this tutor must supply, mirroring the server's rule. */
export function requiredDocumentTypes(requiredTypes?: string[]): string[] {
  if (requiredTypes && requiredTypes.length > 0) return requiredTypes;
  // With no level chosen yet, the pair every Ethiopian application needs.
  return ["UNIVERSITY_TRANSCRIPT", "UNIVERSITY_ENTRANCE_RESULT"];
}

export interface TutorFormProps {
  /**
   * Open on this step instead of the first.
   *
   * The job screen sends a tutor here with the first step their profile is
   * missing (Requirement 7.2), so they are not asked again for what they
   * already filled in.
   */
  startAt?: number | null;
}

export default function TutorForm({ startAt = null }: TutorFormProps = {}) {
  const {
    values,
    stepIndex,
    saving,
    draftLoaded,
    draftStepIndex,
    error,
    submitError,
    submitted,
    setValue,
    goToStep,
    resumeDraft,
    discardDraft,
    submit,
  } = useTutorStore();

  const [fieldError, setFieldError] = useState<string | null>(null);
  const [resumeOffered, setResumeOffered] = useState(false);
  const [existingDocuments, setExistingDocuments] = useState<string[]>([]);
  const [documentsReady, setDocumentsReady] = useState(false);
  const topRef = useRef<HTMLDivElement | null>(null);

  // Load the draft and, for a returning tutor, which documents are already on
  // file. One effect, so the form cannot open on a step it is about to replace.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const hasDraft = await useTutorStore.getState().loadDraft();
      if (cancelled) return;

      if (startAt !== null) {
        // Arrived from the job screen: go straight to the missing step rather
        // than offering to resume where they were before (Requirement 7.2).
        useTutorStore.getState().goToStep(startAt);
        return;
      }
      if (hasDraft && useTutorStore.getState().draftStepIndex !== null) {
        setResumeOffered(true);
      }

      try {
        const profile = await api.get<{ document_types?: string[] }>(
          "/miniapp/tutor/profile",
        );
        if (!cancelled) setExistingDocuments(profile?.document_types ?? []);
      } catch {
        // No profile yet: every document is still outstanding, which is the
        // correct thing to show a first-time applicant.
      }
    })();
    return () => {
      cancelled = true;
    };
    // Runs once on mount: `startAt` is fixed for the life of the screen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const step: TutorStep | undefined = TUTOR_STEPS[stepIndex];
  const isReview = step === undefined;

  const validateCurrent = useCallback((): boolean => {
    if (!step) return true;
    if (step.kind === "documents") {
      if (!documentsReady) {
        setFieldError("Please attach every required document.");
        return false;
      }
      setFieldError(null);
      return true;
    }
    const message = validateStepValue(
      step.field,
      values[step.field],
      values,
      step.optional === true,
    );
    setFieldError(message);
    if (message) {
      topRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      return false;
    }
    return true;
  }, [step, values, documentsReady]);

  const nextStep = () => {
    if (!validateCurrent()) return;
    goToStep(stepIndex + 1);
  };

  const previousStep = () => {
    setFieldError(null);
    goToStep(Math.max(0, stepIndex - 1));
  };

  const locations = (values.locations as SelectedLocation[] | undefined) ?? [];
  const documentTypes = useMemo(
    () => requiredDocumentTypes(),
    [values.education_level],
  );
  // Documents already on file, plus any uploaded before the application was
  // submitted. A first-time applicant has no profile at all yet, so the
  // profile call 404s and the staged uploads are the only record that those
  // files exist — without this they would be asked for twice.
  const uploadedTypes = useMemo(() => {
    const staged = (values.documents as { documentType: string }[] | undefined) ?? [];
    return Array.from(
      new Set([...existingDocuments, ...staged.map((d) => d.documentType)]),
    );
  }, [existingDocuments, values.documents]);

  if (submitted) {
    return (
      <div className="p-4 text-center" data-testid="tutor-submitted">
        <h1 className="mb-2 text-lg font-semibold">
          {submitted.alreadyApplied ? "Application already on file" : "Application received"}
        </h1>
        <p className="mb-1">
          Your TDR ID is <strong>{submitted.publicTutorId}</strong>.
        </p>
        <p className="text-sm text-slate-600">
          {submitted.alreadyApplied
            ? "We already have your application, so nothing needs resending. Our team is reviewing it now."
            : "Our team is reviewing your profile and documents now."}
        </p>
      </div>
    );
  }

  if (!draftLoaded) {
    return (
      <div className="p-4 text-center text-sm text-slate-500" data-testid="tutor-loading">
        Loading your application…
      </div>
    );
  }

  if (resumeOffered) {
    return (
      <div className="p-4" data-testid="resume-prompt">
        <h1 className="mb-2 text-lg font-semibold">Continue your application?</h1>
        <p className="mb-4 text-sm text-slate-600">
          We saved your answers up to step {(draftStepIndex ?? 0) + 1}.
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            className="rounded-lg bg-tedor-500 px-4 py-2 text-white"
            data-testid="resume-draft"
            onClick={() => {
              resumeDraft();
              setResumeOffered(false);
            }}
          >
            Continue where I left off
          </button>
          <button
            type="button"
            className="rounded-lg border border-slate-300 px-4 py-2"
            data-testid="start-over"
            onClick={() => {
              discardDraft();
              setResumeOffered(false);
            }}
          >
            Start again
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-4" data-testid="tutor-form">
      <ProgressBar
        current={Math.min(stepIndex + 1, TUTOR_STEPS.length)}
        total={TUTOR_STEPS.length}
      />
      {saving ? (
        <p className="mb-2 text-xs text-slate-500" data-testid="saving">
          Saving…
        </p>
      ) : null}

      <div ref={topRef}>
        {isReview ? (
          <ReviewSummary values={values} documentsReady={documentsReady} />
        ) : (
          <>
            <h1 className="mb-1 text-lg font-semibold">{step.label}</h1>
            {step.help ? (
              <p className="mb-3 text-sm text-slate-600">{step.help}</p>
            ) : null}
            <StepControl
              step={step}
              values={values}
              error={fieldError}
              locations={locations}
              documentTypes={documentTypes}
              existingDocuments={uploadedTypes}
              onChange={(value) => setValue(step.field, value)}
              onDocumentsReady={setDocumentsReady}
            />
          </>
        )}
      </div>

      {error ? (
        <p className="mt-2 text-sm text-amber-700" role="status">
          {error}
        </p>
      ) : null}
      {submitError ? (
        <p className="mt-2 text-sm text-red-600" role="alert" data-testid="submit-error">
          {submitError}
        </p>
      ) : null}

      <div className="mt-6 flex gap-2">
        {stepIndex > 0 ? (
          <button
            type="button"
            className="rounded-lg border border-slate-300 px-4 py-2"
            onClick={previousStep}
          >
            Back
          </button>
        ) : null}
        {isReview ? (
          <button
            type="button"
            className="flex-1 rounded-lg bg-tedor-500 px-4 py-2 text-white"
            data-testid="submit-application"
            onClick={() => void submit()}
          >
            Submit application
          </button>
        ) : (
          <button
            type="button"
            className="flex-1 rounded-lg bg-tedor-500 px-4 py-2 text-white"
            data-testid="next-step"
            onClick={nextStep}
          >
            {step?.optional ? "Skip" : "Next"}
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
  locations,
  documentTypes,
  existingDocuments,
  onChange,
  onDocumentsReady,
}: {
  step: TutorStep;
  values: Record<string, unknown>;
  error: string | null;
  locations: SelectedLocation[];
  documentTypes: string[];
  existingDocuments: string[];
  onChange: (value: unknown) => void;
  onDocumentsReady: (ready: boolean) => void;
}) {
  const inputClass = "w-full rounded-lg border border-slate-300 px-3 py-2";

  if (step.kind === "location") {
    return (
      <LocationSearch
        value={locations}
        onChange={onChange}
        optional={false}
        error={error}
      />
    );
  }

  if (step.kind === "documents") {
    return (
      <div>
        <FileUpload
          requiredTypes={documentTypes}
          existingTypes={existingDocuments}
          onComplete={onDocumentsReady}
          onChange={(documents: UploadedDocument[]) => onChange(documents)}
        />
        {/* The step-level error lives here rather than inside FileUpload:
            without it, tapping Next on an incomplete upload step did nothing
            visible at all, which reads to the tutor as a dead button. */}
        {error ? (
          <p
            className="mt-3 rounded-lg bg-red-50 p-3 text-sm font-medium text-red-700"
            role="alert"
            data-testid="error-documents"
          >
            {error}
          </p>
        ) : null}
      </div>
    );
  }

  if (step.kind === "multiselect") {
    return (
      <fieldset>
        <legend className="sr-only">{step.label}</legend>
        <div className="grid grid-cols-2 gap-2">
          {(step.options ?? []).map((option) => {
            const selected = ((values[step.field] as string[]) ?? []).includes(option.value);
            return (
              <label
                key={option.value}
                className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm ${
                  selected ? "border-tedor-500 bg-tedor-50" : "border-slate-200"
                }`}
              >
                <input
                  type="checkbox"
                  checked={selected}
                  onChange={(event) => {
                    const current = (values[step.field] as string[]) ?? [];
                    onChange(
                      event.target.checked
                        ? [...current, option.value]
                        : current.filter((item) => item !== option.value),
                    );
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

  if (step.kind === "choice" || step.kind === "english") {
    return (
      <fieldset>
        <legend className="sr-only">{step.label}</legend>
        <div className="grid gap-2">
          {(step.options ?? []).map((option) => (
            <label
              key={option.value}
              className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm ${
                values[step.field] === option.value
                  ? "border-tedor-500 bg-tedor-50"
                  : "border-slate-200"
              }`}
            >
              <input
                type="radio"
                name={step.field}
                value={option.value}
                checked={values[step.field] === option.value}
                onChange={() => onChange(option.value)}
              />
              <span>{option.label}</span>
            </label>
          ))}
        </div>
        {error ? (
          <p className="mt-2 text-sm text-red-600" role="alert">
            {error}
          </p>
        ) : null}
      </fieldset>
    );
  }

  const numeric = ["int", "year", "cgpa", "average", "max_score", "exam_score"].includes(
    step.kind,
  );
  return (
    <div>
      <label className="mb-1 block text-sm font-medium" htmlFor={`field-${step.field}`}>
        {step.label}
      </label>
      <input
        id={`field-${step.field}`}
        data-testid={`input-${step.field}`}
        className={inputClass}
        type={numeric ? "number" : step.kind === "phone" ? "tel" : "text"}
        inputMode={numeric ? "decimal" : undefined}
        step={step.kind === "cgpa" || step.kind === "exam_score" || step.kind === "average" ? "0.01" : "1"}
        max={step.maximum}
        value={(values[step.field] as string | number | undefined) ?? ""}
        onChange={(event) => onChange(event.target.value)}
      />
      {error ? (
        <p className="mt-1 text-sm text-red-600" role="alert" data-testid={`error-${step.field}`}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** The review screen: every answer, one line each. */
function ReviewSummary({
  values,
  documentsReady,
}: {
  values: Record<string, unknown>;
  documentsReady: boolean;
}) {
  return (
    <div data-testid="review-summary">
      <h1 className="mb-3 text-lg font-semibold">Check your answers</h1>
      <dl className="space-y-2 text-sm">
        {TUTOR_STEPS.filter((step) => step.kind !== "documents").map((step) => {
          const raw = values[step.field];
          const text = Array.isArray(raw) ? raw.join(", ") : (raw as string | undefined);
          if (!text) return null;
          return (
            <div key={step.field} className="flex justify-between gap-4">
              <dt className="text-slate-500">{step.label}</dt>
              <dd className="text-right font-medium">{text}</dd>
            </div>
          );
        })}
        <div className="flex justify-between gap-4">
          <dt className="text-slate-500">Academic documents</dt>
          <dd className="text-right font-medium">
            {documentsReady ? "Uploaded" : "Still missing"}
          </dd>
        </div>
      </dl>
    </div>
  );
}