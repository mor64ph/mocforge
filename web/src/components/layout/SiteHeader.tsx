import Link from "next/link";
import { API_MODE } from "@/lib/api";
import { Tag } from "@/components/ui/Tag";
import styles from "./SiteHeader.module.css";

export function SiteHeader() {
  return (
    <header className={styles.header}>
      <a className={styles.skipLink} href="#main">
        Skip to content
      </a>
      <div className={styles.inner}>
        <Link className={styles.brand} href="/">
          <span className={styles.wordmark}>MOCForge</span>
          <span className={styles.tagline}>Build something new from the sets you own</span>
        </Link>
        {API_MODE === "mock" ? <Tag tone="caution">Mock catalogue</Tag> : null}
      </div>
    </header>
  );
}
