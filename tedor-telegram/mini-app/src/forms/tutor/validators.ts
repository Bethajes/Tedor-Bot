/**
 * Client-side validators mirroring `app/services/tutor_profile_service.py`.
 *
 * These exist to catch a mistake while the tutor is still looking at the field,
 * not to decide what is stored. The server validates everything again on
 * submit — a browser check that disagreed with it would only be a second,
 * quieter source of truth. So every rule here is the same rule, kept in the
 * same shape: return `null` when the value passes, or the message to show.
 *
 * The numbers are the deployed defaults from `app/config.py`
 * (`TUTOR_MIN_AGE`…`CGPA_MAX`); a deployment that changes them server-side also
 * changes what it accepts, and the server's answer wins either way.
 *
 * Requirements: 3.3, 10.2
 */

export const AGE_MIN = 18;
export const AGE_MAX = 100;
export const ENGLISH_MIN = 1;
export const ENGLISH_MAX = 10;
export const CGPA_MIN = 0;
export const CGPA_MAX = 4.0;
export const AVERAGE_MAX = 100;
export const YEAR_MIN = 1950;
/** Mirrors `validate_year`: up to six years ahead of the current year. */
export const YEAR_MAX = () => new Date().getFullYear() + 6;

/** Mirrors `tutor_profile_service.PHONE_RE`. */
export const PHONE_RE = /^\+?[0-9][0-9\s\-()]{6,19}$/;

/** Every validator returns `null` on pass, or a message to display. */
export type Validator = (value: unknown) => string | null;

/** A blank value is absent, not invalid — the step's own rules decide. */
function isBlank(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  return typeof value === "string" && value.trim() === "";
}

/** Parses user input the way the server's `_as_number` does. */
function asNumber(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const text = String(value).trim().replace(/,/g, "");
  if (text === "") return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
}

export function isWholeNumber(value: number): boolean {
  return Number.isInteger(value);
}

export const validateAge: Validator = (value) => {
  if (isBlank(value)) return null;
  const number = asNumber(value);
  if (number === null || !isWholeNumber(number)) {
    return "Please enter your age as a whole number.";
  }
  if (number < AGE_MIN || number > AGE_MAX) {
    return `Age must be between ${AGE_MIN} and ${AGE_MAX}.`;
  }
  return null;
};

export const validateEnglishProficiency: Validator = (value) => {
  if (isBlank(value)) return null;
  const number = asNumber(value);
  if (number === null || !isWholeNumber(number)) {
    return "English level must be a whole number from 1 to 10.";
  }
  if (number < ENGLISH_MIN || number > ENGLISH_MAX) {
    return `English level must be between ${ENGLISH_MIN} and ${ENGLISH_MAX}.`;
  }
  return null;
};

export const validateCgpa: Validator = (value) => {
  if (isBlank(value)) return null;
  const number = asNumber(value);
  if (number === null) return "Please enter your CGPA as a number.";
  if (number < CGPA_MIN || number > CGPA_MAX) {
    return `Your CGPA must be between ${CGPA_MIN} and ${CGPA_MAX}.`;
  }
  return null;
};

export const validateGradeAverage: Validator = (value) => {
  if (isBlank(value)) return null;
  const number = asNumber(value);
  if (number === null) return "Please enter the average as a number.";
  if (number < 0 || number > AVERAGE_MAX) {
    return `The average must be between 0 and ${AVERAGE_MAX}.`;
  }
  return null;
};

export const validateYear: Validator = (value) => {
  if (isBlank(value)) return null;
  const number = asNumber(value);
  if (number === null || !isWholeNumber(number)) {
    return "Please enter a whole year, e.g. 2021.";
  }
  if (number < YEAR_MIN || number > YEAR_MAX()) {
    return "That year looks out of range — please check it.";
  }
  return null;
};

export const validatePhone: Validator = (value) => {
  const text = String(value ?? "").trim();
  if (!text) return "A phone number is required.";
  if (!PHONE_RE.test(text)) {
    return "Please enter a valid phone number, for example +251911234567.";
  }
  return null;
};

export const validateEntranceExamMaxScore: Validator = (value) => {
  if (isBlank(value)) return null;
  const number = asNumber(value);
  if (number === null || number <= 0) {
    return "The maximum score must be a number above zero.";
  }
  return null;
};

/**
 * The score itself. Mirrors `validate_entrance_exam_score`, which checks the
 * score against the maximum the tutor gave on the previous step — a score from
 * a different scale must not be stored.
 */
export const validateEntranceExamScore =
  (maxScore?: unknown): Validator =>
  (value) => {
    if (isBlank(value)) return null;
    const number = asNumber(value);
    if (number === null) return "Please enter your entrance exam score as a number.";
    if (number < 0) return "Your entrance exam score cannot be negative.";
    const ceiling = asNumber(maxScore);
    if (ceiling !== null && ceiling > 0 && number > ceiling) {
      return `Your score cannot be higher than the maximum of ${ceiling}.`;
    }
    return null;
  };

/** Mirrors `validate_text`: trimmed, whitespace-collapsed, length-capped. */
export const makeValidateText =
  (label: string, maximum = 500): Validator =>
  (value) => {
    if (isBlank(value)) return null;
    const text = String(value).replace(/\s+/g, " ").trim();
    if (!text) return null;
    if (text.length > maximum) {
      return `${label} is too long — keep it under ${maximum} characters.`;
    }
    return null;
  };

export const validateFullName = makeValidateText("Full name", 160);
export const validateAddress = makeValidateText("Current address", 240);
export const validateUniversity = makeValidateText("University", 200);
export const validateDepartment = makeValidateText("Department", 160);
export const validateExperience = makeValidateText("Experience description", 500);

/** Mirrors `validate_gender`: any short string, capped at 20 characters. */
export const validateGender: Validator = (value) => {
  if (isBlank(value)) return null;
  if (String(value).trim().length > 20) {
    return "Please enter a shorter value for gender.";
  }
  return null;
};

export const validateSubjects: Validator = (value) => {
  const list = Array.isArray(value) ? value : [];
  if (list.length === 0) return "Please choose at least one subject you can teach.";
  return null;
};

export const validateGrades: Validator = (value) => {
  const list = Array.isArray(value) ? value : [];
  if (list.length === 0) return "Please choose at least one grade level.";
  return null;
};

/**
 * Mirrors `validate_locations`: an in-person tutor must name at least one
 * area, an online-only tutor need not.
 */
export const makeValidateLocations =
  (teachingMode: string): Validator =>
  (value) => {
    const list = Array.isArray(value) ? value : [];
    if (list.length === 0 && String(teachingMode).toUpperCase() !== "ONLINE") {
      return "Please choose at least one area you can tutor in.";
    }
    return null;
  };

/**
 * The validator for a step's field, or `null` when the step has no rule of its
 * own. `values` is the whole form, because two rules — the entrance score
 * against its maximum, and locations against the teaching mode — depend on
 * another field.
 */
export function validatorForField(
  field: string,
  values: Record<string, unknown> = {},
): Validator | null {
  switch (field) {
    case "full_name":
      return validateFullName;
    case "gender":
      return validateGender;
    case "age":
      return validateAge;
    case "phone":
      return validatePhone;
    case "current_address":
      return validateAddress;
    case "locations":
      return makeValidateLocations(String(values.teaching_mode ?? "IN_PERSON"));
    case "grades":
      return validateGrades;
    case "subjects":
      return validateSubjects;
    case "english_proficiency":
      return validateEnglishProficiency;
    case "university":
      return validateUniversity;
    case "department":
      return validateDepartment;
    case "university_year":
      return validateYear;
    case "cgpa":
      return validateCgpa;
    case "entrance_exam_max_score":
      return validateEntranceExamMaxScore;
    case "entrance_exam_score":
      return validateEntranceExamScore(values.entrance_exam_max_score);
    case "entrance_exam_year":
      return validateYear;
    case "grade8_exam_average":
    case "grade9_average":
    case "grade10_average":
    case "grade11_average":
    case "grade12_average":
      return validateGradeAverage;
    case "teaching_experience_years": {
      // A whole number of years, or nothing at all: the step is optional, and a
      // negative duration is not one.
      const validator: Validator = (value) => {
        if (isBlank(value)) return null;
        const number = asNumber(value);
        if (number === null || !isWholeNumber(number)) {
          return "Please enter a whole number of years.";
        }
        if (number < 0 || number > 80) return "Please check the number of years.";
        return null;
      };
      return validator;
    }
    case "teaching_experience_description":
      return validateExperience;
    default:
      return null;
  }
}

/** Whether a step's value passes, treating an optional empty value as a pass. */
export function validateStep(
  field: string,
  value: unknown,
  values: Record<string, unknown> = {},
): string | null {
  const validator = validatorForField(field, values);
  if (!validator) return null;
  const error = validator(value);
  if (error && isBlank(value)) {
    // An empty value on an optional step is a skip, not a failure.
    return null;
  }
  return error;
}

/**
 * The step-level verdict: a required step must be answered, and then its
 * validator must accept the answer.
 *
 * Required-ness is the step's own (`optional` in ``steps.ts``), not the
 * validator's: a blank phone number already says "required", while a blank
 * CGPA says "not provided", and the two must not be conflated.
 */
export function validateStepValue(
  field: string,
  value: unknown,
  values: Record<string, unknown> = {},
  optional = false,
): string | null {
  if (isBlank(value) || (Array.isArray(value) && value.length === 0)) {
    // An object-valued step (locations) is never "blank": one selection is.
    if (!(field === "locations" && Array.isArray(value) && value.length > 0)) {
      if (optional) return null;
      return "This field is required.";
    }
  }
  return validateStep(field, value, values);
}