/**
 * The tutor application steps, mirroring the bot conversation.
 *
 * `app/bot/handlers/tutor_onboarding.py` is the source of truth: the same
 * fields, asked in the same order, with the same options. Any drift between
 * this list and the conversation shows up as a tutor who answered everything
 * in one channel and is asked for it again in the other.
 *
 * The one step deliberately absent is `english_voice` — a WebApp cannot record
 * a voice message, and asking for a file that can never be produced would be a
 * dead end. That leaves 26 collectable steps, plus the review screen.
 *
 * Requirements: 3.1
 */

export type FieldKind =
  | "text"
  | "address"
  | "phone"
  | "int"
  | "year"
  | "cgpa"
  | "average"
  | "max_score"
  | "exam_score"
  | "english"
  | "choice"
  | "multiselect"
  | "location"
  | "documents"
  | "summary";

export interface StepOption {
  value: string;
  label: string;
}

export interface TutorStep {
  /** The payload key the API stores it under. */
  field: string;
  label: string;
  kind: FieldKind;
  /** Optional steps may be skipped with Continue. */
  optional?: boolean;
  options?: StepOption[];
  /** Hint rendered under the control. */
  help?: string;
  /** Client-side length cap, mirroring the server's `validate_text`. */
  maximum?: number;
}

const GENDERS: StepOption[] = [
  { value: "Male", label: "Male" },
  { value: "Female", label: "Female" },
  { value: "Other", label: "Other" },
];

const EDUCATION_LEVELS: StepOption[] = [
  { value: "UNIVERSITY_STUDENT", label: "University student" },
  { value: "UNIVERSITY_GRADUATE", label: "University graduate" },
  { value: "MASTERS_STUDENT", label: "Master's student" },
  { value: "MASTERS_GRADUATE", label: "Master's graduate" },
  { value: "OTHER", label: "Other" },
];

const ACADEMIC_STREAMS: StepOption[] = [
  { value: "NATURAL", label: "🧪 Natural Sciences" },
  { value: "SOCIAL", label: "🌍 Social Sciences" },
];

/** Mirrors `app.bot.options.ETH_GRADES`. */
const GRADES: StepOption[] = [
  "Kindergarten",
  "Primary school (1-6)",
  "Middle school (7-8)",
  "High school (9-10)",
  "Preparatory school (11-12) — Social",
  "Preparatory school (11-12) — Natural",
  "University",
].map((label) => ({ value: label, label }));

/** Mirrors `app.bot.options.SUBJECTS`. */
const SUBJECTS: StepOption[] = [
  "Mathematics",
  "Physics",
  "Chemistry",
  "Biology",
  "English",
  "Amharic",
  "Geography",
  "History",
  "Economics",
  "Computer Science",
  "ICT",
  "Programming",
  "Accounting",
  "Business",
  "Creative Arts",
  "Music",
  "Sports",
  "French",
  "Arabic",
  "Other",
].map((label) => ({ value: label, label }));

/** The 1-10 scale, mirroring `app.bot.options.ETH_ENGLISH_LEVELS`. */
const ENGLISH_LEVELS: StepOption[] = [
  { value: "1", label: "1 — Beginner" },
  { value: "2", label: "2 — Basic" },
  { value: "3", label: "3 — Elementary" },
  { value: "4", label: "4 — Lower-intermediate" },
  { value: "5", label: "5 — Intermediate" },
  { value: "6", label: "6 — Upper-intermediate" },
  { value: "7", label: "7 — Good" },
  { value: "8", label: "8 — Very good" },
  { value: "9", label: "9 — Excellent" },
  { value: "10", label: "10 — Near-fluent" },
];

export const TUTOR_STEPS: TutorStep[] = [
  {
    field: "full_name",
    label: "Full name",
    kind: "text",
    help: "Your full name as it appears on your national ID.",
    maximum: 160,
  },
  {
    field: "gender",
    label: "Gender",
    kind: "choice",
    options: GENDERS,
  },
  {
    field: "age",
    label: "Age",
    kind: "int",
    help: "Your age in years (18 or older).",
  },
  {
    field: "phone",
    label: "Phone number",
    kind: "phone",
    help: "Include your country code, e.g. +251911234567.",
  },
  {
    field: "current_address",
    label: "Current address",
    kind: "address",
    help: "Where you live now, e.g. Bole, Addis Ababa.",
    maximum: 240,
  },
  {
    field: "locations",
    label: "Tutoring areas",
    kind: "location",
    help: "The areas you can tutor in. Online-only tutors can skip this.",
  },
  {
    field: "grades",
    label: "Grade groups you can tutor",
    kind: "multiselect",
    options: GRADES,
  },
  {
    field: "subjects",
    label: "Subjects you can tutor",
    kind: "multiselect",
    options: SUBJECTS,
  },
  {
    field: "english_proficiency",
    label: "English communication skill",
    kind: "english",
    options: ENGLISH_LEVELS,
    help: "Your own rating, from 1 to 10.",
  },
  {
    field: "education_level",
    label: "Current / highest education",
    kind: "choice",
    options: EDUCATION_LEVELS,
  },
  { field: "university", label: "University / institution", kind: "text", maximum: 200 },
  { field: "department", label: "Department", kind: "text", maximum: 160 },
  {
    field: "university_year",
    label: "Year",
    kind: "year",
    help: "Your graduation year, or the year you are currently studying in.",
  },
  {
    field: "cgpa",
    label: "CGPA",
    kind: "cgpa",
    optional: true,
    help: "Out of 4, e.g. 3.75.",
  },
  {
    field: "entrance_exam_max_score",
    label: "Maximum score for your entrance exam",
    kind: "max_score",
    help: "e.g. 700 for the current exam, 600 for the older one.",
  },
  {
    field: "entrance_exam_score",
    label: "Your entrance exam score",
    kind: "exam_score",
    help: "e.g. 612.",
  },
  {
    field: "entrance_exam_year",
    label: "Exam year",
    kind: "year",
    help: "e.g. 2016.",
  },
  {
    field: "academic_stream",
    label: "Preparatory stream",
    kind: "choice",
    options: ACADEMIC_STREAMS,
    help: "This tells us which preparatory subjects you can cover.",
  },
  {
    field: "grade8_exam_average",
    label: "Grade 8 exam average",
    kind: "average",
    optional: true,
    help: "Out of 100, e.g. 82.",
  },
  {
    field: "grade9_average",
    label: "Grade 9 average",
    kind: "average",
    optional: true,
    help: "Out of 100, e.g. 78.",
  },
  {
    field: "grade10_average",
    label: "Grade 10 average",
    kind: "average",
    optional: true,
    help: "Out of 100, e.g. 80.",
  },
  {
    field: "grade11_average",
    label: "Grade 11 average",
    kind: "average",
    optional: true,
    help: "Out of 100, e.g. 84.",
  },
  {
    field: "grade12_average",
    label: "Grade 12 average",
    kind: "average",
    optional: true,
    help: "Out of 100, e.g. 86.",
  },
  {
    field: "teaching_experience_years",
    label: "Teaching experience",
    kind: "int",
    optional: true,
    help: "How many years of tutoring or teaching experience?",
  },
  {
    field: "teaching_experience_description",
    label: "Experience details",
    kind: "text",
    optional: true,
    help: "Subjects taught, grades, and where.",
    maximum: 500,
  },
  {
    field: "documents",
    label: "Academic documents",
    kind: "documents",
    help: "Your university transcript and university entrance exam result.",
  },
];

/** The review screen, mirroring the bot's summary step. */
export const TUTOR_SUMMARY_STEP: TutorStep = {
  field: "summary",
  label: "Review your application",
  kind: "summary",
};

/** Every step the tutor walks through, summary last. */
export const ALL_TUTOR_STEPS: TutorStep[] = [...TUTOR_STEPS, TUTOR_SUMMARY_STEP];

/** The steps a tutor actually answers — everything but the review screen. */
export const INPUT_TUTOR_STEPS: TutorStep[] = TUTOR_STEPS;

/** How many steps the form shows in its progress bar. */
export const TUTOR_STEP_COUNT = TUTOR_STEPS.length;

export function stepIndexFor(field: string): number {
  return TUTOR_STEPS.findIndex((step) => step.field === field);
}

/** The step that collects a given payload key, if any. */
export function stepForField(field: string): TutorStep | undefined {
  return TUTOR_STEPS.find((step) => step.field === field);
}

export default TUTOR_STEPS;