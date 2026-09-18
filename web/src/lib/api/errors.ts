import type { ApiErrorBody } from "./contract";

/**
 * Every failure the client can see, carrying the contract's error `code` so
 * the UI can distinguish "no such set" from "the service is down" without
 * matching on message text.
 */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly detail: unknown;

  constructor(code: string, message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.detail = detail;
  }

  /** `404 set_not_found` carries `detail: { unresolved: string[] }`. */
  get unresolvedSets(): string[] {
    const detail = this.detail;
    if (detail === null || typeof detail !== "object") return [];
    const unresolved = (detail as { unresolved?: unknown }).unresolved;
    if (!Array.isArray(unresolved)) return [];
    return unresolved.filter((entry): entry is string => typeof entry === "string");
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

/** Narrow an unknown JSON payload to the contract's `Error` shape. */
export function parseErrorBody(body: unknown): ApiErrorBody["error"] | null {
  if (body === null || typeof body !== "object") return null;
  const error = (body as { error?: unknown }).error;
  if (error === null || typeof error !== "object") return null;
  const { code, message, detail } = error as Record<string, unknown>;
  if (typeof code !== "string" || typeof message !== "string") return null;
  return detail === undefined ? { code, message } : { code, message, detail };
}

export const ERROR_COPY: Record<string, string> = {
  set_not_found: "That set is not in the catalogue.",
  invalid_request: "The request was rejected as invalid.",
  design_not_found:
    "This design is no longer available. Design ids are not kept across service restarts.",
  network: "Could not reach the MOCForge service.",
  render_failed: "This design could not be drawn, so no instructions could be built.",
  package_failed: "The instruction pack could not be assembled.",
  step_not_found: "That build step is not part of this design.",
  feature_unavailable:
    "This build of MOCForge cannot produce instruction packs.",
};

/** A sentence fit for a user, never a raw exception string. */
export function describeError(error: unknown): string {
  if (isApiError(error)) {
    return ERROR_COPY[error.code] ?? error.message;
  }
  return "Something went wrong. Please try again.";
}
