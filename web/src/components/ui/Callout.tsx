import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import styles from "./Callout.module.css";

type Tone = "info" | "success" | "caution" | "danger";

type CalloutProps = {
  tone?: Tone;
  title?: string;
  /** `assertive` only for errors the user must notice immediately. */
  live?: "polite" | "assertive";
  children: ReactNode;
};

/**
 * A bordered notice. Tone is carried by a left border and a tinted ground, not
 * by colour alone: `danger` and `caution` always also carry a title that says
 * what happened.
 */
export function Callout({ tone = "info", title, live, children }: CalloutProps) {
  return (
    <div
      className={cx(styles.callout, styles[tone])}
      {...(tone === "danger" ? { role: "alert" } : {})}
      {...(live ? { "aria-live": live } : {})}
    >
      <div className={styles.body}>
        {title ? <p className={styles.title}>{title}</p> : null}
        <div className={styles.text}>{children}</div>
      </div>
    </div>
  );
}
