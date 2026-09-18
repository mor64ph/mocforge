"use client";

import { useMemo, useSyncExternalStore } from "react";
import type { GenerateResponse, SetSummary } from "./api/contract";
import { MAX_SETS } from "./api/contract";

/**
 * The declared inventory and the last generation result.
 *
 * Both live in `sessionStorage`, so a reload or a move to the instructions page
 * does not discard work that took seconds to produce. The generation entry is
 * keyed by the inventory it came from, so a stale result is never shown against
 * a changed inventory.
 *
 * Storage is an external system, so it is read through `useSyncExternalStore`
 * rather than copied into state by an effect: there is one source of truth, and
 * no render pass where the UI holds a value storage has already replaced.
 */
const INVENTORY_KEY = "mocforge.inventory.v1";
const GENERATION_KEY = "mocforge.generation.v1";

export function inventoryKey(setNums: readonly string[]): string {
  return [...setNums].sort().join("+");
}

type GenerationEntry = {
  key: string;
  response: GenerateResponse;
};

type Snapshot = {
  sets: SetSummary[];
  generation: GenerationEntry | null;
  /** False while server-rendering, so nothing claims to know the inventory. */
  hydrated: boolean;
};

const SERVER_SNAPSHOT: Snapshot = { sets: [], generation: null, hydrated: false };

let snapshot: Snapshot | null = null;
const listeners = new Set<() => void>();

function readStored<T>(key: string, isValid: (value: unknown) => value is T): T | null {
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return isValid(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function writeStored(key: string, value: unknown): void {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage that is full or blocked is not worth interrupting the flow for.
  }
}

const isSetList = (value: unknown): value is SetSummary[] =>
  Array.isArray(value) &&
  value.every(
    (entry) =>
      entry !== null &&
      typeof entry === "object" &&
      typeof (entry as SetSummary).setNum === "string" &&
      typeof (entry as SetSummary).name === "string",
  );

const isGenerationEntry = (value: unknown): value is GenerationEntry =>
  value !== null &&
  typeof value === "object" &&
  typeof (value as GenerationEntry).key === "string" &&
  typeof (value as GenerationEntry).response === "object";

/** Cached so repeated reads are referentially equal, as the hook requires. */
function getSnapshot(): Snapshot {
  if (typeof window === "undefined") return SERVER_SNAPSHOT;
  snapshot ??= {
    sets: readStored(INVENTORY_KEY, isSetList) ?? [],
    generation: readStored(GENERATION_KEY, isGenerationEntry),
    hydrated: true,
  };
  return snapshot;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function commit(next: Snapshot): void {
  snapshot = next;
  for (const listener of listeners) listener();
}

function setSets(sets: SetSummary[]): void {
  writeStored(INVENTORY_KEY, sets);
  commit({ ...getSnapshot(), sets });
}

function addSet(set: SetSummary): void {
  const { sets } = getSnapshot();
  if (sets.length >= MAX_SETS) return;
  if (sets.some((entry) => entry.setNum === set.setNum)) return;
  setSets([...sets, set]);
}

function removeSet(setNum: string): void {
  setSets(getSnapshot().sets.filter((entry) => entry.setNum !== setNum));
}

function clearSets(): void {
  setSets([]);
}

function saveGeneration(response: GenerateResponse): void {
  const generation: GenerationEntry = {
    key: inventoryKey(response.inventory.setNums),
    response,
  };
  writeStored(GENERATION_KEY, generation);
  commit({ ...getSnapshot(), generation });
}

export type Workspace = {
  sets: readonly SetSummary[];
  hydrated: boolean;
  atCapacity: boolean;
  generation: GenerationEntry | null;
  addSet: (set: SetSummary) => void;
  removeSet: (setNum: string) => void;
  clearSets: () => void;
  hasSet: (setNum: string) => boolean;
  saveGeneration: (response: GenerateResponse) => void;
};

export function useWorkspace(): Workspace {
  const state = useSyncExternalStore(subscribe, getSnapshot, () => SERVER_SNAPSHOT);

  return useMemo(
    () => ({
      sets: state.sets,
      hydrated: state.hydrated,
      atCapacity: state.sets.length >= MAX_SETS,
      generation: state.generation,
      addSet,
      removeSet,
      clearSets,
      saveGeneration,
      hasSet: (setNum: string) => state.sets.some((entry) => entry.setNum === setNum),
    }),
    [state],
  );
}
