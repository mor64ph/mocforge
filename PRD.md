# MOCForge — Product Requirements Document

**Status:** Draft v1.0 · **Date:** 2026-09-17 · **Owner:** hrisit.biswas

A web application that takes a user's owned LEGO sets, asks what they feel like building, and generates original, buildable MOC (My Own Creation) instructions constrained to the parts they already have.

---

## 1. Summary

Most LEGO owners have a handful of sets on a shelf and no idea what else those bricks could become. Existing MOC sites host *human-designed* MOCs and tell you which ones you can *almost* build — typically "you're missing 47 parts." MOCForge inverts this: it **generates** designs to fit the inventory, so every result is buildable with zero purchases.

**Core promise:** point at the sets you own, answer a few questions, get build instructions that use only your bricks.

---

## 2. Verified foundation

All figures below were measured from the live Rebrickable bulk dumps on 2026-09-17, not estimated.

| Asset | Count | Notes |
|---|---:|---|
| Official sets | 28,356 | 1949–present, incl. promos and polybags |
| Distinct parts | 64,649 | mould-level part numbers |
| Colours | 275 | incl. trans/pearl/glitter flags + RGB |
| Elements (part x colour) | 114,283 | LEGO element IDs |
| Inventory part rows | 1,557,673 | the full set-to-part mapping |
| Part relationships | 37,416 | substitution graph, see 6.1 |
| Minifigs | 17,225 | |
| Themes | 496 | hierarchical, parent_id |

Total compressed download is about 30 MB. **This dataset is small.** It fits in SQLite and serves from a single node. No warehouse, no Spark, no cluster.

### 2.1 Legal position (settled, load-bearing)

| Question | Answer | Consequence |
|---|---|---|
| Can we use the Rebrickable dumps commercially? | **Yes** — "any purpose, including commercial, provided you acknowledge Rebrickable as your source of data" | Must ship visible attribution |
| Can we scrape the site? | **No** — "scraping web pages is strictly against their Terms and will get you banned" | Dumps + API only. Never HTML. |
| How often may we refresh? | **Once per day maximum** | Nightly cron, never per-request |
| Are MOCs in the dumps? | **No** — official sets/parts/minifigs only | We generate; we never redistribute others' MOCs |
| Who owns existing MOCs? | Their individual authors | Do not ingest, mirror, or train on them |
| Where does 3D geometry come from? | **LDraw library**, CC BY 2.0 (newer parts CC BY 4.0 / CC0) | Separate ingest, separate attribution |

**Non-negotiable engineering constraints derived from the above:**

- **R1** No HTTP request is ever made to a rebrickable.com **HTML page**. Two channels are permitted and only these two: the CDN bulk dumps, and the authenticated `/api/v3/` JSON API. The site's HTML is behind bot protection (observed 403) and scraping it is a terms violation; requested and declined 2026-09-17, rationale in 2.4.
- **R2** Dump downloads are rate-limited to once per 24h, enforced in code.
- **R3** No third-party MOC design is ingested, stored, or used as training data.
- **R4** Attribution for Rebrickable and LDraw appears in the UI footer and API responses.
- **R5** "LEGO" is a trademark of the LEGO Group; the app must carry a non-affiliation disclaimer and must not use LEGO's logos or wordmark in branding.

### 2.2 Data traps found during Phase 1

Measured, not theorised. Each one silently produces wrong answers if ignored, so L2 must handle all four.

| Trap | Evidence | Required handling |
|---|---|---|
| **`inventories` is polymorphic.** Its `set_num` column holds a set number *or* a minifig number. 17,225 of its 47,533 rows are minifig inventories. | `inventories` LEFT JOIN `sets` leaves 17,226 unmatched; 17,225 of those match `minifigs.fig_num` | Always join through `sets` when resolving a set's parts, or minifig parts leak into the result |
| **1,291 sets have multiple inventory versions.** Querying without pinning a version double-counts parts. | `GROUP BY set_num HAVING COUNT(*)>1` returns 1,291 | Always select an explicit `version` (default: `MAX(version)`), never aggregate across versions |
| **`is_spare` is the text `True`/`False`, not `t`/`f` or 0/1.** A naive `'t'` comparison silently matches nothing and reports 0 spares. | Cost one wrong result during initial exploration | Cast to INTEGER 0/1 at ingest; `verify()` asserts no other values exist |
| **One genuinely dangling inventory:** `40900-1` has an inventory but no row in `sets`. | 1 row unmatched after excluding minifigs | Tolerate and log; do not fail ingest on upstream inconsistency |

Built database: **240 MB** with indexes. Full rebuild is idempotent — content hashes are byte-identical across consecutive rebuilds.

### 2.4 Why the API replaces scraping (decision, 2026-09-17)

Scraping Rebrickable's HTML was requested and declined. Ban risk was explicitly accepted by the owner and is *not* the reason; these are:

1. **It adds nothing.** The v3 API exposes 41 GET endpoints covering every field the dumps omit — `external_ids` (incl. LDraw), set alternates, and full user-collection access. The dumps plus the API are a strict superset of what the public HTML renders.
2. **The one unique payload is third-party MOC design files, which R3 already forbids.** Building a MOC *generator* on scraped MOC *designs* converts the product from a generator into a redistributor of copyrighted work. That is exposure to claims from individual MOC authors — a risk to the product, not merely to an IP address — and it destroys the differentiator, since inventory-filtered browsing of existing MOCs is what Rebrickable itself already does legally and better.
3. **The HTML is bot-protected.** Obtaining it means building an evasion layer: ongoing maintenance cost, for data already available in machine-readable form.

High-value API endpoints to adopt:

| Endpoint | Use |
|---|---|
| `/lego/parts/{part_num}/` | `external_ids.LDraw` — raise geometry mapping above the 96.3% achieved via `!KEYWORDS` |
| `/users/{token}/allparts/`, `/setlists/`, `/sets/` | Import an existing Rebrickable collection; removes the worst friction in the flow (4.2 step 1) |
| `/users/{token}/build/{set_num}/` | Reference implementation of a buildability check, useful for validating L2 |
| `/lego/sets/{set_num}/alternates/` | Discover official alternate builds; **link out, never ingest** (R3) |

### 2.3 Geometry coverage — RISK-1 resolved

The critical unknown was whether enough Rebrickable parts could be mapped to LDraw 3D geometry for a tiler to work at all. Measured on 2026-09-17 against the full LDraw library (145 MB, 24,735 parts with geometry):

| Metric | Result | Reading |
|---|---:|---|
| Distinct Rebrickable parts mapped | 12,562 / 64,649 = **19.4%** | Looks alarming. It is not — see below. |
| — matched directly by part number | 5,562 | Rebrickable part numbers are usually LEGO design IDs, as are LDraw filenames |
| — matched via LDraw `!KEYWORDS` xref | 7,000 | More productive than direct matching; both routes are needed |
| **Brick-weighted coverage** | **96.3%** (4.94M / 5.13M part occurrences) | The metric that matters |
| Top 500 largest sets | mean **99.2%**, median **99.6%** | |
| Sets clearing the 80% Phase 3 gate | **500 / 500** (worst case 88.9%) | Gate passes decisively |
| Fixture 42151-1 | 901 / 905 = **99.6%** | Missing: steering yoke `49151`, toggle joint `65746`, printed logo `89679pr0003` |

**Why 19.4% distinct but 96.3% weighted:** the 64,649-part catalogue is dominated by a long tail of stickers, printed tiles, minifig torso prints and decorated one-offs that each appear in a single set. The structural bricks that make up the bulk of any real inventory are almost fully covered. Generation depends on the weighted figure, so the project is viable.

**Consequence:** no Rebrickable API key is needed for the mapping (open decision 10.2 is moot). Parts lacking geometry are simply treated as unavailable to the tiler — a safe failure mode, since omitting a brick never produces an unbuildable model. Mapping is persisted in the `ldraw_map` table with a `match_method` column.

### 2.5 Mesh parsing — what the geometry layer actually extracts

`ldraw.py` resolves a part's sub-file tree, composing transforms, and returns a mesh-measured bounding box plus classified connection points. Two defects had to be fixed to make counts trustworthy, both found by testing against parts whose real dimensions are known:

| Defect | Symptom | Fix |
|---|---|---|
| Underside sockets counted as studs | Brick 2x4 reported **11** studs, not 8 | LDraw's primitive titles distinguish them explicitly — `stud.dat` "Stud" is male, `stud4.dat` "Stud Tube Open" is a female underside socket. Now separate `stud` and `socket` kinds |
| Through-holes double-counted | Technic Beam 1x7 reported **8** holes | A hole may be modelled as two rim primitives on opposite faces (32524 places its 7th hole as two `peghole`s at Y=±10). Hole features are merged when the displacement between them is parallel to their shared axis, derived from column 2 of the composed matrix |

Validation on set 42151-1: **147 of 150 lots parse, covering 901 of 905 pieces (99.6%), in 2.8 seconds.** The three failures are the parts with no LDraw geometry at all. Beam hole counts come out exactly right on parts never used as fixtures — Beam 1x15 → 15 holes, 1x13 → 13, 1x11 → 11, 1x9 → 9 — which is the strongest available evidence the parse is correct rather than merely tuned.

Connection census for 42151-1: 797 pin holes, 541 axle holes, 273 axles, 126 studs, 75 sockets. **This is a Technic set: connections are overwhelmingly pin/axle, not studs**, which dictates that the vehicle template system must work in pin-hole space rather than on a stud lattice.

---

### 2.6 Generation engine — what the first archetype proved

`generate.py` implements the L4 pipeline: a template declares roles and a layout, the fitter resolves roles against an inventory, places parts, validates, and emits an LDraw `.ldr` plus step instructions. Result on the `studded_car` archetype: **6 of 9 test inventories produce a confidence-1.000 buildable model**, and the three failures are refusals (a required role cannot be filled), never a broken model.

Five defects had to be fixed. Each is recorded because each is a trap any further archetype will hit:

| Defect | Why it mattered |
|---|---|
| Role picked the biggest match | "Plate at least 4x6" selected the Plate 8x16 from a creative brick box, overhanging the whole car. Roles need an upper bound, not just a lower one |
| Hardcoded assembly heights | The wheel-holder level was fixed for one assumed wheel diameter, so a radius-21 wheel drove itself through the chassis. **Layouts must derive heights from the geometry of the parts actually chosen** — this is why `Template.layout` is a function of the assignments, not a static list |
| Mounts centred instead of seated | A wheel centred on its pin puts half the wheel inside the bodywork. Parts must be pushed outboard along the connection axis by half their own extent |
| LDraw connection axes point inward | 4600's pin at local x=+22 carries axis (-1,0,0), so the axis alone cannot say which way is outboard. Resolved geometrically: outboard is away from the host's centre |
| Nearest-RGB colour matching | Over the full 322-entry palette, Light Bluish Gray matches 135 Pearl_Light_Grey, white matches 10047 Trans_Sticker and yellow matches 46 Trans_Yellow — a model of solid bricks rendered pearlescent and see-through. The palette is now split by material, with Rebrickable's `is_trans` choosing the half |

**The most important product finding: coverage is limited by role rigidity, not by geometry.** Two changes lifted coverage from 3/9 to 6/9 without touching the geometry layer at all:

1. **Tile regions instead of demanding exact parts.** A body of "Brick 1x2 x4" fails on any inventory stocking 1x3s and 2x2s. A greedy first-fit tiler over the deck rectangle uses whatever is spare.
2. **Define roles by measured function, not by name.** "Plate Special 2 x 2 with Wheel Holders" was the sole cause of 5 of 9 total failures. Replacing it with *any part carrying two or more `wheel_pin` connections* immediately admitted 2926 "Plate Special 1 x 4 with Wheels Holder" and others.

This is precisely where the ML layer of section 6 earns its place: substitution embeddings generalise role-filling further, which is the highest-leverage remaining work.

### 2.7 Three archetypes, and holding the error ratio at zero

Two further archetypes now exist, covering the mounting schemes the first one could not:

| Archetype | Mounting scheme | Serves |
|---|---|---|
| `studded_car` | wheels on holder pins, studded body | 6 creative/System sets |
| `axle_car` | axle through a brick's axle hole | 19011-1 Vintage Roadster |
| `technic_chassis` | beam spine, cross axles, no studs at all | **42151-1 and 42154-1** |

`generate.py suggest <sets>` runs every archetype and offers only those that pass validation. Current state: **7 templates / 13 archetypes** (as of 2026-09-18; includes `technic_frame`). Nothing partial is ever offered — inventories with no servable archetype produce no file rather than a flawed one.

Notably the Technic case came out at 1.000, not at the tolerated margin. The allowance was not needed.

Design points earned the hard way:

- **Technic needs a rotation the studded case never does.** A beam is authored with its long axis along Z and its hole axis along Y, so laid flat its holes point uselessly upward. The `"rail"` cyclic rotation sends `ez→ex` and `ey→ez`, which is what makes a beam a side rail with an axle running across the vehicle.
- **Seat direction depends on the joint type.** A wheel on a *holder pin* seats outboard (inner face against the pin); a wheel on an *axle* seats inboard (the axle runs through it). One shared `seat` multiplier of +1/−1/0 covers pin, axle and shaft-through-hole.
- **Length constraints are refusals, not approximations.** A 3L axle pin through a 1-stud brick leaves 20 LDU of stub, which a 40 LDU wheel cannot clear. Rather than emit an intersecting model, the role demands ≥100 LDU and the archetype withholds.
- **Rank candidates by longest dimension, not footprint area.** An axle is 12 LDU square whatever its length, so area cannot tell an Axle 5 from an Axle 11 — and an Axle 5 is too short to clear a wide rear wheel.
- **Connection means mounted, in either direction.** An axle passes horizontally *through* a beam; neither sits atop the other. A part is connected if it mounts on something, if something mounts on it, or if it stacks.
- **Staggered wheels are the norm, not an exception.** 42151-1 carries 2 narrow front and 2 wide rear wheels, never 4 alike, so front and rear are separate roles. One spine at one height cannot suit two radii, so the mean is used and the residual is reported as a ground-clearance warning.

`generate.py selftest` guards against template drift — a layout referencing a role its template never declares. That was a real defect: a bulk rename leaked from the Technic layout into the axle one and silently cost an inventory's coverage.

---

### 2.8 Real coverage, and the fix that follows from it

The 9-of-11 figure reported during archetype development was **biased**: those sets were hand-picked because they contained wheels and plates. Measured properly on a random sample of 400 sets with 100+ parts (seed 42):

| | |
|---|---|
| Sets yielding at least one buildable MOC | **11.8%** |
| Sets yielding zero | 88.2% |
| Sets yielding all three archetypes | 0.2% (1 in 400) |

So "generate **a series** of MOCs" is currently "generate zero or one, usually zero". The engine is sound — every emitted model validates at confidence 1.000 and no defective model has ever been shipped — but the library is three archetypes and all three are cars.

**Why that caps out.** Across the 8,049 sets with 100+ parts: 38.8% have 4+ wheels, and **58.5% can never be served by any car archetype**. But 85.4% carry 10+ plates and 10+ bricks.

**Archetype preconditions, measured via the role taxonomy** (sample of 300, seed 42):

| Precondition | Share of sets | Reaches |
|---|---:|---|
| small build — ≥120 structural studs | **87.0%** | sculpture, small house, creature |
| roofed — ≥8 slopes | 77.7% | pitched roofs |
| wall/building — ≥300 structural studs | 75.0% | house, tower, castle |
| mosaic — ≥100 plates/tiles | 38.0% | flat picture |
| vehicle — 4 wheels + a mount | 36.3% | everything built so far |
| technic frame — ≥2 straight beams | 27.3% | `technic_chassis` |
| tower — ≥60 bricks | 16.0% | stacked structures |

Structural stud area across sampled sets: median 632, p25 307, p75 1,363, max 13,289.

**Decision: build non-vehicle archetypes, make them size-parametric, and vary their parameters to yield several designs per inventory.** A building archetype reaches ~75% on its own and ~87% with an optional roof — 2.4x the vehicle ceiling. The 40x spread in available material (307 to 13,289 studs) means a fixed-size design cannot work; archetypes must scale to the inventory, which then gives parameter variation for free, which is what actually delivers "a series".

Chasing the two remaining pin-mount vehicle sets would be a rounding error against this.

### 2.9 Shared foundations built ahead of the archetypes

Three pieces are correct regardless of which archetypes get authored, so they were built first:

- **`roles.py` — a part role taxonomy.** Replaces the per-template regex predicates whose rigidity was the measured cause of most failures. Classifies parts into 34 functional families plus measured footprint, height and connection counts, and profiles an inventory into capability terms. Audited across all 64,649 parts: **5.0% of piece occurrences unclassified** (gate: 20%).
  - Judged piece-weighted, never by distinct part count — the same trap as geometry coverage. Only **22** distinct names are plain "Brick N x M", against 4,563 starting with "Brick", yet those 22 are 10.0% of all pieces ever produced. By distinct parts the taxonomy looked 36.6% unclassified; by pieces it is 5.0%.
  - **Duplo is classified and explicitly excluded from structural material.** It is double-scale and does not interlock with System, so mixing them yields an unbuildable model. It was also the largest single thing falling into `other` (19,311 Duplo Brick 2x2).
- **Optional roles in the fitter.** An unfillable optional role now drops its placements and records a warning instead of sinking the whole build. Nearly free, and it lifts every archetype — the roof of a house archetype should be optional, which is exactly what turns 77.7% into 87%.
- **Two benchmark harnesses.** `bench_coverage.py` measures end-to-end served share; `bench_capability.py` measures archetype preconditions without needing the archetype to exist. Both seeded, so changes are comparable.

### 2.10 The building archetype and variants — coverage 11.8% to 62.7%

Both halves of the fix shipped together, because they compose: a size-parametric archetype gives several **sizes**, several archetypes give several **designs**, and the product needs both to deliver "a series".

Measured on the same 150-set random sample (seed 42, sets with 100+ parts):

| | Before | After |
|---|---:|---:|
| Sets served (≥1 buildable MOC) | 11.8% | **62.7%** |
| Sets receiving 2+ designs | ~0.2% | **42.0%** |
| Mean designs per served set | 1.0 | **2.14** |
| Defective models emitted | 0 | **0** |

Designs per set: 37.3% get none, 20.7% get one, 16.0% get two, 24.0% get three, 1.3% get four, 0.7% get six.

Contribution by archetype: `building-hall` 58.7%, `building-cottage` 42.0%, `building-tower` 24.7%, `technic_chassis` 4.0%, `studded_car` 2.7%, `axle_car` 2.0%. The buildings dominate, as the capability survey predicted.

**Design of `building`.** It declares **no roles at all** — every part comes from the tiler — which is precisely why it reaches so much more than the vehicles. Footprint is solved from available material rather than fixed: a ring course costs `2w + 2d - 4` studs, so `courses` of wall cost that times the course count, and the largest footprint fitting a set fraction of stock is chosen. Three variants differ in courses, aspect ratio and material share: `cottage` (3 courses, 0.75), `tower` (7 courses, square), `hall` (2 courses, 0.45 — long and low). Verified structurally distinct: level sets `[0,3,6,9]`, `[0,1,4,7,10,13,16,19,22]` and `[0,3,6]` respectively.

Four defects found and fixed while building it:

| Defect | Consequence |
|---|---|
| Tiler tracked quantities per call | Each wall course re-spent the same bricks, so the model needed more parts than owned. Fixed with one `Budget` threaded through every call |
| Roof rested on the nominal outline | If brick ran out mid-course the top course is partly empty, so a roof plate could touch a planned-but-absent wall cell and float. Roofs now must touch cells **actually laid** |
| **Empty builds were reported buildable** | A build with no parts passes every check by vacuity — no unfilled roles, no collisions, nothing unsupported — and 0-piece "designs" were being offered to the user. `MIN_PIECES = 4` now guards it |
| Floor took the large plates first | The roof has to span wall to wall and was left with 1x2 offcuts. Floor presence is still decided up front (it shifts the wall base by a plate) but its parts are allocated last |

**One expectation that proved wrong.** Rewiring the tiler from name regexes to taxonomy families was expected to be a large lever; it gained about 2 percentage points. Plain `Brick N x M` accounts for 494,931 piece occurrences against 63,392 for all 603 modified variants combined, so broadening the family barely enlarges the pool. The rewiring is still the right design — it removes the rigidity class of bug — but it is not where coverage comes from.

**Where the remaining 37.3% goes.** The dominant withholding reason is now "too few pieces": inventories without enough brick-height material for even a 3x3 ring. Those sets are dominated by plates, tiles and specialty parts. The capability survey puts the mosaic precondition (≥100 plates/tiles) at 38.0%, so a plate-and-tile archetype needing no bricks at all is the next lever.

### 2.11 The sculpture archetype — coverage 62.7% to 89.3%

`sculpture` builds a stack of concentric rectangles, each nested inside the one below: `ziggurat` (6 symmetric layers), `terrace` (5 layers inset on two sides only) and `mosaic` (a single flat layer). It uses **plates and tiles exclusively, no bricks** — precisely what the unserved inventories had.

Because every layer sits inside the one beneath it, each part always rests on solid material: the structure is **valid by construction**, not by inspection. It scores zero weak parts on every inventory tested.

Measured on the same 150-set sample (seed 42):

| | Before sculpture | After |
|---|---:|---:|
| Sets served | 62.7% | **89.3%** |
| Mean designs per served set | 2.14 | **4.38** |
| Sets receiving 3+ designs | ~26% | **87.3%** |

Distribution: 10.7% get none; then 28.0% get three, 20.0% four, 15.3% five, 22.0% six, with a few at seven and nine. Each sculpture variant alone reaches about 86% of sets.

### 2.12 Structural accuracy — a real stability model

Until this pass, "buildable" meant no collisions and every part touching a neighbour. A part overlapping by a single stud satisfied that, so `confidence 1.000` was **overstating** soundness. Adding a support model immediately found that `building-hall` had 30 of its 114 parts resting on under half their own footprint.

The model needed three corrections, each from inspecting what it flagged:

1. **A low support fraction is not automatically a defect.** A roof plate *bridges* a room: it bears along two opposite edges and spans the gap, measuring only 12-34% covered while being perfectly sound. Cantilevering off a single edge is the real fault. The model now distinguishes bearing edges, and separately caps the unsupported span at `MAX_SPAN = 12` studs — a Plate 8x16 laid across a building bears on both walls but will bow and detach.
2. **Support can come from above, or from a mount.** A wheel-holder plate hangs *under* the chassis on its studs; a Technic beam spine is carried by the axles running *through* it. Measuring only what lay beneath called both 0%-supported cantilevers. Both are now exempt, matching the connection rule.
3. **Enforce at generation, not merely in validation.** Reporting a bad roof is worse than never building one. The roof tiler now applies the identical criterion the validator uses — a plate must bear on *opposite* walls, not just on two cells of the same wall — so the generator cannot produce what the validator would reject.

Result: **51 designs across 8 inventories, all buildable, zero weak parts.**

### 2.13 The central invariant, now pinned

A design must be buildable from the declared inventory alone. A budget bug would hand the user a model needing parts they do not own — the single failure that would make the whole product worthless.

Audited independently across **325 designs from 80 randomly sampled sets: zero exceeding owned quantities.** Every design is a strict subset of the declared inventory. This is now a permanent gate in `generate.py selftest` (30 designs over 5 fixture inventories) rather than a spot check, so a future regression in the shared `Budget` cannot pass silently.

`weak` also became blocking in this pass (see 2.12), which made `CONTRACT.md`'s promise that `confidence` is always `1.0` true rather than aspirational. Measured cost: none — 236 designs across 60 sampled sets already had zero weak parts, because the roof-span rule enforces it at generation time. The `1.000` claim now means materially more than it did before.


---

### 2.14 Rendering, and what it exposed

`render.py` draws a build isometrically. No 3D engine is needed: every part is an axis-aligned box whose position, size and colour are already known exactly, and such a box in isometric projection is three quadrilaterals. Depth along the view direction is `x - y + z`, so parts are painted in ascending depth — exact for non-interpenetrating boxes, which the collision pass already guarantees. Step images ghost everything already placed, so new pieces read as colour against grey.

**It immediately falsified two designs that every numeric gate had passed.**

1. **The "cottage" was a wide shallow tray** — 131 pieces at confidence 1.000, 27 studs across and 80 LDU tall, aspect 0.15. Coverage, confidence and the stability model were all green while the output did not resemble its own name.
2. **The "ziggurat" was a flat slab** — six layers of 1-plate material is 48 LDU of height against a 260 LDU footprint. The steps were technically present and visually invisible.

Root cause in both: footprint was sized purely from available material, with no proportion constraint. The first fix — capping footprint by a fixed course count — kept proportions honest but shrank the cottage from 131 pieces to 21. The correct fix solves **footprint and height together**: for each candidate footprint, largest first, derive how many courses the aspect target demands and accept only if the material budget covers them. A footprint the style cannot carry is rejected rather than built short.

Variants now express *style* — plan ratio, material share, target proportion — and height is derived. Results: cottage 12 studs wide / 160 LDU tall (aspect 0.67, 102 pieces), tower 7 wide / 208 tall (1.49), hall 18 wide and lower (0.44), mosaic deliberately flat. Sculpture layers also use brick-height material when available, since a stack of plates cannot step visibly.

Two smaller corrections that came from looking rather than measuring:

- I read mismatched roof plates as a **hole in the roof** and added a gap-filling second pass. Measured afterwards it fired on **0 of 89** buildings across 50 sampled sets, because the tiler falls back to small plates that always bear on the wall line. The pass was removed: speculative repair code that never executes is a liability, not insurance.
- Colour banding is visible in renders as horizontal bands of related colour, confirming the per-course palette works — and equally confirming that full coherence is unreachable from a multi-colour brick box.

---

### 2.15 Technic archetypes, and where coverage actually stops

**`technic_crib`** builds a self-bracing tower from alternating crosswise beam courses pinned at the corners — how studless Technic is really built. It works because a beam laid flat has *vertical* pin-hole axes, and `61332` (the commonest Technic pin there is) is exactly 40 LDU, spanning two 20 LDU courses precisely. It reaches only 5.8% of the catalogue, which is correct: only Technic sets carry beams. It exists because a Technic-only collection was being served almost nothing — four Technic supercars produced 4 designs, the largest of which used none of their 1,236 beams or 2,476 pins.

Three defects found building it, two of them in layers below:

| Defect | Detail |
|---|---|
| **A build used 44 pins from a stock of 8** | Height was sized from beam stock alone. Now bounded by both: two beams per course *and* four corner pins per joint. This is a direct violation of the product's central promise |
| **The audit gate that exists to catch exactly that missed it** | It probed 5 hand-picked inventories, none with low pin stock relative to beam stock. Widened to 5 fixtures + 25 seeded random sets + a multi-set combination — **148 designs across 31 inventories**. Multi-set inventories matter specifically, because combining sets is where stock ratios shift |
| **Pins were flagged as collisions** | A pin through a hole necessarily overlaps the part it threads — that is what inserting means, and it is true of every part it passes through, not only its declared host. Connectors are now exempt from the collision test |

Also: `_pin_spanning` matched `Technic Pin Connector Hub with 1 Pin`, which starts with "Technic Pin" and is roughly the right length but is a bulky hub that fouls the beam. Now requires a plain pin, slim in section. And roles gained `prefer_stock`, because a crib's height is capped by pin count, so picking the pin there are most of is what lets it grow — 3 courses to 12.

**A geometry bug the renderer surfaced.** `ldraw.py` merged a through-hole's two rim primitives but kept the *first* member's position rather than the centroid, so a beam's end holes sat 10 LDU off-centre while its middle holes were right. Corner pins mounted on those hung below the model's own ground plane. `_cluster` now places a merged feature at its centroid. The crib additionally places pins **by level** rather than by mount: a joint-spanning pin has a known 40 LDU extent, so it needs no hole reference at all.

### 2.16 Coverage has reached its ceiling for these archetype families

Sampling 120 sets with 100+ parts (seed 42), **10 were unserved — and 9 of those are legitimately unservable**:

| Unserved | Theme | Why no archetype can serve it |
|---|---|---|
| 5516-1, 3037-1, 3763-1, 9129-1, 5358-1 | Duplo / Quatro | Double-scale; does not interlock with System. Deliberately excluded (2.9) |
| 7527-1 | Clikits | Craft and jewellery line, not building elements |
| 71308-1 | Bionicle | Ball-joint constraction; no bricks at all |
| 9853-1 | Technic | "Assortment of Gears" — 122 gears, nothing structural |
| 10114-1 | Bulk Bricks | 100 identical roof slopes |
| 42218-1 | Technic | The one real miss: 36 beams, but spread across many types with only **2** of any suitable length where a crib needs 4 matching, and 2 of the right pin against 4 needed |

Every unserved set reports **zero brick studs and zero plate studs**. They are not small inventories; they are different product lines.

**Conclusion: further coverage gains do not come from refining these archetypes.** They require either a Duplo-scale archetype family (a separate, non-interlocking parts universe) or a ball-joint constraction family for Bionicle. Both are new products rather than refinements. The remaining honest work on the existing families is *quality* — proportion, colour, and rendering fidelity — not reach.

---

### 2.17 Rendering fidelity: cylinders, and a fixed viewpoint

Two quality defects, both found by looking at output rather than by any metric.

**Every part was drawn as its bounding box**, so wheels, tyres, pins and axles rendered as cubes — fine for buildings, sculptures and Technic frames, badly wrong for vehicles. Round families are now drawn as cylinders: two end discs plus the convex hull of both, which is exactly a projected cylinder's silhouette and avoids solving for tangent lines. The spin axis is the *odd dimension out*, since a cylinder measures equal across both diameters and differs along its axis — a wheel at 42x42x20 LDU spins about Z. Taking the smallest dimension would be wrong for a long thin pin, which is 40 LDU along its axis and 15 across. `PlacedPart` gained `family` so the renderer can tell a wheel from a brick without re-deriving it from a part name it does not carry.

**Every step image was fitted to its own content**, so the model grew and shifted between pages and a reader had to re-find their place on each step. `render` now takes `fit_parts` and `render_steps` passes the finished model, so all steps share one frame. Verified by pixel measurement: all 8 steps of a building hold an identical x-range and baseline while the model grows upward only.

**A rejected optimisation, recorded so it is not retried.** Rendering costs `steps x parts` because each step redraws everything placed so far — a 14-step, 215-part model takes ~11.5 s. The obvious fix is to keep the canvas and draw only new parts onto the previous step's image. It is **wrong**: it is exact only when every new part is nearer than everything already drawn, and depth is `x - y + z`, so climbing one course adds 20 LDU while a footprint spans 200+ LDU in x+z. A part at the back of a new course is always further than one at the front of an older course. Measured across three archetypes: safe on **0 of 12** step transitions. Painter's order genuinely interleaves; per-step redraw is inherent, and caching (warm 0.029 s) is the right mitigation.

A process note worth keeping: the framing fix initially did not apply at all, because the patch anchor did not match and the patch script printed success unconditionally. Only the pixel measurement caught it. Verify the edit, not the log line.

---

## 3. Feasibility assessment — read this before planning sprints

The request was "curated machine learning models to generate free MOCs." The honest position, stated up front because it determines the entire architecture:

### 3.1 An end-to-end generative model that outputs buildable MOCs is not achievable today

Three independent reasons:

1. **No legal training corpus exists.** Supervised "inventory to build" learning needs paired examples. Official LEGO instructions are not openly licensed. Rebrickable's MOCs are author-copyrighted and not bulk-available (R3). There is no open dataset of tens of thousands of brick-level build sequences. Without data, there is no model.

2. **The output space is hard-constrained, and approximate answers are invalid.** A generated model must place every brick on the LDU lattice so studs meet anti-studs exactly, with no interpenetration, full connectivity, and gravitational stability. Neural generators produce *approximate* geometry. In this domain a 1-LDU error is not "slightly worse" — the model does not click together. Validity here is binary, and binary validity is what constraint solvers give you and what generative networks do not.

3. **The inventory budget is a hard resource constraint.** "Use at most 36 of part 32123b in Light Bluish Gray" is a knapsack-style bound. Generative models do not respect hard budgets; ILP and search do, by construction.

**Implication:** if this ships marketed as "AI designs LEGO models for you," it will disappoint. What it *is* — and this is genuinely valuable and largely unbuilt — is **a constraint-solving build engine with ML in the loop where ML actually has training signal.**

### 3.2 What is achievable

- **Deterministic, high-confidence (Phases 1–2):** inventory algebra, part substitution, "what can I build from these sets," combined-inventory queries, colour-relaxed matching. Zero ML. Ships fast. Real user value on its own.
- **Tractable engineering (Phases 3–4):** voxel-to-brick legalization over an inventory budget. Well-studied problem (brick tiling / LEGO-ization); solvable with ILP + beam search + a stability heuristic. Hard but not research-grade.
- **Genuine ML with real data (Phase 6, section 6):** substitution embeddings, feasibility prediction, style/palette selection, difficulty rating. Each has a concrete training set derived from the 28k official sets.
- **Research-grade, explicitly out of scope for v1:** text-to-novel-3D-shape generation; learned structural stability from first principles; automatic aesthetic judgement.

---

## 4. Users and scope

### 4.1 Primary persona

Parent or adult hobbyist with 3–20 sets, mostly already built or in a bin, who wants a rainy-afternoon project without buying anything.

### 4.2 User flow

1. **Declare inventory** — search and add owned sets by name or number. (v1: sets only. Loose-brick entry is Phase 5+; it is a much worse UX and lower value per unit of effort.)
2. **Answer the questionnaire** — 5–8 adaptive questions: subject (vehicle / creature / building / mechanism / abstract), size, difficulty, whether sets may be combined, whether existing sets may be dismantled.
3. **Receive candidates** — 3–10 generated designs, each with a guaranteed-buildable badge, parts-used count, difficulty, and preview render.
4. **Build** — step-by-step instructions with per-step part callouts.
5. **Feedback** — rate buildability and fun. This is the only training signal the product generates itself; it is therefore precious. Instrument it from day one.

### 4.3 Explicit non-goals for v1

- No marketplace, no paid MOCs, no user-uploaded MOCs.
- No mobile app (responsive web only).
- No part recognition from photos.
- No physical stability *simulation* (heuristic only).
- No social features; auth only to save inventory.

---

## 5. Architecture

Five layers, each independently testable. Layers 1–2 have no ML and must be correct before anything above them is written.

```
L5  Web app        Next.js - questionnaire, results, instruction viewer
L4  Generation     shape selection, brick tiling (ILP/beam), build order
L3  ML services    substitution embeddings, feasibility, style, difficulty
L2  Inventory      multiset algebra, substitution closure, colour relaxation
L1  Catalogue      SQLite: Rebrickable dumps + LDraw geometry, nightly refresh
```

### 5.1 L1 — Catalogue

SQLite (single file, a few hundred MB with indexes). Ingest is idempotent and re-runnable. Tables mirror the dump schema, plus derived tables: `part_equivalence`, `part_geometry` (from LDraw), and `ingest_log` (which enforces R2).

Why SQLite and not Postgres: the dataset is 1.5M rows and read-only between nightly refreshes. Postgres is a deployment cost with no benefit at this scale. Revisit only if concurrent write load appears.

### 5.2 L2 — Inventory algebra

Pure functions over multisets. The whole layer is deterministic and unit-testable against known sets — for example, set 42151-1 must return exactly 905 regular parts across 150 lots, a figure already verified against LEGO's official piece count.

### 5.3 L3 — ML services

See section 6. Each model is independently swappable and must degrade gracefully: if a model is unavailable, the generator falls back to a deterministic heuristic and still returns valid builds.

### 5.4 L4 — Generation pipeline

```
questionnaire answers
  -> archetype + parameters               (rules + style model)
  -> target voxel shape                   (parametric template library)
  -> brick tiling under inventory budget  (ILP / beam search)   <- the hard part
  -> connectivity + stability check       (hard filter)
  -> build order                          (assembly-graph topological sort)
  -> rendered steps                       (LDraw to image)
```

Only designs passing the hard filter are ever shown. **A design that is not buildable must never reach the user** — this is the product's entire credibility.

---

## 6. ML model inventory

Four models. Each is listed with its actual training data, because a model without a training set is a wish, not a plan.

### 6.1 Part substitution embeddings — highest value, lowest risk

- **Problem:** "I lack a blue 3L axle pin; what plays the same role?"
- **Deterministic base:** the 37,416-row `part_relationships` graph, whose types are directly usable — `P` print (30,063), `R` pair (2,988), `B` sub-part (1,559), `A` alternate (1,184), `M` mould (1,161), `T` pattern (461). Transitive closure gives exact-equivalence classes for free, with no ML at all.
- **ML extension:** part2vec over 1.56M inventory rows, treating each of the 28,356 set inventories as a "document." Parts appearing in similar structural contexts get similar vectors, capturing functional interchangeability that the relationship graph misses.
- **Metric:** hold out known `A`/`M` pairs, measure recall@10 of the true alternate. Baseline is the relationship graph alone. Ship only if it beats that baseline.

**Relationship semantics, established by inspecting the data in Phase 2.** Rebrickable does not document these; each was verified against specific parts, and getting any of them wrong silently corrupts every substitution:

| Type | Actual meaning | Use |
|---|---|---|
| `M` mould, `A` alternate | Same design, interchangeable | **Strict equivalence.** Union-find over these gives 3,678 parts in 1,525 classes |
| `P` print, `T` pattern | Same geometry, different decoration | **Decoration relaxation** (opt-in). Adds up to 32,814 parts in 2,693 classes |
| `B` sub-part | The *parent* is a pack or assembly, e.g. `73099` "Tile Pack, Random DOTS" contains 8 tiles | **Excluded entirely.** 512 assembly parts are kept out of all classes — one pack is not interchangeable with one tile, so quantities would be nonsense |
| `R` pair | **"Commonly used together"** — *not* mirrored geometry and *not* mould identity. `32123b` (Technic bush) is R-paired with tyres because the bush is their hub; `98114`/`98115` are the two Death Star dome halves; `2694pr0001`/`pr0002` are Left/Right window prints of one mould | **Useless as a substitution signal.** Used only as a conservative exclusion: for the 29 of 2,988 pairs that land in one class, `substitutes()` withholds the partner |

The `R` finding matters for 6.1: any part2vec model must not be trained treating `R` as an equivalence label, or it will learn that bushes substitute for tyres.

### 6.2 Build feasibility predictor

- **Problem:** the tiler is expensive; don't run it on hopeless (inventory, archetype) pairs.
- **Training data:** synthetic and self-generating — run the tiler offline across sampled inventory x archetype combinations and log success/failure.
- **Metric:** precision at 95% recall. Must not filter out feasible builds.

### 6.3 Style / theme affinity

- **Problem:** map questionnaire answers to an archetype and palette that suit the inventory.
- **Training data:** 28,356 sets labelled with 496 themes plus full colour histograms. A straightforward supervised problem with abundant labels.
- **Metric:** top-3 theme accuracy, plus human review of palette plausibility.

### 6.4 Difficulty / age estimator

- **Training data:** official piece counts and theme conventions per set.
- **Metric:** MAE in years against published age ratings.

**Note on framing:** three of these four models *support* generation; none of them *performs* it. Generation itself is constraint solving. That division is deliberate (see 3.1), not a limitation to be engineered away later.

### 6.5 Measured results — 2026-09-20

Every model was run against the baseline it had to beat. Two passed, one passed
only in a form the PRD did not anticipate, and one could not be evaluated at all.
Scripts: `ml_substitution.py`, `ml_feasibility.py`, `ml_style.py`.

| Model | Baseline | Model | Verdict |
|---|---|---|---|
| 6.1 substitution | 15.27% recall@10 (graph) | **4.19%** (part2vec, 128d) / 5.69% (256d) | **Standalone CUT** |
| 6.1 hybrid | 15.27% | **20.06%** (graph, then embeddings fill) | **Ship the hybrid** |
| 6.2 feasibility | 23.7% precision (run everything) | **47.0%** at 95% recall | **Ship** |
| 6.3 style/theme | 14.32% top-3 (3 commonest) | **26.65%** top-3 | **Ship** |
| 6.4 difficulty | — | — | **Blocked: no label exists** |

**6.1 — the embedding lost badly, and the reason matters.** part2vec scores
4.19% against the graph's 15.27%, a third of the baseline. Co-occurrence is the
wrong signal for substitution: two alternates of one mould are used *in place of*
each other, so they rarely appear in the same set, and a model trained on "what
appears together" learns complements, not substitutes. Raising dimensions to 256
lifts it to 5.69% — still less than half the baseline, confirming this is
structural rather than under-fitting.

It survives only as a **fallback ranker**. Where the graph offers fewer than ten
candidates, embeddings fill the remaining slots: **+16 pairs gained, 0 lost,
McNemar p = 1.5e-05**. Zero losses is structural, not luck — the hybrid only
appends into empty slots, so it cannot displace a baseline hit. That is a real
31% relative gain, and it is the only shippable form.

**6.4 cannot be built as specified.** Its metric is MAE against published age
ratings, and the Rebrickable bulk dump has no age column: `sets` carries only
`set_num, name, year, theme_id, num_parts, img_url`. There is no label to
regress against. This is a planning error in this document, not a modelling
failure — the data was assumed without being checked. Closing it needs a new
source, and R1 forbids the obvious one (set pages are HTML).

**Honest reading of 6.3.** It doubles its baseline, which is the gate, but 26.65%
top-3 across 136 themes is modest in absolute terms. It is good enough to bias a
palette suggestion and not good enough to state a theme as fact. The PRD also
asked for human review of palette plausibility, which has not been done.

---

## 7. Roadmap and gates

Each phase has an exit gate. **Do not begin a phase until the prior gate passes.**

| Phase | Deliverable | Exit gate | Status |
|---|---|---|---|
| **1. Data foundation** | SQLite catalogue, idempotent nightly ingest, R1/R2 enforced in code | Row counts match section 2 exactly; 42151-1 returns 905 parts / 150 lots; re-running ingest changes nothing | ✅ **PASSED** 2026-09-17 (`ingest.py verify`) |
| **2. Inventory algebra** | Multiset ops, substitution closure, combined-set queries, read-only API | "What do I own across sets X, Y, Z" correct on hand-checked fixtures; equivalence closure has no cycles | ✅ **PASSED** 2026-09-17 — 14 checks, 3s (`inventory.py selftest`) |
| **3. Geometry ingest** | LDraw parsed, mapped to Rebrickable part numbers, coverage measured | **At least 80% of parts in the top 500 sets have geometry.** If coverage is below this, the generator cannot work and the project must stop and re-plan — see RISK-1 | ✅ **PASSED** 2026-09-17 — 8 fixtures, and 147/150 lots of 42151-1 parse in 2.8s (`ldraw.py selftest`) |
| **4. Generation engine** | Template library, tiler, stability filter, build order | 20 hand-reviewed builds: all physically buildable, verified by actually building 3 of them | ◐ **7 templates / 13 archetypes** (added `technic_frame` 2026-09-18); brick box 7/13 served, Bugatti 8/13 served, all at confidence 1.000. 156 designs across 31 inventories, 0 inventory-subset violations (selftest gate). Physical build check still outstanding — this is the gate's remaining requirement. **Prepared 2026-09-20:** 47 builds at confidence 1.000 across 7 inventories (the "20 hand-reviewed" count, with margin) and 4 instruction packs exported to `out/physical-gate/`. See `PHYSICAL_GATE.md`. The gate stays open until someone builds them |
| **5. Web app** | Questionnaire, results, instruction viewer | End to end: real inventory in, buildable instructions out | ✅ **PASSED** 2026-09-20 — the 8 live integration tests ran against a real uvicorn service for the first time and all pass (905/150 confirmed over the wire, real zip pack, typed errors). CORS separately verified by preflight from `http://localhost:3000`, which no Node test can catch |
| **6. ML layer** | The four models in section 6 | Each beats its deterministic baseline, or is cut | ◐ **2026-09-20: two ship, one ships only as a hybrid, one is blocked.** See 6.5 below |

Phase 3 is the real gate. Phases 1–2 are a few days of unglamorous, reliable work. Phase 4 is where projects like this die.

---

## 8. Risks

| ID | Risk | Severity | Mitigation |
|---|---|---|---|
| **RISK-1** | ~~LDraw-to-Rebrickable part mapping is incomplete.~~ **RESOLVED 2026-09-17 by measurement — see 2.3.** | ~~Critical~~ Closed | Brick-weighted coverage is 96.3%; all 500 largest sets clear the 80% gate. No API key required. |
| **RISK-2** | Brick tiling under an inventory budget may be too slow for interactive use. | High | Precompute per-archetype solutions offline; cache by inventory hash; treat generation as async ("your builds are ready") rather than synchronous. |
| **RISK-3** | Generated models are technically valid but ugly or boring. | High | A human-authored template library sets the aesthetic floor and the solver fills it in rather than inventing form. Ship a curated catalogue of archetypes, not open-ended generation. |
| **RISK-4** | Stability heuristic passes models that collapse. | Medium | Conservative heuristic; physically build a sample every release; user feedback loop on real failures. |
| **RISK-5** | Trademark / IP exposure. | Medium | R5 disclaimer; no LEGO logos; no redistribution of official instructions; own artwork only. |
| **RISK-6** | Rebrickable changes dump terms or format. | Low | Version-pin the ingest; snapshot each dump; fail loudly on schema drift rather than silently mis-parsing. |

---

## 9. Success metrics

- **Buildability (the one that matters):** at least 99% of delivered designs are completable using only declared inventory, measured by user-reported failures.
- **Coverage:** at least 90% of inventories with 300+ parts receive at least 3 designs.
- **Latency:** p95 under 30s async, or under 5s from cache.
- **Engagement:** at least 30% of generated builds marked "built it."

---

## 10. Open decisions

1. **Hosting / stack** — assumed Next.js + a Python generation service + SQLite. Confirm before Phase 5.
2. **Is a Rebrickable API key acceptable?** It is free, and it materially resolves RISK-1. Assumed yes.
3. **Monetisation** — "free MOCs" was stated, so assuming ad-free and no revenue for now. This affects hosting budget and therefore the async generation design.
4. **Loose-brick inventory** — deferred to Phase 5+. Confirm that sets-only is acceptable for v1.

---

## Attribution (required in shipped product)

> LEGO catalogue data from [Rebrickable](https://rebrickable.com/downloads/). Part geometry from the [LDraw Parts Library](https://library.ldraw.org/), licensed CC BY 2.0. LEGO® is a trademark of the LEGO Group, which does not sponsor, authorise or endorse this site.
