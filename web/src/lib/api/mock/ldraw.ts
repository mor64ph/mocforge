import type { Design } from "../contract";
import { PARTS } from "./parts";

const LDU_PER_STUD = 20;
const LDU_PER_PLATE = 8;
/** LDraw identity rotation matrix, written on every placement line. */
const IDENTITY = "1 0 0 0 1 0 0 0 1";

/**
 * Render a design as LDraw text, in the same header-plus-type-1-lines shape the
 * engine emits.
 *
 * The placements are laid out on a plain grid rather than being the tiler's
 * real coordinates — the mock does not carry geometry — so the header says so.
 * The point of this file is that the download path, filename and MIME type are
 * exercised for real before the service lands.
 */
export function renderLdr(design: Design): string {
  const lines: string[] = [
    `0 ${design.title}`,
    `0 Name: ${ldrFilename(design)}`,
    "0 Author: MOCForge (generated)",
    "0 !LDRAW_ORG Unofficial_Model",
    "0 // Catalogue data from Rebrickable. Geometry from the LDraw Parts",
    "0 // Library, CC BY 2.0. LEGO is a trademark of the LEGO Group,",
    "0 // which does not sponsor or endorse this model.",
    `0 // confidence ${design.confidence.toFixed(3)}  pieces ${design.pieceCount}`,
    "0 // mock transport: placements are grid-laid, not tiler output",
  ];

  for (const step of design.steps) {
    lines.push(`0 // step ${step.index} — level ${step.level}`);
    let cursor = 0;
    for (const part of step.parts) {
      const geometry = PARTS[part.partNum];
      const depth = geometry ? Math.max(1, Math.round(geometry.depth)) : 1;
      for (let n = 0; n < part.quantity; n += 1) {
        const x = (cursor % 24) * LDU_PER_STUD;
        const z = Math.floor(cursor / 24) * LDU_PER_STUD;
        const y = -step.level * LDU_PER_PLATE;
        lines.push(
          `1 ${part.colorId} ${x} ${y} ${z} ${IDENTITY} ${part.partNum}.dat`,
        );
        cursor += depth;
      }
    }
  }

  lines.push("0");
  return `${lines.join("\n")}\n`;
}

/** A filename in the engine's convention: `<archetype>-<variant>_<sets>.ldr`. */
export function ldrFilename(design: Design): string {
  const stem = design.variant
    ? `${design.archetype}-${design.variant}`
    : design.archetype;
  return `${stem}.ldr`;
}
