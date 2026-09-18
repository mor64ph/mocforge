"use client";

import { Button, ButtonLink } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Panel } from "@/components/ui/Panel";
import styles from "./page.module.css";

/**
 * Last-resort boundary for a render-time fault. Deliberately shows the digest
 * rather than the raw message: the digest is what correlates with the server
 * log, and the message may be sanitised in production anyway.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <>
      <h1 className={styles.pageTitle}>Something broke</h1>
      <Panel title="This page could not be rendered">
        <Callout tone="danger" title="Unexpected error">
          {error.digest
            ? `Reference ${error.digest}. Retrying may be enough; if not, start again from your inventory.`
            : "Retrying may be enough; if not, start again from your inventory."}
        </Callout>
        <div className={styles.actions}>
          <Button onClick={reset}>Try again</Button>
          <ButtonLink variant="secondary" href="/">
            Back to the inventory
          </ButtonLink>
        </div>
      </Panel>
    </>
  );
}
