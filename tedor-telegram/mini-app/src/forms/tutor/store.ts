/**
 * The tutor application store.
 *
 * One store for the whole form: the answers, the current step, and the draft
 * the server holds. Answers reach the server through the ``draftPersistence``
 * middleware rather than from a component effect, so a step cannot advance
 * without the previous answers being persisted — a tutor who closes the Mini
 * App half way through comes back to the same step with the same answers
 * (Requirements 8.1–8.4).
 */

import { create } from "zustand";
import type { StateCreator } from "zustand/vanilla";

import api, { ApiError } from "../../api/client";
import { TUTOR_STEPS } from "./steps";

/** One selected tutoring area, as the API stores it. */
export interface SelectedLocation {
  id: number | null;
  name: string;
  normalizedName: string;
  city: string;
  latitude: number | null;
  longitude: number | null;
}

/** One uploaded document type. */
export interface UploadedDocument {
  documentType: string;
  label: string;
  fileName?: string;
}

export type TutorValues = Record<string, unknown>;

export interface TutorState {
  values: TutorValues;
  /** 0-based index into TUTOR_STEPS; ``TUTOR_STEPS.length`` means review. */
  stepIndex: number;
  /** When the draft was last written, for the "saved" hint. */
  savedAt: string | null;
  /** True while a draft save is in flight. */
  saving: boolean;
  /** True once the server draft has been loaded or confirmed absent. */
  draftLoaded: boolean;
  /** The step a draft was left on, so the resume prompt can offer it. */
  draftStepIndex: number | null;
  error: string | null;
  submitError: string | null;
  submitted: {
    publicTutorId: string;
    status: string;
    /** True when this account had already applied before this submit. */
    alreadyApplied?: boolean;
  } | null;

  setValue: (field: string, value: unknown) => void;
  setValues: (values: TutorValues) => void;
  /** Advance to `index`; the middleware saves the draft on the way. */
  goToStep: (index: number) => void;
  clearErrors: () => void;
  loadDraft: () => Promise<boolean>;
  resumeDraft: () => void;
  discardDraft: () => void;
  saveDraft: () => Promise<void>;
  submit: () => Promise<void>;
  reset: () => void;
}

/** Only real answers are sent; UI-only fields never reach the API. */
const TRANSIENT_FIELDS = new Set(["stepIndex", "savedAt"]);

/**
 * Fields the API must not receive on submit.
 *
 * `documents` is here because the Mini App does not send document data at
 * submit: each file was already uploaded to
 * `POST /api/miniapp/documents/upload` and is held server-side, then attached
 * to the new application when it is created. The form's own list uses the UI's
 * shape (`documentType`), which is not what the submit payload speaks — sending
 * it anyway is what produced "422 Unknown document type: None".
 */
const SUBMIT_EXCLUDED_FIELDS = new Set([...TRANSIENT_FIELDS, "documents", "_step"]);

/**
 * The draft payload: the answers plus the step, under ``_step`` so a resumed
 * draft reopens where the tutor left off.
 */
export function draftPayload(values: TutorValues, stepIndex: number): TutorValues {
  const payload: TutorValues = {};
  for (const [key, value] of Object.entries(values)) {
    if (TRANSIENT_FIELDS.has(key)) continue;
    if (value === undefined) continue;
    payload[key] = value;
  }
  payload._step = stepIndex;
  return payload;
}

/** The payload `POST /api/miniapp/tutor/submit` expects — no ``_step``, no documents. */
export function submitPayload(values: TutorValues): TutorValues {
  const payload: TutorValues = {};
  for (const [key, value] of Object.entries(values)) {
    if (SUBMIT_EXCLUDED_FIELDS.has(key) || value === undefined) continue;
    payload[key] = value;
  }
  return payload;
}

// ---------------------------------------------------------------------------
// Draft persistence middleware
// ---------------------------------------------------------------------------

/**
 * The store itself: the answers, the steps, and the actions that move them.
 *
 * It never writes to the network; the draft save belongs to the middleware
 * below, which wraps `set` so a step change and its persistence cannot be
 * separated.
 */
const createTutorState: StateCreator<TutorState> = (set, get) => ({
  values: {},
  stepIndex: 0,
  savedAt: null,
  saving: false,
  draftLoaded: false,
  draftStepIndex: null,
  error: null,
  submitError: null,
  submitted: null,

  setValue(field, value) {
    set({ values: { ...get().values, [field]: value }, error: null });
  },

  setValues(values) {
    set({ values: { ...get().values, ...values } });
  },

  goToStep(index) {
    set({ stepIndex: Math.max(0, Math.min(TUTOR_STEPS.length, index)) });
  },

  clearErrors() {
    set({ error: null, submitError: null });
  },

  /** Load the server draft. Returns true when there is one to resume. */
  async loadDraft() {
    try {
      const draft = await api.get<TutorValues>("/miniapp/tutor/draft");
      const { _step, ...values } = draft ?? {};
      const stepIndex = typeof _step === "number" && _step >= 0 ? _step : null;
      set({
        values,
        // Only a draft that carries a step may move the form. Otherwise the
        // tutor keeps the step they opened on — the job screen hands the form
        // a step of its own, and a draft lookup must not undo that.
        ...(stepIndex === null ? {} : { stepIndex }),
        draftStepIndex: stepIndex,
        draftLoaded: true,
        savedAt: new Date().toISOString(),
      });
      return stepIndex !== null;
    } catch {
      // A 404 means there is nothing to resume, which is not an error here.
      set({ draftLoaded: true, draftStepIndex: null });
      return false;
    }
  },

  resumeDraft() {
    const { draftStepIndex } = get();
    if (draftStepIndex !== null) set({ stepIndex: draftStepIndex });
  },

  discardDraft() {
    set({ values: {}, stepIndex: 0, draftStepIndex: null });
  },

  async saveDraft() {
    const state = get();
    set({ saving: true, error: null });
    try {
      await api.post("/miniapp/tutor/draft", draftPayload(state.values, state.stepIndex));
      set({ saving: false, savedAt: new Date().toISOString() });
    } catch (error) {
      set({
        saving: false,
        error:
          error instanceof Error ? error.message : "We could not save your progress.",
      });
    }
  },

async submit() {
      set({ submitError: null });
      try {
        const result = await api.post<{ public_tutor_id: string; status: string }>(
          "/miniapp/tutor/submit",
          submitPayload(get().values),
        );
        set({ submitted: { publicTutorId: result.public_tutor_id, status: result.status } });
      } catch (error) {
        // 409 means this account already has an application — which is what
        // the tutor was trying to achieve, not a failure. Showing a raw
        // conflict error after a completed 26-step form is a dead end, so the
        // existing application is fetched and shown instead.
        if (error instanceof ApiError && error.status === 409) {
          try {
            const profile = await api.get<{ public_tutor_id: string; status: string }>(
              "/miniapp/tutor/profile",
            );
            if (profile?.public_tutor_id) {
              set({
                submitted: {
                  publicTutorId: profile.public_tutor_id,
                  status: profile.status ?? "PENDING",
                  alreadyApplied: true,
                },
              });
              return;
            }
          } catch {
            // The profile could not be read either; fall through to the error
            // below, which at least says something.
          }
        }
        set({
          submitError:
            error instanceof Error ? error.message : "We could not submit your application.",
        });
      }
    },

  reset() {
    set({ values: {}, stepIndex: 0, draftStepIndex: null, submitted: null });
  },
});

/** How `set` is typed inside a state creator. */
type SetState = (
  partial: Partial<TutorState> | ((state: TutorState) => Partial<TutorState>),
  replace?: false,
) => void;

/**
 * Writes the draft to the server whenever the step changes.
 *
 * Implemented as a wrapper around `set` rather than as an exported function so
 * no component can move the form forward without the save happening: the
 * transition and its persistence are one operation.
 */
const draftPersistence: StateCreator<TutorState, [], [], TutorState> = (
  set,
  get,
  store,
) => {
  // The store's own first state is not readable while it is being created, so
  // the comparison starts at the initial step (0) rather than at whatever the
  // state happens to be mid-construction.
  let lastStep = 0;

  const persist = async () => {
    const state = get();
    set({ saving: true, error: null });
    try {
      await api.post("/miniapp/tutor/draft", draftPayload(state.values, state.stepIndex));
      set({ saving: false, savedAt: new Date().toISOString() });
    } catch (error) {
      set({
        saving: false,
        error:
          error instanceof Error ? error.message : "We could not save your progress.",
      });
    }
  };

  const setWithPersist: SetState = (partial, replace) => {
    set(partial, replace);
    const step = get().stepIndex;
    if (step !== lastStep) {
      lastStep = step;
      void persist();
    }
  };

  return createTutorState(setWithPersist as never, get, store as never);
};

/** The application store, persisting to the server on every step change. */
export const useTutorStore = create<TutorState>()(draftPersistence);

/** The step the tutor is on, or undefined once they reach the review screen. */
export function currentStep(index: number) {
  return TUTOR_STEPS[index];
}

/** True once the tutor has reached the review screen. */
export function isReviewStep(index: number): boolean {
  return index >= TUTOR_STEPS.length;
}