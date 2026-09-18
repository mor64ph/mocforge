import { FlowSteps } from "@/components/layout/FlowSteps";
import { InventoryWorkspace } from "@/components/inventory/InventoryWorkspace";
import styles from "./page.module.css";

export default function InventoryPage() {
  return (
    <>
      <FlowSteps current="inventory" />
      <div className={styles.intro}>
        <h1 className={styles.title}>Build something new from the sets you own</h1>
        <p className={styles.lead}>
          Name the sets in your bin. MOCForge generates designs that use only
          those parts, and gives you the steps to build them. Nothing to buy.
        </p>
      </div>
      <InventoryWorkspace />
    </>
  );
}
