import styles from "./SiteFooter.module.css";

/**
 * Attribution is a licence and trademark obligation, not decoration — PRD
 * "Attribution (required in shipped product)" and CONTRACT.md behaviour
 * requirement 2. It renders in the root layout, so it is on every page.
 */
export function SiteFooter() {
  return (
    <footer className={styles.footer}>
      <div className={styles.inner}>
        <h2 className={styles.heading}>Data sources and trademark</h2>
        <div className={styles.attribution}>
          <p>
            LEGO catalogue data from{" "}
            <a href="https://rebrickable.com/downloads/" rel="noreferrer noopener">
              Rebrickable
            </a>
            . Part geometry from the{" "}
            <a href="https://library.ldraw.org/" rel="noreferrer noopener">
              LDraw Parts Library
            </a>
            , licensed{" "}
            <a
              href="https://creativecommons.org/licenses/by/2.0/"
              rel="noreferrer noopener"
            >
              CC BY 2.0
            </a>
            .
          </p>
          <p className={styles.disclaimer}>
            LEGO&reg; is a trademark of the LEGO Group, which does not sponsor,
            authorise or endorse this site.
          </p>
        </div>
      </div>
    </footer>
  );
}
