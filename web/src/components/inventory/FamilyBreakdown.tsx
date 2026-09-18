import type { InventoryFamily } from "@/lib/api/contract";
import { STRUCTURAL_FAMILIES, familyLabel } from "@/lib/families";
import { formatCount } from "@/lib/format";
import { cx } from "@/lib/cx";
import styles from "./FamilyBreakdown.module.css";

const VISIBLE_ROWS = 8;

type Row = {
  key: string;
  label: string;
  pieces: number;
  studs: number;
  structural: boolean;
  aggregate: boolean;
};

function toRows(families: readonly InventoryFamily[]): Row[] {
  const ranked = [...families].sort((a, b) => b.pieces - a.pieces);
  const shown = ranked.slice(0, VISIBLE_ROWS).map((entry) => ({
    key: entry.family,
    label: familyLabel(entry.family),
    pieces: entry.pieces,
    studs: entry.studs,
    structural: STRUCTURAL_FAMILIES.has(entry.family),
    aggregate: false,
  }));

  const rest = ranked.slice(VISIBLE_ROWS);
  if (rest.length > 0) {
    shown.push({
      key: "__rest",
      label: `${rest.length} smaller ${rest.length === 1 ? "family" : "families"}`,
      pieces: rest.reduce((total, entry) => total + entry.pieces, 0),
      studs: rest.reduce((total, entry) => total + entry.studs, 0),
      structural: false,
      aggregate: true,
    });
  }
  return shown;
}

/**
 * `Inventory.families` as a table with a proportional bar per row. The bar is
 * decoration over a number that is always present, so nothing depends on
 * reading a graphic.
 */
export function FamilyBreakdown({ families }: { families: readonly InventoryFamily[] }) {
  const rows = toRows(families);
  const largest = rows.reduce((max, row) => Math.max(max, row.pieces), 0);

  return (
    <>
      <table className={styles.table}>
        <caption>What kind of part your sets are made of, largest group first.</caption>
        <thead>
          <tr>
            <th scope="col">Part family</th>
            <th scope="col" className={styles.numeric}>
              Pieces
            </th>
            <th scope="col" className={styles.numeric}>
              Studs
            </th>
            <th scope="col" className={styles.share}>
              Share
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <th scope="row">{row.label}</th>
              <td className={styles.numeric}>{formatCount(row.pieces)}</td>
              <td className={styles.numeric}>
                {row.studs > 0 ? formatCount(row.studs) : "—"}
              </td>
              <td className={styles.share}>
                <div
                  className={cx(
                    styles.bar,
                    row.aggregate ? styles.other : row.structural && styles.structural,
                  )}
                  aria-hidden="true"
                >
                  <div
                    className={styles.fill}
                    style={{
                      width: largest > 0 ? `${(row.pieces / largest) * 100}%` : "0%",
                    }}
                  />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className={styles.footnote}>
        Stud area is only counted for parts with a whole-stud footprint, because
        only those can be tiled into a wall or a layer. A wedge or a minifigure
        part therefore shows no studs.
      </p>
    </>
  );
}
