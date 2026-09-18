import type {
  BuildStep,
  BuildStepPart,
  Design,
  Inventory,
  InventoryFamily,
  InventoryLot,
  WithheldDesign,
} from "../contract";
import { SET_BY_NUM } from "./catalogue";
import { colorOrThrow } from "./colors";
import { partOrThrow, type MockPart } from "./parts";
import {
  FLAT_FAMILIES,
  STRUCTURAL_FAMILIES,
  WALL_FAMILIES,
  familyOf,
} from "../../families";

/**
 * A deterministic stand-in for the generation engine.
 *
 * It is not the real tiler — it does no collision or support checking. What it
 * does reproduce is the engine's *decision structure*, because that is what the
 * client has to render: material is pooled by family and measured in studs, an
 * archetype either fits the footprint its variant asks for or is withheld with
 * a quantitative reason, and a design is described as ordered steps of part
 * callouts grouped by level.
 *
 * Same input, same output — `Design.id` must be stable for
 * (archetype, variant, setNums) per CONTRACT.md, so nothing here is random.
 */

type PoolLot = {
  partNum: string;
  colorId: number;
  part: MockPart;
  family: string;
  quantity: number;
  remaining: number;
};

const STUDS_PER_PLATE_HEIGHT = 3;

function studArea(part: MockPart): number {
  if (!part.rectangular) return 0;
  return Math.round(part.width) * Math.round(part.depth);
}

function buildPool(setNums: readonly string[]): PoolLot[] {
  const merged = new Map<string, PoolLot>();
  for (const setNum of setNums) {
    const set = SET_BY_NUM.get(setNum);
    if (!set) continue;
    for (const [partNum, colorId, quantity] of set.lots) {
      const key = `${partNum}:${colorId}`;
      const existing = merged.get(key);
      if (existing) {
        existing.quantity += quantity;
        existing.remaining += quantity;
        continue;
      }
      const part = partOrThrow(partNum);
      merged.set(key, {
        partNum,
        colorId,
        part,
        family: familyOf(part.name),
        quantity,
        remaining: quantity,
      });
    }
  }
  // Largest lots first, then by part number, so every derived list is stable.
  return [...merged.values()].sort(
    (a, b) => b.quantity - a.quantity || a.partNum.localeCompare(b.partNum),
  );
}

export function lotsFor(setNums: readonly string[]): InventoryLot[] {
  return buildPool(setNums).map((lot) => {
    const color = colorOrThrow(lot.colorId);
    return {
      partNum: lot.partNum,
      partName: lot.part.name,
      colorId: color.id,
      colorName: color.name,
      colorRgb: color.rgb,
      isTrans: color.isTrans,
      quantity: lot.quantity,
      imgUrl: null,
    };
  });
}

export function inventoryFor(setNums: readonly string[]): Inventory {
  const pool = buildPool(setNums);
  const byFamily = new Map<string, InventoryFamily>();
  let pieces = 0;
  let geometryPieces = 0;

  for (const lot of pool) {
    pieces += lot.quantity;
    if (lot.part.geometry) geometryPieces += lot.quantity;
    const entry = byFamily.get(lot.family) ?? {
      family: lot.family,
      pieces: 0,
      studs: 0,
    };
    entry.pieces += lot.quantity;
    entry.studs += studArea(lot.part) * lot.quantity;
    byFamily.set(lot.family, entry);
  }

  const families = [...byFamily.values()].sort(
    (a, b) => b.pieces - a.pieces || a.family.localeCompare(b.family),
  );

  return {
    setNums: [...setNums],
    pieces,
    lots: pool.length,
    geometryPieces,
    families,
    structuralStuds: families
      .filter((entry) => STRUCTURAL_FAMILIES.has(entry.family))
      .reduce((total, entry) => total + entry.studs, 0),
  };
}

// --------------------------------------------------------------- allocation

type Allocation = {
  parts: BuildStepPart[];
  studs: number;
};

const isWallMaterial = (lot: PoolLot): boolean =>
  WALL_FAMILIES.has(lot.family) &&
  lot.part.rectangular &&
  lot.part.height === STUDS_PER_PLATE_HEIGHT;

const isFlatMaterial = (lot: PoolLot): boolean =>
  FLAT_FAMILIES.has(lot.family) && lot.part.rectangular && lot.part.height === 1;

function availableStuds(pool: readonly PoolLot[], accept: (lot: PoolLot) => boolean): number {
  return pool.reduce(
    (total, lot) => (accept(lot) ? total + studArea(lot.part) * lot.remaining : total),
    0,
  );
}

/**
 * Fill a stud budget from the pool, biggest footprint first — the engine's
 * ordering, because large parts have to be committed while there is still room
 * for them. Consumes from `remaining`, so a later course sees what is left.
 */
function allocate(
  pool: PoolLot[],
  accept: (lot: PoolLot) => boolean,
  budget: number,
  note: string,
): Allocation {
  const candidates = pool
    .filter((lot) => accept(lot) && lot.remaining > 0 && studArea(lot.part) > 0)
    .sort(
      (a, b) =>
        studArea(b.part) - studArea(a.part) || a.partNum.localeCompare(b.partNum),
    );

  const parts: BuildStepPart[] = [];
  let left = budget;
  let studs = 0;

  for (const lot of candidates) {
    if (left <= 0) break;
    const area = studArea(lot.part);
    const take = Math.min(lot.remaining, Math.floor(left / area));
    if (take <= 0) continue;
    lot.remaining -= take;
    left -= take * area;
    studs += take * area;
    const color = colorOrThrow(lot.colorId);
    parts.push({
      partNum: lot.partNum,
      partName: lot.part.name,
      colorId: color.id,
      colorName: color.name,
      quantity: take,
      note,
    });
  }

  return { parts, studs };
}

function takeExact(
  pool: PoolLot[],
  accept: (lot: PoolLot) => boolean,
  count: number,
  note: string,
): BuildStepPart[] {
  const candidates = pool
    .filter((lot) => accept(lot) && lot.remaining > 0)
    .sort((a, b) => b.remaining - a.remaining || a.partNum.localeCompare(b.partNum));

  const parts: BuildStepPart[] = [];
  let left = count;
  for (const lot of candidates) {
    if (left <= 0) break;
    const take = Math.min(lot.remaining, left);
    lot.remaining -= take;
    left -= take;
    const color = colorOrThrow(lot.colorId);
    parts.push({
      partNum: lot.partNum,
      partName: lot.part.name,
      colorId: color.id,
      colorName: color.name,
      quantity: take,
      note,
    });
  }
  return parts;
}

function countOf(pool: readonly PoolLot[], accept: (lot: PoolLot) => boolean): number {
  return pool.reduce((total, lot) => (accept(lot) ? total + lot.remaining : total), 0);
}

// ----------------------------------------------------------------- geometry

/** Ring of cells a wall course occupies: a course is a perimeter, not a slab. */
const perimeterCells = (w: number, d: number): number => 2 * (w + d) - 4;

function insetArea(w: number, d: number, k: number, mode: "all" | "two"): number {
  if (mode === "two") return Math.max(0, w - k) * Math.max(0, d - k);
  return Math.max(0, w - 2 * k) * Math.max(0, d - 2 * k);
}

/** Largest footprint whose whole wall stack fits the wall material available. */
function fitWalls(
  wallStuds: number,
  courses: number,
  ratio: number,
  scale: number,
): { width: number; depth: number } {
  const usable = wallStuds * scale;
  let best = { width: 0, depth: 0 };
  for (let w = 4; w <= 48; w += 1) {
    const d = Math.max(4, Math.round(w * ratio));
    if (courses * perimeterCells(w, d) <= usable) best = { width: w, depth: d };
    else break;
  }
  return best;
}

/** Largest footprint whose whole nested stack fits the plate area available. */
function fitLayers(
  plateStuds: number,
  layers: number,
  ratio: number,
  scale: number,
  mode: "all" | "two",
): { width: number; depth: number } {
  const usable = plateStuds * scale;
  let best = { width: 0, depth: 0 };
  for (let w = 4; w <= 48; w += 1) {
    const d = Math.max(4, Math.round(w * ratio));
    let cost = 0;
    for (let k = 0; k < layers; k += 1) cost += insetArea(w, d, k, mode);
    if (cost <= usable) best = { width: w, depth: d };
    else break;
  }
  return best;
}

// ---------------------------------------------------------------- archetypes

type FitResult =
  | { ok: true; steps: BuildStep[]; warnings: string[]; notes: string[] }
  | { ok: false; reason: string };

type Variant = {
  name: string | null;
  fit: (pool: PoolLot[]) => FitResult;
};

type Archetype = {
  name: string;
  title: string;
  studded: boolean;
  variants: Variant[];
};

function step(index: number, level: number, parts: BuildStepPart[]): BuildStep {
  return { index, level, parts };
}

function buildingVariant(
  name: string,
  courses: number,
  ratio: number,
  scale: number,
): Variant {
  return {
    name,
    fit: (pool) => {
      const wallStuds = availableStuds(pool, isWallMaterial);
      const footprint = fitWalls(wallStuds, courses, ratio, scale);
      if (footprint.width < 4) {
        const minimum = Math.ceil((courses * perimeterCells(4, 4)) / scale);
        return {
          ok: false,
          reason:
            `wall material ${wallStuds} studs, below the ${minimum} needed for ` +
            `${courses} courses at the minimum 4 x 4 footprint`,
        };
      }

      const { width, depth } = footprint;
      const steps: BuildStep[] = [];
      const warnings: string[] = [];
      // Authored beside each diagnostic, not derived from it. The real service
      // translates in one place (`export.builder_notes`) and publishes the
      // result; a second table here would be the drift that arrangement
      // exists to prevent. These are fixture prose: what the engine's own
      // translation would say for this case.
      const notes: string[] = [];
      let level = 0;

      const floorBudget = width * depth;
      const floor = allocate(pool, isFlatMaterial, floorBudget, "floor");
      if (floor.studs >= floorBudget * 0.6) {
        steps.push(step(steps.length + 1, level, floor.parts));
        level += 1;
      } else {
        warnings.push(
          `floor omitted: ${floor.studs} studs of plate laid against the ` +
            `${floorBudget} the ${width} x ${depth} footprint needs`,
        );
        notes.push(
          "This building has no floor: there were not enough flat plates to " +
            "cover it. Build it straight onto the table.",
        );
        // Hand the part-studs back; an omitted floor is not a consumed floor.
        for (const part of floor.parts) {
          const lot = pool.find(
            (candidate) =>
              candidate.partNum === part.partNum && candidate.colorId === part.colorId,
          );
          if (lot) lot.remaining += part.quantity;
        }
      }

      const courseBudget = perimeterCells(width, depth);
      for (let course = 1; course <= courses; course += 1) {
        const laid = allocate(pool, isWallMaterial, courseBudget, `wall course ${course}`);
        if (laid.parts.length === 0) {
          warnings.push(
            `wall stops at course ${course - 1} of ${courses}: no brick-height ` +
              `material left`,
          );
          notes.push(
            `The walls are ${courses - course + 1} courses lower than planned ` +
              "because the bricks ran out. It is finished as it stands.",
          );
          break;
        }
        if (laid.studs < courseBudget * 0.75) {
          warnings.push(
            `course ${course} is ${courseBudget - laid.studs} studs short of a ` +
              `closed ring`,
          );
          notes.push(
            `Course ${course} leaves a gap in the wall. Treat it as a window, ` +
              "or close it with any brick of the same height.",
          );
        }
        steps.push(step(steps.length + 1, level, laid.parts));
        level += STUDS_PER_PLATE_HEIGHT;
      }

      const roof = allocate(pool, isFlatMaterial, width * depth, "roof");
      if (roof.parts.length > 0) steps.push(step(steps.length + 1, level, roof.parts));
      else {
        warnings.push("roof omitted: no plate material left after the walls");
        notes.push(
          "This design has no roof: the walls used the plates one would have " +
            "needed. It is complete and stands up without one.",
        );
      }

      const placed = steps.reduce(
        (total, entry) =>
          total + entry.parts.reduce((sum, part) => sum + part.quantity, 0),
        0,
      );
      if (placed < 4) return { ok: false, reason: "produced no placements" };

      return { ok: true, steps, warnings, notes };
    },
  };
}

function sculptureVariant(
  name: string,
  layers: number,
  ratio: number,
  scale: number,
  mode: "all" | "two",
): Variant {
  return {
    name,
    fit: (pool) => {
      const plateStuds = availableStuds(pool, isFlatMaterial);
      const footprint = fitLayers(plateStuds, layers, ratio, scale, mode);
      if (footprint.width < 4) {
        let minimumCost = 0;
        for (let k = 0; k < layers; k += 1) minimumCost += insetArea(4, 4, k, mode);
        const minimum = Math.ceil(minimumCost / scale);
        return {
          ok: false,
          reason:
            `plate area ${plateStuds} studs, below the ${minimum} needed for a ` +
            `${layers}-layer 4 x 4 ${name}`,
        };
      }

      const { width, depth } = footprint;
      const steps: BuildStep[] = [];
      const warnings: string[] = [];
      // Authored beside each diagnostic, not derived from it. The real service
      // translates in one place (`export.builder_notes`) and publishes the
      // result; a second table here would be the drift that arrangement
      // exists to prevent. These are fixture prose: what the engine's own
      // translation would say for this case.
      const notes: string[] = [];

      for (let k = 0; k < layers; k += 1) {
        const cells = insetArea(width, depth, k, mode);
        if (cells < 2) {
          warnings.push(
            `${layers} layers requested, ${k} laid: the stack ran out of footprint`,
          );
          notes.push(
            `The stack is ${k} layers rather than ${layers}: the steps get too ` +
              "small to sit on each other above that.",
          );
          break;
        }
        const laid = allocate(pool, isFlatMaterial, cells, `layer ${k + 1}`);
        if (laid.parts.length === 0) {
          warnings.push(`${layers} layers requested, ${k} laid: plate material ran out`);
          notes.push(
            `The stack is ${k} layers rather than ${layers} because the plates ` +
              "ran out. Every layer it does have is complete.",
          );
          break;
        }
        steps.push(step(steps.length + 1, k, laid.parts));
      }

      const placed = steps.reduce(
        (total, entry) =>
          total + entry.parts.reduce((sum, part) => sum + part.quantity, 0),
        0,
      );
      if (placed < 4) return { ok: false, reason: "produced no placements" };

      return { ok: true, steps, warnings, notes };
    },
  };
}

const hasWheelPins = (lot: PoolLot): boolean => (lot.part.wheelPins ?? 0) >= 2;
const isWheel = (lot: PoolLot): boolean => lot.family === "wheel";
const isTyre = (lot: PoolLot): boolean => lot.family === "tyre";
const isAxle = (lot: PoolLot): boolean => lot.family === "technic_axle";
const hasAxleHole = (lot: PoolLot): boolean => (lot.part.axleHoles ?? 0) >= 1;
const hasLongBeam = (lot: PoolLot): boolean => (lot.part.pinHoles ?? 0) >= 11;

type VehicleRole = {
  describe: string;
  accept: (lot: PoolLot) => boolean;
  need: number;
  note: string;
};

/** Vehicles share one shape: mount roles, then wheels, then a tiled body. */
function vehicleVariant(roles: readonly VehicleRole[]): Variant {
  return {
    name: null,
    fit: (pool) => {
      for (const role of roles) {
        if (countOf(pool, role.accept) < role.need) {
          return { ok: false, reason: `${role.describe} (need ${role.need})` };
        }
      }

      const steps: BuildStep[] = [];
      const warnings: string[] = [];
      // Authored beside each diagnostic, not derived from it. The real service
      // translates in one place (`export.builder_notes`) and publishes the
      // result; a second table here would be the drift that arrangement
      // exists to prevent. These are fixture prose: what the engine's own
      // translation would say for this case.
      const notes: string[] = [];

      const chassis = allocate(pool, isFlatMaterial, 32, "chassis");
      if (chassis.studs < 16) {
        return {
          ok: false,
          reason: "chassis: no plate large enough to span the axles",
        };
      }
      steps.push(step(steps.length + 1, 0, chassis.parts));

      for (const role of roles) {
        const parts = takeExact(pool, role.accept, role.need, role.note);
        steps.push(step(steps.length + 1, 1, parts));
      }

      const tyres = takeExact(pool, isTyre, 4, "tyres");
      if (tyres.length > 0) steps.push(step(steps.length + 1, 1, tyres));
      else {
        warnings.push("wheels are bare: the inventory has no tyres to fit them");
        notes.push(
          "The wheels go on bare: there are no tyres in your sets to fit over " +
            "them. It still rolls.",
        );
      }

      const body = allocate(pool, isWallMaterial, 48, "body");
      if (body.parts.length > 0) steps.push(step(steps.length + 1, 2, body.parts));
      else {
        warnings.push("body omitted: no brick-height material for a shell");
        notes.push(
          "This car is an open chassis: no bricks were left over for a body.",
        );
      }

      const placed = steps.reduce(
        (total, entry) =>
          total + entry.parts.reduce((sum, part) => sum + part.quantity, 0),
        0,
      );
      if (placed < 4) return { ok: false, reason: "produced no placements" };

      return { ok: true, steps, warnings, notes };
    },
  };
}

const ARCHETYPES: readonly Archetype[] = [
  {
    name: "building",
    title: "MOCForge brick building",
    studded: true,
    variants: [
      buildingVariant("cottage", 3, 0.75, 0.45),
      buildingVariant("tower", 7, 1.0, 0.4),
      buildingVariant("hall", 2, 0.45, 0.6),
    ],
  },
  {
    name: "sculpture",
    title: "MOCForge stepped plate sculpture",
    studded: true,
    variants: [
      sculptureVariant("ziggurat", 6, 1.0, 0.55, "all"),
      sculptureVariant("terrace", 5, 0.7, 0.55, "two"),
      sculptureVariant("mosaic", 1, 1.0, 0.7, "all"),
    ],
  },
  {
    name: "studded_car",
    title: "MOCForge 4-wide car",
    studded: true,
    variants: [
      vehicleVariant([
        {
          describe: "plate with 2 or more wheel pins",
          accept: hasWheelPins,
          need: 2,
          note: "wheel mounts",
        },
        { describe: "wheel", accept: isWheel, need: 4, note: "wheels" },
      ]),
    ],
  },
  {
    name: "axle_car",
    title: "MOCForge axle-mounted car",
    studded: true,
    variants: [
      vehicleVariant([
        {
          describe: "brick with an axle hole",
          accept: hasAxleHole,
          need: 2,
          note: "axle bricks",
        },
        { describe: "Technic axle", accept: isAxle, need: 2, note: "axles" },
        { describe: "wheel", accept: isWheel, need: 4, note: "wheels" },
      ]),
    ],
  },
  {
    name: "technic_chassis",
    title: "MOCForge Technic chassis",
    studded: false,
    variants: [
      vehicleVariant([
        {
          describe: "Technic beam with 11 or more pin holes",
          accept: hasLongBeam,
          need: 2,
          note: "chassis spine",
        },
        { describe: "Technic axle", accept: isAxle, need: 2, note: "axles" },
        { describe: "wheel", accept: isWheel, need: 4, note: "wheels" },
      ]),
    ],
  },
];

export const ARCHETYPE_SUMMARIES = ARCHETYPES.map((archetype) => ({
  name: archetype.name,
  title: archetype.title,
  variants: archetype.variants
    .map((variant) => variant.name)
    .filter((name): name is string => name !== null),
  studded: archetype.studded,
}));

function designId(archetype: string, variant: string | null, setNums: readonly string[]): string {
  return [archetype, variant ?? "default", [...setNums].sort().join("+")].join(".");
}

export type GenerationResult = {
  designs: Design[];
  withheld: WithheldDesign[];
};

export function generateFor(
  setNums: readonly string[],
  requested?: readonly string[],
): GenerationResult {
  const wanted =
    requested && requested.length > 0 ? new Set(requested) : null;

  const designs: Design[] = [];
  const withheld: WithheldDesign[] = [];

  for (const archetype of ARCHETYPES) {
    if (wanted && !wanted.has(archetype.name)) continue;
    for (const variant of archetype.variants) {
      // Each variant competes for the whole inventory, exactly as the engine
      // does: designs are alternatives to choose between, not a shared build.
      const pool = buildPool(setNums);
      const result = variant.fit(pool);
      if (!result.ok) {
        withheld.push({
          archetype: archetype.name,
          variant: variant.name,
          reason: result.reason,
        });
        continue;
      }
      const pieceCount = result.steps.reduce(
        (total, entry) =>
          total + entry.parts.reduce((sum, part) => sum + part.quantity, 0),
        0,
      );
      const id = designId(archetype.name, variant.name, setNums);
      designs.push({
        id,
        archetype: archetype.name,
        variant: variant.name,
        title: variant.name ? `${archetype.title} — ${variant.name}` : archetype.title,
        pieceCount,
        confidence: 1,
        warnings: result.warnings,
        // CONTRACT.md v1.2. The real service translates `warnings` into these
        // once, server-side; the mock authors them beside each diagnostic it
        // invents, so the UI has both fields with the distinct text it will
        // see in production.
        builderNotes: result.notes,
        steps: result.steps,
        ldrUrl: `/api/v1/designs/${encodeURIComponent(id)}/ldr`,
      });
    }
  }

  designs.sort((a, b) => b.pieceCount - a.pieceCount || a.id.localeCompare(b.id));
  return { designs, withheld };
}
