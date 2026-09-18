import type { Metadata } from "next";
import { InstructionsView } from "@/components/instructions/InstructionsView";
import { FlowSteps } from "@/components/layout/FlowSteps";
import styles from "../../page.module.css";

export const metadata: Metadata = {
  title: "Instructions",
};

export default async function InstructionsPage({
  params,
}: {
  params: Promise<{ designId: string }>;
}) {
  // Next decodes dynamic segments, so this is the id the API issued.
  const { designId } = await params;

  return (
    <>
      <FlowSteps current="instructions" />
      <h1 className={styles.pageTitle}>Build instructions</h1>
      <InstructionsView designId={designId} />
    </>
  );
}
