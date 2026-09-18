"use client";

import { useEffect, useState } from "react";
import { GenerationProgress } from "@/components/generate/GenerationProgress";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Panel } from "@/components/ui/Panel";
import { api } from "@/lib/api";
import type { GenerateResponse, Inventory } from "@/lib/api/contract";
import { describeError, isApiError } from "@/lib/api/errors";
import { formatCount, pluralise } from "@/lib/format";
import { inventoryKey, useWorkspace } from "@/lib/workspace";
import { DesignResults } from "./DesignResults";
import styles from "./DesignsView.module.css";

type Outcome =
  | { kind: "working"; inventory: Inventory | null }
  | { kind: "ready"; response: GenerateResponse; fromCache: boolean }
  | { kind: "error"; message: string; unresolved: string[] };

/**
 * Runs the generate call and decides which state to show.
 *
 * A cached result for the current inventory is shown straight away rather than
 * spending seconds regenerating something identical — design ids are stable for
 * (archetype, variant, setNums), so the cached response *is* the response.
 * "Generate again" bumps `attempt`, which is part of the request identity, so
 * the cache is bypassed with no special-casing.
 *
 * Every piece of state here is keyed by that request identity, so a response
 * that arrives for an inventory the user has already changed is never shown.
 */
export function DesignsView() {
  const { sets, hydrated, generation, saveGeneration } = useWorkspace();
  const setNums = sets.map((set) => set.setNum);
  const key = inventoryKey(setNums);

  const [attempt, setAttempt] = useState(0);
  const [entry, setEntry] = useState<{ request: string; outcome: Outcome } | null>(null);
  const [variantCount, setVariantCount] = useState<number | null>(null);

  const request = `${key}#${attempt}`;
  const cached = attempt === 0 && generation?.key === key ? generation.response : null;

  const outcome: Outcome | null =
    !hydrated || setNums.length === 0
      ? null
      : (entry?.request === request ? entry.outcome : null) ??
        (cached ? { kind: "ready", response: cached, fromCache: true } : null) ??
        { kind: "working", inventory: null };

  useEffect(() => {
    if (!hydrated || setNums.length === 0) return;
    if (attempt === 0 && generation?.key === key) return;

    let live = true;
    const requested = key.split("+");

    void (async () => {
      try {
        const inventory = await api().getInventory({ setNums: requested });
        if (live) setEntry({ request, outcome: { kind: "working", inventory } });
        const response = await api().generate({ setNums: requested });
        if (!live) return;
        saveGeneration(response);
        setEntry({ request, outcome: { kind: "ready", response, fromCache: false } });
      } catch (cause) {
        if (!live) return;
        setEntry({
          request,
          outcome: {
            kind: "error",
            message: describeError(cause),
            unresolved: isApiError(cause) ? cause.unresolvedSets : [],
          },
        });
      }
    })();

    return () => {
      live = false;
    };
    // `request` carries both the inventory and the attempt number.
  }, [request, key, attempt, hydrated, setNums.length, generation, saveGeneration]);

  useEffect(() => {
    let live = true;
    api()
      .getArchetypes()
      .then((response) => {
        if (!live) return;
        setVariantCount(
          response.archetypes.reduce(
            (total, archetype) => total + Math.max(1, archetype.variants.length),
            0,
          ),
        );
      })
      .catch(() => {
        // The variant count is context, not content; its absence is not an error.
      });
    return () => {
      live = false;
    };
  }, []);

  if (!hydrated) {
    return (
      <Panel eyebrow="Step 3" title="Designs">
        <p className={styles.status}>Restoring your inventory…</p>
      </Panel>
    );
  }

  if (setNums.length === 0) {
    return (
      <Panel
        eyebrow="Step 3"
        title="No inventory declared yet"
        description="MOCForge can only generate from parts you have told it about."
      >
        <ButtonLink href="/">Add the sets you own</ButtonLink>
      </Panel>
    );
  }

  if (outcome === null || outcome.kind === "working") {
    return (
      <Panel
        eyebrow="Step 3"
        title="Generating designs"
        description={`Trying every archetype against ${formatCount(sets.length)} ${pluralise(sets.length, "set")}.`}
      >
        <GenerationProgress
          inventory={outcome?.kind === "working" ? outcome.inventory : null}
          variantCount={variantCount}
        />
      </Panel>
    );
  }

  if (outcome.kind === "error") {
    return (
      <Panel eyebrow="Step 3" title="Generation failed">
        <div className={styles.stack}>
          <Callout tone="danger" title="The generator could not run">
            {outcome.message}
            {outcome.unresolved.length > 0 ? (
              <> Unresolved sets: {outcome.unresolved.join(", ")}.</>
            ) : null}
          </Callout>
          <div className={styles.actions}>
            <Button onClick={() => setAttempt((value) => value + 1)}>Try again</Button>
            <ButtonLink variant="secondary" href="/">
              Change the inventory
            </ButtonLink>
          </div>
        </div>
      </Panel>
    );
  }

  return (
    <DesignResults
      response={outcome.response}
      fromCache={outcome.fromCache}
      onRegenerate={() => setAttempt((value) => value + 1)}
    />
  );
}
