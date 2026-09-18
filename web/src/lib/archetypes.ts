import { FLAT_FAMILIES, WALL_FAMILIES } from "./families";

/**
 * Human copy for the archetypes the service offers, and the material each one
 * competes for.
 *
 * CONTRACT.md carries `archetype` and `variant` as opaque strings, so the
 * wording lives here. `archetypeCopy` falls back to a neutral description, so
 * an archetype added by a newer service renders sensibly instead of breaking.
 */
export type MaterialKind = "wall" | "flat" | "rolling";

export type ArchetypeCopy = {
  label: string;
  summary: string;
  material: MaterialKind;
};

const ROLLING_FAMILIES: ReadonlySet<string> = new Set([
  "wheel",
  "tyre",
  "technic_axle",
  "plate_special",
  "technic_beam",
]);

export const MATERIAL_COPY: Readonly<Record<MaterialKind, string>> = {
  wall: "brick-height material",
  flat: "plate-height material",
  rolling: "wheels, axles and something to mount them on",
};

export function materialFamilies(kind: MaterialKind): ReadonlySet<string> {
  if (kind === "wall") return WALL_FAMILIES;
  if (kind === "flat") return FLAT_FAMILIES;
  return ROLLING_FAMILIES;
}

const ARCHETYPE_COPY: Readonly<Record<string, ArchetypeCopy>> = {
  building: {
    label: "Brick building",
    summary:
      "Walls coursed from ordinary bricks around a rectangular footprint, with a plate floor and roof.",
    material: "wall",
  },
  sculpture: {
    label: "Stepped plate sculpture",
    summary:
      "Nested plate layers, each one inset inside the layer below, so every piece rests on solid material.",
    material: "flat",
  },
  studded_car: {
    label: "Four-wide car",
    summary: "A studded chassis with wheels on wheel-holder plates, and a brick body.",
    material: "rolling",
  },
  axle_car: {
    label: "Axle-mounted car",
    summary: "Wheels on Technic axles carried by bricks with axle holes, under a brick body.",
    material: "rolling",
  },
  technic_chassis: {
    label: "Technic chassis",
    summary: "A studless beam spine with axles pinned through it, carrying Technic wheels.",
    material: "rolling",
  },
};

export function archetypeCopy(archetype: string): ArchetypeCopy {
  return (
    ARCHETYPE_COPY[archetype] ?? {
      label: humanise(archetype),
      summary: "A design the generator fitted to this inventory.",
      material: "wall",
    }
  );
}

export function humanise(token: string): string {
  const spaced = token.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** "Brick building · cottage", the label shown on a design. */
export function designKind(archetype: string, variant: string | null): string {
  const { label } = archetypeCopy(archetype);
  return variant ? `${label} · ${variant}` : label;
}
