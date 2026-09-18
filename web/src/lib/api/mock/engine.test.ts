import { describe, expect, test } from "vitest";
import { SETS } from "./catalogue";
import { generateFor, inventoryFor } from "./engine";

/**
 * Guards the fixture layer the UI tests are written against: the inventory
 * arithmetic, the determinism `Design.id` stability depends on, and the two
 * inventories that make the populated and empty results states reachable.
 */
describe("fixture inventories", () => {
  test("every set's lots sum to its published piece count", () => {
    for (const { summary, lots } of SETS) {
      const summed = lots.reduce((total, [, , quantity]) => total + quantity, 0);
      expect(summed, summary.setNum).toBe(summary.numParts);
    }
  });

  test("combining sets adds pieces and merges shared lots", () => {
    const one = inventoryFor(["10696-1"]);
    const two = inventoryFor(["10696-1", "10715-1"]);
    const other = inventoryFor(["10715-1"]);

    expect(two.pieces).toBe(one.pieces + other.pieces);
    // Both boxes share part-and-colour combinations, so lots must not just add.
    expect(two.lots).toBeLessThan(one.lots + other.lots);
  });

  test("geometry coverage is reported below the total, as the catalogue has gaps", () => {
    const inventory = inventoryFor(["31120-1"]);
    expect(inventory.geometryPieces).toBeGreaterThan(0);
    expect(inventory.geometryPieces).toBeLessThan(inventory.pieces);
  });

  test("family pieces account for the whole inventory", () => {
    const inventory = inventoryFor(["60380-1"]);
    const summed = inventory.families.reduce((total, entry) => total + entry.pieces, 0);
    expect(summed).toBe(inventory.pieces);
  });

  test("stud area is zero for families with no whole-stud footprint", () => {
    const inventory = inventoryFor(["31088-1"]);
    const byFamily = new Map(inventory.families.map((entry) => [entry.family, entry]));

    for (const family of ["wedge", "minifig", "bar", "plant"]) {
      const entry = byFamily.get(family);
      expect(entry?.pieces, family).toBeGreaterThan(0);
      expect(entry?.studs, family).toBe(0);
    }

    // 230 pieces, but almost none of it tileable — this is the inventory that
    // makes the empty-results path reachable.
    expect(inventory.pieces).toBe(230);
    expect(inventory.structuralStuds).toBeLessThan(40);
  });
});

describe("generation", () => {
  test("is deterministic, so design ids stay stable for the same inventory", () => {
    const first = generateFor(["10696-1", "42151-1"]);
    const second = generateFor(["42151-1", "10696-1"]);

    expect(first.designs.map((design) => design.id)).toEqual(
      second.designs.map((design) => design.id),
    );
    expect(first.designs.map((design) => design.pieceCount)).toEqual(
      second.designs.map((design) => design.pieceCount),
    );
  });

  test("sorts designs by piece count, descending", () => {
    const { designs } = generateFor(["10698-1"]);
    const counts = designs.map((design) => design.pieceCount);
    expect([...counts].sort((a, b) => b - a)).toEqual(counts);
  });

  test("never returns a design below the contract's four-part floor", () => {
    for (const { summary } of SETS) {
      for (const design of generateFor([summary.setNum]).designs) {
        expect(design.pieceCount, `${summary.setNum} ${design.id}`).toBeGreaterThanOrEqual(4);
        expect(design.confidence).toBe(1);
        expect(design.steps.length).toBeGreaterThan(0);
      }
    }
  });

  test("a set of specialised shapes yields no designs, with a reason for each", () => {
    const { designs, withheld } = generateFor(["31088-1"]);

    expect(designs).toHaveLength(0);
    expect(withheld.length).toBe(9);
    for (const entry of withheld) {
      expect(entry.reason.length).toBeGreaterThan(0);
    }
  });

  test("withheld reasons are quantitative where material is the constraint", () => {
    const { withheld } = generateFor(["31088-1"]);
    const building = withheld.find((entry) => entry.archetype === "building");

    expect(building?.reason).toMatch(/wall material \d+ studs, below the \d+ needed/);
  });

  test("a Technic set builds a chassis but not a studded car", () => {
    const { designs, withheld } = generateFor(["42151-1"]);

    expect(designs.some((design) => design.archetype === "technic_chassis")).toBe(true);
    expect(
      withheld.find((entry) => entry.archetype === "studded_car")?.reason,
    ).toBe("plate with 2 or more wheel pins (need 2)");
  });

  test("a step never calls for more of a part than the inventory holds", () => {
    const setNums = ["10696-1", "10715-1"];
    const inventory = inventoryFor(setNums);
    const { designs } = generateFor(setNums);

    for (const design of designs) {
      const used = new Map<string, number>();
      for (const step of design.steps) {
        for (const part of step.parts) {
          const lot = `${part.partNum}:${part.colorId}`;
          used.set(lot, (used.get(lot) ?? 0) + part.quantity);
        }
      }
      const total = [...used.values()].reduce((sum, count) => sum + count, 0);
      expect(total, design.id).toBe(design.pieceCount);
      expect(total).toBeLessThanOrEqual(inventory.pieces);
    }
  });
});
