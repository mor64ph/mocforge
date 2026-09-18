/**
 * Integration test: the real HTTP client against a live MOCForge API.
 *
 * The rest of the suite runs against the in-process mock, which by
 * construction agrees with the client's own types. That proves the UI works
 * but proves nothing about the actual service, so every field name,
 * nullability and number format across the wire is untested until this runs.
 *
 * Skipped unless MOCFORGE_LIVE_API is set to the origin of a running service:
 *
 *   python -m uvicorn api.main:app --port 8140
 *   MOCFORGE_LIVE_API=http://127.0.0.1:8140 npm test
 */
import { describe, expect, it } from "vitest";

import { createHttpApi } from "./http";
import type { GenerateResponse, Inventory } from "./contract";
import { FEATURE_DESIGN_PACKAGE } from "./contract";

const origin = process.env.MOCFORGE_LIVE_API?.trim();
const live = describe.skipIf(!origin);

// 42151-1 is the Bugatti Bolide: verified in the engine's own gate at exactly
// 905 regular parts across 150 lots, so the numbers here are independently
// known rather than whatever the service happens to return.
const TECHNIC_SET = "42151-1";
const BRICK_BOX = "10696-1";

live("the live API satisfies the contract the client is built against", () => {
  const api = createHttpApi(origin ?? "");

  it("reports health with catalogue counts and its v1.1 features", async () => {
    const health = await api.health();
    expect(health.status).toBe("ok");
    expect(health.catalogue.sets).toBeGreaterThan(20000);
    expect(health.catalogue.parts).toBeGreaterThan(60000);
    expect(Number.isNaN(Date.parse(health.catalogue.updatedAt))).toBe(false);
    // The download affordance is offered on the strength of this list alone.
    expect(health.features).toContain(FEATURE_DESIGN_PACKAGE);
  });

  it("resolves a bare set number the way the search box relies on", async () => {
    const { results } = await api.searchSets("42151", 10);
    expect(results.length).toBeGreaterThan(0);
    const hit = results.find((r) => r.setNum === TECHNIC_SET);
    expect(hit).toBeDefined();
    // Every field the chip UI renders must be present, not merely typed.
    expect(typeof hit?.name).toBe("string");
    expect(hit?.name.length).toBeGreaterThan(0);
    expect(typeof hit?.numParts).toBe("number");
  });

  it("returns an inventory whose totals match the engine's verified fixture", async () => {
    const inv: Inventory = await api.getInventory({ setNums: [TECHNIC_SET] });
    expect(inv.setNums).toContain(TECHNIC_SET);
    expect(inv.pieces).toBe(905);
    expect(inv.lots).toBe(150);
    expect(inv.geometryPieces).toBeLessThanOrEqual(inv.pieces);
    expect(inv.families.length).toBeGreaterThan(0);
    for (const family of inv.families) {
      expect(typeof family.family).toBe("string");
      expect(typeof family.pieces).toBe("number");
      expect(typeof family.studs).toBe("number");
    }
  });

  it("generates designs that honour every invariant the UI assumes", async () => {
    const res: GenerateResponse = await api.generate({ setNums: [BRICK_BOX] });
    expect(res.designs.length).toBeGreaterThan(0);
    expect(typeof res.elapsedMs).toBe("number");

    // Sorted by pieceCount descending - the results list does not re-sort.
    const counts = res.designs.map((d) => d.pieceCount);
    expect([...counts].sort((a, b) => b - a)).toEqual(counts);

    for (const design of res.designs) {
      expect(design.confidence).toBe(1);
      expect(design.pieceCount).toBeGreaterThanOrEqual(4);
      expect(design.id).toMatch(/^[a-z0-9]+$/i);
      expect(design.steps.length).toBeGreaterThan(0);

      // v1.2: both fields present, and `builderNotes` is a translation rather
      // than a copy — it is what the UI renders, so no engine units in it.
      expect(Array.isArray(design.warnings)).toBe(true);
      expect(Array.isArray(design.builderNotes)).toBe(true);
      expect(design.builderNotes.length).toBeLessThanOrEqual(design.warnings.length);
      for (const note of design.builderNotes) {
        expect(note).not.toMatch(/LDU|ground plane|optional role/);
      }

      // Step indices are 1..n with no gaps, and the parts across all steps
      // account for exactly pieceCount - the instructions view depends on both.
      const indices = design.steps.map((s) => s.index);
      expect(indices).toEqual(indices.map((_, i) => i + 1));
      const summed = design.steps.reduce(
        (total, step) =>
          total + step.parts.reduce((n, part) => n + part.quantity, 0),
        0,
      );
      expect(summed).toBe(design.pieceCount);

      for (const step of design.steps) {
        expect(typeof step.level).toBe("number");
        for (const part of step.parts) {
          expect(part.partName.length).toBeGreaterThan(0);
          expect(part.colorName.length).toBeGreaterThan(0);
          expect(part.quantity).toBeGreaterThan(0);
        }
      }
    }

    for (const held of res.withheld) {
      expect(typeof held.archetype).toBe("string");
      expect(held.reason.length).toBeGreaterThan(0);
    }
  });

  it("serves the LDraw file for a generated design", async () => {
    const res = await api.generate({ setNums: [BRICK_BOX] });
    const design = res.designs[0];
    expect(design).toBeDefined();
    if (!design) return;
    const ldr = await api.getLdr(design);
    const partLines = ldr
      .split("\n")
      .filter((line) => line.startsWith("1 "));
    expect(partLines.length).toBe(design.pieceCount);
    // R4/R5: attribution must travel with the file itself.
    expect(ldr).toMatch(/Rebrickable/);
    expect(ldr).toMatch(/LDraw/);
    expect(ldr).toMatch(/LEGO is a trademark/i);
  });

  it("serves an instruction pack a browser can save", async () => {
    const res = await api.generate({ setNums: [BRICK_BOX] });
    const design = res.designs[0];
    expect(design).toBeDefined();
    if (!design) return;

    const blob = await api.getPackage(design);
    expect(blob.type).toBe("application/zip");
    // Big enough to hold a rendered document, small enough to be a download.
    expect(blob.size).toBeGreaterThan(50_000);
    expect(blob.size).toBeLessThan(20_000_000);

    // A zip really does start "PK". Asserting the size alone would
    // pass on an HTML error page of the right length.
    const head = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
    expect([...head]).toEqual([0x50, 0x4b, 0x03, 0x04]);
  });

  it("surfaces an unknown set as a typed error, not a crash", async () => {
    await expect(
      api.getInventory({ setNums: ["99999999-1"] }),
    ).rejects.toMatchObject({ code: "set_not_found" });
  });

  it("surfaces a pack for an unknown design as a typed error", async () => {
    await expect(
      api.getPackage({
        id: "0000000000000000",
        archetype: "building",
        variant: null,
        title: "not a design",
        pieceCount: 0,
        confidence: 1,
        warnings: [],
        builderNotes: [],
        steps: [],
        ldrUrl: "/api/v1/designs/0000000000000000/ldr",
      }),
    ).rejects.toMatchObject({ code: "design_not_found", status: 404 });
  });
});
