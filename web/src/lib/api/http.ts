import type { MocForgeApi, RequestOptions } from "./client";
import type {
  ArchetypesResponse,
  Design,
  GenerateRequest,
  GenerateResponse,
  HealthResponse,
  Inventory,
  InventoryRequest,
  SearchResponse,
  SetSummary,
} from "./contract";
import { ApiError, parseErrorBody } from "./errors";

/** Transport for the real service. Speaks only CONTRACT.md. */
export function createHttpApi(origin: string): MocForgeApi {
  const base = `${origin.replace(/\/+$/, "")}/api/v1`;

  async function request<T>(
    path: string,
    init: RequestInit,
    options?: RequestOptions,
  ): Promise<T> {
    const response = await send(`${base}${path}`, init, options);
    return (await response.json()) as T;
  }

  async function send(
    url: string,
    init: RequestInit,
    options?: RequestOptions,
  ): Promise<Response> {
    let response: Response;
    try {
      response = await fetch(url, {
        ...init,
        ...(options?.signal ? { signal: options.signal } : {}),
        headers: { accept: "application/json", ...init.headers },
      });
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
      throw new ApiError("network", "The MOCForge service is unreachable.", 0, cause);
    }
    if (!response.ok) throw await toApiError(response);
    return response;
  }

  async function toApiError(response: Response): Promise<ApiError> {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    const parsed = parseErrorBody(body);
    if (parsed) {
      return new ApiError(parsed.code, parsed.message, response.status, parsed.detail);
    }
    return new ApiError(
      "unexpected_response",
      `The service returned ${response.status} without a contract error body.`,
      response.status,
    );
  }

  function json(body: unknown): RequestInit {
    return {
      method: "POST",
      body: JSON.stringify(body),
      headers: { "content-type": "application/json" },
    };
  }

  return {
    health: (options) => request<HealthResponse>("/health", { method: "GET" }, options),

    searchSets: (query, limit, options) =>
      request<SearchResponse>(
        `/sets/search?q=${encodeURIComponent(query)}&limit=${limit}`,
        { method: "GET" },
        options,
      ),

    getSet: (setNum, options) =>
      request<SetSummary>(
        `/sets/${encodeURIComponent(setNum)}`,
        { method: "GET" },
        options,
      ),

    getInventory: (body: InventoryRequest, options) =>
      request<Inventory>("/inventory", json(body), options),

    generate: (body: GenerateRequest, options) =>
      request<GenerateResponse>("/generate", json(body), options),

    getArchetypes: (options) =>
      request<ArchetypesResponse>("/archetypes", { method: "GET" }, options),

    getLdr: async (design: Design, options) => {
      const url = new URL(design.ldrUrl, `${origin}/`).toString();
      const response = await send(url, { method: "GET", headers: { accept: "text/plain" } }, options);
      return response.text();
    },

    // The id goes into the path as an opaque token, which is not the same as
    // parsing it (CONTRACT.md rule 5). `Design` deliberately carries no
    // `packageUrl`: `ldrUrl` is the one sanctioned artefact URL, and a design
    // holding URLs for some of its artefacts but not others is worse than
    // building all of them the same way.
    getPackage: async (design: Design, options) => {
      const response = await send(
        `${base}/designs/${encodeURIComponent(design.id)}/package`,
        { method: "GET", headers: { accept: "application/zip" } },
        options,
      );
      return response.blob();
    },
  };
}
