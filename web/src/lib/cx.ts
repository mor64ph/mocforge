/**
 * Join class names, dropping anything falsy.
 *
 * Deliberately not a dependency: the whole need is three lines, and CSS Modules
 * mean the class lists here are short and conditional rather than long utility
 * strings.
 */
export function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter((value): value is string => Boolean(value)).join(" ");
}
