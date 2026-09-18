"use client";

import { VisuallyHidden } from "@/components/ui/VisuallyHidden";
import type { SetSummary } from "@/lib/api/contract";
import { formatCount, pluralise } from "@/lib/format";
import styles from "./InventoryChips.module.css";

type InventoryChipsProps = {
  sets: readonly SetSummary[];
  onRemove: (setNum: string) => void;
};

/** The declared sets, each removable. A list, because it is one. */
export function InventoryChips({ sets, onRemove }: InventoryChipsProps) {
  return (
    <ul className={styles.list}>
      {sets.map((set) => (
        <li key={set.setNum} className={styles.chip}>
          <span className={styles.text}>
            <span className={styles.name}>{set.name}</span>
            <span className={styles.meta}>
              <span className={styles.number}>{set.setNum}</span>
              {set.year !== null ? ` · ${set.year}` : ""}
              {set.numParts > 0
                ? ` · ${formatCount(set.numParts)} ${pluralise(set.numParts, "piece")}`
                : " · piece count unknown"}
            </span>
          </span>
          <button
            type="button"
            className={styles.remove}
            onClick={() => onRemove(set.setNum)}
          >
            <RemoveGlyph />
            <VisuallyHidden>
              Remove {set.name}, set {set.setNum}
            </VisuallyHidden>
          </button>
        </li>
      ))}
    </ul>
  );
}

function RemoveGlyph() {
  return (
    <svg
      className={styles.glyph}
      viewBox="0 0 14 14"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M1.5 1.5 12.5 12.5M12.5 1.5 1.5 12.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
    </svg>
  );
}
