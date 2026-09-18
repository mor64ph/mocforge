import type { MocForgeApi } from "../client";
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
} from "../contract";
import { MAX_SEARCH_LIMIT, MAX_SETS } from "../contract";
import { ApiError } from "../errors";
import { SETS, SET_BY_NUM, canonicaliseSetNum } from "./catalogue";
import { ARCHETYPE_SUMMARIES, generateFor, inventoryFor } from "./engine";
import { CATALOGUE_PART_COUNT } from "./parts";
import { renderLdr } from "./ldraw";

export type MockApiOptions = {
  /** Simulated service latency. Off in tests, on in the browser. */
  latency?: boolean;
};

/**
 * In-process implementation of CONTRACT.md.
 *
 * It enforces the contract's rules rather than just returning happy-path data:
 * `q` must be non-empty, an inventory is 1 to 10 sets, unresolved set numbers
 * come back as `404 set_not_found` with `detail.unresolved`, and an `.ldr` is
 * only served for a design id this process has actually issued — the real
 * service does not persist ids across restarts either.
 */
export function createMockApi(options: MockApiOptions = {}): MocForgeApi {
  const withLatency = options.latency ?? true;
  const issued = new Map<string, Design>();

  async function delay(ms: number, signal?: AbortSignal): Promise<void> {
    if (!withLatency || ms <= 0) return;
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        signal?.removeEventListener("abort", onAbort);
        resolve();
      }, ms);
      function onAbort() {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      }
      if (signal?.aborted) {
        onAbort();
        return;
      }
      signal?.addEventListener("abort", onAbort, { once: true });
    });
  }

  function resolveSetNums(raw: readonly string[]): string[] {
    if (raw.length === 0 || raw.length > MAX_SETS) {
      throw new ApiError(
        "invalid_request",
        `An inventory is 1 to ${MAX_SETS} sets; ${raw.length} were sent.`,
        400,
      );
    }
    const resolved: string[] = [];
    const unresolved: string[] = [];
    for (const entry of raw) {
      const canonical = canonicaliseSetNum(entry);
      if (canonical) resolved.push(canonical);
      else unresolved.push(entry);
    }
    if (unresolved.length > 0) {
      throw new ApiError(
        "set_not_found",
        unresolved.length === 1
          ? `Set ${unresolved[0]} is not in the catalogue.`
          : `${unresolved.length} of the sets sent are not in the catalogue.`,
        404,
        { unresolved },
      );
    }
    return [...new Set(resolved)];
  }

  function rank(query: string): SetSummary[] {
    const needle = query.trim().toLowerCase();
    const canonical = canonicaliseSetNum(needle);

    const exact: SetSummary[] = [];
    const numberPrefix: SetSummary[] = [];
    const byName: SetSummary[] = [];

    for (const entry of SETS) {
      const { summary } = entry;
      const setNum = summary.setNum.toLowerCase();
      if (canonical === summary.setNum) exact.push(summary);
      else if (setNum.startsWith(needle)) numberPrefix.push(summary);
      else if (summary.name.toLowerCase().includes(needle)) byName.push(summary);
    }

    numberPrefix.sort((a, b) => a.setNum.localeCompare(b.setNum));
    byName.sort((a, b) => b.numParts - a.numParts || a.setNum.localeCompare(b.setNum));
    return [...exact, ...numberPrefix, ...byName];
  }

  return {
    async health(options) {
      await delay(80, options?.signal);
      return {
        status: "ok",
        catalogue: {
          sets: SETS.length,
          parts: CATALOGUE_PART_COUNT,
          updatedAt: "2026-09-17T00:00:00Z",
        },
        // No features: the pack needs the isometric renderer and the real
        // LDraw geometry, neither of which exists in the browser. Reporting an
        // empty list is what stops the UI offering a download that cannot
        // work, and it is the same mechanism a service built without the
        // renderer would use.
        features: [],
      } satisfies HealthResponse;
    },

    async searchSets(query, limit, options) {
      if (query.trim().length === 0) {
        throw new ApiError("invalid_request", "A search term is required.", 400);
      }
      if (limit < 1 || limit > MAX_SEARCH_LIMIT) {
        throw new ApiError(
          "invalid_request",
          `limit must be between 1 and ${MAX_SEARCH_LIMIT}.`,
          400,
        );
      }
      await delay(140, options?.signal);
      return { results: rank(query).slice(0, limit) } satisfies SearchResponse;
    },

    async getSet(setNum, options) {
      await delay(100, options?.signal);
      const canonical = canonicaliseSetNum(setNum);
      const entry = canonical ? SET_BY_NUM.get(canonical) : undefined;
      if (!entry) {
        throw new ApiError("set_not_found", `Set ${setNum} is not in the catalogue.`, 404, {
          unresolved: [setNum],
        });
      }
      return entry.summary;
    },

    async getInventory(body: InventoryRequest, options) {
      const setNums = resolveSetNums(body.setNums);
      await delay(260, options?.signal);
      return inventoryFor(setNums) satisfies Inventory;
    },

    async generate(body: GenerateRequest, options) {
      const setNums = resolveSetNums(body.setNums);
      const inventory = inventoryFor(setNums);
      const startedAt = Date.now();
      // Generation is synchronous and takes seconds; the cost tracks inventory
      // size because the tiler walks every lot for every archetype variant.
      await delay(Math.min(4200, 900 + Math.round(inventory.pieces * 0.8)), options?.signal);
      const { designs, withheld } = generateFor(setNums, body.archetypes);
      for (const design of designs) issued.set(design.id, design);
      return {
        inventory,
        designs,
        withheld,
        elapsedMs: Date.now() - startedAt,
      } satisfies GenerateResponse;
    },

    async getArchetypes(options) {
      await delay(90, options?.signal);
      return { archetypes: ARCHETYPE_SUMMARIES } satisfies ArchetypesResponse;
    },

    async getLdr(design, options) {
      await delay(180, options?.signal);
      const known = issued.get(design.id);
      if (!known) {
        throw new ApiError(
          "design_not_found",
          "That design id was not issued by this service instance.",
          404,
        );
      }
      return renderLdr(known);
    },

    async getPackage() {
      throw new ApiError(
        "feature_unavailable",
        "This build cannot produce instruction packs; it has no renderer.",
        501,
      );
    },
  };
}
