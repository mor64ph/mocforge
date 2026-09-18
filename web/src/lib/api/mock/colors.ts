/** Rebrickable colour ids, names and RGB (6 hex digits, no leading `#`). */
export type MockColor = {
  id: number;
  name: string;
  rgb: string;
  isTrans: boolean;
};

const COLOR_LIST: readonly MockColor[] = [
  { id: 0, name: "Black", rgb: "05131D", isTrans: false },
  { id: 1, name: "Blue", rgb: "0055BF", isTrans: false },
  { id: 2, name: "Green", rgb: "237841", isTrans: false },
  { id: 4, name: "Red", rgb: "C91A09", isTrans: false },
  { id: 10, name: "Bright Green", rgb: "4B9F4A", isTrans: false },
  { id: 14, name: "Yellow", rgb: "F2CD37", isTrans: false },
  { id: 15, name: "White", rgb: "FFFFFF", isTrans: false },
  { id: 19, name: "Tan", rgb: "E4CD9E", isTrans: false },
  { id: 25, name: "Orange", rgb: "FE8A18", isTrans: false },
  { id: 28, name: "Dark Tan", rgb: "958A73", isTrans: false },
  { id: 47, name: "Trans-Clear", rgb: "FCFCFC", isTrans: true },
  { id: 70, name: "Reddish Brown", rgb: "582A12", isTrans: false },
  { id: 71, name: "Light Bluish Gray", rgb: "A0A5A9", isTrans: false },
  { id: 72, name: "Dark Bluish Gray", rgb: "6C6E68", isTrans: false },
  { id: 191, name: "Bright Light Orange", rgb: "F8BB3D", isTrans: false },
  { id: 212, name: "Bright Light Blue", rgb: "9FC3E9", isTrans: false },
  { id: 226, name: "Bright Light Yellow", rgb: "FFF03A", isTrans: false },
  { id: 308, name: "Dark Brown", rgb: "352100", isTrans: false },
  { id: 320, name: "Dark Red", rgb: "720E0F", isTrans: false },
  { id: 321, name: "Dark Azure", rgb: "078BC9", isTrans: false },
  { id: 322, name: "Medium Azure", rgb: "36AEBF", isTrans: false },
];

export const COLORS: ReadonlyMap<number, MockColor> = new Map(
  COLOR_LIST.map((color) => [color.id, color]),
);

export function colorOrThrow(colorId: number): MockColor {
  const color = COLORS.get(colorId);
  if (!color) throw new Error(`Fixture colour ${colorId} is not in the mock catalogue.`);
  return color;
}
