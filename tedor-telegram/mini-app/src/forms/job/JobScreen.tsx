/**
 * The job application screen, opened from a job post's Mini App button.
 *
 * Three outcomes, decided entirely by the API's answer:
 *
 * 1. The tutor already applied — show the status and a support link, and no
 *    form. Re-applying is not a thing the database allows, and offering the
 *    button anyway would only produce an error.
 * 2. The profile is incomplete — list exactly what is outstanding and drop
 *    the tutor into the application form on the first missing step. Completing
 *    the form is the only way forward, so it is the only button offered.
 * 3. The profile is complete — show the job and the match score, and confirm.
 *
 * Requirements: 7.1–7.5
 */

import { useCallback, useEffect, useState } from "react";

import api from "../../api/client";
import { navigate } from "../../pages";

/** One job, as `GET /api/miniapp/jobs/{ref}` returns it. */
export interface JobDetails {
  job_reference: string;
  /** Present when the tutor already applied. */
  status?: string;
  status_label?: string;
  /** Present when the profile cannot be evaluated yet. */
  profile_state?: string;
  profile_percentage?: number;
  /** Outstanding items, labelled for the tutor to read. */
  missing_fields?: string[];
  /** The same items as payload field keys, for navigating the form. */
  missing_field_keys?: string[];
  missing_documents?: string[];
  reason?: string;
  /** Present for a complete profile. */
  location?: string;
  student_level?: string;
  subjects?: string;
  duration?: string;
  frequency?: string;
  compensation?: string;
  tutor_category?: string;
  score?: number;
  recommendation?: string;
  is_strong_match?: boolean;
}

/** Statuses that mean "this application is finished". */
export const TERMINAL_STATUSES: readonly string[] = [
  "SUBMITTED",
  "UNDER_REVIEW",
  "SHORTLISTED",
  "REJECTED",
];

export function isTerminal(status: string | undefined): boolean {
  return status !== undefined && TERMINAL_STATUSES.includes(status);
}

/**
 * The step the application form should open on: the earliest one the profile
 * is missing.
 *
 * The walk is over the *steps*, not the API's list: the tutor should be taken
 * to the first gap in the form, whichever order the service happened to
 * report them in. A field key the form does not collect is simply skipped, so
 * an unrecognised label cannot send them somewhere meaningless.
 */
export function firstMissingStepIndex(
  missingFields: string[],
  fieldsByIndex: string[],
): number {
  const missing = new Set(missingFields);
  for (const [index, field] of fieldsByIndex.entries()) {
    if (missing.has(field)) return index;
  }
  return 0;
}

export interface JobScreenProps {
  jobReference: string;
  /** Fields in step order — injected so the screen stays testable. */
  fieldsByIndex?: string[];
  /** Injected in tests to render the completion form inline. */
  renderCompletion?: (startAt: number) => JSX.Element;
}

export default function JobScreen({
  jobReference,
  fieldsByIndex,
  renderCompletion,
}: JobScreenProps) {
  const [job, setJob] = useState<JobDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmed, setConfirmed] = useState<string | null>(null);
  const [completing, setCompleting] = useState(false);

  const load = useCallback(async () => {
    if (!jobReference) {
      setError("This link is missing the job reference.");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const details = await api.get<JobDetails>(
        `/miniapp/jobs/${encodeURIComponent(jobReference)}`,
      );
      setJob(details);
    } catch {
      setError(
        "We could not load this job. It may have been closed — please try again later.",
      );
    } finally {
      setLoading(false);
    }
  }, [jobReference]);

  useEffect(() => {
    void load();
  }, [load]);

  const confirm = async () => {
    setConfirming(true);
    setError(null);
    try {
      const result = await api.post<{ status: string }>(
        `/miniapp/jobs/${encodeURIComponent(jobReference)}/confirm`,
      );
      setConfirmed(result.status);
    } catch {
      setError("We could not submit your application. Please try again.");
    } finally {
      setConfirming(false);
    }
  };

  if (loading) {
    return (
      <div className="p-4 text-center text-sm text-slate-500" data-testid="job-loading">
        Loading this job…
      </div>
    );
  }

  if (error && !job) {
    return (
      <div className="p-4 text-center" data-testid="job-error">
        <p className="text-sm text-red-600">{error}</p>
        <button
          type="button"
          className="mt-3 rounded-lg bg-tedor-500 px-4 py-2 text-white"
          onClick={() => void load()}
        >
          Try again
        </button>
      </div>
    );
  }

  if (!job) return null;

  // (1) Already applied: status only, no application form.
  if (isTerminal(job.status)) {
    return (
      <div className="p-4" data-testid="job-status">
        <h1 className="mb-1 text-lg font-semibold">Application status</h1>
        <p className="mb-4">
          <span className="rounded-full bg-slate-100 px-3 py-1 text-sm">
            {job.status_label ?? job.status}
          </span>
        </p>
        <p className="text-sm text-slate-600">
          Your application for <strong>{job.job_reference}</strong> is with our team.
          Nothing more is needed from you.
        </p>
        <a
          href="https://t.me/TedorTutorsBot"
          className="mt-4 inline-block rounded-lg border border-slate-300 px-4 py-2 text-sm"
          data-testid="contact-support"
        >
          Contact Support
        </a>
      </div>
    );
  }

  // (2) Incomplete profile: what is missing, then straight into the form.
  if (job.profile_state === "INCOMPLETE") {
    const missing = job.missing_fields ?? [];
    // Navigate by field key; the labels above are written for people and do
    // not have to match a control's name.
    const startAt = firstMissingStepIndex(
      job.missing_field_keys ?? [],
      fieldsByIndex ?? [],
    );
    return (
      <div className="p-4" data-testid="job-incomplete">
        <h1 className="mb-1 text-lg font-semibold">Almost there</h1>
        <p className="mb-3 text-sm text-slate-600">
          Your profile is {job.profile_percentage ?? 0}% complete. Finish these details
          to apply for this job.
        </p>
        <ul className="mb-4 space-y-1 text-sm" data-testid="missing-fields">
          {missing.map((item) => (
            <li key={item}>• {item}</li>
          ))}
        </ul>
        <button
          type="button"
          className="w-full rounded-lg bg-tedor-500 px-4 py-2 text-white"
          data-testid="complete-profile"
          onClick={() => {
            if (renderCompletion) {
              setCompleting(true);
            } else {
              // The form reads its own resume draft, so it is entered the
              // ordinary way; the missing list above tells the tutor what it
              // will ask for.
              navigate({ screen: "tutor" });
            }
          }}
        >
          Complete my profile
        </button>
        {completing && renderCompletion ? (
          <div className="mt-4">{renderCompletion(startAt)}</div>
        ) : null}
      </div>
    );
  }

  // (3) A complete profile: the job, the score, and a confirmation.
  if (confirmed) {
    return (
      <div className="p-4 text-center" data-testid="job-confirmed">
        <h1 className="mb-2 text-lg font-semibold">Application submitted</h1>
        <p className="text-sm text-slate-600">
          Your application for <strong>{job.job_reference}</strong> has been sent. Our
          team will be in touch.
        </p>
      </div>
    );
  }

  return (
    <div className="p-4" data-testid="job-details">
      <h1 className="mb-1 text-lg font-semibold">{job.job_reference}</h1>
      <dl className="mb-4 space-y-1 text-sm">
        {job.subjects ? <Row label="Subjects" value={job.subjects} /> : null}
        {job.student_level ? <Row label="Level" value={job.student_level} /> : null}
        {job.location ? <Row label="Location" value={job.location} /> : null}
        {job.duration ? <Row label="Duration" value={job.duration} /> : null}
        {job.frequency ? <Row label="Frequency" value={job.frequency} /> : null}
        {job.compensation ? <Row label="Compensation" value={job.compensation} /> : null}
        {job.tutor_category ? <Row label="Tutor" value={job.tutor_category} /> : null}
      </dl>

      {typeof job.score === "number" ? (
        <div className="mb-4 rounded-lg bg-tedor-50 p-3" data-testid="match-score">
          <p className="text-sm font-medium">
            Match score: {job.score}/100
            {job.is_strong_match ? " — strong match" : ""}
          </p>
          {job.recommendation ? (
            <p className="mt-1 text-sm text-slate-600">{job.recommendation}</p>
          ) : null}
        </div>
      ) : null}

      {job.reason ? (
        <p className="mb-3 text-sm text-amber-700">{job.reason}</p>
      ) : null}
      {error ? (
        <p className="mb-3 text-sm text-red-600" role="alert">
          {error}
        </p>
      ) : null}

      <button
        type="button"
        className="w-full rounded-lg bg-tedor-500 px-4 py-2 text-white disabled:opacity-50"
        data-testid="confirm-application"
        disabled={confirming}
        onClick={() => void confirm()}
      >
        {confirming ? "Submitting…" : "Confirm Application"}
      </button>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-right font-medium">{value}</dd>
    </div>
  );
}