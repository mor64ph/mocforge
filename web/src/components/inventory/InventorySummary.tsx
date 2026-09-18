"use client";

import { useEffect, useState } from "react";
import { Callout } from "@/components/ui/Callout";
import { Panel } from "@/components/ui/Panel";
import { Stat, StatGrid } from "@/components/ui/Stat";
import { VisuallyHidden } from "@/components/ui/VisuallyHidden";
import { api } from "@/lib/api";
import type { Inventory } from "@/lib/api/contract";
import { describeError, isApiError } from "@/lib/api/errors";
import { formatCount, formatPercent, pluralise } from "@/lib/format";
import { FamilyBreakdown } from "./FamilyBreakdown";
import styles from "./InventorySummary.module.css";

type Outcome =
  | { status: "ready"; inventory: Inventory }
  | { status: "error"; message: string; unresolved: string[] };

/**
 * `POST /inventory` for the declared sets.
 *
 * The result is stored against the set list it came from, and "loading" is the
 * absence of a result for the current list rather than a separate flag. That
 * removes the render in which a previous inventory is shown under a new set
 * list, and keeps the effect free of synchronous state updates.
 */
export function InventorySummary({ setNums }: { setNums: readonly string[] }) {
  const key = [...setNums].join(",");
  const [entry, setEntry] = useState<{ key: string; outcome: Outcome } | null>(null);

  useEffect(() => {
    if (key.length === 0) return;
    const controller = new AbortController();
    api()
      .getInventory({ setNums: key.split(",") }, { signal: controller.signal })
      .then((inventory) => setEntry({ key, outcome: { status: "ready", inventory } }))
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setEntry({
          key,
          outcome: {
            status: "error",
            message: describeError(cause),
            unresolved: isApiError(cause) ? cause.unresolvedSets : [],
          },
        });
      });
    return () => controller.abort();
  }, [key]);

  const outcome = entry?.key === key ? entry.outcome : null;

  return (
    <Panel
      eyebrow="Step 2"
      title="What you have to build with"
      description="Combined across every set you added, excluding spare parts."
      aside={
        outcome?.status === "ready"
          ? `${formatCount(outcome.inventory.setNums.length)} ${pluralise(outcome.inventory.setNums.length, "set")}`
          : null
      }
    >
      <div aria-busy={outcome === null}>
        {outcome === null ? <SummarySkeleton /> : null}

        {outcome?.status === "error" ? (
          <Callout tone="danger" title="Could not read that inventory">
            {outcome.message}
            {outcome.unresolved.length > 0 ? (
              <>
                {" "}
                Unresolved:{" "}
                {outcome.unresolved.map((unresolved, index) => (
                  <span key={unresolved}>
                    {index > 0 ? ", " : ""}
                    <code>{unresolved}</code>
                  </span>
                ))}
                .
              </>
            ) : null}
          </Callout>
        ) : null}

        {outcome?.status === "ready" ? (
          <div className={styles.body}>
            <StatGrid>
              <Stat
                label="Pieces"
                value={formatCount(outcome.inventory.pieces)}
                hint="Regular parts, spares excluded"
              />
              <Stat
                label="Lots"
                value={formatCount(outcome.inventory.lots)}
                hint="Distinct part-and-colour combinations"
              />
              <Stat
                label="With geometry"
                value={formatCount(outcome.inventory.geometryPieces)}
                hint={`${formatPercent(
                  outcome.inventory.pieces > 0
                    ? outcome.inventory.geometryPieces / outcome.inventory.pieces
                    : 0,
                )} have a 3D part model`}
              />
              <Stat
                label="Structural studs"
                value={formatCount(outcome.inventory.structuralStuds)}
                hint="Tileable area in bricks, plates and tiles"
              />
            </StatGrid>
            <FamilyBreakdown families={outcome.inventory.families} />
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

function SummarySkeleton() {
  return (
    <div className={styles.skeletonGrid}>
      <VisuallyHidden>Reading your inventory…</VisuallyHidden>
      {[0, 1, 2, 3].map((index) => (
        <div key={index} className={styles.skeletonStat} aria-hidden="true">
          <div className={styles.bone} style={{ width: "45%" }} />
          <div className={styles.boneWide} style={{ width: "70%" }} />
          <div className={styles.bone} style={{ width: "85%" }} />
        </div>
      ))}
    </div>
  );
}
