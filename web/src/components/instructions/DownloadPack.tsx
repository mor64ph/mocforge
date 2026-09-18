"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { api } from "@/lib/api";
import type { Design } from "@/lib/api/contract";
import { FEATURE_DESIGN_PACKAGE } from "@/lib/api/contract";
import { describeError } from "@/lib/api/errors";
import { downloadBlob, packageFilename } from "@/lib/download";
import { formatBytes, formatCount } from "@/lib/format";
import styles from "./DownloadPack.module.css";

type Availability = "unknown" | "available" | "unavailable";

type Pack = { filename: string; blob: Blob };

type Status =
  | { kind: "idle" }
  | { kind: "preparing" }
  | { kind: "ready"; pack: Pack }
  | { kind: "failed"; message: string };

type DownloadPackProps = {
  design: Design;
  /** The inventory the design was generated from, which names the file. */
  setNums: readonly string[];
};

/**
 * The download affordance for the printable instruction pack.
 *
 * Two independent questions, so two pieces of state. *Can* this service build
 * a pack — answered once from `/health`, because CONTRACT.md v1.1 makes the
 * feature list the sanctioned discovery mechanism and the in-process mock
 * reports none, so offering the button unconditionally would mean offering a
 * download that always fails. And *has* this one been built yet — idle,
 * preparing, ready or failed, which matters because the service renders every
 * step on first request and that takes seconds.
 *
 * A prepared pack is kept, so saving it again is instant rather than a second
 * round trip for bytes already in memory. It is therefore state that must not
 * outlive its design, which the caller guarantees by keying this component on
 * `design.id` rather than by resetting it from an effect.
 */
export function DownloadPack({ design, setNums }: DownloadPackProps) {
  const [availability, setAvailability] = useState<Availability>("unknown");
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  useEffect(() => {
    const controller = new AbortController();
    api()
      .health({ signal: controller.signal })
      .then((health) => {
        setAvailability(
          health.features.includes(FEATURE_DESIGN_PACKAGE)
            ? "available"
            : "unavailable",
        );
      })
      .catch(() => {
        // A failed probe is not a failed download: leaving it unavailable says
        // "not offered here", which is the honest reading of "cannot ask".
        setAvailability("unavailable");
      });
    return () => {
      controller.abort();
    };
  }, []);

  async function download(): Promise<void> {
    if (status.kind === "ready") {
      // Already in memory: saving again is a click, not another half a
      // megabyte over the wire.
      downloadBlob(status.pack.filename, status.pack.blob);
      return;
    }
    setStatus({ kind: "preparing" });
    try {
      const blob = await api().getPackage(design);
      const pack = { filename: packageFilename(design, setNums), blob };
      downloadBlob(pack.filename, pack.blob);
      setStatus({ kind: "ready", pack });
    } catch (cause) {
      setStatus({ kind: "failed", message: describeError(cause) });
    }
  }

  if (availability === "unavailable") {
    return (
      <section className={styles.pack} aria-labelledby="pack-heading">
        <h3 className={styles.heading} id="pack-heading">
          Printable instructions
        </h3>
        <p className={styles.note}>
          This build of MOCForge has no renderer, so it cannot draw the step
          images a printable pack is made of. Connect the generation service to
          download one.
        </p>
      </section>
    );
  }

  const preparing = status.kind === "preparing";

  return (
    <section className={styles.pack} aria-labelledby="pack-heading">
      <h3 className={styles.heading} id="pack-heading">
        Printable instructions
      </h3>
      <p className={styles.note}>
        One zip that opens with software you already have: a PDF to print, with
        the finished model, a parts list to tick off and a page for each of the{" "}
        {formatCount(design.steps.length)} steps. Every step image is in there
        as a PNG too, the parts list as a spreadsheet, and the{" "}
        <code>.ldr</code> for CAD.
      </p>

      <div className={styles.actions}>
        <Button
          onClick={() => void download()}
          disabled={availability === "unknown" || preparing}
        >
          {preparing
            ? "Drawing every step…"
            : status.kind === "ready"
              ? "Save the pack again"
              : "Download the instruction pack"}
        </Button>
        <p className={styles.status} role="status">
          {preparing
            ? "The service is rendering the model and building the document. This takes a few seconds."
            : status.kind === "ready"
              ? `Saved ${status.pack.filename} (${formatBytes(status.pack.blob.size)}).`
              : ""}
        </p>
      </div>

      {status.kind === "failed" ? (
        <Callout tone="danger" title="The pack could not be built" live="assertive">
          {status.message}
        </Callout>
      ) : null}
    </section>
  );
}
