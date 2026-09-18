"use client";

import { Button, ButtonLink } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import type { GenerateResponse } from "@/lib/api/contract";
import { formatCount, formatSeconds, pluralise } from "@/lib/format";
import { DesignCard } from "./DesignCard";
import { WithheldPanel } from "./WithheldPanel";
import styles from "./DesignsView.module.css";

type DesignResultsProps = {
  response: GenerateResponse;
  /** True when this is the stored result for an unchanged inventory. */
  fromCache: boolean;
  onRegenerate: () => void;
};

/**
 * A completed generate response, rendered.
 *
 * An empty `designs` array is a normal outcome, not an error: the contract says
 * so, and the UI treats it that way — the `withheld` reasons become the content
 * of the page rather than being tucked away behind a failure message.
 *
 * Pure presentation, so both the populated and the empty outcome can be tested
 * without a transport.
 */
export function DesignResults({
  response,
  fromCache,
  onRegenerate,
}: DesignResultsProps) {
  const { designs, withheld, inventory } = response;

  return (
    <div className={styles.stack}>
      <p role="status" className={styles.status}>
        {designs.length > 0
          ? `${formatCount(designs.length)} ${pluralise(designs.length, "design")} ready, ${formatCount(withheld.length)} withheld.`
          : `No designs are possible from this inventory. ${formatCount(withheld.length)} ${pluralise(withheld.length, "variant")} were tried and withheld.`}
      </p>

      {designs.length > 0 ? (
        <Panel
          eyebrow="Step 3"
          title={`${formatCount(designs.length)} ${pluralise(designs.length, "design")} you can build`}
          description="Largest first, by how many of your pieces the design uses. Every one has been validated against your inventory."
          aside={
            <span className={styles.meta}>
              <span>
                Generated in{" "}
                <span className={styles.metaValue}>
                  {formatSeconds(response.elapsedMs)}
                </span>
              </span>
            </span>
          }
        >
          <div className={styles.stack}>
            <ul className={styles.list}>
              {designs.map((design) => (
                <DesignCard key={design.id} design={design} inventory={inventory} />
              ))}
            </ul>
            <div className={styles.actions}>
              <Button variant="secondary" onClick={onRegenerate}>
                Generate again
              </Button>
              <ButtonLink variant="ghost" href="/">
                Change the inventory
              </ButtonLink>
              {fromCache ? (
                <span className={styles.status}>
                  Showing the last result for this inventory.
                </span>
              ) : null}
            </div>
          </div>
        </Panel>
      ) : (
        <Panel
          eyebrow="Step 3"
          title="Nothing is buildable from these parts"
          description="This is a normal outcome, not a failure. The generator only offers a design it has validated, and this inventory did not carry enough of any one kind of material."
        >
          <div className={styles.empty}>
            <p className={styles.emptyLead}>
              Every archetype was tried. Below is what each one wanted, and what it
              found instead.
            </p>
            <div className={styles.emptyHelp}>
              <span className={styles.helpItem}>
                Adding a set with plain bricks and plates is the fastest fix — the
                building and sculpture archetypes reach the most inventories.
              </span>
              <span className={styles.helpItem}>
                Sets of specialised shapes, minifigures or stickers add pieces but
                no tileable area.
              </span>
            </div>
            <div className={styles.actions}>
              <ButtonLink href="/">Add another set</ButtonLink>
              <Button variant="secondary" onClick={onRegenerate}>
                Generate again
              </Button>
            </div>
          </div>
        </Panel>
      )}

      {withheld.length > 0 ? (
        <Panel
          title={`${formatCount(withheld.length)} ${pluralise(withheld.length, "variant")} withheld`}
          description="The generator tried these and would not offer them. The reason is measured, not a guess."
        >
          <WithheldPanel withheld={withheld} />
        </Panel>
      ) : null}
    </div>
  );
}
