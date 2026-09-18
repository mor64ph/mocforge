const integer = new Intl.NumberFormat("en-GB");

export function formatCount(value: number): string {
  return integer.format(value);
}

export function formatPercent(fraction: number): string {
  const percent = fraction * 100;
  return `${percent < 10 && percent > 0 ? percent.toFixed(1) : Math.round(percent)}%`;
}

export function formatSeconds(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

/** "2023 · Technic · 905 pieces", skipping whatever the catalogue lacks. */
export function formatSetMeta(set: {
  year: number | null;
  themeName: string | null;
  numParts: number;
}): string {
  const parts: string[] = [];
  if (set.year !== null) parts.push(String(set.year));
  if (set.themeName) parts.push(set.themeName);
  parts.push(
    set.numParts > 0 ? `${formatCount(set.numParts)} pieces` : "piece count unknown",
  );
  return parts.join(" · ");
}

/** A download size, in the units a download dialog would use. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${Math.round(kb)} kB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

export function pluralise(count: number, singular: string, plural?: string): string {
  return count === 1 ? singular : (plural ?? `${singular}s`);
}

/** `#RRGGBB` for a swatch, or null when the catalogue has no RGB for a colour. */
export function swatchColor(rgb: string | null): string | null {
  if (!rgb || !/^[0-9a-f]{6}$/i.test(rgb)) return null;
  return `#${rgb}`;
}
