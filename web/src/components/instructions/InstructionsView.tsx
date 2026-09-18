"use client";

import { useEffect, useState } from "react";
import { GenerationProgress } from "@/components/generate/GenerationProgress";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Panel } from "@/components/ui/Panel";
import { Stat, StatGrid } from "@/components/ui/Stat";
import { Tag } from "@/components/ui/Tag";
import { api } from "@/lib/api";
import type { BuildStep, Design, GenerateResponse } from "@/lib/api/contract";
import { describeError } from "@/lib/api/errors";
import { designKind } from "@/lib/archetypes";
import { formatCount, pluralise } from "@/lib/format";
import { downloadText, ldrFilename } from "@/lib/download";
import { distinctLots } from "@/lib/rationale";
import { inventoryKey, useWorkspace } from "@/lib/workspace";
import { DownloadPack } from "./DownloadPack";
import { StepCard } from "./StepCard";
import styles from "./InstructionsView.module.css";

type Outcome =
  | { kind: "found"; response: GenerateResponse; design: Design }
  | { kind: "missing" }
  | { kind: "error"; message: string };

/**
 * Instructions for one design.
 *
 * The design comes from the cached generate response for the current
 * inventory. Arriving cold — a shared link, or a session whose cache was
 * cleared — regenerates for the declared inventory and looks the id up again,
 * which works because ids are stable for (archetype, variant, setNums). If it
 * still is not found, that is the `design_not_found` case, said plainly.
 */
export function InstructionsView({ designId }: { designId: string }) {
  const { sets, hydrated, generation, saveGeneration } = useWorkspace();
  const setNums = sets.map((set) => set.setNum);
  const key = inventoryKey(setNums);

  const [entry, setEntry] = useState<{ request: string; outcome: Outcome } | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const request = `${key}:${designId}`;
  const cached = generation?.key === key ? generation.response : null;

  const outcome: Outcome | null =
    !hydrated || setNums.length === 0
      ? null
      : (entry?.request === request ? entry.outcome : null) ??
        (cached ? locate(cached, designId) : null);

  useEffect(() => {
    if (!hydrated || setNums.length === 0) return;
    if (generation?.key === key) return;

    let live = true;

    api()
      .generate({ setNums: key.split("+") })
      .then((response) => {
        if (!live) return;
        saveGeneration(response);
        setEntry({ request, outcome: locate(response, designId) });
      })
      .catch((cause: unknown) => {
        if (!live) return;
        setEntry({ request, outcome: { kind: "error", message: describeError(cause) } });
      });

    return () => {
      live = false;
    };
  }, [request, key, designId, hydrated, setNums.length, generation, saveGeneration]);

  async function download(design: Design, sources: readonly string[]) {
    setDownloading(true);
    setDownloadError(null);
    try {
      const text = await api().getLdr(design);
      downloadText(ldrFilename(design, sources), text, "text/plain;charset=utf-8");
    } catch (cause) {
      setDownloadError(describeError(cause));
    } finally {
      setDownloading(false);
    }
  }

  if (!hydrated) {
    return (
      <Panel eyebrow="Step 4" title="Instructions">
        <p className={styles.status}>Restoring your inventory…</p>
      </Panel>
    );
  }

  if (setNums.length === 0) {
    return (
      <Panel
        eyebrow="Step 4"
        title="No inventory declared"
        description="Instructions are generated against the sets you own, so start there."
      >
        <ButtonLink href="/">Add the sets you own</ButtonLink>
      </Panel>
    );
  }

  if (outcome === null) {
    return (
      <Panel
        eyebrow="Step 4"
        title="Rebuilding this design"
        description="This design was not in the current session, so it is being generated again from your inventory."
      >
        <GenerationProgress inventory={null} variantCount={null} />
      </Panel>
    );
  }

  if (outcome.kind === "error") {
    return (
      <Panel eyebrow="Step 4" title="Could not load this design">
        <div className={styles.stack}>
          <Callout tone="danger" title="Generation failed">
            {outcome.message}
          </Callout>
          <ButtonLink href="/designs">Back to designs</ButtonLink>
        </div>
      </Panel>
    );
  }

  if (outcome.kind === "missing") {
    return (
      <Panel
        eyebrow="Step 4"
        title="That design is no longer available"
        description="Design ids are not kept across service restarts, and this one is not among the current results."
      >
        <div className={styles.stack}>
          <Callout tone="caution" title="Nothing was lost">
            Generate again from the same inventory and the equivalent design comes
            back — ids are stable for an archetype, variant and set list.
          </Callout>
          <ButtonLink href="/designs">Back to designs</ButtonLink>
        </div>
      </Panel>
    );
  }

  const { design, response } = outcome;
  const levels = groupByLevel(design.steps);

  return (
    <div className={styles.stack}>
      <Panel
        eyebrow="Step 4"
        title={design.title}
        aside={
          <span className={styles.levelMeta}>
            {formatCount(response.inventory.setNums.length)}{" "}
            {pluralise(response.inventory.setNums.length, "set")} ·{" "}
            {response.inventory.setNums.join(", ")}
          </span>
        }
      >
        <div className={styles.summary}>
          <div className={styles.tags}>
            <Tag tone="accent">{designKind(design.archetype, design.variant)}</Tag>
            <Tag tone="success">Validated against your inventory</Tag>
          </div>

          <StatGrid>
            <Stat label="Pieces" value={formatCount(design.pieceCount)} />
            <Stat label="Steps" value={formatCount(design.steps.length)} />
            <Stat
              label="Levels"
              value={formatCount(levels.length)}
              hint="Groups of plates above the ground"
            />
            <Stat
              label="Part lots"
              value={formatCount(distinctLots(design))}
              hint="Distinct part-and-colour combinations"
            />
          </StatGrid>

          {/* `builderNotes`, never `warnings` — CONTRACT.md behaviour
              requirement 2. The heading matches the one in the downloaded PDF,
              which prints this same field verbatim, so a reader who has both
              in front of them sees one set of caveats said one way. */}
          {design.builderNotes.length > 0 ? (
            <Callout tone="caution" title="Before you start">
              <ul>
                {design.builderNotes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </Callout>
          ) : null}

          {/* Keyed on the design: a prepared pack belongs to one design, and
              the router reuses this tree when the id in the path changes. */}
          <DownloadPack
            key={design.id}
            design={design}
            setNums={response.inventory.setNums}
          />

          <div className={styles.actions}>
            <Button
              variant="secondary"
              onClick={() => void download(design, response.inventory.setNums)}
              disabled={downloading}
            >
              {downloading ? "Preparing the file…" : "Download the LDraw model only"}
            </Button>
            <ButtonLink variant="ghost" href="/designs">
              Back to designs
            </ButtonLink>
          </div>
          <p className={styles.downloadNote}>
            The <code>.ldr</code> is inside the pack as well. On its own it
            opens in LDraw editors such as LeoCAD, BrickLink Studio or LDView,
            where you can rotate the model and step through the build in 3D.
          </p>

          {downloadError ? (
            <Callout tone="danger" title="Download failed">
              {downloadError}
            </Callout>
          ) : null}
        </div>
      </Panel>

      <div className={styles.levels}>
        {levels.map((group) => (
          <section
            key={`${group.level}-${group.steps[0]?.index ?? 0}`}
            className={styles.level}
          >
            <div className={styles.levelHead}>
              <h3 className={styles.levelTitle}>Level {formatCount(group.level)}</h3>
              <p className={styles.levelMeta}>
                {group.level === 0
                  ? "On the ground"
                  : `${formatCount(group.level)} ${pluralise(group.level, "plate")} above the ground`}{" "}
                · {formatCount(group.steps.length)}{" "}
                {pluralise(group.steps.length, "step")}
              </p>
            </div>
            <ol className={styles.steps}>
              {group.steps.map((step) => (
                <StepCard key={step.index} step={step} />
              ))}
            </ol>
          </section>
        ))}
      </div>
    </div>
  );
}

function locate(response: GenerateResponse, designId: string): Outcome {
  const design = response.designs.find((entry) => entry.id === designId);
  return design ? { kind: "found", response, design } : { kind: "missing" };
}

type LevelGroup = { level: number; steps: BuildStep[] };

/** Consecutive runs of the same level, so build order is never reordered. */
function groupByLevel(steps: readonly BuildStep[]): LevelGroup[] {
  const groups: LevelGroup[] = [];
  for (const step of steps) {
    const last = groups.at(-1);
    if (last && last.level === step.level) last.steps.push(step);
    else groups.push({ level: step.level, steps: [step] });
  }
  return groups;
}
