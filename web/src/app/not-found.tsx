import type { Metadata } from "next";
import { ButtonLink } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Page not found",
};

export default function NotFound() {
  return (
    <>
      <h1 className={styles.pageTitle}>Page not found</h1>
      <Panel
        title="Nothing lives at this address"
        description="The link may be out of date, or the page may have moved."
      >
        <ButtonLink href="/">Start from your inventory</ButtonLink>
      </Panel>
    </>
  );
}
