"use client";

import { useEffect, useState } from "react";
import type { Inventory } from "@/lib/api/contract";
import { cx } from "@/lib/cx";
import { formatCount, formatSeconds } from "@/lib/format";
import styles from "./GenerationProgress.module.css";

const TICK_MS = 200;

/**
 * The engine's own stages, in order. `after` is the elapsed time by which the
 * service has certainly moved past the previous stage; the last stage holds
 * until the response lands.
 */
export const PHASES: ReadonlyArray<{ label: string; after: number }> = [
  { label: "Resolving your sets against the catalogue", after: 0 },
  { label: "Pooling parts by family and measuring stud area", after: 400 },
  { label: "Fitting and validating every archetype variant", after: 1100 },
];

export function phaseIndexFor(elapsedMs: number): number {
  let index = 0;
  for (let i = 0; i < PHASES.length; i += 1) {
    const phase = PHASES[i];
    if (phase && elapsedMs >= phase.after) index = i;
  }
  return index;
}

/**
 * Elapsed time since this component mounted.
 *
 * The clock lives here rather than in the caller because mounting *is* the
 * start of generation: the component appears when the request goes out and
 * unmounts when it returns, so there is no separate start time to reset.
 */
function useElapsedSinceMount(): number {
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    const startedAt = Date.now();
    const timer = setInterval(() => setElapsedMs(Date.now() - startedAt), TICK_MS);
    return () => clearInterval(timer);
  }, []);

  return elapsedMs;
}

type GenerationProgressProps = {
  /** Known before generation runs, from `POST /inventory`. */
  inventory: Inventory | null;
  variantCount: number | null;
};

export function GenerationProgress({
  inventory,
  variantCount,
}: GenerationProgressProps) {
  const elapsedMs = useElapsedSinceMount();
  const active = phaseIndexFor(elapsedMs);
  const currentLabel = PHASES[active]?.label ?? "Working";

  return (
    <div className={styles.wrapper}>
      <div
        className={styles.track}
        role="progressbar"
        aria-label="Generation progress"
        aria-valuetext={`${currentLabel}. ${formatSeconds(elapsedMs)} elapsed.`}
      >
        <div className={styles.indeterminate} />
      </div>

      <ol className={styles.phases}>
        {PHASES.map((phase, index) => (
          <li
            key={phase.label}
            className={cx(
              styles.phase,
              index < active && styles.done,
              index === active && styles.active,
            )}
          >
            <span className={styles.marker} aria-hidden="true">
              {index < active ? "done" : index === active ? "→" : ""}
            </span>
            <span>{phase.label}</span>
          </li>
        ))}
      </ol>

      <div className={styles.scope}>
        <span>
          Elapsed <span className={styles.scopeValue}>{formatSeconds(elapsedMs)}</span>
        </span>
        {inventory ? (
          <>
            <span>
              Pieces{" "}
              <span className={styles.scopeValue}>{formatCount(inventory.pieces)}</span>
            </span>
            <span>
              Lots <span className={styles.scopeValue}>{formatCount(inventory.lots)}</span>
            </span>
          </>
        ) : null}
        {variantCount !== null ? (
          <span>
            Variants tried{" "}
            <span className={styles.scopeValue}>{formatCount(variantCount)}</span>
          </span>
        ) : null}
      </div>

      <p className={styles.note}>
        Generation is a single synchronous request in v1, so these stages are the
        engine&rsquo;s own sequence timed against it rather than a live feed from
        the server.
      </p>
    </div>
  );
}
