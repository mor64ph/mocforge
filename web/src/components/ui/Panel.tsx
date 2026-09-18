import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import styles from "./Panel.module.css";

type PanelProps = {
  /** Small uppercase label above the title, e.g. the flow step. */
  eyebrow?: string;
  title: string;
  /** Heading level, so each page keeps one correct outline. */
  headingLevel?: 2 | 3;
  description?: string;
  /** Right-aligned secondary text in the header row. */
  aside?: ReactNode;
  quiet?: boolean;
  id?: string;
  children: ReactNode;
};

export function Panel({
  eyebrow,
  title,
  headingLevel = 2,
  description,
  aside,
  quiet,
  id,
  children,
}: PanelProps) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const headingId = id ? `${id}-title` : undefined;

  return (
    <section
      className={cx(styles.panel, quiet && styles.quiet)}
      {...(id ? { id } : {})}
      {...(headingId ? { "aria-labelledby": headingId } : {})}
    >
      <div className={styles.header}>
        <div>
          {eyebrow ? <span className={styles.eyebrow}>{eyebrow}</span> : null}
          <Heading className={styles.title} {...(headingId ? { id: headingId } : {})}>
            {title}
          </Heading>
          {description ? <p className={styles.description}>{description}</p> : null}
        </div>
        {aside ? <div className={styles.aside}>{aside}</div> : null}
      </div>
      {children}
    </section>
  );
}
