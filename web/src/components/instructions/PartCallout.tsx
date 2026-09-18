import { VisuallyHidden } from "@/components/ui/VisuallyHidden";
import type { BuildStepPart } from "@/lib/api/contract";
import { formatCount } from "@/lib/format";
import styles from "./PartCallout.module.css";

/**
 * The parts one step calls for.
 *
 * Colour is written out as a name rather than shown as a swatch: `BuildStepPart`
 * carries `colorId` and `colorName`, and no v1 endpoint exposes the RGB for a
 * colour (`InventoryLot` is declared in CONTRACT.md but nothing returns it), so
 * a swatch here could only be guessed. A named colour is also the more reliable
 * instruction — "Reddish Brown" is unambiguous where two browns side by side
 * are not.
 */
export function PartCalloutList({ parts }: { parts: readonly BuildStepPart[] }) {
  return (
    <ul className={styles.list}>
      {parts.map((part) => (
        <li key={`${part.partNum}:${part.colorId}:${part.note}`} className={styles.item}>
          <span className={styles.quantity}>
            {formatCount(part.quantity)}
            <VisuallyHidden> of</VisuallyHidden>
            <span aria-hidden="true">&times;</span>
          </span>
          <span className={styles.text}>
            <span className={styles.name}>{part.partName}</span>
            <span className={styles.meta}>
              {part.colorName} · <span className={styles.partNum}>{part.partNum}</span>
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}
