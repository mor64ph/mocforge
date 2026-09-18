import type { Design, Inventory } from "./api/contract";
import { MATERIAL_COPY, archetypeCopy, materialFamilies } from "./archetypes";
import { familyLabel } from "./families";
import { formatCount, formatPercent } from "./format";

/**
 * "Why this design suits this inventory."
 *
 * CONTRACT.md has no such field, so the client derives it — but only from data
 * the contract actually provides: the archetype's material class, the family
 * breakdown in `Inventory.families`, and the design's own steps. Nothing here
 * is speculative, and nothing claims a property the API did not report.
 */
export type Rationale = {
  /** The material argument: what in this inventory the design is built from. */
  headline: string;
  /** Supporting facts, each independently checkable against the response. */
  points: string[];
};

export function rationaleFor(design: Design, inventory: Inventory): Rationale {
  const { material } = archetypeCopy(design.archetype);
  const families = materialFamilies(material);

  const contributing = inventory.families
    .filter((entry) => families.has(entry.family) && entry.pieces > 0)
    .sort((a, b) => b.pieces - a.pieces);

  const headline = materialSentence(material, contributing);

  const points: string[] = [];

  if (inventory.pieces > 0) {
    points.push(
      `Uses ${formatCount(design.pieceCount)} of your ${formatCount(inventory.pieces)} pieces (${formatPercent(
        design.pieceCount / inventory.pieces,
      )}).`,
    );
  }

  const levels = new Set(design.steps.map((step) => step.level)).size;
  if (design.steps.length > 0) {
    points.push(
      `${formatCount(design.steps.length)} ${design.steps.length === 1 ? "step" : "steps"} across ${formatCount(levels)} ${levels === 1 ? "level" : "levels"}, using ${formatCount(distinctLots(design))} of your ${formatCount(inventory.lots)} part lots.`,
    );
  }

  if (material !== "rolling" && inventory.structuralStuds > 0) {
    points.push(
      `${formatCount(inventory.structuralStuds)} studs of structural material available in total.`,
    );
  }

  return { headline, points };
}

function materialSentence(
  material: ReturnType<typeof archetypeCopy>["material"],
  contributing: readonly { family: string; pieces: number; studs: number }[],
): string {
  if (contributing.length === 0) {
    return `Built from the ${MATERIAL_COPY[material]} in this inventory.`;
  }

  const named = contributing
    .slice(0, 2)
    .map((entry) => `${formatCount(entry.pieces)} ${familyLabel(entry.family).toLowerCase()}`);
  const list = named.length === 2 ? `${named[0]} and ${named[1]}` : named[0];

  if (material === "rolling") {
    return `Your inventory carries the ${MATERIAL_COPY[material]} this needs — ${list}.`;
  }

  const studs = contributing.reduce((total, entry) => total + entry.studs, 0);
  return `${formatCount(studs)} studs of ${MATERIAL_COPY[material]} to draw on: ${list}.`;
}

export function distinctLots(design: Design): number {
  const seen = new Set<string>();
  for (const step of design.steps) {
    for (const part of step.parts) seen.add(`${part.partNum}:${part.colorId}`);
  }
  return seen.size;
}

/** Total pieces a step calls for, used in the instructions header. */
export function stepPieceCount(step: { parts: readonly { quantity: number }[] }): number {
  return step.parts.reduce((total, part) => total + part.quantity, 0);
}
