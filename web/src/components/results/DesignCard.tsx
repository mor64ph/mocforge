import Link from "next/link";
import { ButtonLink } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Tag } from "@/components/ui/Tag";
import type { Design, Inventory } from "@/lib/api/contract";
import { designKind } from "@/lib/archetypes";
import { formatCount, pluralise } from "@/lib/format";
import { rationaleFor } from "@/lib/rationale";
import styles from "./DesignCard.module.css";

/** Typed-route href for a design. Ids are opaque, so they are only encoded. */
export function designHref(designId: string): `/designs/${string}` {
  return `/designs/${encodeURIComponent(designId)}`;
}

/**
 * One returned design. `confidence` is always 1.0 in v1 and the API never
 * returns an unvalidated design, so the card states buildability as fact
 * rather than dressing it up as a score.
 */
export function DesignCard({
  design,
  inventory,
}: {
  design: Design;
  inventory: Inventory;
}) {
  const rationale = rationaleFor(design, inventory);
  const levels = new Set(design.steps.map((step) => step.level)).size;

  return (
    <li className={styles.card}>
      <div className={styles.head}>
        <div className={styles.heading}>
          <h3 className={styles.title}>
            <Link className={styles.titleLink} href={designHref(design.id)}>
              {design.title}
            </Link>
          </h3>
          <div className={styles.tags}>
            <Tag tone="accent">{designKind(design.archetype, design.variant)}</Tag>
            <Tag tone="success">Buildable from your parts</Tag>
            {design.builderNotes.length > 0 ? (
              <Tag tone="caution">
                {design.builderNotes.length}{" "}
                {pluralise(design.builderNotes.length, "note")}
              </Tag>
            ) : null}
          </div>
        </div>
        <p className={styles.count}>
          <span className={styles.countValue}>{formatCount(design.pieceCount)}</span>
          <span className={styles.countLabel}>pieces used</span>
        </p>
      </div>

      <div className={styles.rationale}>
        <h4 className={styles.rationaleHead}>Why it suits this inventory</h4>
        <p className={styles.headline}>{rationale.headline}</p>
        <ul className={styles.points}>
          {rationale.points.map((point) => (
            <li key={point} className={styles.point}>
              {point}
            </li>
          ))}
        </ul>
      </div>

      {/* `builderNotes`, never `warnings` — CONTRACT.md behaviour
          requirement 2. A count of raw diagnostics told a user nothing; a
          count of things they need to know before starting does. */}
      {design.builderNotes.length > 0 ? (
        <Callout tone="caution" title="Before you start">
          <ul>
            {design.builderNotes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </Callout>
      ) : null}

      <div className={styles.footer}>
        <ButtonLink variant="secondary" href={designHref(design.id)}>
          Open instructions
        </ButtonLink>
        <span className={styles.summary}>
          {formatCount(design.steps.length)} {pluralise(design.steps.length, "step")} ·{" "}
          {formatCount(levels)} {pluralise(levels, "level")}
        </span>
      </div>
    </li>
  );
}
