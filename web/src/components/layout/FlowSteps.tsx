import Link from "next/link";
import { cx } from "@/lib/cx";
import styles from "./FlowSteps.module.css";

export type Stage = "inventory" | "designs" | "instructions";

const STEPS: ReadonlyArray<{ id: Stage; label: string; href: "/" | "/designs" | null }> = [
  { id: "inventory", label: "Inventory", href: "/" },
  { id: "designs", label: "Designs", href: "/designs" },
  { id: "instructions", label: "Instructions", href: null },
];

/**
 * Where the user is in the three-stage flow. Steps already completed are links
 * back; the current step is marked with `aria-current="step"`.
 */
export function FlowSteps({ current }: { current: Stage }) {
  const currentIndex = STEPS.findIndex((step) => step.id === current);

  return (
    <nav className={styles.nav} aria-label="Progress">
      <ol className={styles.list}>
        {STEPS.map((step, index) => {
          const isCurrent = index === currentIndex;
          const isPast = index < currentIndex;
          return (
            <li key={step.id} className={styles.item}>
              <span className={styles.index} aria-hidden="true">
                {index + 1}
              </span>
              {isPast && step.href ? (
                <Link className={styles.link} href={step.href}>
                  {step.label}
                </Link>
              ) : (
                <span
                  className={cx(isCurrent ? styles.current : styles.pending)}
                  {...(isCurrent ? { "aria-current": "step" } : {})}
                >
                  {step.label}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
