import type { WithheldDesign } from "@/lib/api/contract";
import { MATERIAL_COPY, archetypeCopy } from "@/lib/archetypes";
import { pluralise } from "@/lib/format";
import styles from "./WithheldPanel.module.css";

type Group = {
  archetype: string;
  entries: WithheldDesign[];
};

function groupByArchetype(withheld: readonly WithheldDesign[]): Group[] {
  const groups = new Map<string, Group>();
  for (const entry of withheld) {
    const group = groups.get(entry.archetype) ?? {
      archetype: entry.archetype,
      entries: [],
    };
    group.entries.push(entry);
    groups.set(entry.archetype, group);
  }
  return [...groups.values()];
}

/**
 * Why the generator did not offer something, in its own words.
 *
 * The reasons come straight from the API — they are quantitative ("wall
 * material 38 studs, below the 80 needed") and are the only thing that tells a
 * user what to add. They are shown verbatim rather than being softened.
 */
export function WithheldPanel({ withheld }: { withheld: readonly WithheldDesign[] }) {
  const groups = groupByArchetype(withheld);

  return (
    <div className={styles.groups}>
      {groups.map((group) => {
        const copy = archetypeCopy(group.archetype);
        return (
          <div key={group.archetype} className={styles.group}>
            <div className={styles.groupHead}>
              <h3 className={styles.groupTitle}>
                {copy.label}{" "}
                <span className={styles.needs}>
                  · {group.entries.length}{" "}
                  {pluralise(group.entries.length, "variant")} withheld
                </span>
              </h3>
              <p className={styles.needs}>Wants {MATERIAL_COPY[copy.material]}</p>
            </div>
            <p className={styles.summary}>{copy.summary}</p>
            <dl className={styles.reasons}>
              {group.entries.map((entry) => (
                <div
                  key={`${entry.archetype}-${entry.variant ?? "default"}`}
                  className={styles.pair}
                >
                  <dt className={styles.variant}>{entry.variant ?? "default"}</dt>
                  <dd className={styles.reason}>{entry.reason}</dd>
                </div>
              ))}
            </dl>
          </div>
        );
      })}
    </div>
  );
}
