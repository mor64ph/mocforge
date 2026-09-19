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

Instruction packs are in `out/physical-gate/` (zip: PDF, per-step PNGs, `.ldr`).
Each was chosen to exercise a *different* mechanism, so three builds cover the
engine rather than testing one thing three times.

| Pack | Pieces | Steps | What it puts at risk |
|---|---|---|---|
| `building-cottage_10696-1` | 102 | 8 | Wall courses and the roof tiler. Roof plates must **bear on opposite walls** — the bridging rule from PRD 5. The largest build, so the best test of cumulative drift. |
| `sculpture-ziggurat_10696-1` | 70 | 5 | Stacked concentric layers and the support fraction. Each layer must sit stably on the one below with no overhang that tips. |
| `studded_car_10696-1` | 9 | 4 | Wheel mounting and **seat direction** — a wheel on a holder pin seats outboard. Tiny, but it tests the one rule a plate stack never touches. |
| `technic_frame-wide_42151-1` | 47 | 2 | *Optional fourth.* The newest archetype, least proven: rungs sit one beam-height above the rails on the strength of a zero-Y-overlap calculation. If any build is wrong, expect it to be this one. |

A fourth is listed deliberately — `technic_frame` was added on 2026-09-18 and
has never been built. If you only have appetite for three, swap out the car.

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
