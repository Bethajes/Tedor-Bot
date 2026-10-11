/**
 * Property (frontend): document validation rejects disallowed file types.
 *
 * The endpoint rejects anything that is not a PDF or an image with 422, so a
 * file the client lets through is a 20 MB upload spent to be told no. The
 * rule is therefore exactly the server's list — and it is stated here from the
 * server's constant rather than imported from the component, so the test would
 * notice if the two lists diverged.
 */

import { render, screen } from "@testing-library/react";
import fc from "fast-check";
import { describe, expect, it, vi } from "vitest";

import FileUpload, {
  ALLOWED_EXTENSIONS,
  MAX_UPLOAD_BYTES,
  extensionOf,
  isAllowedFileName,
  validateFile,
} from "./FileUpload";

/** The server's list, restated: `tutor_profile_service.ALLOWED_DOCUMENT_EXTENSIONS`. */
const SERVER_ALLOWED = [".pdf", ".jpg", ".jpeg", ".png", ".webp"];

/** The endpoint's size limit, restated: 20 MB. */
const SERVER_MAX_BYTES = 20 * 1024 * 1024;

/** What the server's `validate_document_file` accepts. */
function serverAccepts(fileName: string): boolean {
  const name = String(fileName ?? "").trim();
  if (!name) return false;
  const extension = "." + name.split(".").pop()!.toLowerCase();
  return SERVER_ALLOWED.includes(extension);
}

// Any name a phone could hand us, and some it could not.
const fileName = fc.stringMatching(/^[A-Za-z0-9 _.'-]{0,40}$/);

// Feature: tedor-mini-app, Property: document client validation rejects disallowed types
describe("Property: document client validation rejects disallowed types", () => {
  it("accepts exactly the extensions the endpoint accepts", () => {
    fc.assert(
      fc.property(fileName, (name) => {
        expect(isAllowedFileName(name)).toBe(serverAccepts(name));
      }),
      { numRuns: 2000 },
    );
  });

  it("accepts only files ending in .pdf, .jpg, .jpeg, .png or .webp", () => {
    fc.assert(
      fc.property(fc.string({ maxLength: 30 }), (stem) => {
        const safeStem = stem.replace(/[\\/:*?"<>|]/g, "").replace(/\./g, "") || "file";
        for (const extension of SERVER_ALLOWED) {
          expect(isAllowedFileName(`${safeStem}${extension}`)).toBe(true);
        }
        for (const extension of [".doc", ".docx", ".txt", ".gif", ".svg", ".exe", ".zip", ""]) {
          expect(isAllowedFileName(`${safeStem}${extension}`)).toBe(false);
        }
      }),
      { numRuns: 500 },
    );
  });

  it("ignores the case of the extension, as the server does", () => {
    fc.assert(
      fc.property(fc.stringMatching(/^[A-Za-z0-9_-]{1,12}$/), (stem) => {
        for (const extension of SERVER_ALLOWED) {
          expect(isAllowedFileName(`${stem}${extension.toUpperCase()}`)).toBe(true);
          expect(isAllowedFileName(`${stem}${extension}`)).toBe(true);
        }
      }),
      { numRuns: 300 },
    );
  });

  it("refuses a name with no extension at all", () => {
    fc.assert(
      fc.property(fc.stringMatching(/^[A-Za-z0-9_-]{1,20}$/), (name) => {
        expect(isAllowedFileName(name)).toBe(false);
        expect(validateFile({ name, size: 1024 })).not.toBeNull();
      }),
      { numRuns: 200 },
    );
  });

  it("reports the extension it refused, so the message can name it", () => {
    fc.assert(
      fc.property(fc.stringMatching(/^[A-Za-z0-9 _-]{1,10}$/), (stem) => {
        expect(extensionOf(`${stem}.docx`)).toBe(".docx");
        expect(extensionOf(`${stem}.PDF`)).toBe(".pdf");
        expect(extensionOf(stem)).toBe("");
      }),
      { numRuns: 300 },
    );
  });

  it("rejects a file over 20 MB and accepts one at the limit", () => {
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 40 }), (megabytes) => {
        const size = megabytes * 1024 * 1024;
        const message = validateFile({ name: "transcript.pdf", size });
        if (size > SERVER_MAX_BYTES) {
          expect(message).not.toBeNull();
        } else {
          expect(message).toBeNull();
        }
      }),
      { numRuns: 100 },
    );
  });

  it("checks the type before the size, so a bad type reads as a bad type", () => {
    fc.assert(
      fc.property(fc.stringMatching(/^[A-Za-z0-9_-]{1,10}$/), (stem) => {
        const message = validateFile({ name: `${stem}.doc`, size: SERVER_MAX_BYTES + 1 });
        expect(message).toContain("not a supported type");
      }),
      { numRuns: 200 },
    );
  });

  it("uses the server's own constants", () => {
    expect([...ALLOWED_EXTENSIONS]).toEqual(SERVER_ALLOWED);
    expect(MAX_UPLOAD_BYTES).toBe(SERVER_MAX_BYTES);
  });
});
// ---------------------------------------------------------------------------
// The step must never trap a tutor who has nothing left to upload
// ---------------------------------------------------------------------------

describe("FileUpload completion", () => {
  const REQUIRED = ["UNIVERSITY_TRANSCRIPT", "UNIVERSITY_ENTRANCE_RESULT"];

  it("reports itself complete when every required type is already on file", () => {
    // A returning tutor, or one who uploaded before submitting: the step opens
    // already satisfied and must say so, or Next refuses to move forever.
    const onComplete = vi.fn();
    render(
      <FileUpload
        requiredTypes={REQUIRED}
        existingTypes={[...REQUIRED]}
        onComplete={onComplete}
      />,
    );

    expect(onComplete).toHaveBeenCalledWith(true);
  });

  it("reports itself incomplete when documents are still missing", () => {
    const onComplete = vi.fn();
    render(
      <FileUpload
        requiredTypes={REQUIRED}
        existingTypes={["UNIVERSITY_TRANSCRIPT"]}
        onComplete={onComplete}
      />,
    );

    // Nothing on file is not "done": the step still has work.
    expect(onComplete).not.toHaveBeenCalledWith(true);
  });

  it("shows a checkmark for a document already on file", () => {
    render(<FileUpload requiredTypes={REQUIRED} existingTypes={["UNIVERSITY_TRANSCRIPT"]} />);
    expect(screen.getByTestId("done-UNIVERSITY_TRANSCRIPT")).toBeInTheDocument();
  });
});
