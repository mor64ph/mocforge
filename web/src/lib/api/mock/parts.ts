/**
 * Fixture part catalogue: real Rebrickable part numbers and names, with the
 * measured properties the generator reasons about (footprint in studs, height
 * in plates, connection counts).
 *
 * `geometry: false` marks a part with no LDraw mesh. The real catalogue has
 * such gaps, which is why `Inventory.geometryPieces` sits below `pieces`.
 */
export type MockPart = {
  name: string;
  /** Footprint in studs; non-rectangular parts carry their bounding box. */
  width: number;
  depth: number;
  /** Height in plates: a brick is 3, a plate or tile is 1. */
  height: number;
  /** Integral stud footprint, so the tiler may use it as bulk material. */
  rectangular: boolean;
  geometry: boolean;
  wheelPins?: number;
  axleHoles?: number;
  pinHoles?: number;
};

const brick = (name: string, w: number, d: number): MockPart => ({
  name,
  width: w,
  depth: d,
  height: 3,
  rectangular: true,
  geometry: true,
});

const plate = (name: string, w: number, d: number): MockPart => ({
  name,
  width: w,
  depth: d,
  height: 1,
  rectangular: true,
  geometry: true,
});

const shaped = (
  name: string,
  w: number,
  d: number,
  h: number,
  geometry = true,
): MockPart => ({
  name,
  width: w,
  depth: d,
  height: h,
  rectangular: false,
  geometry,
});

export const PARTS: Readonly<Record<string, MockPart>> = {
  // ------------------------------------------------------------------ bricks
  "2456": brick("Brick 2 x 6", 2, 6),
  "3001": brick("Brick 2 x 4", 2, 4),
  "3002": brick("Brick 2 x 3", 2, 3),
  "3003": brick("Brick 2 x 2", 2, 2),
  "3004": brick("Brick 1 x 2", 1, 2),
  "3005": brick("Brick 1 x 1", 1, 1),
  "3006": brick("Brick 2 x 10", 2, 10),
  "3008": brick("Brick 1 x 8", 1, 8),
  "3009": brick("Brick 1 x 6", 1, 6),
  "3010": brick("Brick 1 x 4", 1, 4),
  "3622": brick("Brick 1 x 3", 1, 3),
  "6111": brick("Brick 1 x 10", 1, 10),

  // ------------------------------------------------------------------ plates
  "3020": plate("Plate 2 x 4", 2, 4),
  "3021": plate("Plate 2 x 3", 2, 3),
  "3022": plate("Plate 2 x 2", 2, 2),
  "3023": plate("Plate 1 x 2", 1, 2),
  "3024": plate("Plate 1 x 1", 1, 1),
  "3031": plate("Plate 4 x 4", 4, 4),
  "3032": plate("Plate 4 x 6", 4, 6),
  "3034": plate("Plate 2 x 8", 2, 8),
  "3035": plate("Plate 4 x 8", 4, 8),
  "3036": plate("Plate 6 x 8", 6, 8),
  "3623": plate("Plate 1 x 3", 1, 3),
  "3666": plate("Plate 1 x 6", 1, 6),
  "3710": plate("Plate 1 x 4", 1, 4),
  "3795": plate("Plate 2 x 6", 2, 6),
  "3958": plate("Plate 6 x 6", 6, 6),
  "91405": plate("Plate 6 x 14", 6, 14),

  // ------------------------------------------------------------------- tiles
  "3068b": plate("Tile 2 x 2 with Groove", 2, 2),
  "3069b": plate("Tile 1 x 2 with Groove", 1, 2),
  "6636": plate("Tile 1 x 6", 1, 6),
  "63864": plate("Tile 1 x 3", 1, 3),
  "87079": plate("Tile 2 x 4 with Groove", 2, 4),
  "98138": plate("Tile Round 1 x 1", 1, 1),

  // --------------------------------------------------------- slopes / arches
  "3037": brick("Brick Sloped 45 2 x 4", 2, 4),
  "3039": brick("Brick Sloped 45 2 x 2", 2, 2),
  "3040b": brick("Brick Sloped 45 2 x 1", 1, 2),
  "4286": brick("Brick Sloped 33 3 x 1", 1, 3),
  "3659": brick("Brick Arch 1 x 4", 1, 4),
  "11477": shaped("Brick Curved 1 x 2 x 2/3 Double Curved Top", 1, 2, 2),
  "15068": shaped("Brick Curved 2 x 2 x 2/3 Double Curved Top", 2, 2, 2),
  "54200": shaped("Brick Curved 1 x 1 x 2/3 Double Curved Top", 1, 1, 2),

  // ------------------------------------------------------------------- round
  "3062b": brick("Brick Round 1 x 1 with Open Stud", 1, 1),
  "4073": plate("Plate Round 1 x 1", 1, 1),
  "3960": shaped("Dish 4 x 4 Inverted", 4, 4, 2),
  "3941": { ...brick("Brick Round 2 x 2 with Axle Hole", 2, 2), axleHoles: 1 },

  // ------------------------------------------------------- modified, special
  "4600": { ...plate("Plate Special 2 x 2 with Wheel Holders", 2, 2), wheelPins: 2 },
  "99207": shaped("Bracket 1 x 2 - 2 x 2 Inverted", 2, 2, 2),
  "2429": shaped("Hinge Plate 1 x 4 Swivel Base", 1, 4, 1),
  "43722": shaped("Wedge Plate 2 x 2 Right", 2, 2, 1),
  "43723": shaped("Wedge Plate 2 x 2 Left", 2, 2, 1),
  "3049": shaped("Wedge 1 x 2 Sloped", 1, 2, 3),

  // --------------------------------------------------------- wheels and tyres
  "4624": shaped("Wheel Rim 6.4mm D. x 8mm", 1, 1, 2),
  "3641": shaped("Tyre 8/ 18 x 8 Offset Tread", 1, 1, 2),
  "56904": shaped("Wheel 56mm D. x 34mm Technic Racing Small", 3, 3, 7),
  "44309": shaped("Tyre 68.8 x 36 ZR", 4, 4, 9, false),

  // ----------------------------------------------------------------- technic
  "3700": { ...brick("Technic Brick 1 x 2 with Hole", 1, 2), axleHoles: 1 },
  "3701": { ...brick("Technic Brick 1 x 4 with Holes", 1, 4), pinHoles: 3 },
  "3703": { ...brick("Technic Brick 1 x 16 with Holes", 1, 16), pinHoles: 15 },
  "32523": { ...brick("Technic Beam 1 x 3 Thick", 1, 3), pinHoles: 3 },
  "32316": { ...brick("Technic Beam 1 x 5 Thick", 1, 5), pinHoles: 5 },
  "32525": { ...brick("Technic Beam 1 x 11 Thick", 1, 11), pinHoles: 11 },
  "41239": { ...brick("Technic Beam 1 x 13 Thick", 1, 13), pinHoles: 13 },
  "3705": shaped("Technic Axle 4", 1, 4, 1),
  "3706": shaped("Technic Axle 6", 1, 6, 1),
  "44294": shaped("Technic Axle 7", 1, 7, 1),
  "3713": shaped("Technic Bush", 1, 1, 1),
  "2780": shaped("Technic Pin with Friction Ridges Lengthwise", 1, 1, 1),
  "6558": shaped("Technic Pin Long with Friction Ridges", 1, 2, 1),
  "24316": shaped("Technic Panel Curved 11 x 3", 3, 11, 3, false),

  // ------------------------------------------------------- decorative, other
  "60592": shaped("Window 1 x 2 x 2 Frame", 1, 2, 6),
  "60601": shaped("Glass for Window 1 x 2 x 2", 1, 2, 6),
  "3957": shaped("Bar 4L Antenna", 1, 1, 4),
  "30374": shaped("Bar 4L Light Sabre Blade", 1, 1, 4),
  "33291": shaped("Plant Flower 1 x 1 with 5 Petals", 1, 1, 1),
  "3626c": shaped("Minifig Head", 1, 1, 3),
  "973": shaped("Minifig Torso", 1, 2, 4, false),
  "970c00": shaped("Minifig Legs", 1, 2, 5, false),
};

export function partOrThrow(partNum: string): MockPart {
  const part = PARTS[partNum];
  if (!part) throw new Error(`Fixture part ${partNum} is not in the mock catalogue.`);
  return part;
}

export const CATALOGUE_PART_COUNT = Object.keys(PARTS).length;
