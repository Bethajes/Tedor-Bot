/**
 * Document uploads.
 *
 * One control per required document type, because a tutor has to know *which*
 * document they are sending: the transcript and the entrance exam result are
 * reviewed by different eyes. Types already on file arrive from the profile
 * endpoint and are shown as done rather than asked for again.
 *
 * Validation happens before the upload starts — a rejected file must not cost
 * the tutor the 20 MB transfer, and one bad file must not clear the others
 * (Requirements 5.1–5.6).
 */

import { useEffect, useRef, useState } from "react";
import api from "../api/client";
import type { UploadedDocument } from "../forms/tutor/store";

/** Mirrors `tutor_profile_service.ALLOWED_DOCUMENT_EXTENSIONS`. */
export const ALLOWED_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".webp"] as const;

/** Mirrors the endpoint's 20 MB limit (Requirement 5.2). */
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

export const UPLOAD_ACCEPT = ALLOWED_EXTENSIONS.join(",");

/**
 * Whether a file name is one of the allowed types.
 *
 * The extension is lower-cased before comparison, because a phone camera
 * writes ``IMG_1234.JPG`` and the tutor is not at fault for the case.
 */
export function isAllowedFileName(fileName: string): boolean {
  const name = String(fileName ?? "").trim().toLowerCase();
  const dot = name.lastIndexOf(".");
  if (dot === -1) return false;
  return (ALLOWED_EXTENSIONS as readonly string[]).includes(name.slice(dot));
}

/** The extension of a file name, lower-cased, or `""` when there is none. */
export function extensionOf(fileName: string): string {
  const name = String(fileName ?? "").trim().toLowerCase();
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot);
}

/** The inline error for a file, or `null` when it may be uploaded. */
export function validateFile(file: {
  name?: string;
  size?: number;
}): string | null {
  const name = file.name ?? "";
  if (!isAllowedFileName(name)) {
    return `“${name || "That file"}” is not a supported type. Please upload a PDF or an image (JPG, PNG or WEBP).`;
  }
  const size = file.size ?? 0;
  if (size > MAX_UPLOAD_BYTES) {
    const mb = Math.floor(size / (1024 * 1024));
    return `That file is ${mb} MB. The limit is 20 MB — please upload a smaller file.`;
  }
  return null;
}

/** Human names for the document types the API accepts. */
const DOCUMENT_LABELS: Record<string, string> = {
  UNIVERSITY_TRANSCRIPT: "University transcript",
  UNIVERSITY_ENTRANCE_RESULT: "University entrance exam result",
  CURRENT_UNIVERSITY_DOCUMENT: "Current university document",
  BACHELORS_DEGREE: "Bachelor's degree certificate",
  MSC_DEGREE: "Master's degree certificate",
  SECONDARY_LEAVING_RESULT: "Secondary school leaving result",
  OTHER_CERTIFICATE: "Other academic certificate",
};

export function documentLabel(documentType: string): string {
  return (
    DOCUMENT_LABELS[documentType] ??
    documentType.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase())
  );
}

export interface FileUploadProps {
  /** The document types this tutor must provide, derived from education level. */
  requiredTypes: string[];
  /** Types already on file, from the profile endpoint (Requirement 5.6). */
  existingTypes?: string[];
  /** Per-type inline errors. */
  onChange?: (documents: UploadedDocument[]) => void;
  /** Called when every required type has a file. */
  onComplete?: (ready: boolean) => void;
  disabled?: boolean;
}

/** Where one document control is in its upload cycle. */
type UploadStatus = "idle" | "uploading" | "done" | "error";

type UploadState = Record<string, UploadStatus>;

export default function FileUpload({
  requiredTypes,
  existingTypes = [],
  onChange,
  onComplete,
  disabled = false,
}: FileUploadProps) {
  const [states, setStates] = useState<UploadState>(() =>
    Object.fromEntries(
      requiredTypes.map((type) => [type, existingTypes.includes(type) ? "done" : "idle"]),
    ),
  );
  const [errors, setErrors] = useState<Record<string, string | null>>({});
  const [documents, setDocuments] = useState<UploadedDocument[]>(() =>
    requiredTypes
      .filter((type) => existingTypes.includes(type))
      .map((type) => ({ documentType: type, label: documentLabel(type) })),
  );
  const inputs = useRef<Record<string, HTMLInputElement | null>>({});

  // A tutor whose documents are already on file must not be stuck here: the
  // step is complete the moment it opens, not only after a fresh upload.
  // Without this the "everything is uploaded" state is never reported, and the
  // Next button refuses to move for someone who has nothing left to do.
  const completeRef = useRef(onComplete);
  completeRef.current = onComplete;
  const requiredRef = useRef(requiredTypes);
  requiredRef.current = requiredTypes;
  const existingRef = useRef(existingTypes);
  existingRef.current = existingTypes;

  useEffect(() => {
    const required = requiredRef.current;
    const ready =
      required.length > 0 &&
      required.every((type) => existingRef.current.includes(type));
    if (required.length === 0 || ready) {
      completeRef.current?.(true);
    }
  }, []);

  const notify = (next: UploadedDocument[]) => {
    setDocuments(next);
    onChange?.(next);
    const missing = requiredTypes.filter(
      (type) => !next.some((doc) => doc.documentType === type),
    );
    onComplete?.(missing.length === 0);
  };

  const upload = async (documentType: string, file: File) => {
    // Client-side check first: a rejected file never leaves the device.
    const error = validateFile(file);
    if (error) {
      setErrors((prev) => ({ ...prev, [documentType]: error }));
      setStates((prev) => ({ ...prev, [documentType]: "error" }));
      // Only this document is affected; the others stay as they were.
      return;
    }

    setErrors((prev) => ({ ...prev, [documentType]: null }));
    setStates((prev) => ({ ...prev, [documentType]: "uploading" }));

    const form = new FormData();
    form.append("file", file);
    form.append("document_type", documentType);

    try {
      await api.postForm("/miniapp/documents/upload", form);
      setStates((prev) => ({ ...prev, [documentType]: "done" }));
      notify([
        ...documents.filter((doc) => doc.documentType !== documentType),
        { documentType, label: documentLabel(documentType), fileName: file.name },
      ]);
    } catch (caught) {
      setStates((prev) => ({ ...prev, [documentType]: "error" }));
      setErrors((prev) => ({
        ...prev,
        [documentType]:
          caught instanceof Error ? caught.message : "The upload did not finish.",
      }));
    }
  };

  return (
    <div className="space-y-3" data-testid="file-upload">
      {requiredTypes.length === 0 ? (
        <p className="text-sm text-slate-500">
          No documents are required for your education level yet.
        </p>
      ) : null}

      {requiredTypes.map((type) => {
        const state = states[type] ?? "idle";
        const error = errors[type] ?? null;
        return (
          <div key={type} className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">{documentLabel(type)}</span>
              {state === "done" ? (
                <span className="text-sm text-green-600" data-testid={`done-${type}`}>
                  ✓ Uploaded
                </span>
              ) : null}
              {state === "uploading" ? (
                <span className="text-sm text-slate-500" data-testid={`uploading-${type}`}>
                  Uploading…
                </span>
              ) : null}
            </div>
            <input
              ref={(element) => {
                inputs.current[type] = element;
              }}
              type="file"
              accept={UPLOAD_ACCEPT}
              disabled={disabled || state === "uploading"}
              aria-label={`Upload ${documentLabel(type)}`}
              data-testid={`input-${type}`}
              className="mt-2 block w-full text-sm"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void upload(type, file);
              }}
            />
            {error ? (
              <p className="mt-1 text-sm text-red-600" role="alert" data-testid={`error-${type}`}>
                {error}
              </p>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}