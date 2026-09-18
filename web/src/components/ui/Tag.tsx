import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import styles from "./Tag.module.css";

type TagProps = {
  tone?: "neutral" | "accent" | "success" | "caution";
  children: ReactNode;
};

export function Tag({ tone = "neutral", children }: TagProps) {
  return (
    <span className={cx(styles.tag, tone !== "neutral" && styles[tone])}>{children}</span>
  );
}
