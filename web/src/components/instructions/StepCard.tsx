import type { BuildStep } from "@/lib/api/contract";
import { formatCount, pluralise } from "@/lib/format";
import { stepPieceCount } from "@/lib/rationale";
import { PartCalloutList } from "./PartCallout";
import styles from "./StepCard.module.css";

/** The distinct `note` values in a step, which is what the step is *for*. */
function stepLabel(step: BuildStep): string {
  const notes = [...new Set(step.parts.map((part) => part.note).filter(Boolean))];
  if (notes.length === 0) return "Place these parts";
  return notes.join(", ");
}

export function StepCard({ step }: { step: BuildStep }) {
  const pieces = stepPieceCount(step);

  return (
    <li className={styles.step}>
      <div className={styles.head}>
        <h4 className={styles.title}>
          <span className={styles.index}>Step {formatCount(step.index)}</span>
          <span className={styles.note}>{stepLabel(step)}</span>
        </h4>
        <p className={styles.count}>
          {formatCount(pieces)} {pluralise(pieces, "piece")} ·{" "}
          {formatCount(step.parts.length)} {pluralise(step.parts.length, "lot")}
        </p>
      </div>
      <PartCalloutList parts={step.parts} />
    </li>
  );
}
