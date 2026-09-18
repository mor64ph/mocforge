# MOCForge HTTP API contract v1.2

**Normative.** The API service and the web client are built independently against
this document. Neither side may change a shape here without updating this file
first. Where this document and an implementation disagree, this document wins.

Base path: `/api/v1`. All responses `application/json; charset=utf-8` unless
stated. All errors use the `Error` shape with a correct HTTP status.

---

## Types

```ts
type SetSummary = {
  setNum: string;        // canonical, e.g. "42151-1"
  name: string;          // e.g. "Bugatti Bolide"
  year: number | null;
  themeId: number | null;
  themeName: string | null;
  numParts: number;      // official piece count, 0 when unknown
  imgUrl: string | null;
};

type InventoryLot = {
  partNum: string;
  partName: string;
  colorId: number;
  colorName: string;
  colorRgb: string | null;   // 6 hex digits, no leading '#'
  isTrans: boolean;
  quantity: number;
  imgUrl: string | null;
};

type Inventory = {
  setNums: string[];         // as resolved, canonical
  pieces: number;            // regular parts only, excludes spares
  lots: number;
  geometryPieces: number;    // pieces with 3D geometry available
  families: { family: string; pieces: number; studs: number }[];
  structuralStuds: number;
};

type BuildStep = {
  index: number;             // 1-based
  level: number;             // plates above ground
  parts: {
    partNum: string;
    partName: string;
    colorId: number;
    colorName: string;
    quantity: number;
    note: string;            // human label, e.g. "wall course 2"
  }[];
};

type Design = {
  id: string;                // stable for (archetype, variant, setNums)
  archetype: string;         // "building"
  variant: string | null;    // "cottage"
  title: string;
  pieceCount: number;
  confidence: number;        // 0..1; only >=1.0 designs are ever returned
  warnings: string[];        // raw engine diagnostics, may be empty
  builderNotes: string[];    // v1.2; the same facts for a builder, may be empty
  steps: BuildStep[];
  ldrUrl: string;            // GET returns text/plain LDraw
};

type Error = {
  error: { code: string; message: string; detail?: unknown };
};
```

**Invariant the client may rely on:** the API never returns a `Design` that
failed validation. `confidence` is always `1.0` in v1 and a design always has
at least 4 parts. Withheld designs appear only in `GenerateResponse.withheld`.

**`warnings` versus `builderNotes`.** They are the same facts for two
audiences, and both fields are needed:

- `warnings` is the generation engine's own diagnostic text. It names roles by
  the predicate that matched them and measures in LDU, e.g. `lowest point is
  20.0 LDU vs ground plane 0 (sunk below ground)`. It is what someone debugging
  the generator needs, and it is unfit to put in front of a builder.
- `builderNotes` is the subset of those warnings that means something to a
  person holding bricks, in words they can act on. A warning with no builder
  meaning is **dropped**, not reworded, so this list is often shorter than
  `warnings` and frequently empty while `warnings` is not. A diagnostic the
  translation does not recognise is also dropped: an untranslated one is worse
  than an absent one.

**A client must render `builderNotes` and must not render `warnings`.** The
translation is server-side precisely so that the web page and the downloaded
PDF cannot disagree about a design's caveats, and so the table cannot drift
between two languages. Both render this field verbatim.

`builderNotes` is a *response* field. Rule 5 governs unknown **query and body**
fields on requests; adding a field to a response is a compatible change, and a
v1.1 client that ignores it keeps working.

---

## Endpoints

### `GET /api/v1/health`
`200` → `HealthResponse`

```ts
type HealthResponse = {
  status: "ok";
  catalogue: { sets: number; parts: number; updatedAt: string };
  features: string[];      // v1.1; capability tokens, see below
};
```

`features` is how a client discovers optional endpoints. A service that renders
designs reports `"designImage"`, `"stepImage"` and `"designPackage"`; one that
does not omits them, and a client must not call the corresponding endpoint.
Unknown tokens are ignored, so the list may grow without a contract revision.
Probing `/health` rather than calling an endpoint and reading its `404` is
required, because a `404` on a design endpoint already means "no such design".

### `GET /api/v1/sets/search?q=<string>&limit=<1..50>`
Search by set number or name. `q` is required, min length 1.
- `200` → `{ results: SetSummary[] }`
- `400` if `q` missing/empty.

Accepts a bare set number (`42151`) and resolves it to canonical (`42151-1`).
Ranking: exact set-number match first, then prefix matches on number, then
name matches by descending `numParts`.

### `GET /api/v1/sets/{setNum}`
- `200` → `SetSummary`
- `404` `code: "set_not_found"`

### `POST /api/v1/inventory`
Body `{ setNums: string[] }` — 1 to 10 entries.
- `200` → `Inventory`
- `400` `code: "invalid_request"` on empty or >10
- `404` `code: "set_not_found"` with `detail: { unresolved: string[] }`

### `POST /api/v1/generate`
Body `{ setNums: string[], archetypes?: string[] }`
- `200` → `GenerateResponse`
- `400` / `404` as above

```ts
type GenerateResponse = {
  inventory: Inventory;
  designs: Design[];                      // validated only, best first
  withheld: { archetype: string; variant: string | null; reason: string }[];
  elapsedMs: number;
};
```

`designs` is sorted by `pieceCount` descending. May be empty — an inventory
with no buildable design is a normal outcome, not an error, and the client must
present `withheld` reasons in that case.

### `GET /api/v1/designs/{id}/ldr`
- `200` → `text/plain` LDraw model. `Content-Disposition: attachment`.
- `404` `code: "design_not_found"` — ids are not persisted across restarts.

### `GET /api/v1/designs/{id}/image` — v1.1, feature `designImage`
### `GET /api/v1/designs/{id}/steps/{index}/image` — v1.1, feature `stepImage`

Isometric PNG renders, produced by `render.py`. `index` is 1-based and matches
`BuildStep.index`; a step image ghosts everything already placed so the new
pieces read as colour against grey.

- `200` → `image/png`
- `404` `code: "design_not_found"` / `code: "step_not_found"`
- `500` `code: "render_failed"`

An `index` that is not an integer is `400 invalid_request`. An integer outside
the design — including `0` and negatives — is `404 step_not_found`: it is a
wrong question about a real design, not a malformed request.

### `GET /api/v1/designs/{id}/package` — v1.1, feature `designPackage`

The design as one self-contained `.zip`, for a user with no CAD software. The
`.ldr` alone opens in Stud.io, LeoCAD or LDView and nowhere else, so on its own
the product ships nothing an ordinary owner can look at.

- `200` → `application/zip`, `Content-Disposition: attachment`
- `404` `code: "design_not_found"`
- `500` `code: "render_failed"` — the design could not be drawn
- `500` `code: "package_failed"` — the document or the archive could not be written

The archive contains, at minimum:

| Member | |
|---|---|
| `instructions.pdf` | cover with the finished model, a parts list, then one page per step with its image and the parts added |
| `parts-list.csv` | `Part number,Part name,Colour,Quantity`, summed over the whole design |
| `steps/step-NN.png` | every step image, usable on its own; `NN` is `BuildStep.index` |
| `finished-model.png` | the finished model |
| `<archetype>-<variant>_<sets>.ldr` | the same bytes `GET .../ldr` serves |
| `ATTRIBUTION.txt` | data sources, licences and the trademark notice (PRD R4/R5) |

The same notices appear in the PDF, on the cover and in every page footer. A
downloaded pack outlives the page that served it, so attribution travels
inside it.

The three v1.1 endpoints are answered from one render pass per design, so
whichever a client requests first pays the cost and the others are then cheap.
Renders take seconds; a client must show a real preparing state, as it does for
`/generate`.

**`Design` carries no URL for these endpoints.** A client addresses them by
placing `Design.id` in the path as an opaque token, which is not the same as
parsing it (rule 5). `packageUrl` and `imageUrl` were considered and rejected:
`ldrUrl` remains the only sanctioned way to reach the `.ldr`, and one design
holding URLs for some of its artefacts but not others is worse than a client
building all of them the same way. (`builderNotes`, added in v1.2, shows that a
new response field is compatible when it carries data rather than routing.)

### `GET /api/v1/archetypes`
`200` → `{ archetypes: { name, title, variants: string[], studded: boolean }[] }`

---

## Behaviour requirements

1. **Generation is synchronous in v1** but may take seconds. The client must
   show a real progress state, not a spinner with no context. The same applies
   to the v1.1 package and image endpoints, which render on first request.
2. **Design caveats are shown from `builderNotes`, never from `warnings`.**
   See the note under `Design`. A client that prints `warnings` is showing a
   user "20.0 LDU vs ground plane 0", which reads as a fault in a design the
   API has already certified buildable.
3. **Attribution is mandatory** in every client build: catalogue data from
   Rebrickable, geometry from the LDraw Parts Library (CC BY 2.0), and a
   LEGO non-affiliation disclaimer. See PRD "Attribution". Every response also
   carries it as `X-Data-Attribution`, and every downloadable artefact — the
   `.ldr`, the PDF, `ATTRIBUTION.txt` — carries it inside the file.
4. **No endpoint may proxy or scrape rebrickable.com HTML.** The service reads
   only the local SQLite catalogue. See PRD R1.
5. Ids are opaque. The client must not parse them.
6. Unknown query/body fields are rejected, not ignored. This governs requests;
   see `builderNotes` for why a new response field is not a breach of it.
