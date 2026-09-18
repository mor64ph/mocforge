"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Callout } from "@/components/ui/Callout";
import { api } from "@/lib/api";
import type { SetSummary } from "@/lib/api/contract";
import { describeError } from "@/lib/api/errors";
import { cx } from "@/lib/cx";
import { formatSetMeta } from "@/lib/format";
import styles from "./SetSearch.module.css";

const DEBOUNCE_MS = 250;
const RESULT_LIMIT = 8;
const NO_RESULTS: readonly SetSummary[] = [];

type SearchOutcome = {
  term: string;
  results: readonly SetSummary[];
  error: string | null;
};

type SetSearchProps = {
  onAdd: (set: SetSummary) => void;
  isAdded: (setNum: string) => boolean;
  /** True once the inventory holds the contract maximum of 10 sets. */
  disabled: boolean;
};

/**
 * Editable combobox over `GET /sets/search`, following the ARIA 1.2 pattern:
 * the input owns `aria-expanded`, `aria-controls` and `aria-activedescendant`,
 * and the popup is a real listbox of options. Selection never moves focus out
 * of the input, so a keyboard user can add several sets without reaching for
 * the mouse.
 *
 * Results, the highlighted option and any error are all keyed by the search
 * term they belong to. Everything about the current term is therefore derived,
 * and a late response for an abandoned term can never be displayed.
 */
export function SetSearch({ onAdd, isAdded, disabled }: SetSearchProps) {
  const inputId = useId();
  const listboxId = useId();
  const hintId = useId();
  const statusId = useId();

  const [query, setQuery] = useState("");
  const [outcome, setOutcome] = useState<SearchOutcome | null>(null);
  const [active, setActive] = useState<{ term: string; index: number } | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  const inputRef = useRef<HTMLInputElement>(null);

  const term = query.trim();
  const current = outcome?.term === term ? outcome : null;
  const results = current?.results ?? NO_RESULTS;
  const error = current?.error ?? null;
  const searching = term.length > 0 && current === null;
  const activeIndex = active?.term === term ? active.index : -1;
  const showList = results.length > 0 && dismissed !== term && !disabled;

  useEffect(() => {
    if (term.length === 0) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api()
        .searchSets(term, RESULT_LIMIT, { signal: controller.signal })
        .then((response) => {
          setOutcome({ term, results: response.results, error: null });
          setAnnouncement(
            response.results.length === 0
              ? `No sets match ${term}.`
              : `${response.results.length} ${response.results.length === 1 ? "set" : "sets"} found.`,
          );
        })
        .catch((cause: unknown) => {
          if (cause instanceof DOMException && cause.name === "AbortError") return;
          setOutcome({ term, results: NO_RESULTS, error: describeError(cause) });
        });
    }, DEBOUNCE_MS);

    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [term]);

  const select = useCallback(
    (set: SetSummary) => {
      if (isAdded(set.setNum)) {
        setAnnouncement(`${set.setNum} is already in your inventory.`);
        return;
      }
      onAdd(set);
      setAnnouncement(`${set.name}, set ${set.setNum}, added to your inventory.`);
      setQuery("");
      setActive(null);
      setDismissed(null);
      inputRef.current?.focus();
    },
    [isAdded, onAdd],
  );

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (results.length === 0) return;
      setDismissed(null);
      const step = event.key === "ArrowDown" ? 1 : -1;
      const next = activeIndex + step;
      setActive({
        term,
        index: next < 0 ? results.length - 1 : next >= results.length ? 0 : next,
      });
      return;
    }

    if (event.key === "Home" || event.key === "End") {
      if (!showList) return;
      event.preventDefault();
      setActive({ term, index: event.key === "Home" ? 0 : results.length - 1 });
      return;
    }

    if (event.key === "Enter") {
      if (results.length === 0) return;
      event.preventDefault();
      // With nothing highlighted, take the top result: that is what makes a
      // bare "42151" typed and submitted resolve to 42151-1.
      const chosen = results[activeIndex >= 0 ? activeIndex : 0];
      if (chosen) select(chosen);
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      if (showList) {
        setDismissed(term);
        setActive(null);
      } else if (query.length > 0) {
        setQuery("");
      }
    }
  };

  const activeId = activeIndex >= 0 ? `${listboxId}-${activeIndex}` : undefined;

  return (
    <div className={styles.wrapper}>
      <label className={styles.label} htmlFor={inputId}>
        Add a set you own
      </label>
      <p className={styles.hint} id={hintId}>
        Search by set number or name. A bare number works too — 42151 resolves to
        42151-1.
      </p>
      <div className={styles.field}>
        <input
          ref={inputRef}
          id={inputId}
          className={styles.input}
          type="text"
          role="combobox"
          autoComplete="off"
          spellCheck={false}
          placeholder="42151, or Bugatti"
          value={query}
          disabled={disabled}
          aria-expanded={showList}
          aria-controls={listboxId}
          aria-autocomplete="list"
          aria-describedby={`${hintId} ${statusId}`}
          {...(activeId ? { "aria-activedescendant": activeId } : {})}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={onKeyDown}
          onBlur={() => setDismissed(term)}
        />
      </div>

      {showList ? (
        <ul
          className={styles.listbox}
          id={listboxId}
          role="listbox"
          aria-label="Matching sets"
        >
          {results.map((set, index) => {
            const added = isAdded(set.setNum);
            return (
              <li
                key={set.setNum}
                id={`${listboxId}-${index}`}
                className={cx(
                  styles.option,
                  index === activeIndex && styles.active,
                  added && styles.added,
                )}
                role="option"
                aria-selected={index === activeIndex}
                aria-disabled={added}
                // The listbox closes on blur, so selection has to happen before
                // focus leaves the input.
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => select(set)}
              >
                <span className={styles.optionMain}>
                  <span className={styles.optionName}>{set.name}</span>
                  <span className={styles.optionMeta}>
                    {added ? "Already in your inventory · " : ""}
                    {formatSetMeta(set)}
                  </span>
                </span>
                <span className={styles.optionNum}>{set.setNum}</span>
              </li>
            );
          })}
        </ul>
      ) : null}

      <p className={styles.status} id={statusId} aria-live="polite">
        {disabled
          ? "Inventory is full at 10 sets. Remove one to add another."
          : searching
            ? "Searching the catalogue…"
            : announcement}
      </p>

      {error ? (
        <div className={styles.error}>
          <Callout tone="danger" title="Search failed">
            {error}
          </Callout>
        </div>
      ) : null}
    </div>
  );
}
