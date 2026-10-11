/**
 * The parent request steps, mirroring `app/bot/handlers/student.py`.
 *
 * Same fields, same order, same options as the bot conversation, so a parent
 * who starts in Telegram and finishes in the Mini App answers each question
 * once.
 *
 * Requirements: 6.1
 */

export type ParentFieldKind =
  | "text"
  | "choice"
  | "multiselect"
  | "location"
  | "float";

export interface ParentOption {
  value: string;
  label: string;
}

export interface ParentStep {
  field: string;
  label: string;
  kind: ParentFieldKind;
  optional?: boolean;
  options?: ParentOption[];
  help?: string;
  /** Hidden when the teaching mode is ONLINE — there is nowhere to go. */
  hideWhenOnline?: boolean;
}

/** Mirrors `app.bot.options.COUNTRIES`. */
const COUNTRIES: ParentOption[] = [
  "Ethiopia",
  "Kenya",
  "Eritrea",
  "Somalia",
  "Djibouti",
  "Sudan",
  "United States",
  "United Kingdom",
  "Canada",
  "Australia",
  "Germany",
  "Saudi Arabia",
  "United Arab Emirates",
  "Other",
].map((label) => ({ value: label, label }));

/** Mirrors `app.bot.options.GRADE_LEVELS`. */
const GRADE_LEVELS: ParentOption[] = [
  "KG",
  "1st Grader",
  "2nd Grader",
  "3rd Grader",
  "4th Grader",
  "5th Grader",
  "6th Grader",
  "7th Grader",
  "8th Grader",
  "9th Grader",
  "10th Grader",
  "11th Grader",
  "12th Grader",
  "Grade 1-3",
  "Grade 4-6",
  "Grade 7-8",
  "Grade 9-10",
  "Grade 11-12",
  "University",
  "Postgraduate",
  "Adult/Professional",
  "All Levels",
].map((label) => ({ value: label, label }));

/** Mirrors `app.bot.options.TEACHING_MODES` — values are the stored enums. */
const TEACHING_MODES: ParentOption[] = [
  { value: "ONLINE", label: "💻 Online" },
  { value: "IN_PERSON", label: "🏠 In person" },
  { value: "BOTH", label: "🌐 Both" },
];

export const PARENT_STEPS: ParentStep[] = [
  {
    field: "parent_name",
    label: "Parent / guardian name",
    kind: "text",
    help: "Who should we contact about this request?",
  },
  {
    field: "student_name",
    label: "Student name",
    kind: "text",
    help: "Which student needs a tutor?",
  },
  {
    field: "country",
    label: "Country",
    kind: "choice",
    options: COUNTRIES,
    help: "Where is the student based? This decides the currency.",
  },
  {
    field: "education_level",
    label: "Education level",
    kind: "multiselect",
    options: GRADE_LEVELS,
    help: "Choose as many levels as apply.",
  },
  {
    field: "subject",
    label: "Subject",
    kind: "text",
    help: "Which subject do you need help with?",
  },
  {
    field: "preferred_language",
    label: "Preferred language",
    kind: "text",
    optional: true,
    help: "Which language should the tutor teach in?",
  },
  {
    field: "mode",
    label: "Teaching mode preference",
    kind: "choice",
    options: TEACHING_MODES,
    help: "Online, in person, or both?",
  },
  {
    field: "location",
    label: "Where should the tutoring happen?",
    kind: "location",
    // Online teaching has no place, so this step is skipped entirely.
    hideWhenOnline: true,
  },
  {
    field: "budget",
    label: "Hourly budget",
    kind: "float",
    optional: true,
    help: "What is your comfortable hourly budget?",
  },
  {
    field: "schedule",
    label: "Preferred schedule",
    kind: "text",
    help: "e.g. Weekdays 16:00-18:00, Saturday mornings",
  },
  {
    field: "additional_requirements",
    label: "Additional requirements",
    kind: "text",
    optional: true,
    help: "Anything else we should know?",
  },
  {
    field: "contact",
    label: "Contact information",
    kind: "text",
    help: "Phone number or Telegram username so tutors can reach you.",
  },
];

/** The steps for this request, with the location step dropped when online. */
export function stepsForMode(mode: unknown): ParentStep[] {
  if (String(mode ?? "").toUpperCase() !== "ONLINE") return PARENT_STEPS;
  return PARENT_STEPS.filter((step) => !step.hideWhenOnline);
}

export default PARENT_STEPS;