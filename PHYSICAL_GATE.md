# Phase 4 physical build gate — worksheet

**Status: NOT PASSED.** This document prepares the gate; it cannot close it.
The gate requires a human to assemble real bricks, which is exactly why the
PRD chose it. Everything below is the preparation.

> **Exit gate (PRD 7):** 20 hand-reviewed builds, all physically buildable,
> verified by actually building 3 of them.

## Part 1 — the 20 hand-reviewed builds

Generated 2026-09-20 across seven inventories. **47 builds, every one at
confidence 1.000**, which satisfies the "20 hand-reviewed" count with margin.
Reproduce with `python generate.py suggest <set>`.

| Inventory | Pieces | Builds offered | Piece counts |
|---|---|---|---|
| 10696-1 Creative Box | 484 | 7 | 9–112 |
| 42151-1 Bugatti Bolide | 905 | 8 | 7–47 |
| 10275-1 Elf Club House | 1,180 | 6 | 31–53 |
| 21045-1 Trafalgar Square | 1,197 | 6 | 24–43 |
| 31132-1 Viking Ship | 1,170 | 5 | 7–143 |
| 71813-1 Wolf Mask Dojo | 1,151 | 6 | 19–47 |
| 75392-1 Droid Builder | 1,179 | 6 | 6–57 |

**Reviewer's note, recorded before building.** Validation confidence is not the
same as a satisfying model. Several builds are disproportionately small for
their inventory — a 6-piece "building" from 75392-1's 1,179 pieces, a 7-piece
one from 31132-1's 1,170. These pass every geometric check and are genuinely
buildable, so they do not fail this gate, but they are the proportion problem
PRD 5 flags as the remaining quality work. Worth noticing that the numeric
gates cannot see it.

## Part 2 — the 3 to actually build

**Scoped to 42151-1 (Bugatti Bolide), the only set owned.** An earlier draft of
this worksheet used packs from 10696-1 as well; those were removed, because a
model you cannot physically build tests nothing. All eight designs this
inventory supports are exported to `out/physical-gate/` (zip: PDF, per-step
PNGs, `.ldr`), so the choice below can be overridden.

Build **three**, chosen to exercise three *different* mechanisms rather than
one mechanism three times:

| Pack | Pieces | Steps | What it puts at risk |
|---|---|---|---|
| `technic_frame-wide` | 47 | 2 | **Build this one first.** Newest archetype (added 2026-09-18), never built. Rungs sit one beam-height above the rails purely on a zero-Y-overlap calculation, and the span relies on `MAX_SPAN = 12`. If any design is wrong, expect it to be this. |
| `sculpture-ziggurat` | 24 | 3 | The only **non-Technic** mechanism this inventory supports: stacked plate layers and the support fraction. Each layer must sit stably with no overhang that tips. |
| `technic_chassis` | 7 | 2 | Axle mounting and **seat direction** — a wheel on an axle seats inboard, unlike one on a holder pin. Also the ground-clearance warning from staggered front/rear wheel radii. |
| `technic_crib-tower` | 14 | 3 | *Optional fourth.* Stacked beam-and-pin, distinct from the frame's rung-on-rail. Cheap to build if the first three go quickly. |

**Two honest caveats about this inventory.**

*These builds are small.* 7–47 pieces, because the Bugatti's 905 parts are
spread across 150 lots with few duplicates, and the archetypes need matching
parts. The best test of cumulative drift would have been the 102-piece cottage
from 10696-1, which is not available here. A 7-piece chassis exercises a rule
but not the accumulation of error across many steps, so this gate will be a
*weaker* test than the PRD imagined. Worth recording alongside the result.

*This costs you a built model.* 42151-1 is presumably assembled as the Bugatti.
Every design here needs its beams, pins and axles, so closing this gate means
taking it apart. That is a real price, and it is the reason to build
`technic_frame-wide` first: if it fails, the finding is worth the teardown on
its own, and the other two can wait.

## Part 3 — what to record

For each build, the gate is not "did it look right" but these five:

- [ ] **Every part in the PDF was in the box.** If a part is missing, the
      inventory algebra is wrong — a far more serious failure than a wobble.
- [ ] **Every piece physically fits where the step puts it.** No forcing, no
      part occupying space another already holds.
- [ ] **It holds together when lifted** by its topmost piece.
- [ ] **Nothing sags, bows or detaches** — especially any plate spanning a gap
      (the `MAX_SPAN = 12` studs assumption).
- [ ] **The step order is followable** — no step requires a piece to pass
      through a part already placed.

Record failures as: pack name, step number, part, and what physically went
wrong. A single reproducible failure is worth more than three vague passes,
because it points at a specific rule in the validator.

## Part 4 — if a build fails

Do not adjust the model to make the symptom disappear. Each of the five checks
above maps to a specific rule, and a failure means that rule is wrong:

| Symptom | Rule to re-examine |
|---|---|
| Parts overlap in space | `COLLISION_EPS = 5.0` LDU — too permissive |
| Something sags or bows | `MAX_SPAN = 12` studs — too generous |
| A piece falls off | `MIN_SUPPORT = 0.5`, or the bridge-test exemption |
| A piece floats unattached | the connection rule (1.5 LDU in Y, >1.0 overlap in X and Z) |
| A step is impossible to perform | build ordering, not geometry |
| A rung sits at the wrong height | `technic_frame`'s `rung_level = rail_height / LDU_PER_PLATE` |
| A part named in the PDF is not in the box | not geometry at all — the inventory algebra or the version pinning |
