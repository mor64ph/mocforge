/**
 * Types transcribed from CONTRACT.md (MOCForge HTTP API contract v1).
 *
 * CONTRACT.md is normative: if this file and that document ever disagree, the
 * document wins and this file is the bug. Nothing here may be widened or
 * loosened to suit the client.
 */

export type SetSummary = {
  setNum: string;
  name: string;
  year: number | null;
  themeId: number | null;
  themeName: string | null;
  numParts: number;
  imgUrl: string | null;
};

export type InventoryLot = {
  partNum: string;
  partName: string;
  colorId: number;
  colorName: string;
  colorRgb: string | null;
  isTrans: boolean;
  quantity: number;
  imgUrl: string | null;
};

export type InventoryFamily = {
  family: string;
  pieces: number;
  studs: number;
};

export type Inventory = {
  setNums: string[];
  pieces: number;
  lots: number;
  geometryPieces: number;
  families: InventoryFamily[];
  structuralStuds: number;
};

export type BuildStepPart = {
  partNum: string;
  partName: string;
  colorId: number;
  colorName: string;
  quantity: number;
  note: string;
};

export type BuildStep = {
  index: number;
  level: number;
  parts: BuildStepPart[];
};

export type Design = {
  id: string;
  archetype: string;
  variant: string | null;
  title: string;
  pieceCount: number;
  confidence: number;
  /**
   * Raw engine diagnostics — roles named by their match predicate, distances
   * in LDU. CONTRACT.md behaviour requirement 2: **never render these.** They
   * are here for debugging the generator, and "20.0 LDU vs ground plane 0"
   * reads to a user as a fault in a design the API has certified buildable.
   */
  warnings: string[];
  /**
   * v1.2. The subset of `warnings` that means something to someone holding
   * bricks, translated server-side. Render this. A diagnostic with no builder
   * meaning is dropped rather than reworded, so this is often shorter than
   * `warnings` and frequently empty while `warnings` is not. The downloaded
   * PDF prints the same field verbatim, which is what stops the page and the
   * printed instructions disagreeing about a design's caveats.
   */
  builderNotes: string[];
  steps: BuildStep[];
  ldrUrl: string;
};

export type WithheldDesign = {
  archetype: string;
  variant: string | null;
  reason: string;
};

export type GenerateResponse = {
  inventory: Inventory;
  designs: Design[];
  withheld: WithheldDesign[];
  elapsedMs: number;
};

export type SearchResponse = {
  results: SetSummary[];
};

export type ArchetypeSummary = {
  name: string;
  title: string;
  variants: string[];
  studded: boolean;
};

export type ArchetypesResponse = {
  archetypes: ArchetypeSummary[];
};

export type HealthResponse = {
  status: "ok";
  catalogue: {
    sets: number;
    parts: number;
    updatedAt: string;
  };
  /** v1.1 capability tokens. Unknown tokens are ignored, so this may grow. */
  features: string[];
};

export type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    detail?: unknown;
  };
};

/** Request bodies, so the mock and the HTTP transport cannot drift apart. */
export type InventoryRequest = { setNums: string[] };
export type GenerateRequest = { setNums: string[]; archetypes?: string[] };

/**
 * CONTRACT.md v1.1: the token `GET /health` reports when it can build the
 * downloadable instruction pack. A client must not call the package endpoint
 * unless the service it is talking to names this, because the mock transport
 * and any service without the renderer do not implement it.
 */
export const FEATURE_DESIGN_PACKAGE = "designPackage";

/** CONTRACT.md: 1 to 10 set numbers per inventory. */
export const MAX_SETS = 10;
/** CONTRACT.md: GET /sets/search accepts limit 1..50. */
export const MAX_SEARCH_LIMIT = 50;
