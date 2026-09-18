import type { Design } from "./api/contract";

/**
 * Naming and saving the files a design can be downloaded as.
 *
 * Both names are built from `archetype`, `variant` and the inventory's set
 * numbers, in the engine's own convention — never by parsing `Design.id`,
 * which CONTRACT.md rule 5 declares opaque. The service names its own downloads
 * the same way, so a file saved through the UI and one fetched straight from
 * the API arrive with the same name.
 */
function stem(design: Design, setNums: readonly string[]): string {
  const kind = design.variant
    ? `${design.archetype}-${design.variant}`
    : design.archetype;
  const sets = [...setNums].sort().join("+");
  return `${kind}_${sets || "model"}`;
}

function safe(name: string): string {
  return name.replace(/[^A-Za-z0-9._+-]/g, "_");
}

/** `<archetype>-<variant>_<sets>.ldr`, for LDraw editors. */
export function ldrFilename(design: Design, setNums: readonly string[]): string {
  return safe(`${stem(design, setNums)}.ldr`);
}

/** `mocforge_<archetype>-<variant>_<sets>.zip`, the printable instruction pack. */
export function packageFilename(design: Design, setNums: readonly string[]): string {
  return safe(`mocforge_${stem(design, setNums)}.zip`);
}

/** Save a blob as a file, via a revoked object URL so nothing leaks. */
export function downloadBlob(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function downloadText(filename: string, text: string, mime: string): void {
  downloadBlob(filename, new Blob([text], { type: mime }));
}
