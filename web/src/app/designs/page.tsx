import type { Metadata } from "next";
import { FlowSteps } from "@/components/layout/FlowSteps";
import { DesignsView } from "@/components/results/DesignsView";
import styles from "../page.module.css";

export const metadata: Metadata = {
  title: "Designs",
};

export default function DesignsPage() {
  return (
    <>
      <FlowSteps current="designs" />
      <h1 className={styles.pageTitle}>Designs for your inventory</h1>
      <DesignsView />
    </>
  );
}
