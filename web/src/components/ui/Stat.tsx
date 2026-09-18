import type { ReactNode } from "react";
import styles from "./Stat.module.css";

/** Figures are a description list: each label genuinely describes its value. */
export function StatGrid({ children }: { children: ReactNode }) {
  return <dl className={styles.grid}>{children}</dl>;
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className={styles.stat}>
      <dt className={styles.label}>{label}</dt>
      <dd className={styles.value}>{value}</dd>
      {hint ? <dd className={styles.hint}>{hint}</dd> : null}
    </div>
  );
}
