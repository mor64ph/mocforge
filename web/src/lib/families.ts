/**
 * Part-family taxonomy for the mock service.
 *
 * Ported from the engine's rule table (roles.py FAMILY_RULES): ordered, most
 * specific first, first match wins. Kept in rule form rather than hard-coding
 * a family per fixture part so the mock classifies the way the real service
 * does — a fixture part named "Brick Special ..." lands in `brick_special`
 * here for the same reason it will there.
 */
const FAMILY_RULES: ReadonlyArray<readonly [family: string, pattern: RegExp]> = [
  ["duplo", /^Duplo\b|^Quatro\b|^Primo\b/i],
  ["sticker", /^Sticker|^Tape\b/i],
  ["minifig", /^Minifig|^Torso\b|^Legs\b|^Hair\b|^Headgear/i],
  ["tyre", /^Tyre\b/i],
  ["wheel", /^Wheel\b/i],
  ["technic_axle", /^Technic Axle\b/i],
  ["technic_pin", /^Technic Pin\b/i],
  ["technic_beam", /^Technic Beam\b/i],
  ["technic_gear", /^Technic Gear\b|^Technic Turntable|^Technic Worm/i],
  ["technic_panel", /^Technic Panel\b/i],
  ["technic_other", /^Technic\b/i],
  ["bracket", /^Bracket\b/i],
  ["hinge", /^Hinge\b/i],
  ["clip", /\bwith Clip/i],
  ["slope", /^Brick Sloped\b|^Slope\b|^Brick Curved\b/i],
  ["wedge", /^Wedge\b/i],
  ["panel", /^Panel\b/i],
  ["arch", /^Brick Arch\b/i],
  ["round_brick", /^Brick Round\b|^Cylinder\b|^Cone\b|^Dome\b/i],
  ["round_plate", /^Plate Round\b|^Dish\b/i],
  ["brick_special", /^Brick Special\b/i],
  ["plate_special", /^Plate Special\b/i],
  ["brick", /^Brick \d+ x \d+$/i],
  ["plate", /^Plate \d+ x \d+$/i],
  ["tile", /^Tile \d+ x \d+$|^Tile\b/i],
  ["brick_modified", /^Brick \d+ x \d+/i],
  ["plate_modified", /^Plate \d+ x \d+/i],
  ["window", /^Window\b|^Door\b|^Windscreen\b|^Glass\b/i],
  ["plant", /^Plant\b|^Animal\b|^Food\b/i],
  ["bar", /^Bar \d|^Bar\b/i],
  ["baseplate", /^Baseplate\b/i],
  ["figure_acc", /^Weapon\b|^Tool\b|^Utensil\b|^Container\b|^Flag\b/i],
  ["electric", /^Electric\b|^Power Functions|^Light\b|^Motor\b/i],
];

export function familyOf(partName: string): string {
  for (const [family, pattern] of FAMILY_RULES) {
    if (pattern.test(partName)) return family;
  }
  return "other";
}

/** roles.py STRUCTURAL — families usable as bulk material for walls and decks. */
export const STRUCTURAL_FAMILIES: ReadonlySet<string> = new Set([
  "brick",
  "plate",
  "tile",
  "slope",
  "wedge",
  "arch",
  "round_brick",
  "round_plate",
]);

/** Families the building archetype can course a wall from, given brick height. */
export const WALL_FAMILIES: ReadonlySet<string> = new Set([
  "brick",
  "brick_modified",
  "slope",
  "arch",
  "round_brick",
]);

/** Families the sculpture archetype can lay a layer from, given plate height. */
export const FLAT_FAMILIES: ReadonlySet<string> = new Set([
  "plate",
  "plate_modified",
  "tile",
  "round_plate",
]);

/** Human labels for the families the UI surfaces. */
export const FAMILY_LABELS: Readonly<Record<string, string>> = {
  arch: "Arches",
  bar: "Bars",
  baseplate: "Baseplates",
  bracket: "Brackets",
  brick: "Bricks",
  brick_modified: "Modified bricks",
  brick_special: "Special bricks",
  clip: "Clip parts",
  duplo: "Duplo",
  electric: "Electrics",
  figure_acc: "Accessories",
  hinge: "Hinges",
  minifig: "Minifigure parts",
  other: "Other",
  panel: "Panels",
  plant: "Plants and animals",
  plate: "Plates",
  plate_modified: "Modified plates",
  plate_special: "Special plates",
  round_brick: "Round bricks",
  round_plate: "Round plates",
  slope: "Slopes",
  sticker: "Stickers",
  technic_axle: "Technic axles",
  technic_beam: "Technic beams",
  technic_gear: "Technic gears",
  technic_other: "Technic, other",
  technic_panel: "Technic panels",
  technic_pin: "Technic pins",
  tile: "Tiles",
  tyre: "Tyres",
  wedge: "Wedges",
  wheel: "Wheels",
  window: "Windows and doors",
};

export function familyLabel(family: string): string {
  return FAMILY_LABELS[family] ?? family.replace(/_/g, " ");
}
