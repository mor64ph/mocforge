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

export type RequestOptions = {
  signal?: AbortSignal | undefined;
};

/**
 * The whole surface the client uses, one method per CONTRACT.md endpoint.
 *
 * Two implementations exist: the in-process mock (src/lib/api/mock) and the
 * HTTP transport (src/lib/api/http). Nothing above this interface knows which
 * one it is talking to, so landing the real service is a transport swap.
 */
export interface MocForgeApi {
  health(options?: RequestOptions): Promise<HealthResponse>;
  searchSets(query: string, limit: number, options?: RequestOptions): Promise<SearchResponse>;
  getSet(setNum: string, options?: RequestOptions): Promise<SetSummary>;
  getInventory(body: InventoryRequest, options?: RequestOptions): Promise<Inventory>;
  generate(body: GenerateRequest, options?: RequestOptions): Promise<GenerateResponse>;
  getArchetypes(options?: RequestOptions): Promise<ArchetypesResponse>;
  /** Takes the whole design because `Design.ldrUrl` is the only sanctioned way
   *  to reach the file — CONTRACT.md rule 5 forbids parsing `Design.id`. */
  getLdr(design: Design, options?: RequestOptions): Promise<string>;
  /**
   * The design as a self-contained zip: a printable PDF, every step image, the
   * parts list as CSV, the `.ldr` and the attribution notice.
   *
   * CONTRACT.md v1.1, and optional: only call it when `health()` reports
   * `FEATURE_DESIGN_PACKAGE`. A transport that cannot build one rejects with
   * `code: "feature_unavailable"` rather than pretending.
   */
  getPackage(design: Design, options?: RequestOptions): Promise<Blob>;
}
