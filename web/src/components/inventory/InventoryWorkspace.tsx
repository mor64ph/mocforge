"use client";

import { useState } from "react";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Panel } from "@/components/ui/Panel";
import { api } from "@/lib/api";
import { MAX_SETS } from "@/lib/api/contract";
import { describeError } from "@/lib/api/errors";
import { formatCount, pluralise } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";
import { InventoryChips } from "./InventoryChips";
import { InventorySummary } from "./InventorySummary";
import { SetSearch } from "./SetSearch";
import styles from "./InventoryWorkspace.module.css";

/** Real sets, offered so the first screen is never a bare text field. */
const SUGGESTIONS: ReadonlyArray<{ setNum: string; name: string }> = [
  { setNum: "10696-1", name: "Medium Creative Brick Box" },
  { setNum: "42151-1", name: "Bugatti Bolide" },
  { setNum: "21034-1", name: "London" },
];

export function InventoryWorkspace() {
  const { sets, hydrated, atCapacity, addSet, removeSet, clearSets, hasSet } =
    useWorkspace();
  const [suggestionError, setSuggestionError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  async function addSuggestion(setNum: string) {
    setPending(setNum);
    setSuggestionError(null);
    try {
      addSet(await api().getSet(setNum));
    } catch (cause) {
      setSuggestionError(describeError(cause));
    } finally {
      setPending(null);
    }
  }

  const totalDeclared = sets.reduce((total, set) => total + set.numParts, 0);

  return (
    <div className={styles.stack}>
      <Panel
        eyebrow="Step 1"
        title="Tell MOCForge what you own"
        description={`Add up to ${MAX_SETS} sets. Every design you get back is built only from the parts in these boxes.`}
        aside={
          hydrated && sets.length > 0
            ? `${sets.length} of ${MAX_SETS} sets`
            : null
        }
      >
        <div className={styles.entry}>
          <SetSearch onAdd={addSet} isAdded={hasSet} disabled={atCapacity} />

          {!hydrated ? null : sets.length > 0 ? (
            <div className={styles.declared}>
              <div className={styles.declaredHead}>
                <h3 className={styles.declaredTitle}>
                  Your sets{" "}
                  <span className={styles.actionNote}>
                    · {formatCount(totalDeclared)} catalogued{" "}
                    {pluralise(totalDeclared, "piece")}
                  </span>
                </h3>
                <Button variant="ghost" size="small" onClick={clearSets}>
                  Remove all
                </Button>
              </div>
              <InventoryChips sets={sets} onRemove={removeSet} />
            </div>
          ) : (
            <div className={styles.empty}>
              <p className={styles.emptyText}>
                Nothing added yet. Search above, or start from one of these.
              </p>
              <div className={styles.suggestions}>
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion.setNum}
                    type="button"
                    className={styles.suggestion}
                    disabled={pending !== null}
                    onClick={() => void addSuggestion(suggestion.setNum)}
                  >
                    <span>
                      {pending === suggestion.setNum ? "Adding…" : suggestion.name}
                    </span>
                    <span className={styles.suggestionNum}>{suggestion.setNum}</span>
                  </button>
                ))}
              </div>
              {suggestionError ? (
                <Callout tone="danger" title="Could not add that set">
                  {suggestionError}
                </Callout>
              ) : null}
            </div>
          )}
        </div>
      </Panel>

      {hydrated && sets.length > 0 ? (
        <>
          <InventorySummary setNums={sets.map((set) => set.setNum)} />
          <div className={styles.actions}>
            <ButtonLink href="/designs">
              Generate designs from {sets.length} {pluralise(sets.length, "set")}
            </ButtonLink>
            <p className={styles.actionNote}>
              Generation runs the whole archetype library against your parts. It
              takes a few seconds.
            </p>
          </div>
        </>
      ) : null}
    </div>
  );
}
